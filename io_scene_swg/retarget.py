"""User-operated FBX/Mixamo -> SWG calibration, preview and action baking."""

from collections import defaultdict
import json
import math
import sys
import time

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Matrix, Quaternion, Vector

from . import animation_io, animation_rig, retarget_math as rm


PREFIX='[SWG Retarget]'


def log(message):
    print(PREFIX+' '+str(message),flush=True)


def invalidate(self,context):
    settings=self if hasattr(self,'calibration') else getattr(self.id_data,'swg_retarget',None)
    if settings is not None:
        settings.calibration=''
        settings.status='Settings changed: calibrate before preview/bake'


class SWGRetargetBone(bpy.types.PropertyGroup):
    enabled:BoolProperty(name='Enabled',default=True,update=invalidate)
    source_bone:StringProperty(name='Source Bone',update=invalidate)
    target_bone:StringProperty(name='SWG Control',update=invalidate)
    required:BoolProperty(default=False,options={'HIDDEN'})


class SWGRetargetSettings(bpy.types.PropertyGroup):
    source:PointerProperty(name='FBX Source',type=bpy.types.Object,poll=lambda self,obj:obj.type=='ARMATURE',update=invalidate)
    target:PointerProperty(name='SWG Rig',type=bpy.types.Object,poll=lambda self,obj:obj.type=='ARMATURE',update=invalidate)
    source_action:PointerProperty(name='Source Action',type=bpy.types.Action)
    mapping:CollectionProperty(type=SWGRetargetBone)
    active_mapping:IntProperty(default=0)
    reference_mode:EnumProperty(name='Reference Pose',items=(
        ('AUTO','Automatic A/T-Pose Correction','Match rest bone directions and compensate for different bone rolls'),
        ('POSE','Use Current Reference Poses','Pose both rigs to the same posture before calibrating')),
        default='AUTO',update=invalidate)
    align_facing:BoolProperty(name='Align Facing and Up',default=True,update=invalidate,
                              description='Infer body axes from hips and head to align the two armatures')
    translation_scale:FloatProperty(name='Motion Scale',default=0,min=0,max=100,
                                    description='Zero uses the calibrated leg-length ratio; positive values override it')
    start_frame:IntProperty(name='Start Frame',default=1)
    end_frame:IntProperty(name='End Frame',default=250)
    frame_step:IntProperty(name='Frame Step',default=1,min=1,max=10)
    motion_mode:EnumProperty(name='Root Motion',items=(
        ('IN_PLACE','In Place','Keep horizontal travel out of the SWG action; retain hip bounce and body rotation'),
        ('LOCOMOTION','SWG Locomotion','Store horizontal travel in separate SWG locomotion channels; preview in place')),
        default='IN_PLACE')
    extract_heading:BoolProperty(name='Extract Facing Rotation',default=False,
                                 description='Store hip heading changes as locomotion rotation; hip sway may need cleanup')
    action_name:StringProperty(name='New Action Name',default='')
    calibration:StringProperty(default='',options={'HIDDEN'})
    status:StringProperty(default='Load a mapping preset, then calibrate',options={'HIDDEN'})
    last_report:StringProperty(default='',options={'HIDDEN'})


def rig_for(obj):
    if obj and obj.get('swg_rig_type')=='FK':return obj
    if animation_rig.is_game_skeleton(obj):
        return obj.get('swg_control_rig_object') or bpy.data.objects.get(obj.get('swg_control_rig',''))
    return None


def inputs(context,settings,allow_discovery=False):
    source=settings.source
    target=rig_for(settings.target)
    if allow_discovery:
        active=context.active_object
        if source is None and active and active.type=='ARMATURE' and not rig_for(active) and not animation_rig.is_game_skeleton(active):
            source=active
        if source is None:
            candidates=[obj for obj in context.scene.objects if obj.type=='ARMATURE'
                        and any(b.name.split(':')[-1]=='Hips' for b in obj.data.bones)]
            if len(candidates)==1:source=candidates[0]
        if target is None:
            candidates=[obj for obj in context.scene.objects if obj.type=='ARMATURE' and obj.get('swg_rig_type')=='FK']
            if len(candidates)==1:target=candidates[0]
    if not source or source.type!='ARMATURE':raise ValueError('Choose the imported FBX armature as FBX Source')
    if not target:raise ValueError('Choose a SWG FK control rig, or create one from the native skeleton first')
    game=animation_rig.game_skeleton_for(target)
    if not game:raise ValueError('The target rig has no native SWG game skeleton')
    if source in (target,game):raise ValueError('FBX source and SWG target must be different armatures')
    return source,target,game


