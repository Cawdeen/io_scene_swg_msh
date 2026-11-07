"""Bake between SWG joint deltas and Blender actions on native/FK armatures."""

from collections import defaultdict
import json
import hashlib
import math
from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
from mathutils import Matrix, Quaternion, Vector

from . import ans_format, animation_rig, skeleton_math


LOC_T = 'swg_locomotion_translation'
LOC_Q = 'swg_locomotion_rotation'
LOC_SPEED = 'swg_locomotion_speed'


def targets(context):
    obj=context.active_object
    source=animation_rig.game_skeleton_for(obj)
    if source is None and obj and obj.type=='MESH':
        source=animation_rig.game_skeleton_for(obj.get('swg_weight_skeleton'))
        if source is None:
            source=next((animation_rig.game_skeleton_for(m.object) for m in obj.modifiers
                         if m.type=='ARMATURE' and animation_rig.game_skeleton_for(m.object)),None)
    if source is None:
        raise ValueError("Select a native SWG skeleton, its FK rig, or a mesh bound to it")
    rig=source.get('swg_control_rig_object')
    if not rig:
        rig=bpy.data.objects.get(source.get('swg_control_rig',''))
    return source,rig or source


def native_bones(source):
    bones=[b for b in source.data.bones if b.use_deform]
    names={b.name for b in bones}
    if not bones or any(b.parent and b.parent.name not in names for b in bones):
        raise ValueError("Animation requires a complete native game-joint hierarchy")
    return bones


def rest_data(bones):
    return {b.name:skeleton_math.export_joint_data(b,b.parent) for b in bones}


def clip_frames(clip,bones,rest,frame):
    worlds={}

    def resolve(bone):
        if bone.name not in worlds:
            pre,post,translation,bind,_=rest[bone.name]
            track=clip.tracks.get(bone.name.lower())
            q,t=track.sample(frame) if track else ((1,0,0,0),(0,0,0))
            rotation=Quaternion(post) @ Quaternion(q) @ Quaternion(bind) @ Quaternion(pre)
            local=Matrix.Translation(Vector(translation)+Vector(t)) @ rotation.to_matrix().to_4x4()
            worlds[bone.name]=resolve(bone.parent) @ local if bone.parent else local
        return worlds[bone.name]

    return {bone.name:skeleton_math.SWG_TO_BLENDER @ resolve(bone) @ skeleton_math.JOINT_AXIS_REFLECTION
            for bone in bones}


def pose_basis(bone,desired,frames):
    if bone.parent:
        return bone.matrix_local.inverted() @ bone.parent.matrix_local @ frames[bone.parent.name].inverted() @ desired
    return bone.matrix_local.inverted() @ desired


def add_curve(action,path,index,frames,values,group):
    curve=action.fcurves.new(path,index=index,action_group=group)
    curve.keyframe_points.add(len(frames))
    curve.keyframe_points.foreach_set('co',[value for pair in zip(frames,values) for value in pair])
    for key in curve.keyframe_points:
        key.interpolation='LINEAR'
    curve.update()


def allow_joint_translation(context,owner):
    """Upgrade early FK rigs without changing their rest frames or parenting."""
    if owner.get('swg_rig_type')!='FK' or not any(b.use_connect for b in owner.data.bones):
        return
    active=context.view_layer.objects.active
    selected=list(context.selected_objects)
    mode=context.mode
    hidden=owner.hide_get()
    if mode=='POSE':bpy.ops.object.mode_set(mode='OBJECT')
    if owner.data.users>1:owner.data=owner.data.copy()
    for obj in context.selected_objects:obj.select_set(False)
    owner.hide_set(False);owner.select_set(True);context.view_layer.objects.active=owner
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in owner.data.edit_bones:bone.use_connect=False
    bpy.ops.object.mode_set(mode='OBJECT')
    owner.select_set(False);owner.hide_set(hidden)
    for obj in selected:
        if not obj.hide_get():obj.select_set(True)
    context.view_layer.objects.active=active
    if mode=='POSE':bpy.ops.object.mode_set(mode='POSE')


