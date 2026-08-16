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


def structure_tree_is_depth_first():
    """
    The Project Structure dialog used to draw every depth-2 collection after
    BLOCKING, so LIGHTS/CAMERAS looked like children of BLOCKING instead of
    STUDIO. Walk the same helper the dialog draws from and assert the order
    is a real depth-first traversal.
    """
    children = et._structure_children()
    order = []

    def walk(parent, depth):
        for index, name in children.get(parent, []):
            order.append((name, depth, parent))
            walk(name, depth + 1)

    walk(None, 0)
    names = [n for n, _d, _p in order]

    assert names == ['PRODUCTION',
                     'STUDIO', 'LIGHTS', 'CAMERAS',
                     'MODULES', 'FLOOR', 'WALLS', 'CEILING', 'PROPS', 'DECALS',
                     'BLOCKING'], names

    depth_of = {n: d for n, d, _p in order}
    parent_of = {n: p for n, _d, p in order}
    assert depth_of['LIGHTS'] == 2 and parent_of['LIGHTS'] == 'STUDIO'
    assert depth_of['FLOOR'] == 2 and parent_of['FLOOR'] == 'MODULES'
    assert depth_of['BLOCKING'] == 1 and parent_of['BLOCKING'] == 'PRODUCTION'

    # every collection must appear immediately after its parent's subtree start
    for name, _depth, parent in order:
        if parent is not None:
            assert names.index(parent) < names.index(name), \
                f"{name} drawn before its parent {parent}"

check("project structure draws a real tree", structure_tree_is_depth_first)


def organize_scene_builds_real_hierarchy():
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    # Removing a collection orphans its objects out of the view layer; put them
    # back so the checks after this one still have something selectable.
    for obj in bpy.data.objects:
        if not obj.users_collection:
            scene.collection.objects.link(obj)
    bpy.ops.et.organize_scene('EXEC_DEFAULT')

    def parent_of(name):
        for coll in bpy.data.collections:
            if name in coll.children:
                return coll.name
        return 'SCENE_ROOT' if name in scene.collection.children else None

    for name, expected, _color in et.COLLECTION_STRUCTURE:
        got = parent_of(name)
        want = expected if expected is not None else 'SCENE_ROOT'
        assert got == want, f"{name} parented to {got}, expected {want}"
    notes.append("organize_scene hierarchy matches COLLECTION_STRUCTURE exactly")

check("et.organize_scene nests collections correctly",
      organize_scene_builds_real_hierarchy)


def organize_scene_is_idempotent():
    before = len(bpy.data.collections)
    bpy.ops.et.organize_scene('EXEC_DEFAULT', use_colls=(True,) * 11)
    assert len(bpy.data.collections) == before, \
        "re-running created duplicate collections"

check("et.organize_scene re-run creates nothing new", organize_scene_is_idempotent)


def add_to_collection_move_and_link():
    target = bpy.data.collections.new("CtxTarget")
    scene.collection.children.link(target)

    bpy.ops.object.select_all(action='DESELECT')
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube

    scene.et_semantic.move = True
    bpy.ops.et.add_to_collection('EXEC_DEFAULT', collection_name="CtxTarget")
    assert [c.name for c in cube.users_collection] == ["CtxTarget"], \
        [c.name for c in cube.users_collection]

    other = bpy.data.collections.new("CtxOther")
    scene.collection.children.link(other)
    scene.et_semantic.move = False
    bpy.ops.et.add_to_collection('EXEC_DEFAULT', collection_name="CtxOther")
    names = sorted(c.name for c in cube.users_collection)
    assert names == ["CtxOther", "CtxTarget"], names
    scene.et_semantic.move = True

check("et.add_to_collection honours move and link", add_to_collection_move_and_link)


def add_to_collection_rejects_missing():
    # An operator that reports {'ERROR'} surfaces as a RuntimeError in Python,
    # so a clean rejection is the exception — not a {'CANCELLED'} return.
    try:
        bpy.ops.et.add_to_collection('EXEC_DEFAULT',
                                     collection_name="DoesNotExist")
    except RuntimeError as exc:
        assert "not found" in str(exc), exc
        return
    raise AssertionError("a missing collection name was accepted")

check("et.add_to_collection rejects a missing name", add_to_collection_rejects_missing)


def new_collection_for_selection():
    bpy.ops.object.select_all(action='DESELECT')
    sphere.select_set(True)
    bpy.context.view_layer.objects.active = sphere
    bpy.ops.et.new_collection_for_selection(
        'EXEC_DEFAULT', name="FreshGroup", parent="CtxTarget", color='COLOR_05')

    coll = bpy.data.collections.get("FreshGroup")
    assert coll is not None, "collection not created"
    assert coll.color_tag == 'COLOR_05', coll.color_tag
    assert sphere.name in coll.objects
    assert "FreshGroup" in bpy.data.collections["CtxTarget"].children, \
        "not nested under the requested parent"

check("et.new_collection_for_selection creates + parents + tags",
      new_collection_for_selection)


