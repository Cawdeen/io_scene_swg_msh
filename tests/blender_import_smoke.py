"""Run in an isolated Blender 3.6 process:

blender --background --factory-startup --python-exit-code 1 \
    --python tests/blender_import_smoke.py -- D:/r3git/dsrc

Imports four real DSRC assets, then repeats from a temporary flat client layout.
Does not save preferences, blend files, or changes to the source assets.
"""

from pathlib import Path
import shutil
import sys
import tempfile

import bpy
import addon_utils

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
addon_utils.enable("io_scene_swg", default_set=True)
from io_scene_swg import asset_paths, support

prefs = bpy.context.preferences.addons["io_scene_swg"].preferences
dsrc = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
prefs.dsrc_root = str(dsrc)
prefs.asset_layout = 'DSRC'
prefs.convert_tex_to_png = False
roots = support.get_asset_roots(bpy.context)
assert len(roots) == 3, roots
resolved = {}
missing = []
original_find = support.find_file


def record_find(relative, root):
    found = original_find(relative, root)
    if found:
        resolved[str(relative).replace("\\", "/")] = found
    else:
        missing.append(relative)
    return found


support.find_file = record_find
samples = (
    ("msh", "appearance/mesh/a1_deluxe_floater_l0.msh"),
    ("mgn", "appearance/maul_probe_droid_l0.mgn"),
    ("lod", "appearance/lod/a1_deluxe_floater.lod"),
    ("pob", "appearance/bestine_humanities.pob"),
)


def run_imports(label):
    for extension, relative in samples:
        # Clear Blender's name-based material/image reuse between imports.
        for data in (bpy.data.objects, bpy.data.meshes, bpy.data.materials,
                     bpy.data.images, bpy.data.collections):
            for block in list(data):
                data.remove(block, do_unlink=True)
        filename = support.find_file(relative, support.get_asset_roots(bpy.context))
        assert filename, relative
        operator = getattr(bpy.ops.import_scene, extension)
        arguments = {"filepath": filename}
        if extension != "mgn":
            arguments["files"] = [{"name": Path(filename).name}]
        result = operator('EXEC_DEFAULT', **arguments)
        assert result == {'FINISHED'}, (relative, result)
        meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH']
        assert meshes, relative
        images = [image for image in bpy.data.images if image.source == 'FILE']
        assert images and all(len(image.pixels) > 0 for image in images), (relative, [(im.name, im.has_data, im.filepath) for im in images])
        assert not missing, missing
        print("SMOKE PASS", label, extension, "meshes", len(meshes), "images", len(images))


run_imports("DSRC")
with tempfile.TemporaryDirectory(prefix="swg_flat_smoke_") as directory:
    for relative, source in list(resolved.items()):
        target = Path(directory) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    prefs.swg_root = directory
    prefs.asset_layout = 'FLAT'
    assert support.get_asset_roots(bpy.context) == (directory,)
    texture = "texture/Seeker_probe_droid.dds"
    dsrc_image = support.load_shared_image(texture, roots)
    flat_image = support.load_shared_image(texture, (directory,))
    assert dsrc_image != flat_image, "Repository switch reused the other root's texture"
    assert support.load_shared_image(texture, (directory,)) == flat_image
    run_imports("FLAT")
    prefs.asset_layout = 'AUTO'
    assert support.get_asset_roots(bpy.context) == (directory,)
    prefs.auto_root = str(dsrc)
    assert support.get_asset_roots(bpy.context) == roots
    prefs.asset_layout = 'DSRC'
    assert support.get_asset_roots(bpy.context) == roots
    assert prefs.swg_root == directory
support.find_file = original_find
print("SMOKE COMPLETE: all four importers, both layouts, Auto, and saved-path switching passed")
