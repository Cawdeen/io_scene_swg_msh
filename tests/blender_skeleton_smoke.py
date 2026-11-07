"""Blender 3.6 --background --factory-startup --python-exit-code 1
--python tests/blender_skeleton_smoke.py -- D:/r3git/dsrc

Only writes temporary fixtures/exports, never the repository's assets.
"""

import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import addon_utils
import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
addon_utils.enable("io_scene_swg", default_set=True)
from io_scene_swg import animation_rig, export_skt, import_skt, nsg_iff, skeleton_math, swg_types


def near(a, b, tolerance=3e-5):
    error = max(abs(a[row][col] - b[row][col]) for row in range(4) for col in range(4))
    assert error < tolerance, (error, a, b)


def client_rotation(quaternion):
    # Independent transcription of sharedMath/Quaternion.cpp, not the addon math.
    w, x, y, z = quaternion
    return Matrix(((1-2*y*y-2*z*z, 2*x*y-2*w*z, 2*x*z+2*w*y, 0),
                   (2*x*y+2*w*z, 1-2*x*x-2*z*z, 2*y*z-2*w*x, 0),
                   (2*x*z-2*w*y, 2*y*z+2*w*x, 1-2*x*x-2*y*y, 0),
                   (0, 0, 0, 1)))


def reference_frames(skeleton):
    result = {}

    def frame(index):
        if index not in result:
            local = (client_rotation(skeleton.joint_post_rotations[index])
                     @ client_rotation(skeleton.joint_bind_rotations[index])
                     @ client_rotation(skeleton.joint_pre_rotations[index]))
            local.translation = skeleton.joint_translations[index]
            parent = skeleton.joint_parents[index]
            result[index] = local if parent < 0 else frame(parent) @ local
        return result[index]

    conversion = Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    local_axis = Matrix.Diagonal(Vector((-1, 1, 1, 1)))
    return [conversion @ frame(i) @ local_axis for i in range(skeleton.joint_count)]


def compare_skeletons(first, second):
    # Blender may reorder bones into hierarchy order. Compare by joint name.
    assert set(first.joint_names) == set(second.joint_names)
    first_frames = reference_frames(first)
    second_frames = reference_frames(second)
    for i, name in enumerate(first.joint_names):
        j = second.joint_names.index(name)
        near(first_frames[i], second_frames[j])
        for attribute in ("joint_pre_rotations", "joint_post_rotations", "joint_bind_rotations"):
            a, b = Quaternion(getattr(first, attribute)[i]), Quaternion(getattr(second, attribute)[j])
            assert abs(a.dot(b)) > 1 - 1e-5, (name, attribute)
        assert first.joint_rotation_order[i] == second.joint_rotation_order[j]
        parent_a, parent_b = first.joint_parents[i], second.joint_parents[j]
        assert (first.joint_names[parent_a] if parent_a >= 0 else None) == (
            second.joint_names[parent_b] if parent_b >= 0 else None)


