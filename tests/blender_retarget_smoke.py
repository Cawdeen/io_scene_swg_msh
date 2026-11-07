"""Blender --background --factory-startup --python-exit-code 1
--python tests/blender_retarget_smoke.py -- D:/r3git/dsrc [source_fixture.blend]

Tests a generated T-pose/unit-scaled source by default. An optional isolated
library fixture exercises the user's imported FBX without changing their scene.
"""
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
addon_utils.enable('io_scene_swg',default_set=True)
from io_scene_swg import animation_io,animation_rig,ans_format,retarget,swg_types


def activate(obj):
    for other in bpy.context.selected_objects:other.select_set(False)
    obj.select_set(True);bpy.context.view_layer.objects.active=obj


def near(a,b,tolerance=0.001):
    assert max(abs(a[r][c]-b[r][c]) for r in range(4) for c in range(4))<tolerance,(a,b)


def synthetic_source():
    data=bpy.data.armatures.new('mixamo_test')
    obj=bpy.data.objects.new(data.name,data)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world=Matrix.Rotation(1.5707963267948966,4,'X') @ Matrix.Scale(0.01,4)
    positions={'Hips':(0,0,1),'Spine':(0,0,1.12),'Spine1':(0,0,1.27),'Spine2':(0,0,1.43),
               'Neck':(0,0,1.57),'Head':(0,0,1.69)}
    parents={'Hips':None,'Spine':'Hips','Spine1':'Spine','Spine2':'Spine1','Neck':'Spine2','Head':'Neck'}
    for side,sign in (('Left',1),('Right',-1)):
        for name,position,parent in (
            ('Shoulder',(0.08*sign,0,1.47),'Spine2'),('Arm',(0.20*sign,0,1.47),side+'Shoulder'),
            ('ForeArm',(0.48*sign,0,1.47),side+'Arm'),('Hand',(0.75*sign,0,1.47),side+'ForeArm'),
            ('UpLeg',(0.09*sign,0,0.95),'Hips'),('Leg',(0.09*sign,0,0.52),side+'UpLeg'),
            ('Foot',(0.09*sign,0,0.08),side+'Leg'),('ToeBase',(0.09*sign,-0.13,0.02),side+'Foot')):
            positions[side+name]=position;parents[side+name]=parent
    activate(obj);bpy.ops.object.mode_set(mode='EDIT')
    inverse=obj.matrix_world.inverted()
    for index,(name,position) in enumerate(positions.items()):
        bone=data.edit_bones.new('mixamorig:'+name)
        children=[key for key,value in parents.items() if value==name]
        end=Vector(positions[children[0]]) if children else Vector(position)+Vector((0,0,0.07))
        bone.head=inverse @ Vector(position);bone.tail=inverse @ end
        bone.roll=0.13*index
    for name,parent in parents.items():
        if parent:data.edit_bones['mixamorig:'+name].parent=data.edit_bones['mixamorig:'+parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    action=bpy.data.actions.new('mixamo_motion')
    obj.animation_data_create();obj.animation_data.action=action
    for frame,yaw,swing in ((1,0,0),(9,0.25,0.5),(17,0.5,-0.25)):
        root=obj.pose.bones['mixamorig:Hips'];root.rotation_mode='QUATERNION'
        root.rotation_quaternion=Quaternion((0,1,0),yaw)
        world_delta=Vector((0.005*(frame-1),-0.03*(frame-1),0.03 if frame==9 else 0))
        root.location=root.bone.matrix_local.to_3x3().inverted() @ obj.matrix_world.to_3x3().inverted() @ world_delta
        root.keyframe_insert('location',frame=frame);root.keyframe_insert('rotation_quaternion',frame=frame)
        for name in ('LeftArm','RightArm','LeftLeg','LeftToeBase','RightToeBase'):
            bone=obj.pose.bones['mixamorig:'+name];bone.rotation_mode='QUATERNION'
            bone.rotation_quaternion=Quaternion((0,0,1),swing)
            bone.keyframe_insert('rotation_quaternion',frame=frame)
    return obj


args=sys.argv[sys.argv.index('--')+1:]
dsrc=Path(args[0])
if len(args)>1:
    with bpy.data.libraries.load(args[1],link=False) as (available,loaded):
        loaded.objects=['Armature'] if 'Armature' in available.objects else available.objects[:1]
    source=loaded.objects[0]
    bpy.context.scene.collection.objects.link(source)
else:source=synthetic_source()
assert source and source.type=='ARMATURE'
source_action=source.animation_data.action
path=dsrc/'sku.0/sys.shared/compiled/game/appearance/skeleton/all_b.skt'
assert bpy.ops.import_scene.skt('EXEC_DEFAULT',filepath=str(path),create_control_rig=True)=={'FINISHED'}
target=bpy.context.active_object;game=animation_rig.game_skeleton_for(target)
settings=bpy.context.scene.swg_retarget
settings.source,settings.target=source,target
assert bpy.ops.swg.retarget_mixamo_preset()=={'FINISHED'}
activate(target)
bpy.ops.object.mode_set(mode='EDIT')
target.data.edit_bones['lshin'].use_connect=True
bpy.ops.object.mode_set(mode='OBJECT')
assert bpy.ops.swg.retarget_calibrate()=={'FINISHED'}
assert not any(b.use_connect for b in target.data.bones),'Early FK connection flags were not upgraded'
cal=__import__('json').loads(settings.calibration)
assert len(cal['mapping'])>=22
before_source={b.name:b.matrix_local.copy() for b in source.data.bones}
before_target={b.name:b.matrix_local.copy() for b in target.data.bones}
before_game={b.name:b.matrix_local.copy() for b in game.data.bones}
bpy.context.scene.frame_set(3)
before_pose={b.name:b.matrix_basis.copy() for b in source.pose.bones}
settings.motion_mode='IN_PLACE'
assert bpy.ops.swg.retarget_preview()=={'FINISHED'}
preview=target.animation_data.action
assert preview.get('swg_retarget_preview') is True
assert source.animation_data.action==source_action
for name,value in before_pose.items():
    # Preview deliberately moves the playback cursor to its first frame. At
    # the original frame, the source must reproduce its original pose.
    bpy.context.scene.frame_set(3)
    near(source.pose.bones[name].matrix_basis,value,0.0001)
samples=[settings.start_frame,(settings.start_frame+settings.end_frame)//2,settings.end_frame]
in_place={}
alignment=Matrix([cal['alignment'][i:i+4] for i in range(0,16,4)]).to_3x3()
for frame in samples:
    bpy.context.scene.frame_set(frame)
    depsgraph=bpy.context.evaluated_depsgraph_get()
    src=source.evaluated_get(depsgraph);tgt=target.evaluated_get(depsgraph)
    in_place[frame]={b.name:(tgt.matrix_world @ tgt.pose.bones[b.name].matrix).copy()
                     for b in game.data.bones if b.use_deform}
    for name,source_name in cal['mapping'].items():
        if name==cal['root'] or not any(child.use_deform for child in game.data.bones[name].children):
            # Root and terminal display tails are arbitrary. Compare
            # world rotation deltas, not the source/target display directions.
            source_rest=src.matrix_world @ source.data.bones[source_name].matrix_local
            source_pose=src.matrix_world @ src.pose.bones[source_name].matrix
            source_delta=source_pose.to_quaternion().to_matrix() @ source_rest.to_quaternion().to_matrix().inverted()
            expected_delta=alignment @ source_delta @ alignment.inverted()
            target_rest=tgt.matrix_world @ target.data.bones[name].matrix_local
            target_pose=tgt.matrix_world @ tgt.pose.bones[name].matrix
            actual_delta=target_pose.to_quaternion().to_matrix() @ target_rest.to_quaternion().to_matrix().inverted()
            assert max(abs(actual_delta[r][c]-expected_delta[r][c]) for r in range(3) for c in range(3))<0.001
            # Hip sockets must rotate with that pelvis delta while retaining
            # their target offsets, without an extra root-tail alignment swing.
            for hip in (('lthigh','rthigh') if name==cal['root'] else ()):
                rest_offset=target.data.bones[hip].head_local-target.data.bones[name].head_local
                expected_offset=expected_delta @ (tgt.matrix_world.to_3x3() @ rest_offset)
                actual_offset=in_place[frame][hip].translation-target_pose.translation
                assert (actual_offset-expected_offset).length<0.001,(hip,actual_offset,expected_offset)
            continue
        actual=(tgt.matrix_world.to_3x3() @ tgt.pose.bones[name].matrix.to_3x3().col[1]).normalized()
        expected=(alignment @ (src.matrix_world @ src.pose.bones[source_name].matrix).to_3x3().col[1]).normalized()
        assert (actual-expected).length<0.001,(name,actual,expected)
    root=in_place[frame][cal['root']].translation
    assert (Vector((root.x,root.y))-Vector(cal['target_root'][:2])).length<0.0001
assert source.animation_data.action==source_action
print('RETARGET PASS automatic A/T correction: source physical bone directions match target; in-place root verified')

settings.motion_mode='LOCOMOTION';settings.extract_heading=False
source.hide_set(True)
assert bpy.ops.swg.retarget_bake()=={'FINISHED'}
assert source.hide_get(),'Retarget changed the source visibility preference'
baked=target.animation_data.action
assert baked!=preview and baked.use_fake_user
assert preview in list(bpy.data.actions)
for frame in samples:
    bpy.context.scene.frame_set(frame)
    evaluated=target.evaluated_get(bpy.context.evaluated_depsgraph_get())
    for name,expected in in_place[frame].items():near(evaluated.matrix_world @ evaluated.pose.bones[name].matrix,expected)
assert target.get(animation_io.LOC_T) is not None
with tempfile.TemporaryDirectory(prefix='swg_retarget_export_') as directory:
    output=Path(directory)/'retargeted.ans'
    animation_io.export_animation(bpy.context,str(output))
    clip=ans_format.load(output)
    assert clip.locomotion_translation is not None
    assert Vector(clip.locomotion_translation.sample(0)).length<1e-6
    assert clip.last_frame==settings.end_frame-settings.start_frame
    # Actual export/import through the game skeleton must preserve the pose.
    animation_io.import_animation(bpy.context,str(output),settings.start_frame,True)
    for frame in samples:
        bpy.context.scene.frame_set(frame)
        evaluated=target.evaluated_get(bpy.context.evaluated_depsgraph_get())
        for name,expected in in_place[frame].items():near(evaluated.matrix_world @ evaluated.pose.bones[name].matrix,expected)
print('RETARGET PASS new Action, locomotion translation, ANS export/import roundtrip')

settings.extract_heading=True
assert bpy.ops.swg.retarget_bake()=={'FINISHED'}
for frame in samples:
    bpy.context.scene.frame_set(frame)
    evaluated=target.evaluated_get(bpy.context.evaluated_depsgraph_get())
    q=Quaternion(evaluated.get(animation_io.LOC_Q))
    world_q=game.matrix_world.to_quaternion() @ q @ game.matrix_world.to_quaternion().inverted()
    origin=game.matrix_world.translation
    undo=Matrix.Translation(origin) @ world_q.to_matrix().to_4x4() @ Matrix.Translation(-origin)
    for name,expected in in_place[frame].items():near(undo @ evaluated.matrix_world @ evaluated.pose.bones[name].matrix,expected)
print('RETARGET PASS locomotion heading extraction reconstructs all original joint poses')

settings.reference_mode='POSE'
bpy.context.scene.frame_set(settings.start_frame)
settings.extract_heading=False
assert bpy.ops.swg.retarget_calibrate()=={'FINISHED'}
assert bpy.ops.swg.retarget_bake()=={'FINISHED'}
print('RETARGET PASS current-pose calibration')

# Failures must preserve the current target Action and every rest matrix.
before_action=target.animation_data.action
wrong_action=bpy.data.actions.new('wrong_rig_action')
curve=wrong_action.fcurves.new('pose.bones["not_a_source_bone"].location',index=0)
curve.keyframe_points.insert(1,0)
settings.source_action=wrong_action
try:
    retarget.bake(bpy.context,settings)
    raise AssertionError('Unmatched source Action accepted')
except ValueError as error:assert 'channel names do not match' in str(error)
assert target.animation_data.action==before_action
settings.source_action=None
root_row=next(row for row in settings.mapping if row.target_bone=='root')
saved=root_row.source_bone;root_row.source_bone='missing_source_bone'
try:
    retarget.calibrate(bpy.context,settings)
    raise AssertionError('Missing required mapping accepted')
except ValueError as error:assert 'Missing source bone' in str(error)
assert target.animation_data.action==before_action
root_row.source_bone=saved
for obj,originals in ((source,before_source),(target,before_target),(game,before_game)):
    for name,expected in originals.items():near(obj.data.bones[name].matrix_local,expected,1e-7)
assert source.animation_data.action==source_action
assert all(len(b.constraints)==1 for b in game.pose.bones)
assert 'SUCCESS' in settings.last_report
print('RETARGET PASS errors, source Action, rest matrices and native FK constraints preserved')
print('RETARGET COMPLETE')