def mixamo_preset(context,settings):
    source,target,game=inputs(context,settings,True)
    settings.source,settings.target=source,target
    names=defaultdict(list)
    for bone in source.data.bones:names[bone.name.split(':')[-1].casefold()].append(bone.name)
    pairs=[('Hips','root'),('Spine','spine1'),('Spine1','spine2'),('Spine2','spine3'),('Neck','neck'),('Head','head')]
    for side,prefix in (('Left','l'),('Right','r')):
        pairs.extend((side+name,prefix+suffix) for name,suffix in (
            ('Shoulder','clav'),('Arm','arm'),('ForeArm','forearm'),('Hand','wrist'),
            ('UpLeg','thigh'),('Leg','shin'),('Foot','ankle'),('ToeBase','toe'),
            ('HandThumb1','thumb01'),('HandThumb2','thumb02'),
            ('HandIndex1','index01'),('HandIndex2','index02'),
            ('HandRing1','ring01'),('HandRing2','ring02')))
    settings.mapping.clear()
    essential={'root','spine1','lthigh','lshin','lankle','rthigh','rshin','rankle','larm','rarm','lforearm','rforearm'}
    for source_name,target_name in pairs:
        row=settings.mapping.add()
        matches=names[source_name.casefold()]
        row.source_bone=matches[0] if len(matches)==1 else source_name
        row.target_bone=target_name
        row.required=target_name in essential
        if len(matches)!=1:log('WARNING: preset source bone missing or ambiguous: '+source_name)
    use_action_range(source,settings)
    settings.calibration=''
    settings.status='Mixamo preset loaded: calibrate next'
    log('Mixamo preset loaded: {} rows; source={} target={}'.format(len(pairs),source.name,target.name))


def use_action_range(source,settings):
    action=settings.source_action or (source.animation_data.action if source.animation_data else None)
    if not action:raise ValueError('Choose a Source Action, or assign one to the FBX armature')
    if not action.fcurves:raise ValueError('The source action has no animation curves')
    settings.start_frame=math.floor(action.frame_range[0])
    settings.end_frame=math.ceil(action.frame_range[1])
    settings.status='Source range: {}..{}'.format(settings.start_frame,settings.end_frame)
    log('Source range: {}..{} ({})'.format(settings.start_frame,settings.end_frame,action.name))


def checked_mapping(settings,source,target,game):
    mapping={}
    warnings=[]
    native={b.name for b in game.data.bones if b.use_deform}
    for row in settings.mapping:
        if not row.enabled:continue
        if row.target_bone in mapping:raise ValueError('Duplicate target mapping: '+row.target_bone)
        if row.target_bone not in native or row.target_bone not in target.data.bones:
            raise ValueError('Target must be a game-joint control, not an output helper: '+row.target_bone)
        if row.source_bone not in source.data.bones:
            message='Missing source bone: {} -> {}'.format(row.source_bone,row.target_bone)
            if row.required:raise ValueError(message)
            warnings.append(message);continue
        mapping[row.target_bone]=row.source_bone
    if not mapping:raise ValueError('Load the Mixamo preset or add bone mappings first')
    roots=[b.name for b in game.data.bones if b.use_deform and b.parent is None]
    root=next((name for name in roots if name in mapping),None)
    if root is None:raise ValueError('Map the SWG root control to the source hips/root bone')
    return mapping,root,warnings


def check_objects(source,target,game):
    for obj in (source,target,game):
        scale=obj.matrix_world.to_scale()
        if min(scale)<1e-8 or obj.matrix_world.to_3x3().determinant()<=0:
            raise ValueError('Negative/zero object scale is unsupported: '+obj.name)
        if max(scale)-min(scale)>max(scale)*0.0001:
            raise ValueError('Apply nonuniform object scale on a working copy before retargeting: '+obj.name)
    if max(abs(target.matrix_world[r][c]-game.matrix_world[r][c]) for r in range(4) for c in range(4))>1e-5:
        raise ValueError('SWG controls and game skeleton object transforms must match. Align the FBX source instead')


