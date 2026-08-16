"""Validate every icon= string and every settings property the UI references.

An unknown icon name raises at draw time, which breaks the whole panel, and a
typo'd property name does the same — neither shows up in a headless op test.
"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

import importlib.util
import re
import sys

import bpy

SRC = _os.path.join(_ROOT, "EasyTasks", "__init__.py")
spec = importlib.util.spec_from_file_location("EasyTasks", SRC,
                                              submodule_search_locations=[])
et = importlib.util.module_from_spec(spec)
sys.modules["EasyTasks"] = et
spec.loader.exec_module(et)
et.register()

failures = []

# --- icons -----------------------------------------------------------------
valid = set(bpy.types.UILayout.bl_rna.functions['prop'].parameters['icon'].enum_items.keys())
source = open(SRC, encoding='utf-8').read()
used = set(re.findall(r"icon\s*=\s*'([A-Z0-9_]+)'", source))
used |= {icon for _k, _l, icon, _c in et.SEMANTIC_CATEGORIES}

unknown = sorted(used - valid)
print(f"{len(used)} distinct icon names referenced")
if unknown:
    failures.append(f"unknown icons: {unknown}")
    print(f"  FAIL  unknown icon names: {unknown}")
else:
    print("  PASS  every icon name is valid")

# --- settings properties referenced by the panel ---------------------------
settings = bpy.context.scene.et_semantic
referenced = set(re.findall(r"settings,\s*'(\w+)'", source))
missing = sorted(p for p in referenced if not hasattr(settings, p))
print(f"{len(referenced)} settings properties referenced by the UI")
if missing:
    failures.append(f"missing settings properties: {missing}")
    print(f"  FAIL  missing properties: {missing}")
else:
    print("  PASS  every referenced settings property exists")

# --- every operator/menu idname referenced in the UI actually registered ----
op_ids = set(re.findall(r"\.operator\(\s*'(et\.\w+)'", source))
missing_ops = sorted(o for o in op_ids
                     if not hasattr(bpy.ops.et, o.split('.', 1)[1]))
menu_ids = set(re.findall(r"\.menu\(\s*'(ET_MT_\w+)'", source))
missing_menus = sorted(m for m in menu_ids if not hasattr(bpy.types, m))

print(f"{len(op_ids)} et.* operators and {len(menu_ids)} menus referenced")
if missing_ops or missing_menus:
    failures.append(f"unregistered: {missing_ops} {missing_menus}")
    print(f"  FAIL  unregistered ops {missing_ops} menus {missing_menus}")
else:
    print("  PASS  every referenced operator and menu is registered")

et.unregister()

print("=" * 60)
if failures:
    print("RESULT: FAILURES:", failures)
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