def exercise(path, temporary, all_lods=False):
    result = bpy.ops.import_scene.skt('EXEC_DEFAULT', filepath=str(path), lod_0_only=not all_lods)
    assert result == {'FINISHED'}
    source = bpy.context.active_object
    collection = source.users_collection[0]
    arms = sorted([obj for obj in collection.objects if obj.type == 'ARMATURE'],
                  key=lambda obj: obj.data["swg_lod"])
    originals = []
    for index, arm in enumerate(arms):
        skeleton = swg_types.SktFile(str(path))
        assert skeleton.load(index)
        originals.append(skeleton)
        frames = reference_frames(skeleton)
        for i, name in enumerate(skeleton.joint_names):
            bone = arm.data.bones[name]
            near(bone.matrix_local, frames[i])
            # Handedness check; Blender's float bone bases can differ by a few
            # units in the fifth decimal place even when rotation entries pass.
            assert abs(bone.matrix_local.to_3x3().determinant() - 1) < 1e-4, (name, bone.matrix_local.to_3x3().determinant())
            parent = skeleton.joint_parents[i]
            assert (bone.parent.name if bone.parent else None) == (
                skeleton.joint_names[parent] if parent >= 0 else None)
            direction = (bone.tail_local - bone.head_local).normalized()
            assert (direction - frames[i].to_3x3().col[1]).length < 1e-5
        assert arm.matrix_world == Matrix.Identity(4)
    before = {bone.name: bone.matrix_local.copy() for bone in source.data.bones}
    # Use the actual sidebar operator and verify native frames never change.
    assert bpy.ops.swg.create_fk_rig() == {'FINISHED'}
    rig = bpy.context.active_object
    assert rig["swg_game_skeleton"] == source.name
    assert not any(bone.use_deform for bone in rig.data.bones)
    assert any(bone.use_connect for bone in rig.data.bones)
    bpy.context.view_layer.update()
    for bone in source.pose.bones:
        near(bone.matrix, before[bone.name])
    # Rotate both the root and a child control; translated/scaled rig objects
    # also exercise world-space constraints rather than coincident rest frames.
    root = next(b for b in rig.pose.bones if not b.parent)
    root.rotation_quaternion = Quaternion(Vector((0.3, 0.5, 0.2)).normalized(), 0.31)
    root.location = (0.1, -0.05, 0.2)
    child = next(b for b in rig.pose.bones if b.parent and not b.name.startswith("SWG_OUTPUT_"))
    child.rotation_quaternion = Quaternion(Vector((0.2, 0.1, 0.8)).normalized(), -0.43)
    rig.location = (0.25, 0.5, -0.1)
    bpy.context.view_layer.update()
    for original in source.pose.bones:
        constraint = original.constraints[0]
        output = rig.pose.bones[constraint.subtarget]
        control = rig.pose.bones[original.name]
        offset = control.bone.matrix_local.inverted() @ original.bone.matrix_local
        near(rig.matrix_world @ control.matrix @ offset, source.matrix_world @ original.matrix)
        near(source.data.bones[original.name].matrix_local, before[original.name])
    # A weighted vertex must follow the native skeleton even while its display
    # is hidden, matching the rest-frame inverse used by game skinning.
    bone = source.pose.bones[0]
    vertex = before[bone.name] @ Vector((0.03, 0.04, -0.02))
    mesh = bpy.data.meshes.new("skinning_check")
    mesh.from_pydata([vertex], [], [])
    mesh_object = bpy.data.objects.new(mesh.name, mesh)
    collection.objects.link(mesh_object)
    mesh_object.vertex_groups.new(name=bone.name).add([0], 1.0, 'REPLACE')
    mesh_object.modifiers.new("SWG Skeleton", 'ARMATURE').object = source
    bpy.context.view_layer.update()
    evaluated = mesh_object.evaluated_get(bpy.context.evaluated_depsgraph_get())
    expected_vertex = bone.matrix @ before[bone.name].inverted() @ vertex
    assert (evaluated.data.vertices[0].co - expected_vertex).length < 3e-5
    try:
        animation_rig.create_fk_rig(bpy.context, source)
        raise AssertionError("Duplicate rig was accepted")
    except ValueError:
        pass
    # Export while controls are posed. SKT must contain unchanged rest data and
    # exclude the control armature. No object transforms may be applied.
    output = Path(temporary) / (path.stem + "_roundtrip.skt")
    source_world, rig_world = source.matrix_world.copy(), rig.matrix_world.copy()
    assert export_skt.export_skt(bpy.context, str(output), collection) == {'FINISHED'}
    near(source.matrix_world, source_world)
    near(rig.matrix_world, rig_world)
    for index, original in enumerate(originals):
        decoded = swg_types.SktFile(str(output))
        assert decoded.load(index)
        compare_skeletons(original, decoded)
    decoded = swg_types.SktFile(str(output))
    assert not decoded.load(len(originals))
    # The actual export operator must resolve the selected control rig back to
    # its game skeleton's collection, even with the scene collection active.
    export_directory = Path(temporary) / (path.stem + "_ui_export")
    export_directory.mkdir()
    source.name += "_renamed"
    rig.name += "_renamed"
    assert animation_rig.game_skeleton_for(rig) == source
    assert bpy.ops.export_scene.skt('EXEC_DEFAULT', filepath=str(export_directory / "ignored.skt")) == {'FINISHED'}
    ui_export = swg_types.SktFile(str(export_directory / (collection.name.split('.')[0] + '.skt')))
    assert ui_export.load(0)
    compare_skeletons(originals[0], ui_export)
    print("SKELETON PASS", path.name, "LODs", len(arms), "joints", len(before),
          "native frames, FK controls, posed constraints, SKT roundtrip")