def calibrate(context,settings):
    if context.mode!='OBJECT':raise ValueError('Switch to Object Mode to calibrate')
    source,target,game=inputs(context,settings)
    check_objects(source,target,game)
    mapping,root,warnings=checked_mapping(settings,source,target,game)
    animation_io.allow_joint_translation(context,target)
    context.view_layer.update()
    if settings.reference_mode=='POSE':
        depsgraph=context.evaluated_depsgraph_get()
        src_eval,tgt_eval=source.evaluated_get(depsgraph),target.evaluated_get(depsgraph)
        source_frames={b.name:src_eval.matrix_world @ src_eval.pose.bones[b.name].matrix for b in source.data.bones}
        target_frames={b.name:tgt_eval.matrix_world @ tgt_eval.pose.bones[b.name].matrix for b in game.data.bones if b.use_deform}
        neutral={name:rm.flatten(target.pose.bones[name].matrix_basis) for name in target_frames}
    else:
        source_frames={b.name:source.matrix_world @ b.matrix_local for b in source.data.bones}
        target_frames={b.name:target.matrix_world @ target.data.bones[b.name].matrix_local for b in game.data.bones if b.use_deform}
        neutral={name:rm.flatten(Matrix.Identity(4)) for name in target_frames}
    sp={name:frame.translation for name,frame in source_frames.items()}
    tp={name:frame.translation for name,frame in target_frames.items()}
    alignment=Matrix.Identity(3)
    if settings.align_facing:
        required=('head','lthigh','rthigh')
        if not all(name in mapping for name in required):
            raise ValueError('Automatic facing alignment needs head, lthigh and rthigh mappings; disable it for a custom partial mapping')
        src_axes=rm.anatomical_frame(sp,mapping[root],mapping['head'],mapping['lthigh'],mapping['rthigh'])
        tgt_axes=rm.anatomical_frame(tp,root,'head','lthigh','rthigh')
        alignment=tgt_axes @ src_axes.inverted()
    ratios=[]
    for side in ('l','r'):
        chain=(side+'thigh',side+'shin',side+'ankle')
        if all(name in mapping for name in chain):
            source_length=rm.leg_length(sp,[mapping[name] for name in chain])
            if source_length>1e-6:ratios.append(rm.leg_length(tp,chain)/source_length)
    auto_scale=sum(ratios)/len(ratios) if ratios else 1.0
    if not ratios:warnings.append('Leg mappings incomplete: automatic motion scale uses 1.0; set Motion Scale explicitly')
    # The root branches to both hips and the spine. Its display tail may point
    # toward any one child, so it is not an anatomical limb direction. Preserve
    # its reference rotation and transfer only the source pelvis rotation delta.
    # Terminal game joints also have no anatomical child direction: their FK
    # tails are extrapolated for display. Matching those tails introduces false
    # toe/finger/head bends even when the source joint has no animation.
    directional={b.name for b in game.data.bones if any(child.use_deform for child in b.children)}-{root}
    offsets={name:rm.orientation_offset(source_frames[src],target_frames[name],alignment,
                                       settings.reference_mode=='AUTO' and name in directional)
             for name,src in mapping.items()}
    calibration={'version':3,'signature':rm.signature(source,target),'mapping':mapping,'root':root,
        'alignment':rm.flatten(alignment.to_4x4()),'offsets':{name:rm.flatten(value.to_4x4()) for name,value in offsets.items()},
        'source_root':list(sp[mapping[root]]),'target_root':list(tp[root]),
        'target_reference':{name:rm.flatten(value) for name,value in target_frames.items()},
        'neutral':neutral,'auto_scale':auto_scale,'target_world':rm.flatten(target.matrix_world),
        'game_world':rm.flatten(game.matrix_world),'reference_mode':settings.reference_mode,'align_facing':settings.align_facing}
    settings.calibration=json.dumps(calibration)
    settings.status='Calibrated: {} mapped, {} inherited'.format(len(mapping),len(target_frames)-len(mapping))
    lines=[settings.status,'Reference: '+settings.reference_mode,
           'Source: '+source.name,'Target: '+target.name,
           'Object source scale: '+str(tuple(round(v,6) for v in source.matrix_world.to_scale())),
           'Automatic translation scale: {:.6f}'.format(auto_scale)]
    if settings.reference_mode=='AUTO':
        lines.append('Preserved rest orientation for root/terminal controls: '+', '.join(sorted(set(mapping)-directional)))
    for name,src in mapping.items():lines.append('MAP {} -> {}'.format(src,name))
    inherited=sorted(set(target_frames)-set(mapping))
    if inherited:lines.append('Inherited from parents: '+', '.join(inherited))
    lines.extend('WARNING: '+message for message in warnings)
    settings.last_report='\n'.join(lines)
    for line in lines:log(line)
    log('CALIBRATION SUCCESS')
    return calibration