def import_animation(context,filepath,start_frame=1,set_fps=True,strict_joints=False):
    source,owner=targets(context)
    if context.mode not in ('OBJECT','POSE'):
        raise ValueError("Leave Edit Mode before importing animation")
    clip=ans_format.load(filepath)
    if clip.last_frame>32766:
        raise ValueError("Animation is too long for the current baked-action import (maximum 32767 samples)")
    bones=native_bones(source)
    unknown=sorted(set(clip.tracks)-{b.name.lower() for b in bones})
    if len(unknown)==len(clip.tracks):
        raise ValueError("Animation has no joint names matching the selected skeleton")
    if unknown and strict_joints:
        raise ValueError("Animation joints are absent from this skeleton: " + ', '.join(unknown[:12]))
    if owner==source and any(b.constraints for b in source.pose.bones):
        raise ValueError("Use the SWG FK rig or remove constraints before importing directly onto game joints")
    if owner.animation_data:
        if owner.animation_data.drivers or any(not track.mute and track.strips for track in owner.animation_data.nla_tracks):
            raise ValueError("Mute NLA tracks and remove drivers before importing a new action; export can bake them")
    if owner!=source and any(b.name not in owner.data.bones for b in bones):
        raise ValueError("The FK rig is missing controls for this skeleton")
    rest=rest_data(bones)
    scene=context.scene
    fps=clip.fps if set_fps else scene.render.fps/scene.render.fps_base
    frames=[start_frame+i*fps/clip.fps for i in range(clip.last_frame+1)]
    curves={bone.name:[] for bone in bones}
    previous={}
    object_conversion=owner.matrix_world.inverted() @ source.matrix_world
    for frame in range(clip.last_frame+1):
        desired=clip_frames(clip,bones,rest,frame)
        if owner!=source:
            desired={name:object_conversion @ matrix @ source.data.bones[name].matrix_local.inverted()
                     @ owner.data.bones[name].matrix_local for name,matrix in desired.items()}
        for bone in bones:
            basis=pose_basis(owner.data.bones[bone.name],desired[bone.name],desired)
            location,rotation,scale=basis.decompose()
            if bone.name in previous and rotation.dot(previous[bone.name])<0:
                rotation.negate()
            previous[bone.name]=rotation.copy()
            curves[bone.name].append((tuple(location),tuple(rotation)))
    # Everything above is read-only; create and assign the new action only
    # after decoding, matching and calculating the complete bake successfully.
    allow_joint_translation(context,owner)
    action=bpy.data.actions.new(Path(filepath).stem)
    action.use_fake_user=True
    action['swg_source_format']=clip.source_format
    action['swg_source_path']=str(Path(filepath).resolve())
    action['swg_fps']=clip.fps
    action['swg_start_frame']=start_frame
    action['swg_last_frame']=clip.last_frame
    action['swg_unmapped_joints']=json.dumps(unknown)
    action['swg_import_warnings']=json.dumps(clip.warnings)
    extra_tracks={}
    for name in unknown:
        key='swg_extra_'+hashlib.sha1(name.encode('ascii')).hexdigest()[:16]
        extra_tracks[name]=key
        owner[key]=[1.0,0.0,0.0,0.0,0.0,0.0,0.0]
        values=[]
        for frame in range(clip.last_frame+1):
            q,t=clip.tracks[name].sample(frame)
            q=ans_format.normalize(q)
            if values and sum(a*b for a,b in zip(values[-1][:4],q))<0:q=tuple(-v for v in q)
            values.append(tuple(q)+tuple(t))
        for index in range(7):
            add_curve(action,'["'+key+'"]',index,frames,[value[index] for value in values],'Extra Joint: '+name)
    action['swg_extra_tracks']=json.dumps(extra_tracks)
    for bone in bones:
        pose=owner.pose.bones[bone.name]
        pose.rotation_mode='QUATERNION'
        for index in range(3):
            add_curve(action,pose.path_from_id('location'),index,frames,[row[0][index] for row in curves[bone.name]],bone.name)
        for index in range(4):
            add_curve(action,pose.path_from_id('rotation_quaternion'),index,frames,[row[1][index] for row in curves[bone.name]],bone.name)
    conversion=skeleton_math.SWG_TO_BLENDER
    inverse=skeleton_math.BLENDER_TO_SWG
    if clip.locomotion_translation:
        owner[LOC_T]=[0.0,0.0,0.0]
        values=[conversion.to_3x3() @ Vector(clip.locomotion_translation.sample(i)) for i in range(clip.last_frame+1)]
        for index in range(3):
            add_curve(action,'["'+LOC_T+'"]',index,frames,[value[index] for value in values],'SWG Locomotion')
        owner[LOC_SPEED]=clip.average_speed
        add_curve(action,'["'+LOC_SPEED+'"]',0,frames,[clip.average_speed]*len(frames),'SWG Locomotion')
    if clip.locomotion_rotation:
        owner[LOC_Q]=[1.0,0.0,0.0,0.0]
        values=[]
        for i in range(clip.last_frame+1):
            q=(conversion @ Quaternion(clip.locomotion_rotation.sample(i)).to_matrix().to_4x4() @ inverse).to_quaternion()
            if values and q.dot(values[-1])<0:q.negate()
            values.append(q)
        for index in range(4):
            add_curve(action,'["'+LOC_Q+'"]',index,frames,[q[index] for q in values],'SWG Locomotion')
    marker_names={}
    marker_timing=[]
    for message in clip.messages:
        for frame in message['frames']:
            marker=action.pose_markers.new(message['name'])
            marker.frame=round(start_frame+frame*fps/clip.fps)
            marker_names[marker.name]=message['name']
            marker_timing.append({'name':marker.name,'frame':marker.frame,'exact':start_frame+frame*fps/clip.fps})
    action['swg_message_names']=json.dumps(marker_names)
    action['swg_message_timing']=json.dumps(marker_timing)
    owner.animation_data_create()
    if owner.animation_data.action:
        owner.animation_data.action.use_fake_user=True
    owner.animation_data.action=action
    owner.animation_data.action_blend_type='REPLACE'
    owner.animation_data.action_influence=1.0
    # Remove stale nonanimated channels from the previous pose/action. Scale is
    # intentionally reset because the SWG animation formats have no scale keys.
    for bone in owner.pose.bones:
        if bone.name in curves:
            bone.scale=(1,1,1)
    if set_fps:
        scene.render.fps=max(1,round(clip.fps))
        scene.render.fps_base=scene.render.fps/clip.fps
    scene.frame_start=start_frame
    scene.frame_end=math.ceil(frames[-1])
    scene.frame_set(start_frame)
    return action,unknown


