"""Compatibility entry points for the evaluated-pose KFAT exporter."""
from .animation_io import export_animation

def save(context, filepath):
    export_animation(context, filepath)
    return {'FINISHED'}

def export_ans(context, filepath, action=None):
    # The active evaluated rig/action is the source, including constraints.
    if action is not None:
        from .animation_io import targets
        _, owner = targets(context)
        if not owner.animation_data or owner.animation_data.action != action:
            raise ValueError("Make the requested action active before exporting")
    return save(context, filepath)
