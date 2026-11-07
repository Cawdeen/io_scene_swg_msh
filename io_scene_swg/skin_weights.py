"""Reference-body skinning with explicit treatment of occlusion seams.

Coordinates are taken from base meshes in world space, never a posed/evaluated
surface. Exact matches retain float weights verbatim; seam interpolation is
kept separate from interior normalization/influence limiting.
"""

from collections import defaultdict
import struct

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from . import animation_rig


SEAM_GROUP = "SWG_SeamLock"


def float_weights(weights):
    return {name: struct.unpack('f', struct.pack('f', value))[0]
            for name, value in weights.items() if value > 0}


def mesh_positions(obj):
    keys = obj.data.shape_keys
    vertices = keys.key_blocks[0].data if keys else obj.data.vertices
    return [obj.matrix_world @ vertex.co for vertex in vertices]


def mesh_zones(obj):
    layer = obj.data.face_maps.active
    labels = []
    memberships = [set() for _ in obj.data.vertices]
    for polygon in obj.data.polygons:
        index = layer.data[polygon.index].value if layer else -1
        label = obj.face_maps[index].name if 0 <= index < len(obj.face_maps) else ""
        labels.append(label)
        if label:
            for vertex in polygon.vertices:
                memberships[vertex].add(label)
    return labels, memberships


def boundary_edges(obj, labels):
    adjacency = defaultdict(list)
    for polygon in obj.data.polygons:
        for edge in polygon.edge_keys:
            adjacency[tuple(sorted(edge))].append(labels[polygon.index])
    return [(edge, set(zones) - {""}) for edge, zones in adjacency.items()
            if len(zones) == 1 or len(set(zones)) > 1]


def group_vertices(obj, name):
    group = obj.vertex_groups.get(name)
    if group is None:
        raise ValueError("Vertex group not found: " + name)
    return {vertex.index for vertex in obj.data.vertices
            if any(g.group == group.index and g.weight > 0 for g in vertex.groups)}


def protected_vertices(obj, settings, labels):
    if settings.seam_selection == 'GROUP':
        vertices = group_vertices(obj, settings.seam_group)
        if not vertices:
            raise ValueError("The seam vertex group is empty")
        return vertices
    return {v for edge, _ in boundary_edges(obj, labels) for v in edge}


def deform_weights(obj, names):
    groups = {group.index: group.name for group in obj.vertex_groups if group.name in names}
    return [{groups[g.group]: g.weight for g in vertex.groups
             if g.group in groups and g.weight > 0} for vertex in obj.data.vertices]


def blend_weights(rows):
    result = defaultdict(float)
    for weights, factor in rows:
        for name, value in weights.items():
            result[name] += factor * value
    return dict(result)