def validate_target(target,game):
    for bone in target.pose.bones:
        if bone.constraints:raise ValueError('Target controls contain constraints. Use a clean SWG FK rig before baking: '+bone.name)
        if bone.name.startswith('SWG_OUTPUT_') and max(abs(bone.matrix_basis[r][c]-float(r==c)) for r in range(4) for c in range(4))>1e-5:
            raise ValueError('Restore hidden SWG output helpers to rest before retargeting')
    if target.animation_data:
        if target.animation_data.drivers:raise ValueError('Target rig has drivers; use a clean SWG FK rig')
        if any(not track.mute and track.strips for track in target.animation_data.nla_tracks):
            raise ValueError('Mute target NLA tracks before preview/bake')
    for bone in game.pose.bones:
        expected=[c for c in bone.constraints if c.type=='COPY_TRANSFORMS' and c.target==target]
        if len(bone.constraints)!=1 or len(expected)!=1:
            raise ValueError('Game skeleton must retain exactly its SWG FK constraint per joint: '+bone.name)


def bake(context,settings,preview=False):
    if context.mode!='OBJECT':raise ValueError('Switch to Object Mode before retargeting')
    source,target,game=inputs(context,settings)
    check_objects(source,target,game)
    validate_target(target,game)
    mapping,root,warnings=checked_mapping(settings,source,target,game)
    if not settings.calibration:raise ValueError('Calibrate before previewing or baking')
    cal=json.loads(settings.calibration)
    if cal.get('version')!=3:
        raise ValueError('Retarget calibration uses an older root/terminal correction: recalibrate')
    if cal['signature']!=rm.signature(source,target) or cal['mapping']!=mapping or cal['reference_mode']!=settings.reference_mode or cal['align_facing']!=settings.align_facing:
        raise ValueError('Mapping or rest skeleton changed: recalibrate')
    for obj,key in ((target,'target_world'),(game,'game_world')):
        reference=rm.matrix(cal[key])
        if max(abs(obj.matrix_world[r][c]-reference[r][c]) for r in range(4) for c in range(4))>1e-5:
            raise ValueError('SWG object placement changed: recalibrate')
    action=settings.source_action or (source.animation_data.action if source.animation_data else None)
    if not action or not action.fcurves:raise ValueError('Choose a source action with animation curves')
    resolved=0
    for curve in action.fcurves:
        try:
            source.path_resolve(curve.data_path)
            resolved+=1
        except (ValueError,KeyError,AttributeError):
            pass
    if resolved==0:
        raise ValueError('Source Action channel names do not match the FBX armature. Choose its original Action or the matching source rig')
    if resolved<len(action.fcurves):
        warnings.append('{} source F-curves do not resolve on this armature'.format(len(action.fcurves)-resolved))
    if source.animation_data and source.animation_data.use_nla and any(not t.mute and t.strips for t in source.animation_data.nla_tracks):
        raise ValueError('Mute source NLA tracks to retarget a single selected action')
    if settings.end_frame<settings.start_frame:raise ValueError('End Frame must be at or after Start Frame')
    if settings.end_frame-settings.start_frame>32766:raise ValueError('Range exceeds the animation exporter limit; shorten it')
    frames=list(range(settings.start_frame,settings.end_frame+1,settings.frame_step))
    if frames[-1]!=settings.end_frame:frames.append(settings.end_frame)
    control_bones=[target.data.bones[b.name] for b in game.data.bones if b.use_deform]
    rows={b.name:[] for b in control_bones}
    expected_poses={}
    check_frames={frames[0],frames[len(frames)//2],frames[-1]}
    alignment=rm.matrix(cal['alignment']).to_3x3()
    offsets={name:rm.matrix(value).to_3x3() for name,value in cal['offsets'].items()}
    neutral={name:rm.matrix(value) for name,value in cal['neutral'].items()}
    target_world=rm.matrix(cal['target_world'])
    game_world=rm.matrix(cal['game_world'])
    game_rotation=rm.rotation(game_world)
    game_inverse=game_world.inverted().to_3x3()
    reference_root=rm.rotation(rm.matrix(cal['target_reference'][root]))
    forward=Vector((0,-1,0))
    forward=game_rotation @ forward
    scale=settings.translation_scale or cal['auto_scale']
    root_reference=Vector(cal['target_root'])
    source_reference=Vector(cal['source_root'])
    first_position,first_heading=None,None
    first_orientations=None
    max_source_angle,max_source_displacement=0.0,0.0
    loc_t,loc_q=[],[]
    previous={}
    scene=context.scene
    saved_frame,saved_subframe=scene.frame_current,scene.frame_subframe
    had_animation_data=source.animation_data is not None
    source.animation_data_create()
    saved_action=source.animation_data.action
    saved_blend=source.animation_data.action_blend_type
    saved_influence=source.animation_data.action_influence
    source_basis=source.matrix_basis.copy()
    source_pose={bone.name:bone.matrix_basis.copy() for bone in source.pose.bones}
    started=time.perf_counter()
    log(('PREVIEW' if preview else 'BAKE')+' START: '+action.name)
    log('Frames {}..{}, step {}, samples {}, scene FPS {:.6f}'.format(frames[0],frames[-1],settings.frame_step,len(frames),scene.render.fps/scene.render.fps_base))
    log('Source Action channels resolved: {}/{}'.format(resolved,len(action.fcurves)))
    log('Motion mode: {}; translation scale {:.6f}; heading extraction {}'.format(settings.motion_mode,scale,settings.extract_heading and settings.motion_mode=='LOCOMOTION'))
    try:
        source.animation_data.action=action
        source.animation_data.action_blend_type='REPLACE'
        source.animation_data.action_influence=1.0
        for sample,frame in enumerate(frames):
            scene.frame_set(frame)
            depsgraph=context.evaluated_depsgraph_get()
            evaluated=source.evaluated_get(depsgraph)
            if max(abs(target.matrix_world[r][c]-target_world[r][c]) for r in range(4) for c in range(4))>1e-5:
                raise ValueError('Target object transforms animate; remove object animation or use a clean SWG rig')
            source_frames={name:evaluated.matrix_world @ evaluated.pose.bones[name].matrix for name in set(mapping.values())}
            for name,value in source_frames.items():
                if any(not math.isfinite(v) for row in value for v in row):
                    raise ValueError('Nonfinite source transform at frame {}: {}'.format(frame,name))
                if value.to_3x3().determinant()<=0:
                    raise ValueError('Mirrored/zero source bone scale at frame {}: {}'.format(frame,name))
            orientations=rm.retarget_orientations(source_frames,mapping,offsets,alignment)
            if first_orientations is None:
                first_orientations={name:value.to_quaternion().normalized() for name,value in orientations.items()}
            for name,value in orientations.items():
                dot=abs(first_orientations[name].dot(value.to_quaternion().normalized()))
                max_source_angle=max(max_source_angle,math.degrees(2*math.acos(min(1.0,dot))))
            position=source_frames[mapping[root]].translation
            if first_position is None:first_position=position.copy()
            max_source_displacement=max(max_source_displacement,scale*(position-first_position).length)
            delta=scale*(alignment @ (position-source_reference))
            root_position=root_reference+Vector((0,0,delta.z))
            travel=scale*(alignment @ (position-first_position));travel.z=0
            heading=rm.heading_angle(orientations[root],reference_root,forward)
            if first_heading is None:first_heading=heading
            yaw=Quaternion((0,0,1),heading-first_heading)
            if settings.motion_mode=='LOCOMOTION' and settings.extract_heading:
                inverse_yaw=yaw.inverted().to_matrix()
                orientations={name:inverse_yaw @ value for name,value in orientations.items()}
                origin=game_world.translation
                root_position=origin+yaw.inverted() @ (root_position-origin)
            poses=rm.target_pose(control_bones,neutral,orientations,target_world,root,root_position)
            if frame in check_frames:expected_poses[frame]={name:value.copy() for name,value in poses.items()}
            for bone in control_bones:
                basis=rm.basis_for_pose(bone,poses[bone.name],poses)
                location,q,bone_scale=basis.decompose()
                if max(abs(v-1) for v in bone_scale)>0.0001:raise ValueError('Reference pose contains unsupported scale: '+bone.name)
                if bone.name in previous and q.dot(previous[bone.name])<0:q.negate()
                previous[bone.name]=q.copy()
                rows[bone.name].append((tuple(location),tuple(q)))
            loc_t.append(tuple(game_inverse @ travel))
            q=(game_rotation.inverted() @ yaw.to_matrix() @ game_rotation).to_quaternion()
            if loc_q and q.dot(Quaternion(loc_q[-1]))<0:q.negate()
            loc_q.append(tuple(q))
            if sample==0 or sample==len(frames)-1 or (sample+1)%100==0:log('Sampled {}/{} (frame {})'.format(sample+1,len(frames),frame))
    finally:
        source.animation_data.action=saved_action
        source.animation_data.action_blend_type=saved_blend
        source.animation_data.action_influence=saved_influence
        source.matrix_basis=source_basis
        for name,basis in source_pose.items():source.pose.bones[name].matrix_basis=basis
        if not had_animation_data:source.animation_data_clear()
        scene.frame_set(saved_frame,subframe=saved_subframe)
    name=settings.action_name.strip() or bpy.path.clean_name(action.name)+'_SWG'
    result=bpy.data.actions.new(('[Preview] ' if preview else '')+name)
    result.use_fake_user=not preview
    result['swg_fps']=scene.render.fps/scene.render.fps_base
    result['swg_retarget_source']=source.name+':'+action.name
    result['swg_retarget_preview']=preview
    result['swg_root_motion_mode']=settings.motion_mode
    result['swg_message_names']='{}'
    result['swg_extra_tracks']='{}'
    for bone in control_bones:
        pose=target.pose.bones[bone.name]
        for index in range(3):animation_io.add_curve(result,pose.path_from_id('location'),index,frames,[row[0][index] for row in rows[bone.name]],bone.name)
        for index in range(4):animation_io.add_curve(result,pose.path_from_id('rotation_quaternion'),index,frames,[row[1][index] for row in rows[bone.name]],bone.name)
    if settings.motion_mode=='LOCOMOTION':
        target[animation_io.LOC_T]=[0.0,0.0,0.0]
        for index in range(3):animation_io.add_curve(result,'["'+animation_io.LOC_T+'"]',index,frames,[row[index] for row in loc_t],'SWG Locomotion')
        duration=(frames[-1]-frames[0])/(scene.render.fps/scene.render.fps_base)
        speed=sum((Vector(b)-Vector(a)).length for a,b in zip(loc_t,loc_t[1:]))/duration if duration>0 else 0.0
        target[animation_io.LOC_SPEED]=speed
        animation_io.add_curve(result,'["'+animation_io.LOC_SPEED+'"]',0,frames,[speed]*len(frames),'SWG Locomotion')
        if settings.extract_heading:
            target[animation_io.LOC_Q]=[1.0,0.0,0.0,0.0]
            for index in range(4):animation_io.add_curve(result,'["'+animation_io.LOC_Q+'"]',index,frames,[row[index] for row in loc_q],'SWG Locomotion')
    target.animation_data_create()
    old_target_action=target.animation_data.action
    old_blend,old_influence=target.animation_data.action_blend_type,target.animation_data.action_influence
    old_target_pose={bone.name:(bone.matrix_basis.copy(),bone.rotation_mode) for bone in target.pose.bones}
    max_position,max_angle=0.0,0.0
    try:
        target.animation_data.action=result
        target.animation_data.action_blend_type='REPLACE';target.animation_data.action_influence=1.0
        for bone in control_bones:
            target.pose.bones[bone.name].rotation_mode='QUATERNION'
            target.pose.bones[bone.name].scale=(1,1,1)
        for frame,poses in expected_poses.items():
            scene.frame_set(frame)
            evaluated=game.evaluated_get(context.evaluated_depsgraph_get())
            for bone in control_bones:
                constraint=next(c for c in game.pose.bones[bone.name].constraints if c.type=='COPY_TRANSFORMS' and c.target==target)
                output=target.data.bones[constraint.subtarget]
                expected=target_world @ poses[bone.name] @ bone.matrix_local.inverted() @ output.matrix_local
                actual=evaluated.matrix_world @ evaluated.pose.bones[bone.name].matrix
                max_position=max(max_position,(expected.translation-actual.translation).length)
                dot=abs(expected.to_quaternion().normalized().dot(actual.to_quaternion().normalized()))
                max_angle=max(max_angle,math.degrees(2*math.acos(min(1.0,dot))))
        if max_position>0.001 or max_angle>0.15:
            raise ValueError('Game-joint validation failed: {:.6f} position / {:.4f} degrees'.format(max_position,max_angle))
    except Exception:
        target.animation_data.action=old_target_action
        target.animation_data.action_blend_type=old_blend;target.animation_data.action_influence=old_influence
        for name,(basis,mode) in old_target_pose.items():
            target.pose.bones[name].rotation_mode=mode;target.pose.bones[name].matrix_basis=basis
        result.use_fake_user=False
        bpy.data.actions.remove(result)
        scene.frame_set(saved_frame,subframe=saved_subframe)
        raise
    if old_target_action:old_target_action.use_fake_user=True
    if max_source_angle<0.05 and max_source_displacement<1e-5:
        warnings.append('Sampled motion is effectively static; verify the Source Action and frame range')
    scene.frame_start=frames[0];scene.frame_end=frames[-1];scene.frame_set(frames[0])
    for obj in context.selected_objects:obj.select_set(False)
    target.hide_set(False);target.select_set(True);context.view_layer.objects.active=target
    settings.status=('Preview ready: ' if preview else 'Bake ready: ')+result.name
    summary=['{} SUCCESS'.format('PREVIEW' if preview else 'BAKE'),
        'Action: '+result.name,'Mappings: {} / {} game joints; {} inherited'.format(len(mapping),len(control_bones),len(control_bones)-len(mapping)),
        'Frames: {}..{}; {} samples'.format(frames[0],frames[-1],len(frames)),
        'Validation: max position {:.8f}; max angle {:.5f} degrees ({} frames)'.format(max_position,max_angle,len(check_frames)),
        'Source motion: max mapped rotation {:.4f} degrees; max root displacement {:.6f}'.format(max_source_angle,max_source_displacement),
        'Source action and rest skeletons preserved; game FK constraints retained',
        'Elapsed {:.3f}s'.format(time.perf_counter()-started)]
    summary.extend('WARNING: '+message for message in warnings)
    if preview:summary.append('Press Space to play. Source edits require a new preview.')
    settings.last_report='\n'.join(summary)
    for line in summary:log(line)
    return result


class SWG_UL_retarget_mapping(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        row=layout.row(align=True)
        row.prop(item,'enabled',text='')
        controls=row.row(align=True);controls.enabled=item.enabled
        if data.source:controls.prop_search(item,'source_bone',data.source.data,'bones',text='')
        else:controls.prop(item,'source_bone',text='')
        controls.label(text='>')
        target=rig_for(data.target)
        if target:controls.prop_search(item,'target_bone',target.data,'bones',text='')
        else:controls.prop(item,'target_bone',text='')
        valid=data.source and target and item.source_bone in data.source.data.bones and item.target_bone in target.data.bones and not item.target_bone.startswith('SWG_OUTPUT_')
        controls.label(text='',icon='CHECKMARK' if valid else 'ERROR')


class SWG_OT_retarget_preset(bpy.types.Operator):
    bl_idname='swg.retarget_mixamo_preset';bl_label='Load Mixamo Preset';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        return run_operator(self,context,lambda s:mixamo_preset(context,s))


class SWG_OT_retarget_range(bpy.types.Operator):
    bl_idname='swg.retarget_source_range';bl_label='Use Source Action Range';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        return run_operator(self,context,lambda s:use_action_range(inputs(context,s)[0],s))


class SWG_OT_retarget_add_mapping(bpy.types.Operator):
    bl_idname='swg.retarget_add_mapping';bl_label='Add Mapping';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        s=context.scene.swg_retarget;s.mapping.add();s.active_mapping=len(s.mapping)-1;invalidate(s,context)
        return {'FINISHED'}


class SWG_OT_retarget_remove_mapping(bpy.types.Operator):
    bl_idname='swg.retarget_remove_mapping';bl_label='Remove Mapping';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        s=context.scene.swg_retarget
        if s.mapping:s.mapping.remove(min(s.active_mapping,len(s.mapping)-1));s.active_mapping=max(0,s.active_mapping-1)
        invalidate(s,context);return {'FINISHED'}


class SWG_OT_retarget_calibrate(bpy.types.Operator):
    bl_idname='swg.retarget_calibrate';bl_label='Calibrate';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        return run_operator(self,context,lambda s:calibrate(context,s))


class SWG_OT_retarget_preview(bpy.types.Operator):
    bl_idname='swg.retarget_preview';bl_label='Preview Retarget';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        return run_operator(self,context,lambda s:bake(context,s,True))


class SWG_OT_retarget_bake(bpy.types.Operator):
    bl_idname='swg.retarget_bake';bl_label='Bake to New Action';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        return run_operator(self,context,lambda s:bake(context,s,False))


class SWG_OT_retarget_report(bpy.types.Operator):
    bl_idname='swg.retarget_print_report';bl_label='Print Last Report'
    def execute(self,context):
        log(context.scene.swg_retarget.last_report or 'No report yet')
        self.report({'INFO'},'Retarget report printed to system console');return {'FINISHED'}


def run_operator(operator,context,function):
    settings=context.scene.swg_retarget
    try:
        function(settings)
    except (ValueError,RuntimeError,KeyError) as error:
        settings.status='ERROR: '+str(error);settings.last_report=settings.status
        log(settings.status);operator.report({'ERROR'},str(error));return {'CANCELLED'}
    operator.report({'INFO'},settings.status)
    if any(line.startswith('WARNING:') for line in settings.last_report.splitlines()):
        operator.report({'WARNING'},'Completed with warnings; inspect the retarget report')
    return {'FINISHED'}


class SWG_PT_retarget(bpy.types.Panel):
    bl_label='Retarget FBX';bl_idname='SWG_PT_retarget';bl_parent_id='SWG_PT_animation'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='SWG'
    def draw(self,context):
        layout=self.layout;s=context.scene.swg_retarget
        layout.prop(s,'source');layout.prop(s,'source_action');layout.prop(s,'target')
        if s.source_action is None:layout.label(text='Uses the FBX source active Action')
        layout.operator('swg.retarget_mixamo_preset')
        row=layout.row();row.template_list('SWG_UL_retarget_mapping','',s,'mapping',s,'active_mapping',rows=6)
        column=row.column(align=True);column.operator('swg.retarget_add_mapping',text='+');column.operator('swg.retarget_remove_mapping',text='-')
        layout.prop(s,'reference_mode');layout.prop(s,'align_facing');layout.operator('swg.retarget_calibrate')
        row=layout.row(align=True);row.prop(s,'start_frame');row.prop(s,'end_frame')
        layout.operator('swg.retarget_source_range');layout.prop(s,'frame_step')
        layout.prop(s,'translation_scale');layout.prop(s,'motion_mode')
        if s.motion_mode=='LOCOMOTION':layout.prop(s,'extract_heading')
        layout.prop(s,'action_name')
        layout.operator('swg.retarget_preview');layout.operator('swg.retarget_bake')
        layout.label(text=s.status,icon='ERROR' if s.status.startswith('ERROR') else 'INFO')
        layout.operator('swg.retarget_print_report')
        if sys.platform=='win32':layout.operator('wm.console_toggle',text='Toggle System Console')
        else:layout.label(text='Reports are also printed to Blender stdout')
