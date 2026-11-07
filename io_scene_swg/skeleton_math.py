"""SWG joint frames, expressed in the same Blender space as default MGN imports.

Client reference: BasicSkeletonTemplate::calculateBindPoseModelToJointTransforms
and Skeleton::calculateJointToRootTransforms. SKT quaternions are w, x, y, z.
"""

import math
from mathutils import Matrix, Quaternion, Vector


# The MGN reader first negates file-space Z. Its default Z-forward/Y-up
# axis conversion then maps the result to (-x, -z, y). The FULL conversion
# is a reflection, so joint frames also need a local-axis reflection to
# remain right-handed Blender bones. This matches the legacy SKT importer's
# intended quaternion conversion (reflect local X, then rotate +90 about X).
# frame_blender = SWG_TO_BLENDER @ frame_swg @ JOINT_AXIS_REFLECTION.
SWG_TO_BLENDER = Matrix(((-1, 0, 0, 0), (0, 0, -1, 0),
                         (0, 1, 0, 0), (0, 0, 0, 1)))
JOINT_AXIS_REFLECTION = Matrix(((-1, 0, 0, 0), (0, 1, 0, 0),
                                (0, 0, 1, 0), (0, 0, 0, 1)))
BLENDER_TO_SWG = SWG_TO_BLENDER.inverted()
SKELETON_VERSION = 3


def set_bone_frame(bone, frame, length):
    bone.head = frame.translation
    bone.tail = bone.head + frame.to_3x3().col[1] * length
    target = frame.to_3x3().col[2]
    bone.align_roll(target)
    # Blender 3.6's near-negative-Y roll conversion can leave a small residual
    # twist. Correct it against the frame Blender actually constructed.
    axis = (bone.tail - bone.head).normalized()
    actual = bone.matrix.to_3x3().col[2].normalized()
    target = (target - axis * target.dot(axis)).normalized()
    bone.roll += math.atan2(axis.dot(actual.cross(target)), actual.dot(target))


def joint_local_matrix(translation, pre, bind, post):
    rotation = Quaternion(post) @ Quaternion(bind) @ Quaternion(pre)
    return Matrix.Translation(Vector(translation)) @ rotation.to_matrix().to_4x4()


def bind_matrices(skeleton):
    """Validate a skeleton and resolve its hierarchy, including unordered joints."""
    count = skeleton.joint_count
    arrays = (skeleton.joint_names, skeleton.joint_parents,
              skeleton.joint_translations, skeleton.joint_pre_rotations,
              skeleton.joint_bind_rotations, skeleton.joint_post_rotations,
              skeleton.joint_rotation_order)
    if count < 1 or any(array is None or len(array) != count for array in arrays):
        raise ValueError("Skeleton has incomplete joint data")
    if len(set(skeleton.joint_names)) != count:
        raise ValueError("Skeleton contains duplicate joint names")
    worlds = [None] * count
    visiting = set()

    def resolve(index):
        if worlds[index] is not None:
            return worlds[index]
        if index in visiting:
            raise ValueError("Skeleton joint hierarchy contains a cycle")
        visiting.add(index)
        parent = skeleton.joint_parents[index]
        if parent < -1 or parent >= count:
            raise ValueError("Skeleton joint has an invalid parent index")
        local = joint_local_matrix(skeleton.joint_translations[index],
                                   skeleton.joint_pre_rotations[index],
                                   skeleton.joint_bind_rotations[index],
                                   skeleton.joint_post_rotations[index])
        worlds[index] = local if parent == -1 else resolve(parent) @ local
        visiting.remove(index)
        return worlds[index]

    return [SWG_TO_BLENDER @ resolve(i) @ JOINT_AXIS_REFLECTION for i in range(count)]


def export_joint_data(bone, parent):
    """Recover native parent-space bind data without changing the Blender scene."""
    world = BLENDER_TO_SWG @ bone.matrix_local @ JOINT_AXIS_REFLECTION
    parent_world = BLENDER_TO_SWG @ parent.matrix_local @ JOINT_AXIS_REFLECTION if parent else None
    local = world if parent is None else parent_world.inverted() @ world
    pre = list(bone.get("RPRE", (1, 0, 0, 0)))
    post = list(bone.get("RPST", (1, 0, 0, 0)))
    bind = Quaternion(post).inverted() @ local.to_quaternion() @ Quaternion(pre).inverted()
    original_bind = bone.get("BPRO")
    if original_bind is not None:
        error = min(max(abs(bind[i] - sign * original_bind[i]) for i in range(4))
                    for sign in (1, -1))
        if error < 1e-6:
            bind = original_bind
    return pre, post, list(local.translation), list(bind), int(bone.get("JROR", 0))