class ReferenceSurface:
    def __init__(self, reference, skeleton, settings):
        self.positions = mesh_positions(reference)
        self.labels, self.zones = mesh_zones(reference)
        self.weights = deform_weights(reference, {b.name for b in skeleton.data.bones if b.use_deform})
        if not self.positions or any(not row for row in self.weights):
            raise ValueError("Reference must have bone weights on every vertex for the selected skeleton")
        if any(abs(sum(row.values()) - 1.0) > 0.0001 for row in self.weights):
            raise ValueError("Reference bone weights do not sum to one for this skeleton. Check for missing bones, the wrong skeleton LOD, or incomplete weights")
        self.edges = boundary_edges(reference, self.labels)
        if settings.reference_seam_group:
            allowed = group_vertices(reference, settings.reference_seam_group)
            self.edges = [(edge, zones) for edge, zones in self.edges if set(edge) <= allowed]
            # Explicit reference groups can also define internal garment seams.
            edge_set = {edge for edge, _ in self.edges}
            for edge in reference.data.edges:
                key = tuple(sorted(edge.vertices))
                if set(key) <= allowed and key not in edge_set:
                    self.edges.append((key, self.zones[key[0]] | self.zones[key[1]]))
                    edge_set.add(key)
            self.seams = allowed
        else:
            self.seams = {v for edge, _ in self.edges for v in edge}
        self.kd = KDTree(len(self.positions))
        for i, point in enumerate(self.positions):
            self.kd.insert(point, i)
        self.kd.balance()
        reference.data.calc_loop_triangles()
        self.triangles = [(tuple(tri.vertices), self.labels[tri.polygon_index])
                          for tri in reference.data.loop_triangles
                          if ((self.positions[tri.vertices[1]] - self.positions[tri.vertices[0]]).cross(
                              self.positions[tri.vertices[2]] - self.positions[tri.vertices[0]])).length_squared > 1e-20]
        self.bvhs = {}
        self.known_zones = set(self.labels) - {""}

    def compatible(self, target_zones, reference_zones):
        known = target_zones & self.known_zones
        return not known or known <= reference_zones

    def exact(self, point, zones, tolerance, seam=False):
        candidates = [index for _, index, _ in self.kd.find_range(point, tolerance)
                      if (not seam or index in self.seams) and self.compatible(zones, self.zones[index])]
        if not candidates:
            return None
        rows = [self.weights[index] for index in candidates]
        if any(row != rows[0] for row in rows[1:]):
            raise ValueError("Conflicting reference weights at coincident vertices; select a more specific reference seam group")
        return dict(rows[0])

    def seam_vertex(self, point, zones):
        candidates = [((point - self.positions[i]).length, i) for i in self.seams
                      if self.compatible(zones, self.zones[i])]
        if not candidates:
            raise ValueError("No compatible reference seam vertices")
        distance, index = min(candidates)
        ties = [i for d, i in candidates if abs(d - distance) < 1e-8]
        if any(self.weights[i] != self.weights[index] for i in ties):
            raise ValueError("Equidistant reference seams have different weights; select a reference seam group")
        return dict(self.weights[index]), distance, 'copied'

    def seam(self, point, zones, settings):
        if settings.seam_method == 'COPY':
            return self.seam_vertex(point, zones)
        best = None
        ambiguous = False
        for (a, b), edge_zones in self.edges:
            if not self.compatible(zones, edge_zones):
                continue
            start, end = self.positions[a], self.positions[b]
            direction = end - start
            if direction.length_squared < 1e-20:
                continue
            factor = max(0.0, min(1.0, (point - start).dot(direction) / direction.length_squared))
            distance = (point - (start + direction * factor)).length
            row = blend_weights(((self.weights[a], 1 - factor), (self.weights[b], factor)))
            if best is not None and abs(distance - best[0]) < 1e-8:
                difference = max(abs(row.get(name, 0) - best[1].get(name, 0)) for name in set(row) | set(best[1]))
                if difference > 1e-6:
                    ambiguous = True
            elif best is None or distance < best[0]:
                kind = 'copied' if factor <= 1e-8 or factor >= 1 - 1e-8 else 'interpolated'
                best = (distance, row, kind)
                ambiguous = False
        if best is None:
            # A junction shared by three or more zones has no single matching
            # edge. Preserve the corresponding reference junction's vector.
            return self.seam_vertex(point, zones)
        if ambiguous:
            raise ValueError("Equidistant reference seam edges have different weights; select a reference seam group")
        return best[1], best[0], best[2]

    def surface(self, point, zones, settings):
        key = frozenset(zones & self.known_zones)
        if key not in self.bvhs:
            triangles = [vertices for vertices, label in self.triangles if not key or label in key]
            if not triangles:
                raise ValueError("Reference has no usable triangles for this occlusion zone")
            self.bvhs[key] = (BVHTree.FromPolygons(self.positions, triangles, all_triangles=True), triangles)
        bvh, triangles = self.bvhs[key]
        location, _, index, distance = bvh.find_nearest(point)
        if location is None:
            raise ValueError("Reference surface could not be matched")
        a, b, c = triangles[index]
        start = self.positions[a]
        v0, v1, v2 = self.positions[b] - start, self.positions[c] - start, location - start
        d00, d01, d11 = v0.dot(v0), v0.dot(v1), v1.dot(v1)
        denominator = d00 * d11 - d01 * d01
        if abs(denominator) < 1e-24:
            raise ValueError("Degenerate reference triangle")
        wb = (d11 * v2.dot(v0) - d01 * v2.dot(v1)) / denominator
        wc = (d00 * v2.dot(v1) - d01 * v2.dot(v0)) / denominator
        factors = [max(0.0, v) for v in (1 - wb - wc, wb, wc)]
        weights = blend_weights(zip((self.weights[a], self.weights[b], self.weights[c]), factors))
        if settings.max_influences:
            weights = dict(sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:settings.max_influences])
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("Reference triangle has no usable weights")
        return {name: weight / total for name, weight in weights.items()}, distance


def resolve_inputs(context, settings):
    target = context.active_object
    reference = settings.reference or (target.get('swg_weight_reference') if target else None) or bpy.data.objects.get('hum_m_body_l0')
    skeleton = animation_rig.game_skeleton_for(settings.skeleton or (target.get('swg_weight_skeleton') if target else None))
    if skeleton is None and target:
        for modifier in target.modifiers:
            if modifier.type == 'ARMATURE':
                skeleton = animation_rig.game_skeleton_for(modifier.object)
                if skeleton:
                    break
    if skeleton is None:
        candidates = [obj for obj in context.scene.objects if animation_rig.is_game_skeleton(obj)]
        if len(candidates) == 1:
            skeleton = candidates[0]
    if context.mode != 'OBJECT' or target is None or target.type != 'MESH':
        raise ValueError("Select the target mesh in Object Mode")
    if reference is None or reference.type != 'MESH':
        raise ValueError("Choose a weighted reference body, such as hum_m_body_l0")
    if reference == target:
        raise ValueError("The reference body cannot also be the target")
    if not skeleton:
        raise ValueError("Choose the imported game skeleton or its FK rig")
    return reference, target, skeleton


