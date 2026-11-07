"""Readable FK controls, kept separate from the exportable SWG joint frames."""

import bpy
from mathutils import Vector
from .skeleton_math import SKELETON_VERSION, set_bone_frame


def is_game_skeleton(obj):
    return (obj is not None and obj.type == 'ARMATURE'
            and obj.data.get("swg_skeleton_version") == SKELETON_VERSION)


def game_skeleton_for(obj):
    if is_game_skeleton(obj):
        return obj
    if obj and obj.get("swg_rig_type") == "FK":
        source = obj.get("swg_game_skeleton_object")
        if is_game_skeleton(source):
            return source
        source = bpy.data.objects.get(obj.get("swg_game_skeleton", ""))
        if is_game_skeleton(source):
            return source
    return None


def create_fk_rig(context, source):
    if not is_game_skeleton(source):
        raise ValueError("Import a skeleton with the updated SWG importer first")
    if source.get("swg_control_rig_object") or source.get("swg_control_rig", "") in bpy.data.objects:
        raise ValueError("This skeleton already has a control rig")
    if source.mode != 'OBJECT':
        raise ValueError("Switch to Object Mode before creating a control rig")
    if source.animation_data or any(b.constraints for b in source.pose.bones):
        raise ValueError("Create the rig before adding animation or constraints to the game skeleton")

    data = bpy.data.armatures.new(source.name + "_controls")
    rig = bpy.data.objects.new(data.name, data)
    source.users_collection[0].objects.link(rig)
    rig.matrix_world = source.matrix_world.copy()
    rig.show_in_front = True
    data.display_type = 'OCTAHEDRAL'
    rig["swg_rig_type"] = "FK"
    rig["swg_game_skeleton"] = source.name
    rig["swg_game_skeleton_object"] = source
    context.view_layer.objects.active = rig
    for obj in context.selected_objects:
        obj.select_set(False)
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    controls = {}
    outputs = {}
    # Controls follow joint positions; hidden output bones retain native axes.
    for original in source.data.bones:
        control = data.edit_bones.new(original.name)
        control.head = original.head_local
        children = list(original.children)
        candidates = [child for child in children
                      if (child.head_local - original.head_local).length > 1e-6]
        if candidates:
            # Prefer the longest branch for a readable central chain.
            child = max(candidates, key=lambda b: (b.head_local - original.head_local).length)
            control.tail = child.head_local
        elif original.parent and (original.head_local - original.parent.head_local).length > 1e-6:
            direction = original.head_local - original.parent.head_local
            control.tail = control.head + direction * 0.35
        else:
            control.tail = control.head + original.matrix_local.to_3x3() @ Vector((0, 0.06, 0))
        control.align_roll(original.matrix_local.to_3x3().col[2])
        control.use_deform = False
        controls[original.name] = control
    for original in source.data.bones:
        if original.parent:
            control = controls[original.name]
            control.parent = controls[original.parent.name]
            # Meeting tails/heads provide the readable skeleton shape. Do not
            # weld them: Blender suppresses connected children's translation
            # channels, which SWG uses for telescoping/offset joint animation.
            control.use_connect = False
        output = data.edit_bones.new("SWG_OUTPUT_" + original.name)
        set_bone_frame(output, original.matrix_local, original.length)
        output.parent = controls[original.name]
        output.use_deform = False
        outputs[original.name] = output.name
    bpy.ops.object.mode_set(mode='OBJECT')
    for name, output_name in outputs.items():
        data.bones[output_name].hide = True
        control = rig.pose.bones[name]
        control.rotation_mode = 'QUATERNION'
        # Child joint positions remain fixed; root translation moves the rig.
        control.lock_location = (source.data.bones[name].parent is not None,) * 3
        control.lock_scale = (True, True, True)
        constraint = source.pose.bones[name].constraints.new('COPY_TRANSFORMS')
        constraint.name = "SWG FK Controls"
        constraint.target = rig
        constraint.subtarget = output_name
        constraint.target_space = 'WORLD'
        constraint.owner_space = 'WORLD'
    source["swg_control_rig"] = rig.name
    source["swg_control_rig_object"] = rig
    source.hide_set(True)
    context.view_layer.update()
    return rig


class SWG_OT_create_fk_rig(bpy.types.Operator):
    bl_idname = "swg.create_fk_rig"
    bl_label = "Create FK Control Rig"
    bl_description = "Create connected animation controls while preserving the SWG game joints"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and is_game_skeleton(context.active_object)

    def execute(self, context):
        try:
            rig = create_fk_rig(context, context.active_object)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, "Created FK controls: " + rig.name)
        return {'FINISHED'}


class SWG_PT_animation(bpy.types.Panel):
    bl_label = "SWG Animation"
    bl_idname = "SWG_PT_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SWG"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator('import_scene.swg_ans', text='Import Animation')
        row.operator('export_scene.swg_ans', text='Export Animation')
        from . import animation_io
        try:
            _, owner = animation_io.targets(context)
            if owner.animation_data:
                layout.template_ID(owner.animation_data, 'action')
        except ValueError:
            pass
        layout.label(text='Locomotion channels preview in place.')
        layout.operator("import_scene.skt", text="Import SWG Skeleton", icon='ARMATURE_DATA')
        obj = context.active_object
        if is_game_skeleton(obj):
            layout.label(text="Game skeleton: " + obj.name)
            layout.label(text="Skeleton LOD: " + str(obj.data.get("swg_lod", 0)))
            layout.operator("swg.create_fk_rig", icon='POSE_HLT')
        elif obj and obj.get("swg_rig_type") == "FK":
            layout.label(text="FK controls: " + obj.name)
            layout.label(text="Select bones in Pose Mode to animate.")
            layout.prop(obj.data, "show_names", text="Bone Names")
            layout.prop(obj.data, "show_axes", text="Bone Axes")
        else:
            layout.label(text="Select an imported game skeleton to add controls.")
        if is_game_skeleton(obj) or (obj and obj.get("swg_rig_type") == "FK"):
            layout.operator("export_scene.skt", text="Export Rest Skeleton", icon='EXPORT')
            layout.label(text="SKT exports rest joints, not animation.")