def suggestion_matches_routing():
    props = bpy.data.collections.get("PROPS") or bpy.data.collections.new("PROPS")
    if not et._collection_is_linked(props, scene):
        scene.collection.children.link(props)

    probe = bpy.data.objects.new("Props_Barrel_01", bpy.data.meshes.new("BarrelM"))
    scene.collection.objects.link(probe)
    bpy.ops.object.select_all(action='DESELECT')
    probe.select_set(True)
    bpy.context.view_layer.objects.active = probe

    suggested = et._suggested_collection(bpy.context)
    assert suggested is not None and suggested.name == "PROPS", \
        suggested.name if suggested else None

    # once it is already there, there is nothing left to suggest
    et._assign_to_collection([probe], props, True)
    assert et._suggested_collection(bpy.context) is None, \
        "suggested a collection the object is already in"
    notes.append("context-menu suggestion reuses the Arrange Scene matcher")

check("context menu suggests the routed collection", suggestion_matches_routing)


def context_menu_hooks_installed():
    assert et.draw_object_context_menu in \
        bpy.types.VIEW3D_MT_object_context_menu._dyn_ui_initialize()
    assert et.draw_object_context_menu in \
        bpy.types.OUTLINER_MT_object._dyn_ui_initialize()

check("right-click menu hooks are installed", context_menu_hooks_installed)


def project_root_reuses_existing_hierarchy():
    """
    The reported bug: with PRODUCTION > MODULES > FLOOR already built by
    Project Structure, clicking Floor made a *new* Floor_<project> at the scene
    root instead of using the FLOOR that was already there.
    """
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    for obj in bpy.data.objects:
        if not obj.users_collection:
            scene.collection.objects.link(obj)
    bpy.ops.et.organize_scene('EXEC_DEFAULT')

    st = scene.et_semantic
    st.project_root = "PRODUCTION"
    st.move = True

    before = len(bpy.data.collections)
    bpy.ops.object.select_all(action='DESELECT')
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    bpy.ops.et.assign_semantic(category='FLOOR')

    assert len(bpy.data.collections) == before, \
        "a new collection was created instead of reusing FLOOR"
    floor = bpy.data.collections["FLOOR"]
    assert cube.name in floor.objects, "object did not land in the existing FLOOR"
    assert bpy.data.collections.get(f"Floor_{scene.name}") is None, \
        "the pattern-named collection was created anyway"

    # and FLOOR is still nested where Project Structure put it
    assert "FLOOR" in bpy.data.collections["MODULES"].children, \
        "FLOOR was re-parented out of MODULES"
    notes.append("category resolves into PRODUCTION > MODULES > FLOOR, no new collection")

check("project root reuses the existing hierarchy",
      project_root_reuses_existing_hierarchy)


def quick_slots_detect_hierarchy():
    scene.et_semantic.project_root = "PRODUCTION"
    bpy.ops.et.scan_project()

    found = [s.name for s in scene.et_quick_slots]
    for expected in ('STUDIO', 'LIGHTS', 'CAMERAS', 'MODULES',
                     'FLOOR', 'WALLS', 'CEILING', 'PROPS', 'DECALS', 'BLOCKING'):
        assert expected in found, f"{expected} not detected — got {found}"
    assert 'PRODUCTION' not in found, "the root listed itself as a slot"
    assert scene.et_semantic.source == 'PROJECT', \
        "picking a root did not switch Quick Assign to project mode"

    depths = {s.name: s.depth for s in scene.et_quick_slots}
    assert depths['STUDIO'] == 0 and depths['LIGHTS'] == 1, depths
    notes.append(f"detected {len(found)} collections under PRODUCTION")

check("quick slots detect the project hierarchy", quick_slots_detect_hierarchy)


def quick_slot_selection_round_trips():
    for slot in scene.et_quick_slots:
        slot.use = (slot.name == 'WALLS')
    # rescanning must not wipe the user's picks
    bpy.ops.et.scan_project()
    used = [s.name for s in scene.et_quick_slots if s.use]
    assert used == ['WALLS'], used

    bpy.ops.et.set_all_quick_slots(state=True)
    assert all(s.use for s in scene.et_quick_slots)

check("quick slot picks survive a rescan", quick_slot_selection_round_trips)


def quick_assign_targets_existing_collection():
    walls = bpy.data.collections["WALLS"]
    bpy.ops.object.select_all(action='DESELECT')
    sphere.select_set(True)
    bpy.context.view_layer.objects.active = sphere

    before = len(bpy.data.collections)
    bpy.ops.et.add_to_collection('EXEC_DEFAULT', collection_name="WALLS")
    assert len(bpy.data.collections) == before, "quick assign created a collection"
    assert sphere.name in walls.objects
    assert [c.name for c in sphere.users_collection] == ["WALLS"], \
        [c.name for c in sphere.users_collection]

check("quick assign puts objects in the detected collection",
      quick_assign_targets_existing_collection)


def unset_project_root_falls_back():
    scene.et_semantic.project_root = ""
    assert scene.et_semantic.source == 'PRESET', \
        "clearing the root should fall back to presets"
    assert len(scene.et_quick_slots) == 0, "slots not cleared"

check("clearing the project root falls back to presets", unset_project_root_falls_back)


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