def build_plan(reference, target, skeleton, settings, seams_only=False):
    surface = ReferenceSurface(reference, skeleton, settings)
    labels, zones = mesh_zones(target)
    protected = protected_vertices(target, settings, labels)
    if settings.seam_selection == 'GROUP' and settings.seam_group in {b.name for b in skeleton.data.bones if b.use_deform}:
        raise ValueError("Use a separate helper vertex group for seam selection, not a bone group")
    rows = {}
    counts = defaultdict(int)
    far = []
    maximum_distance = 0.0
    for index, point in enumerate(mesh_positions(target)):
        seam = index in protected
        if seams_only and not seam:
            continue
        exact = surface.exact(point, zones[index], settings.match_tolerance, seam=seam)
        if exact is not None:
            weights, distance, kind = exact, 0.0, 'exact'
        elif seam:
            weights, distance, kind = surface.seam(point, zones[index], settings)
        else:
            weights, distance = surface.surface(point, zones[index], settings)
            kind = 'surface'
        if settings.max_distance > 0 and distance > settings.max_distance:
            far.append(index)
        maximum_distance = max(maximum_distance, distance)
        rows[index] = float_weights(weights)
        counts[kind] += 1
        if seam:
            counts['seams'] += 1
            counts['seam_' + kind] += 1
    if far:
        raise ValueError("{} vertices exceed the reference distance limit (first: {}). Align the meshes or adjust the limit".format(len(far), far[:8]))
    return rows, dict(counts), maximum_distance


def apply_plan(reference, target, skeleton, rows, settings):
    names = {bone.name for bone in skeleton.data.bones if bone.use_deform}
    locked = [g.name for g in target.vertex_groups if g.name in names and g.lock_weight]
    if locked:
        raise ValueError("Unlock target bone groups before transferring: " + ", ".join(locked[:8]))
    if target.library:
        raise ValueError("Make the target object local before transferring weights")
    modifiers = [m for m in target.modifiers if m.type == 'ARMATURE']
    if settings.bind_to_skeleton and (len(modifiers) > 1 or (modifiers and modifiers[0].object not in (None, skeleton))):
        raise ValueError("Target already has a different Armature modifier; resolve it before binding")
    if target.data.users > 1:
        target.data = target.data.copy()
    vertices = list(range(len(target.data.vertices)))
    for group in target.vertex_groups:
        if group.name in names:
            group.remove(vertices)
    for index, weights in rows.items():
        for name, weight in weights.items():
            group = target.vertex_groups.get(name) or target.vertex_groups.new(name=name)
            group.add([index], weight, 'REPLACE')
    target['swg_weight_reference'] = reference
    target['swg_weight_skeleton'] = skeleton
    if settings.bind_to_skeleton:
        modifier = modifiers[0] if modifiers else target.modifiers.new("SWG Skeleton", 'ARMATURE')
        modifier.object = skeleton
        modifier.use_deform_preserve_volume = False


