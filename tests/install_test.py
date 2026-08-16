"""Install the built zip the way a user would, then prove the preferences
block actually resolves — that was the bl_idname bug.

Run with:
  blender --background --factory-startup --python install_test.py
"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

import sys
import bpy

ZIP = _os.path.join(_ROOT, "easy_tasks_2.4.0.zip")
MODULE = "EasyTasks"

failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        failures.append(label)
        print(f"  FAIL  {label} {detail}")


print(f"Blender {bpy.app.version_string}")

bpy.ops.preferences.addon_install(filepath=ZIP, overwrite=True)
bpy.ops.preferences.addon_enable(module=MODULE)

addons = bpy.context.preferences.addons
check("addon enabled under module 'EasyTasks'", MODULE in addons,
      f"-> enabled modules: {[a.module for a in addons]}")

prefs = addons[MODULE].preferences
check("preferences object resolves (bl_idname matches)", prefs is not None)
check("preferences expose auto_route", hasattr(prefs, "auto_route"),
      f"-> {dir(prefs)}")

if hasattr(prefs, "auto_route"):
    prefs.auto_route = False
    check("auto_route is writable", prefs.auto_route is False)
    prefs.auto_route = True

# operators registered from the installed copy
check("et.assign_semantic registered", hasattr(bpy.ops.et, "assign_semantic"))
check("scene.et_semantic registered", hasattr(bpy.context.scene, "et_semantic"))

# keymaps landed in the 3D View keymap, not the global Window keymap
mod = sys.modules.get(MODULE)
keymap_names = {km.name for km, _kmi in mod.addon_keymaps.values()}
check("keymaps registered in the 3D View keymap", keymap_names == {"3D View"},
      f"-> {keymap_names}")

bpy.ops.preferences.addon_disable(module=MODULE)
check("addon disables cleanly", MODULE not in bpy.context.preferences.addons)

print("=" * 60)
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
