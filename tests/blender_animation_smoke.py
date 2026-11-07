"""Blender --background --factory-startup --python-exit-code 1
--python tests/blender_animation_smoke.py -- D:/r3git/dsrc

Tests real CKAT/KFAT clips against independent client matrix equations, then
round-trips actions through evaluated FK joints into actual .ans files.
"""
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
addon_utils.enable('io_scene_swg',default_set=True)
from io_scene_swg import animation_io,animation_rig,ans_format,swg_types,nsg_iff

dsrc=Path(sys.argv[sys.argv.index('--')+1])
directory=dsrc/'sku.0/sys.shared/compiled/game/appearance'
assert bpy.ops.import_scene.skt('EXEC_DEFAULT',filepath=str(directory/'skeleton/all_b.skt'),create_control_rig=True)=={'FINISHED'}
owner=bpy.context.active_object
source=animation_rig.game_skeleton_for(owner)
skeleton=swg_types.SktFile(str(directory/'skeleton/all_b.skt'));skeleton.load(0)


def near(a,b,tolerance=0.0004):
    error=max(abs(a[r][c]-b[r][c]) for r in range(4) for c in range(4))
    assert error<tolerance,(error,a,b)


def client_matrix(q):
    w,x,y,z=q
    return Matrix(((1-2*y*y-2*z*z,2*x*y-2*w*z,2*x*z+2*w*y,0),
                   (2*x*y+2*w*z,1-2*x*x-2*z*z,2*y*z-2*w*x,0),
                   (2*x*z-2*w*y,2*y*z+2*w*x,1-2*x*x-2*y*y,0),(0,0,0,1)))


def client_pose(clip,frame):
    worlds={}
    convert=Matrix(((-1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1)))
    axes=Matrix.Diagonal(Vector((-1,1,1,1)))

    def joint(i):
        if i not in worlds:
            track=clip.tracks.get(skeleton.joint_names[i])
            rotation,translation=track.sample(frame) if track else ((1,0,0,0),(0,0,0))
            local=(client_matrix(skeleton.joint_post_rotations[i]) @ client_matrix(rotation)
                   @ client_matrix(skeleton.joint_bind_rotations[i]) @ client_matrix(skeleton.joint_pre_rotations[i]))
            local.translation=Vector(skeleton.joint_translations[i])+Vector(translation)
            parent=skeleton.joint_parents[i]
            worlds[i]=joint(parent) @ local if parent>=0 else local
        return worlds[i]

    return {name:convert @ joint(i) @ axes for i,name in enumerate(skeleton.joint_names)}


def check_pose(clip,frame,scene_start):
    bpy.context.scene.frame_set(scene_start+frame)
    evaluated=source.evaluated_get(bpy.context.evaluated_depsgraph_get())
    expected=client_pose(clip,frame)
    for name,matrix in expected.items():
        near(evaluated.pose.bones[name].matrix,matrix)


