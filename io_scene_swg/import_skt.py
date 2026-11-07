# SKT Import / Export
#
# Authors:
# Tim "RhapsodyInGeek" Maccabe (https://github.com/RhapsodyInGeek)
# Vera "sinewavey" Lux (https://github.com/sinewavey)
# Nick "NoStyleGuy" Rafalski (https://github.com/nostyleguy)
# (c) 2025
#
# Native joint frames are retained for animation/export. Optional FK controls
# provide readable connected bones without changing those frames.

import os
import bpy
from . import swg_types, skeleton_math


def import_skt(context, filepath, *, lod_0_only=True, connect_bones=False,
               create_control_rig=False, bone_display_length=0.06,
               show_bone_names=False, show_bone_axes=False):
    # Validate every requested LOD before creating any scene objects.
    skeletons = []
    for index in range(10):
        skeleton = swg_types.SktFile(filepath)
        if not skeleton.load(index):
            break
        matrices = skeleton_math.bind_matrices(skeleton)
        skeletons.append((skeleton, matrices))
        if lod_0_only:
            break
    if not skeletons:
        raise ValueError("No supported skeleton LODs found in " + filepath)
    if context.mode != 'OBJECT':
        raise ValueError("Switch to Object Mode before importing a skeleton")

    name = os.path.splitext(os.path.basename(filepath))[0]
    collection = bpy.data.collections.new(name + ".skt")
    context.scene.collection.children.link(collection)
    for obj in context.selected_objects:
        obj.select_set(False)
    imported = []
    for index, (skeleton, matrices) in enumerate(skeletons):
        data = bpy.data.armatures.new(name + "_skt_l" + str(index))
        obj = bpy.data.objects.new(data.name, data)
        collection.objects.link(obj)
        obj.show_in_front = True
        data.display_type = 'STICK'
        data.relation_line_position = 'HEAD'
        data.show_axes = show_bone_axes
        data.show_names = show_bone_names
        data["swg_skeleton_version"] = skeleton_math.SKELETON_VERSION
        data["swg_source_path"] = os.path.abspath(filepath)
        data["swg_lod"] = index
        context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        bones = []
        for i, joint_name in enumerate(skeleton.joint_names):
            bone = data.edit_bones.new(joint_name)
            skeleton_math.set_bone_frame(bone, matrices[i], max(bone_display_length, 0.001))
            bone.use_deform = True
            bone["RPRE"] = skeleton.joint_pre_rotations[i]
            bone["RPST"] = skeleton.joint_post_rotations[i]
            bone["BPRO"] = skeleton.joint_bind_rotations[i]
            bone["BPTR"] = skeleton.joint_translations[i]
            bone["JROR"] = skeleton.joint_rotation_order[i]
            bone["swg_joint_index"] = i
            bones.append(bone)
        for i, parent in enumerate(skeleton.joint_parents):
            if parent >= 0:
                bones[i].parent = bones[parent]
        bpy.ops.object.mode_set(mode='OBJECT')
        for bone in obj.pose.bones:
            bone.rotation_mode = 'QUATERNION'
        obj.select_set(False)
        obj.hide_set(index > 0)
        imported.append(obj)
    first = imported[0]
    first.select_set(True)
    context.view_layer.objects.active = first
    # Old scripts using connect_bones now receive separate readable controls.
    if create_control_rig or connect_bones:
        from .animation_rig import create_fk_rig
        create_fk_rig(context, first)
    return {'FINISHED'}
