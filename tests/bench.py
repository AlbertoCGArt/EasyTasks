"""Benchmark the rewritten hot paths against the original implementations.

Run with: blender --background --factory-startup --python bench.py
"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

import importlib.util
import sys
import time

import bpy
import numpy as np

SRC = _os.path.join(_ROOT, "EasyTasks", "__init__.py")
spec = importlib.util.spec_from_file_location("EasyTasks", SRC,
                                              submodule_search_locations=[])
et = importlib.util.module_from_spec(spec)
sys.modules["EasyTasks"] = et
spec.loader.exec_module(et)
et.register()

scene = bpy.context.scene


def timed(fn, repeats=1):
    best = float('inf')
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best


def row(label, old, new):
    speedup = old / new if new > 0 else float('inf')
    print(f"{label:<34} old {old*1000:9.1f} ms   new {new*1000:8.1f} ms   "
          f"{speedup:6.1f}x")


# --------------------------------------------------------------------------
# Dense mesh for the per-loop work
# --------------------------------------------------------------------------
bpy.ops.mesh.primitive_grid_add(x_subdivisions=400, y_subdivisions=400)
grid = bpy.context.active_object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.uv.unwrap()
bpy.ops.object.mode_set(mode='OBJECT')
mesh = grid.data
print(f"Bench mesh: {len(mesh.polygons):,} polys / {len(mesh.loops):,} loops\n")


# --- Face stretch colour computation --------------------------------------
def old_stretch_colors(obj):
    m = obj.data
    uv_layer = m.uv_layers.active
    ratios = []
    for poly in m.polygons:
        area_3d = poly.area
        uvs = [uv_layer.data[li].uv for li in poly.loop_indices]
        area_uv = 0.0
        n = len(uvs)
        for i in range(n):
            j = (i + 1) % n
            area_uv += uvs[i].x * uvs[j].y - uvs[j].x * uvs[i].y
        area_uv = abs(area_uv) * 0.5
        ratios.append(area_3d / area_uv if area_uv > 1e-10 else None)
    valid = [r for r in ratios if r is not None]
    avg = sum(valid) / len(valid)
    colors = []
    for r in ratios:
        if r is None:
            colors.append((0.5, 0.5, 0.5))
            continue
        t = r / avg
        if t <= 1.0:
            colors.append((0.0, t, 1.0 - t))
        else:
            f = min((t - 1.0), 1.0)
            colors.append((f, 1.0 - f, 0.0))
    return colors


analyzer = et.ET_OT_FaceStretchAnalyzer
row("face stretch: compute colours",
    timed(lambda: old_stretch_colors(grid)),
    timed(lambda: analyzer._loop_colors(analyzer, grid)))


# --- Vertex-colour write ---------------------------------------------------
vcol = analyzer._get_or_create_vcol(analyzer, mesh)
colors = old_stretch_colors(grid)
loop_colors = analyzer._loop_colors(analyzer, grid)


def old_write():
    for pi, poly in enumerate(mesh.polygons):
        col = colors[pi]
        for loop_idx in poly.loop_indices:
            vcol.data[loop_idx].color = (*col, 1.0)


row("face stretch: write vertex colours",
    timed(old_write),
    timed(lambda: vcol.data.foreach_set('color', loop_colors)))


# --- Stacked UV signature --------------------------------------------------
def old_uv_signature(obj):
    m = obj.data
    uv_layer = m.uv_layers.active
    coords = tuple((round(d.uv.x, 3), round(d.uv.y, 3)) for d in uv_layer.data)
    return (len(coords), hash(coords))


detector = et.ET_OT_StackedUVDetector
detector.tolerance = 3
row("stacked UV: build signature",
    timed(lambda: old_uv_signature(grid)),
    timed(lambda: detector._uv_signature(detector, grid)))


# --- Polygon smooth flags --------------------------------------------------
def old_smooth():
    for polygon in mesh.polygons:
        polygon.use_smooth = True


def new_smooth():
    mesh.polygons.foreach_set('use_smooth',
                              np.ones(len(mesh.polygons), dtype=np.int8))


row("shade smooth: set use_smooth", timed(old_smooth), timed(new_smooth))


# --------------------------------------------------------------------------
# Many-object scene for the routing / origin paths
# --------------------------------------------------------------------------
bpy.ops.object.select_all(action='DESELECT')
grid.select_set(True)
bpy.ops.object.delete()

N_OBJECTS = 1200
N_COLLECTIONS = 40
for i in range(N_COLLECTIONS):
    c = bpy.data.collections.new(f"Group{i:02d}s")
    scene.collection.children.link(c)

base_mesh = bpy.data.meshes.new("Shared")
base_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
for i in range(N_OBJECTS):
    obj = bpy.data.objects.new(f"Group{i % N_COLLECTIONS:02d}_Part_{i:04d}",
                               base_mesh)
    scene.collection.objects.link(obj)

print(f"\nRouting scene: {len(scene.objects):,} objects / "
      f"{len(bpy.data.collections):,} collections\n")


def old_find_target(obj):
    type_map = {'LIGHT': 'LIGHTS', 'CAMERA': 'CAMERAS'}
    coll_name = type_map.get(obj.type)
    if coll_name:
        coll = bpy.data.collections.get(coll_name)
        if coll:
            return coll
    parts = et._obj_name_parts(obj)
    for part in parts:
        alias = et._KEYWORD_ALIASES.get(part)
        if alias:
            coll = bpy.data.collections.get(alias)
            if coll:
                return coll
    for coll in bpy.data.collections:
        lower = coll.name.lower()
        keywords = {lower}
        if lower.endswith('s') and len(lower) > 2:
            keywords.add(lower[:-1])
        if parts & keywords:
            return coll
    return None


objects = list(scene.objects)


def old_route_scan():
    for obj in objects:
        old_find_target(obj)


def new_route_scan():
    index = et._build_route_index()
    for obj in objects:
        et._find_target_collection(obj, index)


row("arrange scene: match all objects",
    timed(old_route_scan), timed(new_route_scan))


# --- Auto-route handler steady-state cost ---------------------------------
et._known_objects[scene.name] = {o.name for o in scene.objects}


def old_handler_tick():
    # what the previous handler did on every single depsgraph update
    current = {obj.name for obj in scene.objects}
    known = et._known_objects[scene.name]
    _ = current - known


def new_handler_tick():
    et._auto_route_new_objects(scene, None)


row("auto-route: idle depsgraph tick",
    timed(old_handler_tick, repeats=50),
    timed(new_handler_tick, repeats=50))

et.unregister()
print("\ndone")