with tempfile.TemporaryDirectory(prefix='swg_animation_') as temporary:
    for filename in ('zombie_walk.ans','all_b_cbt_pistol_run_ready.ans'):
        path=directory/'animation'/filename
        clip=ans_format.load(path)
        action,unknown=animation_io.import_animation(bpy.context,str(path),7,True)
        assert owner.animation_data.action==action
        assert bpy.context.scene.frame_end==7+clip.last_frame
        for frame in range(clip.last_frame+1):check_pose(clip,frame,7)
        bpy.context.scene.frame_set(12,subframe=0.25)
        output=Path(temporary)/filename
        baked=animation_io.export_animation(bpy.context,str(output),7,7+clip.last_frame,clip.fps)
        assert bpy.context.scene.frame_current==12 and abs(bpy.context.scene.frame_subframe-0.25)<1e-6
        assert owner.animation_data.action==action
        decoded=ans_format.load(output)
        assert decoded.source_format=='KFAT/0003'
        assert decoded.messages==clip.messages,(decoded.messages,clip.messages)
        assert abs(decoded.average_speed-clip.average_speed)<1e-6
        for name in unknown:
            assert name in decoded.tracks
            for frame in range(clip.last_frame+1):
                first,ta=clip.tracks[name].sample(frame)
                second,tb=decoded.tracks[name].sample(frame)
                assert abs(Quaternion(first).dot(Quaternion(second)))>1-1e-6
                assert (Vector(ta)-Vector(tb)).length<1e-5
        for frame in range(clip.last_frame+1):
            first,second=client_pose(clip,frame),client_pose(decoded,frame)
            for name in first:near(first[name],second[name])
            if clip.locomotion_translation:
                assert (Vector(decoded.locomotion_translation.sample(frame))-Vector(clip.locomotion_translation.sample(frame))).length<1e-5
        previous=owner.animation_data.action
        assert bpy.ops.import_scene.swg_ans('EXEC_DEFAULT',filepath=str(output),start_frame=1)=={'FINISHED'}
        assert previous in list(bpy.data.actions) and previous.use_fake_user
        for frame in (0,clip.last_frame//2,clip.last_frame):check_pose(clip,frame,1)
        print('ANIMATION PASS',filename,clip.source_format,'samples',clip.last_frame+1,'unknown joints',unknown,'client pose, KFAT roundtrip, messages, locomotion, frame restoration')

    # Preserve native timing and message times when the scene FPS is different,
    # including an action whose last key lies on a fractional Blender frame.
    bpy.context.scene.render.fps=24
    bpy.context.scene.render.fps_base=1
    path=directory/'animation/all_b_cbt_pistol_run_ready.ans'
    original=ans_format.load(path)
    animation_io.import_animation(bpy.context,str(path),11,False)
    assert bpy.context.scene.render.fps==24
    timed=animation_io.export_animation(bpy.context,str(Path(temporary)/'different_fps.ans'))
    assert timed.last_frame==original.last_frame
    assert timed.fps==original.fps
    assert timed.messages==original.messages,(timed.messages,original.messages)
    for frame in range(original.last_frame+1):
        a,b=client_pose(original,frame),client_pose(timed,frame)
        for name in a:near(a[name],b[name])
    print('ANIMATION PASS mixed scene/clip FPS: fractional range and exact message timing')
    bpy.context.scene.render.fps=30
    bpy.context.scene.render.fps_base=1

    # Authored Blender action -> evaluated constrained game joints -> SWG.
    action=bpy.data.actions.new('authored_fk')
    owner.animation_data.action=action
    for bone in owner.pose.bones:bone.matrix_basis=Matrix.Identity(4)
    for frame,angle in ((1,0),(9,0.45)):
        for name in ('root','lforearm'):
            bone=owner.pose.bones[name]
            bone.rotation_mode='QUATERNION'
            bone.rotation_quaternion=Quaternion(Vector((0.2,0.7,0.3)).normalized(),angle)
            bone.location=(angle*0.2,0,0) if name=='root' else (0,0,0)
            bone.keyframe_insert('rotation_quaternion',frame=frame)
            bone.keyframe_insert('location',frame=frame)
    expected={}
    for frame in range(1,10):
        bpy.context.scene.frame_set(frame)
        evaluated=source.evaluated_get(bpy.context.evaluated_depsgraph_get())
        expected[frame]={b.name:evaluated.pose.bones[b.name].matrix.copy() for b in source.data.bones}
    output=Path(temporary)/'authored.ans'
    authored=animation_io.export_animation(bpy.context,str(output),1,9,30)
    assert authored.locomotion_translation is None,'Stale locomotion properties leaked from previous action'
    assert authored.messages==[]
    animation_io.import_animation(bpy.context,str(output),1,True)
    for frame in range(1,10):
        bpy.context.scene.frame_set(frame)
        evaluated=source.evaluated_get(bpy.context.evaluated_depsgraph_get())
        for name,matrix in expected[frame].items():near(matrix,evaluated.pose.bones[name].matrix)
    print('ANIMATION PASS authored FK action: root/limb keys, evaluated constraints, no stale metadata, action roundtrip')

    roundtrip_action=owner.animation_data.action
    owner.animation_data.action=None
    track=owner.animation_data.nla_tracks.new()
    track.strips.new('authored_nla',1,action)
    nla=animation_io.export_animation(bpy.context,str(Path(temporary)/'nla.ans'),1,9,30)
    for frame in range(1,10):
        decoded_pose=client_pose(nla,frame-1)
        for name,matrix in expected[frame].items():near(matrix,decoded_pose[name])
    owner.animation_data.nla_tracks.remove(track)
    owner.animation_data.action=roundtrip_action
    print('ANIMATION PASS evaluated NLA pose baking')

    # Unsupported scale must not overwrite an existing file, and current frame
    # must be restored even if a bake fails halfway through.
    sentinel=Path(temporary)/'preserve.ans';sentinel.write_bytes(b'preserve')
    owner.pose.bones['root'].scale=(1.2,1,1)
    bpy.context.scene.frame_set(4)
    try:
        animation_io.export_animation(bpy.context,str(sentinel),1,9,30)
        raise AssertionError('Animated scale accepted')
    except ValueError as error:
        assert 'scale/shear' in str(error),str(error)
    assert sentinel.read_bytes()==b'preserve'
    assert bpy.context.scene.frame_current==4
    owner.pose.bones['root'].scale=(1,1,1)

    # A native armature without controls is also a valid target.
    assert bpy.ops.import_scene.skt('EXEC_DEFAULT',filepath=str(directory/'skeleton/all_b.skt'))=={'FINISHED'}
    source=bpy.context.active_object
    owner=source
    clip=ans_format.load(directory/'animation/zombie_walk.ans')
    animation_io.import_animation(bpy.context,str(directory/'animation/zombie_walk.ans'),1,True)
    for frame in (0,15,30):check_pose(clip,frame,1)
    print('ANIMATION PASS native skeleton without FK controls')

    # Exercise noncommuting pre/bind/post rotations; humanoid joints alone
    # often have identity orientation fields and cannot detect order mistakes.
    path=Path(temporary)/'oriented.skt'
    iff=nsg_iff.IFF(initial_size=4096)
    iff.insertForm('SLOD');iff.insertForm('0000')
    iff.insertChunk('INFO');iff.insert_int16(1);iff.exitChunk('INFO')
    iff.insertForm('SKTM');iff.insertForm('0002')
    entries=(('INFO',[3],iff.insert_int32),('NAME',['root','limb','tip'],iff.insertChunkString),
        ('PRNT',[-1,0,1],iff.insert_int32),
        ('RPRE',[tuple(Quaternion((1,0,0),0.2+i*0.1)) for i in range(3)],iff.insertFloatVector4),
        ('RPST',[tuple(Quaternion((0,0,1),-0.3+i*0.2)) for i in range(3)],iff.insertFloatVector4),
        ('BPTR',[(0.2,1,0.1),(0.1,0.5,0.2),(0.1,0.2,0)],iff.insertFloatVector3),
        ('BPRO',[tuple(Quaternion((0,1,0),0.4+i*0.15)) for i in range(3)],iff.insertFloatVector4),
        ('JROR',[0,3,5],iff.insert_int32))
    for tag,values,writer in entries:
        iff.insertChunk(tag)
        for value in values:writer(value)
        iff.exitChunk(tag)
    iff.exitForm('0002');iff.exitForm('SKTM');iff.exitForm('0000');iff.exitForm('SLOD');iff.write(str(path))
    assert bpy.ops.import_scene.skt('EXEC_DEFAULT',filepath=str(path),create_control_rig=True)=={'FINISHED'}
    owner=bpy.context.active_object;source=animation_rig.game_skeleton_for(owner)
    skeleton=swg_types.SktFile(str(path));skeleton.load(0)
    tracks={name:ans_format.Track(ans_format.Channel([(i,tuple(Quaternion(Vector((0.3,0.7,0.2)).normalized(),i*0.08))) for i in range(9)],True),
        tuple(ans_format.Channel([(i,i*(axis+1)*0.003) for i in range(9)]) for axis in range(3)))
        for name in ('root','limb','tip')}
    clip=ans_format.Clip(24.5,8,tracks,[{'name':'step','frames':[2,6]}],
        ans_format.Channel([(0,(0,0,0)),(8,(0.2,0,0.5))]),
        ans_format.Channel([(0,(1,0,0,0)),(8,tuple(Quaternion((0,1,0),0.35)))],True),1.3)
    input_path=Path(temporary)/'oriented.ans';ans_format.save(input_path,clip)
    animation_io.import_animation(bpy.context,str(input_path),1,True)
    for frame in range(9):check_pose(clip,frame,1)
    output=Path(temporary)/'oriented_roundtrip.ans'
    animation_io.export_animation(bpy.context,str(output))
    decoded=ans_format.load(output)
    for frame in range(9):
        first,second=client_pose(clip,frame),client_pose(decoded,frame)
        for name in first:near(first[name],second[name])
        assert abs(Quaternion(clip.locomotion_rotation.sample(frame)).dot(Quaternion(decoded.locomotion_rotation.sample(frame))))>1-1e-6
    assert decoded.messages==clip.messages
    print('ANIMATION PASS nonidentity joint orientations, translated child joints, fractional FPS, locomotion rotation')
print('ANIMATION COMPLETE')