def fixture(path):
    # Nonidentity pre/bind/post rotations, branched hierarchy, coincident joint,
    # a leaf and a non-XYZ JROR. Human all_b alone has many identity rotations.
    iff = nsg_iff.IFF(initial_size=4096)
    iff.insertForm("SLOD"); iff.insertForm("0000")
    iff.insertChunk("INFO"); iff.insert_int16(1); iff.exitChunk("INFO")
    iff.insertForm("SKTM"); iff.insertForm("0002")
    names = ["root", "limb", "tip", "branch", "coincident"]
    entries = (
        ("INFO", [5], iff.insert_int32),
        ("NAME", names, iff.insertChunkString),
        ("PRNT", [-1, 0, 1, 0, 3], iff.insert_int32),
        ("RPRE", [list(Quaternion((1, 0, 0), 0.17 + i * 0.1)) for i in range(5)], iff.insertFloatVector4),
        ("RPST", [list(Quaternion((0, 0, 1), -0.12 + i * 0.13)) for i in range(5)], iff.insertFloatVector4),
        ("BPTR", [(0.1, 1, 0.2), (0.3, 0.5, -0.2), (0.2, 0.4, 0.1), (-0.4, 0.2, 0), (0, 0, 0)], iff.insertFloatVector3),
        ("BPRO", [list(Quaternion((0, 1, 0), 0.23 + i * 0.11)) for i in range(5)], iff.insertFloatVector4),
        ("JROR", [0, 1, 2, 4, 5], iff.insert_int32))
    for tag, rows, writer in entries:
        iff.insertChunk(tag)
        for row in rows:
            writer(row)
        iff.exitChunk(tag)
    iff.exitForm("0002"); iff.exitForm("SKTM"); iff.exitForm("0000"); iff.exitForm("SLOD")
    iff.write(str(path))


def verify_human_mesh_alignment(directory):
    prefs = bpy.context.preferences.addons['io_scene_swg'].preferences
    prefs.asset_layout = 'DSRC'
    prefs.dsrc_root = str(directory.parents[5])
    mesh_path = directory.parent / 'mesh/hum_m_body_l0.mgn'
    before_objects = set(bpy.data.objects)
    assert bpy.ops.import_scene.mgn('EXEC_DEFAULT', filepath=str(mesh_path),
                                    axis_forward='Z', axis_up='Y') == {'FINISHED'}
    mesh = next(obj for obj in set(bpy.data.objects) - before_objects if obj.type == 'MESH')
    assert bpy.ops.import_scene.skt('EXEC_DEFAULT', filepath=str(directory / 'all_b.skt'),
                                    create_control_rig=True) == {'FINISHED'}
    rig = bpy.context.active_object
    source = animation_rig.game_skeleton_for(rig)
    # Read unconverted file positions independently of SWGMgn.load(). This
    # exercises the actual MGN reader's Z flip plus the operator axis conversion.
    iff = nsg_iff.IFF(filename=str(mesh_path))
    iff.enterAnyForm(); iff.enterAnyForm()
    for tag in ('INFO', 'SKTM', 'XFNM'):
        iff.enterChunk(tag); iff.exitChunk(tag)
    iff.enterChunk('POSN')
    raw_positions = []
    while not iff.atEndOfForm():
        raw_positions.append(Vector(iff.read_vector3()))
    assert len(raw_positions) == len(mesh.data.vertices)
    conversion = skeleton_math.SWG_TO_BLENDER.to_3x3()
    for vertex, raw in zip(mesh.data.vertices, raw_positions):
        assert (vertex.co - conversion @ raw).length < 1e-5

    def weighted_center(name):
        group = mesh.vertex_groups[name].index
        weighted = [(v.co, g.weight) for v in mesh.data.vertices
                    for g in v.groups if g.group == group]
        return sum((co * weight for co, weight in weighted), Vector()) / sum(w for co, w in weighted)

    for side in ('l', 'r'):
        mesh_forward = weighted_center(side + 'toe') - weighted_center(side + 'ankle')
        joint_forward = source.data.bones[side + 'toe'].head_local - source.data.bones[side + 'ankle'].head_local
        assert mesh_forward.y * joint_forward.y > 0, ('Foot faces opposite to mesh', side)
        assert mesh_forward.dot(joint_forward) > 0
    print('SKELETON PASS human MGN/SKT alignment: raw positions, default axes, both feet')


with tempfile.TemporaryDirectory(prefix="swg_skeleton_") as temporary:
    test_path = Path(temporary) / "synthetic.skt"
    fixture(test_path)
    exercise(test_path, temporary)
    # Reject cyclic/invalid parent trees before editing the Blender scene.
    skeleton = swg_types.SktFile(str(test_path)); skeleton.load(0)
    skeleton.joint_parents[0] = 2
    try:
        skeleton_math.bind_matrices(skeleton)
        raise AssertionError("Cycle was accepted")
    except ValueError:
        pass
    skeleton.joint_parents[0] = 99
    try:
        skeleton_math.bind_matrices(skeleton)
        raise AssertionError("Invalid parent was accepted")
    except ValueError:
        pass
    dsrc = Path(sys.argv[sys.argv.index("--") + 1])
    directory = dsrc / "sku.0/sys.shared/compiled/game/appearance/skeleton"
    exercise(directory / "all_b.skt", temporary, all_lods=True)
    exercise(directory / "acklay.skt", temporary)
    exercise(directory / "astromech.skt", temporary)
    verify_human_mesh_alignment(directory)
print("SKELETON COMPLETE")