def export_animation(context,filepath,start_frame=None,end_frame=None,fps=None):
    source,owner=targets(context)
    if context.mode not in ('OBJECT','POSE'):
        raise ValueError("Leave Edit Mode before exporting animation")
    scene=context.scene
    action=owner.animation_data.action if owner.animation_data else None
    active_nla=owner.animation_data and owner.animation_data.use_nla and any(not t.mute and t.strips for t in owner.animation_data.nla_tracks)
    action_range=action.frame_range if action and action.fcurves and not active_nla else (scene.frame_start,scene.frame_end)
    start_frame=action_range[0] if start_frame is None else start_frame
    end_frame=action_range[1] if end_frame is None else end_frame
    scene_fps=scene.render.fps/scene.render.fps_base
    fps=(action.get('swg_fps',scene_fps) if action and not active_nla else scene_fps) if fps is None else fps
    if not math.isfinite(fps) or fps<=0 or end_frame<start_frame:
        raise ValueError("Invalid export frame range or frame rate")
    last_frame=round((end_frame-start_frame)*fps/scene_fps)
    if not 0<=last_frame<=32766:
        raise ValueError("Export must contain between 1 and 32767 samples; shorten the range")
    bones=native_bones(source)
    rest=rest_data(bones)
    rows={b.name:[] for b in bones}
    old_frame,old_subframe=scene.frame_current,scene.frame_subframe
    actions=[action] if action else []
    if owner.animation_data and owner.animation_data.use_nla:
        nla_actions=[strip.action for track in owner.animation_data.nla_tracks if not track.mute
                     for strip in track.strips if strip.action and not strip.mute]
        if any(a.pose_markers for a in nla_actions):
            raise ValueError("NLA message timing is not supported yet. Bake NLA motion and message markers to an active action before export")
        actions.extend(nla_actions)
    paths={curve.data_path for a in actions for curve in a.fcurves}
    has_t='["'+LOC_T+'"]' in paths
    has_q='["'+LOC_Q+'"]' in paths
    extra_tracks={name:key for a in actions for name,key in json.loads(a.get('swg_extra_tracks','{}')).items()
                  if '["'+key+'"]' in paths}
    extra_rows={name:[] for name in extra_tracks if name not in rows}
    locomotion_t,locomotion_q=[],[]
    average_speed=0.0
    first_object_matrix=None
    try:
        for frame in range(last_frame+1):
            time=start_frame+frame*scene_fps/fps
            scene.frame_set(math.floor(time),subframe=time-math.floor(time))
            depsgraph=context.evaluated_depsgraph_get()
            evaluated=source.evaluated_get(depsgraph)
            if first_object_matrix is None:first_object_matrix=evaluated.matrix_world.copy()
            if max(abs(evaluated.matrix_world[r][c]-first_object_matrix[r][c]) for r in range(4) for c in range(4))>1e-5:
                raise ValueError("Game-skeleton object transforms animate. Bake object motion to root-joint or locomotion channels before export")
            native={bone.name:skeleton_math.BLENDER_TO_SWG @ evaluated.pose.bones[bone.name].matrix
                    @ skeleton_math.JOINT_AXIS_REFLECTION for bone in bones}
            for bone in bones:
                local=native[bone.parent.name].inverted() @ native[bone.name] if bone.parent else native[bone.name]
                location,rotation,scale=local.decompose()
                rigid=rotation.to_matrix()
                if max(abs(value-1) for value in scale)>0.0001 or max(abs(local[r][c]-rigid[r][c]) for r in range(3) for c in range(3))>0.0001:
                    raise ValueError("SWG has no animated scale/shear channels: " + bone.name)
                pre,post,translation,bind,_=rest[bone.name]
                delta=Quaternion(post).inverted() @ rotation @ Quaternion(pre).inverted() @ Quaternion(bind).inverted()
                delta.normalize()
                if rows[bone.name] and delta.dot(Quaternion(rows[bone.name][-1][0]))<0:delta.negate()
                rows[bone.name].append((tuple(delta),tuple(location-Vector(translation))))
            evaluated_owner=owner.evaluated_get(depsgraph)
            for name in extra_rows:
                value=evaluated_owner.get(extra_tracks[name])
                if value is None or len(value)!=7:
                    raise ValueError("Extra animation joint channel is missing: "+name)
                extra_rows[name].append((ans_format.normalize(tuple(value[:4])),tuple(value[4:])))
            if has_t:
                vector=skeleton_math.BLENDER_TO_SWG.to_3x3() @ Vector(evaluated_owner.get(LOC_T,(0,0,0)))
                locomotion_t.append((frame,tuple(vector)))
                if frame==0:average_speed=float(evaluated_owner.get(LOC_SPEED,0.0))
            if has_q:
                q=Quaternion(evaluated_owner.get(LOC_Q,(1,0,0,0))).normalized()
                q=(skeleton_math.BLENDER_TO_SWG @ q.to_matrix().to_4x4() @ skeleton_math.SWG_TO_BLENDER).to_quaternion()
                locomotion_q.append((frame,tuple(q)))
    finally:
        scene.frame_set(old_frame,subframe=old_subframe)
    rows.update(extra_rows)
    tracks={name:ans_format.Track(ans_format.Channel([(i,row[0]) for i,row in enumerate(values)],True),
            tuple(ans_format.Channel([(i,row[1][axis]) for i,row in enumerate(values)]) for axis in range(3)))
            for name,values in rows.items()}
    messages=defaultdict(list)
    if action:
        names=json.loads(action.get('swg_message_names','{}'))
        timing=json.loads(action.get('swg_message_timing','[]'))
        for marker in action.pose_markers:
            record=next((row for row in timing if row['name']==marker.name and row['frame']==marker.frame),None)
            exact=record['exact'] if record else marker.frame
            if start_frame-1e-5<=exact<=end_frame+1e-5:
                frame=round((exact-start_frame)*fps/scene_fps)
                messages[names.get(marker.name,marker.name)].append(frame)
    clip=ans_format.Clip(fps,last_frame,tracks,
        [{'name':name,'frames':sorted(set(frames))} for name,frames in messages.items()],
        ans_format.Channel(locomotion_t) if has_t else None,
        ans_format.Channel(locomotion_q,True) if has_q else None,average_speed)
    ans_format.save(filepath,clip)
    return clip