def export_weights(obj, vertices):
    """Export only bone influences and remap indices after excluding helpers."""
    skeleton = obj.get('swg_weight_skeleton')
    if skeleton is None:
        skeleton = next((m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None)
    names = {b.name for b in skeleton.data.bones if b.use_deform} if skeleton and skeleton.type == 'ARMATURE' else None
    groups = [g for g in obj.vertex_groups
              if (g.name in names if names is not None else not g.name.startswith('SWG_'))]
    remap = {group.index: index for index, group in enumerate(groups)}
    rows = [[[remap[g.group], g.weight] for g in vertex.groups if g.group in remap and g.weight > 0]
            for vertex in vertices]
    if not rows or any(not row for row in rows):
        raise ValueError("MGN export requires bone weights on every vertex; helper groups do not count")
    return [group.name for group in groups], rows


class SWGWeightSettings(bpy.types.PropertyGroup):
    reference: PointerProperty(name="Reference Body", type=bpy.types.Object,
                               poll=lambda self, obj: obj.type == 'MESH',
                               description="Weighted rest body; defaults to hum_m_body_l0")
    skeleton: PointerProperty(name="Skeleton / FK Rig", type=bpy.types.Object,
                              poll=lambda self, obj: obj.type == 'ARMATURE')
    seam_selection: EnumProperty(name="Target Seams", items=(
        ('AUTO', "Occlusion Borders + Open Edges", "Find target face-map boundaries and open edges"),
        ('GROUP', "Vertex Group", "Use a hand-picked target seam vertex group")), default='AUTO')
    seam_group: StringProperty(name="Seam Vertex Group", default=SEAM_GROUP)
    reference_seam_group: StringProperty(name="Reference Seam Group",
                                        description="Optional body vertex group restricting correspondence to a specific seam")
    seam_method: EnumProperty(name="Remeshed Seams", items=(
        ('EDGE', "Smooth Reference Edge", "Interpolate reference boundary-edge weights for new or offset seam vertices"),
        ('COPY', "Copy Nearest Vertex Exactly", "Copy one original seam vertex's weight vector without normalization or pruning")), default='EDGE')
    match_tolerance: FloatProperty(name="Exact Match Tolerance", default=0.00001, min=0.0000001, max=0.01, precision=6)
    max_distance: FloatProperty(name="Max Reference Distance", default=0.25, min=0, max=100,
                                description="World-space distance limit; zero disables the limit")
    max_influences: IntProperty(name="Interior Influence Limit", default=0, min=0, max=32,
                               description="Zero is unlimited; exact copies and protected seams are never pruned")
    bind_to_skeleton: BoolProperty(name="Bind to Game Skeleton", default=True)


class SWG_OT_transfer_weights(bpy.types.Operator):
    bl_idname = "swg.transfer_body_weights"
    bl_label = "Transfer Body Weights"
    bl_description = "Transfer reference skin weights; handle protected seams separately from clothing interiors"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        settings = context.scene.swg_skin_weights
        try:
            reference, target, skeleton = resolve_inputs(context, settings)
            rows, counts, distance = build_plan(reference, target, skeleton, settings)
            apply_plan(reference, target, skeleton, rows, settings)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        message = "{} vertices: {} exact copies; seams {} copied / {} interpolated; max offset {:.5f}".format(
            len(rows), counts.get('exact', 0), counts.get('seam_exact', 0) + counts.get('seam_copied', 0),
            counts.get('seam_interpolated', 0), distance)
        target['swg_weight_report'] = message
        self.report({'INFO'}, message)
        if not counts.get('seams'):
            self.report({'WARNING'}, "No protected seams found; choose a seam vertex group if needed")
        return {'FINISHED'}


class SWG_OT_validate_seam_weights(bpy.types.Operator):
    bl_idname = "swg.validate_seam_weights"
    bl_label = "Validate Seam Weights"
    bl_description = "Compare seam weights to the current reference correspondence, without changing weights"

    @classmethod
    def poll(cls, context):
        return SWG_OT_transfer_weights.poll(context)

    def execute(self, context):
        try:
            settings = context.scene.swg_skin_weights
            reference, target, skeleton = resolve_inputs(context, settings)
            rows, counts, distance = build_plan(reference, target, skeleton, settings, seams_only=True)
            current = deform_weights(target, {b.name for b in skeleton.data.bones if b.use_deform})
            mismatches = [index for index, expected in rows.items() if current[index] != expected]
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        if not rows:
            self.report({'WARNING'}, "No seam vertices were selected for validation")
            return {'CANCELLED'}
        if mismatches:
            self.report({'ERROR'}, "{} seam vertices differ from the reference mapping (first: {})".format(len(mismatches), mismatches[:8]))
            return {'CANCELLED'}
        self.report({'INFO'}, "{} seam weights match: {} original vectors, {} interpolated; max offset {:.5f}".format(
            len(rows), counts.get('seam_exact', 0) + counts.get('seam_copied', 0), counts.get('seam_interpolated', 0), distance))
        return {'FINISHED'}


class SWG_PT_skin_weights(bpy.types.Panel):
    bl_label = "Skin Weights"
    bl_idname = "SWG_PT_skin_weights"
    bl_parent_id = "SWG_PT_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "SWG"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.swg_skin_weights
        layout.prop(settings, 'reference')
        if not settings.reference:
            layout.label(text="Default reference: hum_m_body_l0")
        layout.prop(settings, 'skeleton')
        layout.prop(settings, 'seam_selection')
        target = context.active_object
        if settings.seam_selection == 'GROUP' and target and target.type == 'MESH':
            layout.prop_search(settings, 'seam_group', target, 'vertex_groups')
        reference = settings.reference or bpy.data.objects.get('hum_m_body_l0')
        if reference and reference.type == 'MESH':
            layout.prop_search(settings, 'reference_seam_group', reference, 'vertex_groups')
        layout.prop(settings, 'seam_method')
        layout.prop(settings, 'max_distance')
        layout.prop(settings, 'match_tolerance')
        layout.prop(settings, 'max_influences')
        layout.prop(settings, 'bind_to_skeleton')
        layout.operator('swg.transfer_body_weights', icon='MOD_VERTEX_WEIGHT')
        layout.operator('swg.validate_seam_weights', icon='CHECKMARK')
