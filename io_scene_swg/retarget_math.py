"""Rest-direction retargeting, independent of Blender operators and scene state."""

import hashlib
import json
import math

from mathutils import Matrix, Quaternion, Vector


def flatten(matrix):
    return [float(value) for row in matrix for value in row]


def matrix(values):
    return Matrix([values[i:i+4] for i in range(0,16,4)])


def rotation(matrix_value):
    return matrix_value.to_quaternion().normalized().to_matrix()


def rigid(rotation_value,position):
    result=rotation_value.to_4x4()
    result.translation=position
    return result


def signature(source,target):
    values=[]
    for obj in (source,target):
        values.append([(bone.name,bone.parent.name if bone.parent else None,bool(bone.use_connect),
                        [round(v,7) for v in flatten(bone.matrix_local)]) for bone in obj.data.bones])
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def anatomical_frame(positions,root,head,left_hip,right_hip):
    up=positions[head]-positions[root]
    right=positions[right_hip]-positions[left_hip]
    if right.length<1e-6 or up.length<1e-6:
        raise ValueError("Calibration needs distinct hips, root and head positions")
    # Small rest-pose spinal curves should not tilt horizontal root travel.
    dominant=max(range(3),key=lambda i:abs(up[i]))
    if abs(up[dominant])/up.length>0.985:
        sign=1.0 if up[dominant]>0 else -1.0
        up=Vector((0,0,0));up[dominant]=sign
    up.normalize()
    right=right-up*right.dot(up)
    if right.length<1e-6:
        raise ValueError("Calibration body axes are degenerate")
    right.normalize()
    forward=up.cross(right).normalized()
    return Matrix((right,forward,up)).transposed()


def leg_length(positions,chain):
    return sum((positions[b]-positions[a]).length for a,b in zip(chain,chain[1:]))


def orientation_offset(source_reference,target_reference,alignment,automatic=True):
    source=alignment @ rotation(source_reference)
    target=rotation(target_reference)
    if automatic:
        # Match physical bone directions in the calibration posture while
        # retaining source roll, then compensate for target roll. This leaves
        # the source/target rest skeletons intact and corrects A/T pose offsets.
        swing=source.col[1].rotation_difference(target.col[1]).to_matrix()
        source=swing @ source
    return source.inverted() @ target


def retarget_orientations(source_frames,mapping,offsets,alignment):
    return {target:alignment @ rotation(source_frames[source]) @ offsets[target]
            for target,source in mapping.items()}


def heading_angle(root_rotation,reference_rotation,forward):
    vector=(root_rotation @ reference_rotation.inverted()) @ forward
    a=Vector((forward.x,forward.y,0))
    b=Vector((vector.x,vector.y,0))
    if a.length<1e-6 or b.length<1e-6:
        return 0.0
    a.normalize();b.normalize()
    return math.atan2(a.cross(b).z,a.dot(b))


def target_pose(bones,neutral_basis,orientations,owner_world,root_name,root_position):
    """Copy world rotations, retain target limb lengths and reference offsets."""
    result={}
    inverse_rotation=rotation(owner_world).inverted()
    owner_inverse=owner_world.inverted()

    def resolve(bone):
        if bone.name not in result:
            base=bone.matrix_local @ neutral_basis[bone.name]
            if bone.parent:
                base=resolve(bone.parent) @ bone.parent.matrix_local.inverted() @ base
            if bone.name in orientations:
                base=rigid(inverse_rotation @ orientations[bone.name],base.translation)
            if bone.name==root_name:
                base.translation=owner_inverse @ root_position
            result[bone.name]=base
        return result[bone.name]

    for bone in bones:
        resolve(bone)
    return result


def basis_for_pose(bone,pose,poses):
    if bone.parent:
        return bone.matrix_local.inverted() @ bone.parent.matrix_local @ poses[bone.parent.name].inverted() @ pose
    return bone.matrix_local.inverted() @ pose
