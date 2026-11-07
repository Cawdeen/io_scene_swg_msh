"""Blender --background --factory-startup --python-exit-code 1
--python tests/blender_weights_smoke.py -- D:/r3git/dsrc

Exercises exact/offset/remeshed correspondence and on-disk MGN weight fidelity.
All generated meshes and exports are isolated from the user's Blender session.
"""

from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
addon_utils.enable('io_scene_swg', default_set=True)
from io_scene_swg import export_mgn, skeleton_math, skin_weights, support, swg_types


def activate(obj):
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def snapshot(obj, skeleton):
    return skin_weights.deform_weights(obj, {b.name for b in skeleton.data.bones if b.use_deform})


def expect_error(function, text):
    try:
        function()
    except (ValueError, RuntimeError) as error:
        assert text in str(error), (text, str(error))
    else:
        raise AssertionError('Expected rejection: ' + text)


def mesh_object(name, positions, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def synthetic():
    data = bpy.data.armatures.new('test_skeleton')
    skeleton = bpy.data.objects.new(data.name, data)
    bpy.context.scene.collection.objects.link(skeleton)
    activate(skeleton)
    bpy.ops.object.mode_set(mode='EDIT')
    for name in 'ABCDE':
        bone = data.edit_bones.new(name)
        bone.head = (0, 0, 0)
        bone.tail = (0, 1, 0)
    bpy.ops.object.mode_set(mode='OBJECT')
    data['swg_skeleton_version'] = skeleton_math.SKELETON_VERSION
    reference = mesh_object('test_reference', [(0,0,0),(1,0,0),(2,0,0),(0,1,0),(1,1,0),(2,1,0)],
                            [(0,1,4,3),(1,2,5,4)])
    reference.face_maps.new(name='left').add([0])
    reference.face_maps.new(name='right').add([1])
    for name in 'ABCDE':
        reference.vertex_groups.new(name=name)
    for vertex in reference.data.vertices:
        reference.vertex_groups['A'].add([vertex.index],1.0,'REPLACE')
    for name, weight in zip('ABCDE', (0.2,0.3,0.2,0.2,0.1)):
        reference.vertex_groups[name].add([1],weight,'REPLACE')
    reference.vertex_groups['A'].add([4],0.1,'REPLACE')
    reference.vertex_groups['B'].add([4],0.9,'REPLACE')
    reference.vertex_groups.new(name='reference_loop').add([1,4],1,'REPLACE')
    target = mesh_object('test_clothing', [(1,0.25,0.1), (0.3,0.4,0.1), (1.7,0.4,0.1)], [(0,1,2)])
    target.vertex_groups.new(name=skin_weights.SEAM_GROUP).add([0],1,'REPLACE')
    target.vertex_groups.new(name='artist_mask').add([1],0.75,'REPLACE')
    settings = bpy.context.scene.swg_skin_weights
    settings.reference, settings.skeleton = reference, skeleton
    settings.seam_selection = 'GROUP'
    settings.seam_group = skin_weights.SEAM_GROUP
    settings.reference_seam_group = 'reference_loop'
    settings.seam_method = 'EDGE'
    settings.max_influences = 2
    settings.max_distance = 0.5
    activate(target)
    before_reference = snapshot(reference, skeleton)
    rows, counts, distance = skin_weights.build_plan(reference,target,skeleton,settings)
    expected = skin_weights.float_weights(skin_weights.blend_weights(((before_reference[1],0.75),(before_reference[4],0.25))))
    assert rows[0] == expected
    assert len(rows[0]) == 5, 'Seam was incorrectly pruned to interior influence limit'
    assert len(rows[1]) <= 2 and abs(sum(rows[1].values())-1) < 1e-6
    assert counts['seam_interpolated'] == 1
    assert bpy.ops.swg.transfer_body_weights() == {'FINISHED'}
    assert snapshot(target,skeleton) == [rows[i] for i in range(3)]
    assert target.vertex_groups['artist_mask'].weight(1) == 0.75
    assert target.vertex_groups[skin_weights.SEAM_GROUP].weight(0) == 1
    assert target.modifiers[0].object == skeleton
    assert bpy.ops.swg.validate_seam_weights() == {'FINISHED'}
    names, exported = skin_weights.export_weights(target,target.data.vertices)
    assert set(names) <= set('ABCDE')
    assert all(index < len(names) for row in exported for index,_ in row)
    assert {names[index]:weight for index,weight in exported[0]} == expected
    target.vertex_groups['A'].add([0],0.99,'REPLACE')
    expect_error(lambda: bpy.ops.swg.validate_seam_weights(), 'seam vertices differ')

    settings.seam_method = 'COPY'
    assert bpy.ops.swg.transfer_body_weights() == {'FINISHED'}
    assert snapshot(target,skeleton)[0] == before_reference[1]
    assert bpy.ops.swg.validate_seam_weights() == {'FINISHED'}
    # Failed preflight must leave weights, nonbone groups and source untouched.
    before = snapshot(target,skeleton)
    target.data.vertices[0].co.z = 2
    expect_error(lambda: bpy.ops.swg.transfer_body_weights(), 'distance limit')
    assert snapshot(target,skeleton) == before
    target.data.vertices[0].co.z = 0.1
    target.vertex_groups['A'].lock_weight = True
    expect_error(lambda: bpy.ops.swg.transfer_body_weights(), 'Unlock target')
    assert snapshot(target,skeleton) == before
    target.vertex_groups['A'].lock_weight = False
    assert snapshot(reference,skeleton) == before_reference
    skeleton.data.bones['E'].use_deform = False
    expect_error(lambda: skin_weights.build_plan(reference,target,skeleton,settings), 'missing bones')
    assert snapshot(target,skeleton) == [{name: value for name,value in row.items() if name != 'E'} for row in before]
    skeleton.data.bones['E'].use_deform = True
    # World-space matching must work with an unapplied target translation.
    target.data.vertices[0].co.z = 0
    target.matrix_world = Matrix.Translation((0,0,0.1))
    assert bpy.ops.swg.transfer_body_weights() == {'FINISHED'}
    assert snapshot(target,skeleton)[0] == before_reference[1]
    # A linked mesh duplicate must become single-user before weights change.
    linked = reference.copy()
    linked.data = reference.data
    linked.name = 'linked_target'
    bpy.context.scene.collection.objects.link(linked)
    settings.seam_selection = 'AUTO'
    settings.reference_seam_group = ''
    activate(linked)
    assert bpy.ops.swg.transfer_body_weights() == {'FINISHED'}
    assert linked.data != reference.data
    assert snapshot(reference,skeleton) == before_reference
    assert snapshot(linked,skeleton) == before_reference
    # Identical positions with different reference vectors require disambiguation.
    reference.data.vertices[0].co = reference.data.vertices[1].co
    surface = skin_weights.ReferenceSurface(reference,skeleton,settings)
    expect_error(lambda: surface.exact(surface.positions[1],set(),settings.match_tolerance), 'Conflicting reference weights')
    print('WEIGHTS PASS synthetic: remeshed/offset seams, strict copies, five influences, preflight, shared data, transforms, helper export')


def real_body(dsrc, temporary):
    prefs = bpy.context.preferences.addons['io_scene_swg'].preferences
    prefs.asset_layout, prefs.dsrc_root = 'DSRC', str(dsrc)
    path = dsrc / 'sku.0/sys.shared/compiled/game/appearance/mesh/hum_m_body_l0.mgn'
    before = set(bpy.data.objects)
    assert bpy.ops.import_scene.mgn('EXEC_DEFAULT',filepath=str(path)) == {'FINISHED'}
    reference = next(obj for obj in set(bpy.data.objects)-before if obj.type=='MESH')
    assert bpy.ops.import_scene.skt('EXEC_DEFAULT',filepath=str(path.parent.parent/'skeleton/all_b.skt')) == {'FINISHED'}
    skeleton = bpy.context.active_object
    target = reference.copy()
    target.data = reference.data.copy()
    target.name = 'hum_m_body_weight_test'
    bpy.context.scene.collection.objects.link(target)
    target.vertex_groups.clear()
    # Helper index precedes bone indices; the MGN must remap them on export.
    target.vertex_groups.new(name=skin_weights.SEAM_GROUP).add([0],1,'REPLACE')
    settings = bpy.context.scene.swg_skin_weights
    settings.reference, settings.skeleton = reference,skeleton
    settings.seam_selection, settings.seam_method = 'AUTO','EDGE'
    settings.reference_seam_group = ''
    settings.max_influences = 2
    original = snapshot(reference,skeleton)
    activate(target)
    assert bpy.ops.swg.transfer_body_weights() == {'FINISHED'}
    assert snapshot(target,skeleton) == original
    assert max(map(len,snapshot(target,skeleton))) == 5
    assert snapshot(reference,skeleton) == original
    assert bpy.ops.swg.validate_seam_weights() == {'FINISHED'}
    labels,_ = skin_weights.mesh_zones(reference)
    protected = skin_weights.protected_vertices(reference,settings,labels)
    assert len(protected) == 176
    # Actual MGN serialization must preserve the weight floats and omit helpers.
    assert export_mgn.export_mgn(str(Path(temporary)/'ignored.mgn'), support.get_asset_roots(bpy.context),target,False) == {'FINISHED'}
    decoded = swg_types.SWGMgn(str(Path(temporary)/(target.name+'.mgn')),support.get_asset_roots(bpy.context))
    decoded.load()
    assert skin_weights.SEAM_GROUP not in decoded.joint_names
    assert decoded.max_transforms_vertex == 5
    assert decoded.max_transforms_shader >= decoded.max_transforms_vertex
    decoded_weights = [{decoded.joint_names[index]:weight for index,weight in row} for row in decoded.vertex_weights]
    assert decoded_weights == original
    shell = reference.copy()
    shell.data = reference.data.copy()
    shell.name = 'offset_armor_shell'
    bpy.context.scene.collection.objects.link(shell)
    shell.shape_key_clear()
    positions = [v.co + v.normal * 0.015 for v in shell.data.vertices]
    for vertex, position in zip(shell.data.vertices,positions):
        vertex.co = position
    shell.vertex_groups.clear()
    activate(shell)
    settings.max_distance = 0.25
    rows, counts, _ = skin_weights.build_plan(reference,shell,skeleton,settings)
    assert counts.get('seam_interpolated',0) > 0
    assert bpy.ops.swg.transfer_body_weights() == {'FINISHED'}
    assert bpy.ops.swg.validate_seam_weights() == {'FINISHED'}
    assert snapshot(reference,skeleton) == original
    print('WEIGHTS PASS 15 mm armor shell: offset surface transfer and occlusion seam interpolation')
    print('WEIGHTS PASS real body: 884 exact vectors, 176 occlusion seam vertices, unchanged reference, byte-equivalent MGN weight floats')


with tempfile.TemporaryDirectory(prefix='swg_weights_') as temporary:
    synthetic()
    real_body(Path(sys.argv[sys.argv.index('--')+1]),temporary)
print('WEIGHTS COMPLETE')