class SWG_OT_import_animation(bpy.types.Operator,ImportHelper):
    bl_idname='import_scene.swg_ans'
    bl_label='Import SWG Animation'
    bl_options={'REGISTER','UNDO'}
    filename_ext='.ans'
    filter_glob:StringProperty(default='*.ans',options={'HIDDEN'})
    start_frame:IntProperty(name='Start Frame',default=1,min=0)
    set_fps:BoolProperty(name='Use Animation Frame Rate',default=True)
    strict_joints:BoolProperty(name='Require All Joint Names',default=False)

    @classmethod
    def poll(cls,context):
        try:targets(context);return context.mode in ('OBJECT','POSE')
        except ValueError:return False

    def invoke(self,context,event):
        from . import support
        directory=support.asset_directory(context,'appearance/animation')
        if directory:self.filepath=directory
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self,context):
        try:
            action,unknown=import_animation(context,self.filepath,self.start_frame,self.set_fps,self.strict_joints)
        except (ValueError,OSError) as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}
        self.report({'INFO'},'Imported action: '+action.name)
        if unknown:self.report({'WARNING'},'Preserved extra joint channels (not posed): '+', '.join(unknown[:10]))
        warnings=json.loads(action.get('swg_import_warnings','[]'))
        if warnings:self.report({'WARNING'},warnings[0])
        return {'FINISHED'}


