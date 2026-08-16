"""Headless smoke test for the Easy Tasks addon.

Loads the module as a package named 'EasyTasks' (matching how the zip installs),
registers it, exercises the operators that can run without a UI, and unregisters.
Run with:  blender --background --factory-startup --python smoke_test.py
"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

import importlib.util
import sys
import traceback

import bpy

SRC = _os.path.join(_ROOT, "EasyTasks", "__init__.py")
MODULE_NAME = "EasyTasks"

failures = []
notes = []


def check(label, fn):
    try:
        fn()
        print(f"  PASS  {label}")
    except Exception as exc:
        failures.append((label, exc))
        print(f"  FAIL  {label}: {exc.__class__.__name__}: {exc}")
        traceback.print_exc()


print(f"Blender {bpy.app.version_string}")

# --- import as a package, the way Blender installs it ----------------------
spec = importlib.util.spec_from_file_location(MODULE_NAME, SRC,
                                              submodule_search_locations=[])
et = importlib.util.module_from_spec(spec)
sys.modules[MODULE_NAME] = et
spec.loader.exec_module(et)
print(f"module __name__ = {et.__name__!r}  bl_idname target = "
      f"{et.ET_AddonPreferences.bl_idname!r}")

if et.ET_AddonPreferences.bl_idname != MODULE_NAME:
    failures.append(("preferences bl_idname matches module", None))

# --- register --------------------------------------------------------------
check("register()", et.register)

scene = bpy.context.scene

# --- build a small test scene ---------------------------------------------
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 3))
cube = bpy.context.active_object
cube.name = "Floor_Tile_01"
bpy.ops.mesh.primitive_uv_sphere_add(location=(3, 0, 2))
sphere = bpy.context.active_object
sphere.name = "Prop_Rock"
bpy.ops.object.light_add(location=(0, 4, 4))
bpy.ops.object.camera_add(location=(6, 6, 4))

bpy.ops.object.select_all(action='SELECT')
bpy.context.view_layer.objects.active = cube

# --- scene property registered? -------------------------------------------
check("scene.et_semantic exists", lambda: scene.et_semantic.category)

# --- semantic assignment ---------------------------------------------------
def semantic_assign():
    bpy.ops.object.select_all(action='DESELECT')
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    scene.et_semantic.project_source = 'SCENE'
    res = bpy.ops.et.assign_semantic(category='FLOOR')
    assert res == {'FINISHED'}, res
    expected = f"Floor_{scene.name}"
    coll = bpy.data.collections.get(expected)
    assert coll is not None, f"collection {expected!r} not created"
    assert cube.name in coll.objects, "cube not linked into category collection"
    assert len(cube.users_collection) == 1, "move should leave exactly one collection"
    assert coll.color_tag == 'COLOR_04', coll.color_tag
    notes.append(f"semantic collection created: {expected} (tag {coll.color_tag})")

check("et.assign_semantic creates + moves", semantic_assign)


def semantic_reuse():
    before = len(bpy.data.collections)
    bpy.ops.object.select_all(action='DESELECT')
    sphere.select_set(True)
    bpy.context.view_layer.objects.active = sphere
    bpy.ops.et.assign_semantic(category='FLOOR')
    assert len(bpy.data.collections) == before, "reused run created a new collection"
    coll = bpy.data.collections[f"Floor_{scene.name}"]
    assert sphere.name in coll.objects

check("et.assign_semantic reuses existing collection", semantic_reuse)


def semantic_link_mode():
    scene.et_semantic.move = False
    bpy.ops.object.select_all(action='DESELECT')
    sphere.select_set(True)
    bpy.context.view_layer.objects.active = sphere
    bpy.ops.et.assign_semantic(category='PROPS')
    assert len(sphere.users_collection) == 2, \
        f"link mode should keep the old collection, got {len(sphere.users_collection)}"
    scene.et_semantic.move = True

check("et.assign_semantic link mode keeps originals", semantic_link_mode)


def semantic_pattern():
    scene.et_semantic.pattern = "{project}__{category}"
    name = et._semantic_collection_name(scene, "Walls")
    assert name == f"{scene.name}__Walls", name
    scene.et_semantic.pattern = "{bogus}"
    fallback = et._semantic_collection_name(scene, "Walls")
    assert fallback == f"Walls_{scene.name}", fallback
    scene.et_semantic.pattern = "{category}_{project}"

check("naming pattern + bad-token fallback", semantic_pattern)

# --- other operators -------------------------------------------------------
bpy.ops.object.select_all(action='SELECT')
bpy.context.view_layer.objects.active = cube

check("et.organize_scene", lambda: bpy.ops.et.organize_scene())
check("et.arrange_scene", lambda: bpy.ops.et.arrange_scene())
check("et.collection_color_sync", lambda: bpy.ops.et.collection_color_sync())
check("et.select_by_type", lambda: bpy.ops.et.select_by_type(object_type='MESH'))


def origin_to_base():
    bpy.ops.object.select_all(action='DESELECT')
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    corners = [cube.matrix_world @ c.to_3d().copy() for c in
               [__import__('mathutils').Vector(v) for v in cube.bound_box]]
    min_z_before = min(v.z for v in corners)
    bpy.ops.et.origin_to_base()
    assert abs(cube.matrix_world.translation.z - min_z_before) < 1e-5, \
        f"origin z {cube.matrix_world.translation.z} != base {min_z_before}"

check("et.origin_to_base lands on bbox base", origin_to_base)


def clean_up_mesh():
    bpy.ops.object.select_all(action='DESELECT')
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    bpy.ops.et.clean_up_mesh('EXEC_DEFAULT')

check("et.clean_up_mesh", clean_up_mesh)


def wnormals_bevel_no_stack():
    bpy.ops.object.select_all(action='DESELECT')
    sphere.select_set(True)
    bpy.context.view_layer.objects.active = sphere
    bpy.ops.et.wnormals_bevel()
    bpy.ops.et.wnormals_bevel()
    bevels = [m for m in sphere.modifiers if m.type == 'BEVEL']
    wns = [m for m in sphere.modifiers if m.type == 'WEIGHTED_NORMAL']
    assert len(bevels) == 1, f"{len(bevels)} bevel modifiers after two runs"
    assert len(wns) == 1, f"{len(wns)} weighted-normal modifiers after two runs"

check("et.wnormals_bevel does not stack modifiers", wnormals_bevel_no_stack)


def face_stretch_roundtrip():
    mat = bpy.data.materials.new("TestMat")
    bpy.ops.object.select_all(action='DESELECT')
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    cube.data.materials.clear()
    cube.data.materials.append(mat)
    original = [m.name for m in cube.data.materials]

    bpy.ops.et.face_stretch_analyzer()
    assert cube.data.materials[0].name == et._MAT_STRETCH, \
        [m.name for m in cube.data.materials]

    # Deselect everything before toggling off: the old code restored from
    # context.selected_objects and would have lost the material here.
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.et.face_stretch_analyzer()

    restored = [m.name for m in cube.data.materials]
    assert restored == original, f"{restored} != {original}"
    assert bpy.data.materials.get(et._MAT_STRETCH) is None, "temp material left behind"
    notes.append("face stretch restored materials with an empty selection")

check("face stretch survives a selection change", face_stretch_roundtrip)


def smart_duplicate_suffixed_name():
    # The old name-stripping logic mismatched when the source was already .001
    src = bpy.data.objects.new("Crate.001", bpy.data.meshes.new("CrateMesh"))
    target = bpy.data.collections.new("CrateHome")
    scene.collection.children.link(target)
    target.objects.link(src)

    bpy.ops.object.select_all(action='DESELECT')
    src.select_set(True)
    bpy.context.view_layer.objects.active = src
    bpy.ops.et.smart_duplicate()

    copies = [o for o in bpy.context.selected_objects if o is not src]
    assert copies, "no duplicate produced"
    for copy in copies:
        names = [c.name for c in copy.users_collection]
        assert names == ["CrateHome"], f"copy landed in {names}"
        assert et.ET_OT_SmartDuplicate._TAG not in copy, "tag left on duplicate"
    assert et.ET_OT_SmartDuplicate._TAG not in src, "tag left on original"

check("smart duplicate handles .001 source names", smart_duplicate_suffixed_name)


def stacked_uv():
    bpy.ops.et.stacked_uv_detector()

check("et.stacked_uv_detector", stacked_uv)


def consolidate_materials():
    base = bpy.data.materials.new("Wood")
    dup1 = bpy.data.materials.new("Wood.001")
    dup2 = bpy.data.materials.new("Wood.002")
    sphere.data.materials.clear()
    sphere.data.materials.append(dup1)
    cube.data.materials.append(dup2)
    bpy.ops.et.consolidate_materials('EXEC_DEFAULT', remove_unused=False)
    assert bpy.data.materials.get("Wood.001") is None, "duplicate not merged"
    assert bpy.data.materials.get("Wood.002") is None, "duplicate not merged"
    assert sphere.data.materials[0] is base, "user not remapped to base"

check("et.consolidate_materials merges suffixed copies", consolidate_materials)


def consolidate_orphans():
    a = bpy.data.materials.new("Metal.001")
    bpy.data.materials.new("Metal.002")
    sphere.data.materials.append(a)
    bpy.ops.et.consolidate_materials('EXEC_DEFAULT', remove_unused=False)
    promoted = bpy.data.materials.get("Metal")
    assert promoted is not None, "orphan copies were not promoted to a base name"
    assert bpy.data.materials.get("Metal.002") is None

check("et.consolidate_materials promotes orphan copies", consolidate_orphans)


def generate_lods_rerun():
    bpy.ops.object.select_all(action='DESELECT')
    sphere.select_set(True)
    bpy.context.view_layer.objects.active = sphere
    bpy.ops.et.generate_lods('EXEC_DEFAULT')
    first = sphere.name
    bpy.ops.object.select_all(action='DESELECT')
    sphere.select_set(True)
    bpy.context.view_layer.objects.active = sphere
    bpy.ops.et.generate_lods('EXEC_DEFAULT')
    assert sphere.name == first, f"name drifted: {first} -> {sphere.name}"
    assert "_LOD0_LOD" not in sphere.name
    bad = [o.name for o in bpy.data.objects if "_LOD0_LOD" in o.name]
    assert not bad, bad
    dupes = [o.name for o in bpy.data.objects if o.name.startswith(first[:-5] + "_LOD1.")]
    assert not dupes, f"re-run left duplicate LODs: {dupes}"

check("et.generate_lods is idempotent on re-run", generate_lods_rerun)


def scene_stats_cached():
    op = et.ET_OT_SceneStats
    assert isinstance(op._stats, dict)

check("stats operators expose a cache", scene_stats_cached)


def auto_route_handler():
    assert et._auto_route_new_objects in bpy.app.handlers.depsgraph_update_post
    # cheap-path guard: no crash when the handler runs against the live scene
    et._auto_route_new_objects(scene, None)
    et._auto_route_new_objects(scene, None)

check("auto-route handler is installed and re-entrant", auto_route_handler)

# --- unregister ------------------------------------------------------------
check("unregister()", et.unregister)


def reregister_cycle():
    et.register()
    et.unregister()

check("register/unregister cycle repeats cleanly", reregister_cycle)

print("\n" + "=" * 60)
for note in notes:
    print("note:", note)
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S)")
    for label, exc in failures:
        print(f"  - {label}: {exc}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
