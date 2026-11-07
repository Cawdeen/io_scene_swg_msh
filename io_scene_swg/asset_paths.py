"""Asset lookup for flat client extracts and split DSRC repositories.

This module deliberately has no Blender dependency so lookup can be tested alone.
"""

import os


def clean_path(path):
    return os.fspath(path).replace("\\", os.sep).replace("/", os.sep)


def asset_roots(path, layout="AUTO"):
    """Return ordered search roots without recursively searching the repository."""
    if not path:
        return ()
    path = os.path.normpath(clean_path(path))
    sku = os.path.join(path, "sku.0")
    if layout == "DSRC" or (layout == "AUTO" and os.path.isdir(sku)):
        return tuple(os.path.join(sku, system, "compiled", "game")
                     for system in ("sys.client", "sys.shared", "sys.server"))
    return (path,)


def roots_from_preferences(preferences, abspath=lambda path: path):
    layout = getattr(preferences, "asset_layout", "AUTO")
    if layout == "DSRC":
        path = getattr(preferences, "dsrc_root", "")
    elif layout == "FLAT":
        path = preferences.swg_root
    else:
        # Preserve existing installations' saved client extract directory.
        path = getattr(preferences, "auto_root", "") or preferences.swg_root
    return asset_roots(abspath(path), layout) if path else ()


def _roots(root):
    if isinstance(root, (str, os.PathLike)):
        return asset_roots(root)
    return tuple(root or ())


def find_file(relative_path, root):
    """Find an exact virtual asset path; earlier roots win on duplicates."""
    if not relative_path:
        return None
    path = os.path.normpath(clean_path(relative_path))
    if os.path.isabs(path):
        candidates = (path,)
    else:
        candidates = tuple(os.path.join(base, path) for base in _roots(root))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    print("SWG asset not found: {}. Searched: {}".format(
        relative_path, "; ".join(candidates) or "no asset root configured"))
    return None


def browse_directory(relative_path, root):
    """Choose an existing browser directory, falling back to its asset root."""
    roots = _roots(root)
    for base in roots:
        candidate = os.path.join(base, clean_path(relative_path))
        if os.path.isdir(candidate):
            return os.path.join(candidate, "")
    for base in roots:
        if os.path.isdir(base):
            return os.path.join(base, "")
    return ""
