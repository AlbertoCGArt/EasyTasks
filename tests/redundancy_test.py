"""Static audit of the add-on's UI for redundancy and dead entries.

Pure AST analysis — no Blender required, though it runs fine under Blender too.

Catches three things:
  1. The same button drawn twice on surfaces the user sees simultaneously
     (the panels sharing the EasyTasks sidebar tab).
  2. Operators or menus that are registered but reachable from no UI and no
     keymap.
  3. The same entry repeated inside one container.

Entries are keyed by operator id *plus* the property assigned to the returned
operator, so `object.origin_set` with type='ORIGIN_CURSOR' and type='ORIGIN_
GEOMETRY' count as different buttons rather than a false duplicate.
"""
import ast
import collections
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_ROOT, "EasyTasks", "__init__.py")

source = open(SRC, encoding="utf-8").read()
tree = ast.parse(source)

failures = []


DYNAMIC = "<dynamic>"


def collect(node):
    """
    [(kind, key, is_dynamic)] for every .operator()/.menu() call beneath node.

    The key includes the property NAME as well as its value, so Apply
    Scale/Rotation/Location — three calls to object.transform_apply that each
    assign True, but to .scale, .rotation and .location — read as three
    distinct buttons instead of one repeated three times.
    """
    # Map each operator Call to the (attr, value) assigned to its result, e.g.
    #   layout.operator('object.origin_set').type = 'ORIGIN_CURSOR'
    prop_of = {}
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Assign):
            continue
        for target in sub.targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Call)):
                value = (sub.value.value if isinstance(sub.value, ast.Constant)
                         else DYNAMIC)
                prop_of[id(target.value)] = (target.attr, value)

    out = []
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in ("operator", "menu")
                and sub.args
                and isinstance(sub.args[0], ast.Constant)):
            ident = sub.args[0].value
            prop = prop_of.get(id(sub))
            if prop is None:
                key, dynamic = ident, False
            else:
                attr, value = prop
                key = f"{ident}[{attr}={value}]"
                # A value the parser cannot resolve is loop- or state-driven,
                # so the same call site legitimately produces many buttons.
                dynamic = value is DYNAMIC
            out.append((sub.func.attr, key, dynamic))
    return out


refs = collections.OrderedDict()
sidebar_panels = []

for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        attrs = {}
        for item in node.body:
            if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
                for t in item.targets:
                    if isinstance(t, ast.Name):
                        attrs[t.id] = item.value.value
            if isinstance(item, ast.FunctionDef) and item.name == "draw":
                found = collect(item)
                if found:
                    refs.setdefault(node.name, []).extend(found)
        if attrs.get("bl_category") == "EasyTasks":
            sidebar_panels.append(node.name)
    elif isinstance(node, ast.FunctionDef) and node.name.startswith("draw_"):
        found = collect(node)
        if found:
            refs.setdefault(node.name, []).extend(found)

# --- 1. duplicates across the always-visible sidebar ------------------------
print(f"Sidebar panels: {', '.join(sidebar_panels)}")
where = collections.defaultdict(list)
for panel in sidebar_panels:
    for _kind, key, dynamic in refs.get(panel, []):
        if not dynamic:
            where[key].append(panel)

sidebar_dupes = {k: v for k, v in where.items() if len(set(v)) > 1}
if sidebar_dupes:
    for key, panels in sidebar_dupes.items():
        failures.append(f"{key} drawn in {' and '.join(sorted(set(panels)))}")
        print(f"  FAIL  {key} appears in {' and '.join(sorted(set(panels)))}")
else:
    print("  PASS  no button drawn twice in the sidebar")

# --- 2. duplicates inside a single container --------------------------------
inner = []
for container, entries in refs.items():
    static = [key for _kind, key, dynamic in entries if not dynamic]
    for key, count in collections.Counter(static).items():
        if count > 1:
            inner.append(f"{container}: {key} x{count}")
if inner:
    failures.extend(inner)
    for line in inner:
        print(f"  FAIL  repeated entry -> {line}")
else:
    print("  PASS  no container repeats an entry")

# --- 3. registered but unreachable ------------------------------------------
declared_ops = set(re.findall(r'bl_idname\s*=\s*"(et\.\w+)"', source))
declared_menus = set(re.findall(r'bl_idname\s*=\s*"(ET_MT_\w+)"', source))

reachable = set()
for entries in refs.values():
    for _kind, key, _dynamic in entries:
        reachable.add(key.split("[")[0])

# menus opened by a keymap rather than by another menu
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "_KEYMAP_SPEC":
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        reachable.add(sub.value)

orphan_ops = sorted(declared_ops - reachable)
orphan_menus = sorted(declared_menus - reachable)

if orphan_ops or orphan_menus:
    for name in orphan_ops + orphan_menus:
        failures.append(f"unreachable: {name}")
        print(f"  FAIL  registered but reachable from no UI or keymap -> {name}")
else:
    print("  PASS  every registered operator and menu is reachable")

print("=" * 66)
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S)")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