class SWG_OT_export_animation(bpy.types.Operator,ExportHelper):
    bl_idname='export_scene.swg_ans'
    bl_label='Export SWG Animation'
    filename_ext='.ans'
    filter_glob:StringProperty(default='*.ans',options={'HIDDEN'})
    start_frame:FloatProperty(name='Start Frame',default=1,precision=3)
    end_frame:FloatProperty(name='End Frame',default=30,precision=3)
    fps:FloatProperty(name='Output FPS',default=30,min=1,max=240)

    @classmethod
    def poll(cls,context):
        return SWG_OT_import_animation.poll(context)

    def invoke(self,context,event):
        _,owner=targets(context)
        action=owner.animation_data.action if owner.animation_data else None
        active_nla=owner.animation_data and owner.animation_data.use_nla and any(not t.mute and t.strips for t in owner.animation_data.nla_tracks)
        self.start_frame,self.end_frame=action.frame_range if action and action.fcurves and not active_nla else (context.scene.frame_start,context.scene.frame_end)
        self.fps=action.get('swg_fps',context.scene.render.fps/context.scene.render.fps_base) if action and not active_nla else context.scene.render.fps/context.scene.render.fps_base
        self.filepath=bpy.path.clean_name(action.name if action else owner.name)+'.ans'
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self,context):
        try:clip=export_animation(context,self.filepath,self.start_frame,self.end_frame,self.fps)
        except (ValueError,OSError) as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}
        self.report({'INFO'},'Exported KFAT animation: {} joints, {} samples'.format(len(clip.tracks),clip.last_frame+1))
        return {'FINISHED'}
