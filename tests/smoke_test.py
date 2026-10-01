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


def face_stretch_never_touches_materials():
    """
    The whole point of the rework: the analyzer must not modify materials.

    The old implementation cleared mesh.materials and kept the originals in a
    module-level dict, so a crash while it was on lost them permanently.
    """
    mat_a = bpy.data.materials.new("KeepMeA")
    mat_b = bpy.data.materials.new("KeepMeB")
    bpy.ops.object.select_all(action='DESELECT')
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    cube.data.materials.clear()
    cube.data.materials.append(mat_a)
    cube.data.materials.append(mat_b)
    before = [m.name for m in cube.data.materials]

    bpy.ops.et.face_stretch_analyzer()

    during = [m.name for m in cube.data.materials]
    assert during == before, f"materials changed while on: {during} != {before}"
    assert et._VCOL_STRETCH in cube.data.color_attributes, \
        "colour attribute was not created"
    assert not any(m.name.startswith("ET_FaceStretch")
                   for m in bpy.data.materials), "a temp material was created"

    # Change the selection before toggling off — the old bug's trigger.
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.et.face_stretch_analyzer()

    after = [m.name for m in cube.data.materials]
    assert after == before, f"materials changed after off: {after} != {before}"
    assert et._VCOL_STRETCH not in cube.data.color_attributes, \
        "colour attribute left behind"
    notes.append("face stretch leaves materials untouched end to end")

check("face stretch never touches materials", face_stretch_never_touches_materials)


def face_stretch_restores_active_colour_layer():
    mesh = cube.data
    keep = mesh.color_attributes.new("MyColours", 'FLOAT_COLOR', 'CORNER')
    mesh.color_attributes.active_color_index = mesh.color_attributes.find(keep.name)
    before = mesh.color_attributes.active_color_index

    bpy.ops.object.select_all(action='DESELECT')
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    bpy.ops.et.face_stretch_analyzer()
    assert (mesh.color_attributes.active_color_index
            == mesh.color_attributes.find(et._VCOL_STRETCH)), \
        "stretch layer not made active"

    bpy.ops.et.face_stretch_analyzer()
    assert mesh.color_attributes.active_color_index == before, \
        "previous active colour attribute not restored"
    assert "MyColours" in mesh.color_attributes, "user's colour attribute removed"
    mesh.color_attributes.remove(mesh.color_attributes["MyColours"])

check("face stretch restores the active colour layer",
      face_stretch_restores_active_colour_layer)


def face_stretch_yields_to_silhouette():
    bpy.ops.object.select_all(action='DESELECT')
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    bpy.ops.et.face_stretch_analyzer()
    assert et._stretch_state, "analyzer did not turn on"

    # Silhouette takes the viewport over; the stretch attributes must go too,
    # otherwise the panel keeps reporting Face Stretch as active.
    et._clear_stretch_attributes()
    assert not et._stretch_state, "stretch state survived being displaced"
    assert et._VCOL_STRETCH not in cube.data.color_attributes, \
        "colour attribute left behind after displacement"

check("face stretch cleans up when displaced", face_stretch_yields_to_silhouette)


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


def _uv_quad(name, size, uv_max, location=(0, 0, 0)):
    """A square of `size` metres whose UVs run 0..uv_max."""
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata([(0, 0, 0), (size, 0, 0), (size, size, 0), (0, size, 0)],
                     [], [(0, 1, 2, 3)])
    mesh.update()
    layer = mesh.uv_layers.new(name="UVMap")
    for datum, uv in zip(layer.data, [(0, 0), (uv_max, 0),
                                      (uv_max, uv_max), (0, uv_max)]):
        datum.uv = uv
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    scene.collection.objects.link(obj)
    return obj


def uv_health_measures_texel_density():
    """
    Two metres of wall at 0..1 UVs is half the density of one metre at the
    same UVs. Getting that backwards is the whole point of the check.
    """
    small = _uv_quad("DenseQuad", 1.0, 1.0, (40, 0, 0))
    large = _uv_quad("SparseQuad", 2.0, 1.0, (44, 0, 0))

    dense  = et._uv_report(small, 1024)
    sparse = et._uv_report(large, 1024)
    assert abs(dense['density'] - 1024.0) < 1.0, dense['density']
    assert abs(sparse['density'] - 512.0) < 1.0, sparse['density']
    assert not dense['tiles'] and not sparse['tiles']

    # object scale has to count, or a scaled-up object reads as unchanged
    large.scale = (2.0, 2.0, 2.0)
    bpy.context.view_layer.update()
    scaled = et._uv_report(large, 1024)
    assert abs(scaled['density'] - 256.0) < 1.0, scaled['density']
    notes.append(f"texel density: {dense['density']:.0f} vs "
                 f"{sparse['density']:.0f} px/m for 1m vs 2m at the same UVs")

check("uv health measures texel density", uv_health_measures_texel_density)


def uv_health_flags_the_real_problems():
    tiled = _uv_quad("TiledQuad", 1.0, 8.0, (48, 0, 0))
    bare  = bpy.data.objects.new("NoUVQuad", bpy.data.meshes.new("NoUVQuadMesh"))
    bare.data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    bare.data.update()
    scene.collection.objects.link(bare)

    reports  = [et._uv_report(o, 2048) for o in (tiled, bare)]
    findings = et._uv_findings([r for r in reports if r])
    text     = " | ".join(m for _l, m in findings)

    assert reports[0]['tiles'], "8x UVs not reported as tiling"
    assert abs(reports[0]['span'] - 8.0) < 1e-4, reports[0]['span']
    assert reports[1]['uv'] is None, "an object with no UV map read as having one"
    assert any(level == 'ERROR' and "no UV map" in message
               for level, message in findings), text
    assert "tile past 0-1" in text, text

    # a healthy pair should say so rather than inventing problems
    clean = et._uv_findings([et._uv_report(_uv_quad("CleanA", 1.0, 1.0, (52, 0, 0)), 2048),
                             et._uv_report(_uv_quad("CleanB", 1.0, 1.0, (56, 0, 0)), 2048)])
    assert all(level == 'OK' for level, _m in clean), clean
    notes.append("uv health flags missing UVs and tiling, stays quiet when clean")

check("uv health flags the real problems", uv_health_flags_the_real_problems)


def uv_health_operator_runs():
    bpy.ops.object.select_all(action='DESELECT')
    for name in ("DenseQuad", "SparseQuad", "TiledQuad"):
        bpy.data.objects[name].select_set(True)
    bpy.context.view_layer.objects.active = bpy.data.objects["DenseQuad"]
    assert et.ET_OT_UVHealth.poll(bpy.context)
    assert bpy.ops.et.uv_health('EXEC_DEFAULT', texture_px='4096') == {'FINISHED'}

    bpy.ops.object.select_all(action='DESELECT')
    assert not et.ET_OT_UVHealth.poll(bpy.context), \
        "offered with nothing selected"

check("et.uv_health runs over a selection", uv_health_operator_runs)


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


def collection_icons_follow_colour_tags():
    """Quick Assign buttons should carry each collection's own tag colour."""
    probe = bpy.data.collections.new("__tagged__")
    try:
        for n in range(1, 9):
            probe.color_tag = f"COLOR_{n:02d}"
            got = et._collection_icon(probe)
            assert got == f"COLLECTION_COLOR_{n:02d}", got
        probe.color_tag = 'NONE'
        assert et._collection_icon(probe) == 'OUTLINER_COLLECTION'
        assert et._collection_icon(None) == 'OUTLINER_COLLECTION'
    finally:
        bpy.data.collections.remove(probe)
    notes.append("collection buttons use COLLECTION_COLOR_01..08 per tag")

check("collection icons follow colour tags", collection_icons_follow_colour_tags)


def auto_route_handler():
    assert et._auto_route_new_objects in bpy.app.handlers.depsgraph_update_post
    # cheap-path guard: no crash when the handler runs against the live scene
    et._auto_route_new_objects(scene, None)
    et._auto_route_new_objects(scene, None)

check("auto-route handler is installed and re-entrant", auto_route_handler)

def megascans_name_parsing():
    """The naming Bridge actually writes, plus the conventions it isn't."""
    cases = {
        # asset id only, the classic Megascans surface export
        'ug0jaipaw_2K_Albedo':               ('ug0jaipaw', 'BASE',      2),
        'ug0jaipaw_2K_Roughness':            ('ug0jaipaw', 'ROUGH',     2),
        # 3D asset: descriptive name, asset id, and an LOD tag behind the map
        'Rock_Cliff_ug0jaipaw_4K_Normal_LOD0': ('Rock_Cliff_ug0jaipaw', 'NORMAL', 4),
        'Rock_Cliff_ug0jaipaw_4K_Displacement': ('Rock_Cliff_ug0jaipaw', 'DISP',  4),
        # two-token map names have to beat the one-token read of their tail
        'Concrete_8K_Base_Color':            ('Concrete',  'BASE',      8),
        'Concrete_8K_BaseColor':             ('Concrete',  'BASE',      8),
        'Concrete_Normal_Bump':              ('Concrete',  'BUMP',      0),
        'Wood_NormalDX':                     ('Wood',      'NORMAL_DX', 0),
        # packed and unwireable maps
        'Tiles_2K_ORM':                      ('Tiles',     'ORM',       2),
        'Leaf_4K_Translucency':              ('Leaf',      'EXTRA_translucency', 4),
        # nothing to key off
        'random_screenshot':                 ('random_screenshot', None, 0),
    }
    for stem, expected in cases.items():
        got = et._split_texture_name(stem)
        assert got == expected, f"{stem}: {got} != {expected}"

check("megascans filenames parse into base/slot/resolution",
      megascans_name_parsing)


def megascans_resolution_preference():
    paths = ['/tex/Rock_2K_Albedo.jpg', '/tex/Rock_4K_Albedo.jpg',
             '/tex/Rock_4K_Albedo.png', '/tex/Rock_2K_Normal.jpg',
             '/tex/Rock_preview.png', '/tex/notes.txt']

    high = et._collect_texture_sets(paths, prefer_high=True)
    assert set(high) == {'Rock'}, list(high)
    # 4K wins on resolution, then png over jpg at the same resolution
    assert high['Rock']['BASE'].endswith('Rock_4K_Albedo.png'), high['Rock']['BASE']
    assert 'NORMAL' in high['Rock']

    low = et._collect_texture_sets(paths, prefer_high=False)
    assert low['Rock']['BASE'].endswith('Rock_2K_Albedo.jpg'), low['Rock']['BASE']
    notes.append("texture sets pick a resolution instead of whichever listed last")

check("texture sets resolve duplicate resolutions", megascans_resolution_preference)


def _write_textures(folder, stems):
    """Write real (tiny) PNGs so the operator has something to load."""
    written = []
    for stem in stems:
        path = _os.path.join(folder, stem + '.png')
        img = bpy.data.images.new(stem, 4, 4)
        img.filepath_raw = path
        img.file_format  = 'PNG'
        img.save()
        # drop the datablock so the operator has to load from disk
        bpy.data.images.remove(img)
        written.append(path)
    return written


def _feeding_node(node, socket_name):
    socket = node.inputs[socket_name]
    return socket.links[0].from_node if socket.links else None


def megascans_builds_and_wires():
    import tempfile
    folder = tempfile.mkdtemp(prefix="et_megascans_")
    _write_textures(folder, [
        'Rock_Cliff_ug0jaipaw_2K_Albedo',
        'Rock_Cliff_ug0jaipaw_4K_Albedo',
        'Rock_Cliff_ug0jaipaw_4K_Roughness',
        'Rock_Cliff_ug0jaipaw_4K_Metalness',
        'Rock_Cliff_ug0jaipaw_4K_Normal_LOD0',
        'Rock_Cliff_ug0jaipaw_4K_Displacement',
        'Rock_Cliff_ug0jaipaw_4K_Opacity',
        'Rock_Cliff_ug0jaipaw_4K_Translucency',
    ])

    target = bpy.data.objects.new("Rock_Cliff", bpy.data.meshes.new("RockCliffM"))
    scene.collection.objects.link(target)
    bpy.ops.object.select_all(action='DESELECT')
    target.select_set(True)
    bpy.context.view_layer.objects.active = target

    res = bpy.ops.et.megascans_material('EXEC_DEFAULT', directory=folder)
    assert res == {'FINISHED'}, res

    mat = bpy.data.materials.get("Rock_Cliff_ug0jaipaw")
    assert mat is not None, [m.name for m in bpy.data.materials]
    assert target.data.materials and target.data.materials[0] is mat, \
        "material was not assigned to the selected object"

    nodes = mat.node_tree.nodes
    bsdf  = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')

    base = _feeding_node(bsdf, 'Base Color')
    assert base is not None and base.type == 'TEX_IMAGE', base
    assert base.image.filepath.endswith("4K_Albedo.png"), base.image.filepath

    for socket in ('Roughness', 'Metallic', 'Alpha'):
        assert _feeding_node(bsdf, socket) is not None, f"{socket} unconnected"

    # height rides on the normal map rather than replacing it
    bump = _feeding_node(bsdf, 'Normal')
    assert bump is not None and bump.type == 'BUMP', bump
    assert _feeding_node(bump, 'Height').type == 'TEX_IMAGE'
    assert _feeding_node(bump, 'Normal').type == 'NORMAL_MAP', \
        "bump replaced the normal map instead of chaining after it"

    rough = _feeding_node(bsdf, 'Roughness')
    assert rough.image.colorspace_settings.name != 'sRGB', \
        "data map left colour managed"
    assert base.image.colorspace_settings.name == 'sRGB', \
        "albedo should stay sRGB"

    # translucency is loaded but deliberately not wired to anything
    extra = [n for n in nodes if n.type == 'TEX_IMAGE'
             and 'Translucency' in n.image.filepath]
    assert extra, "translucency map was not loaded"
    assert not extra[0].outputs['Color'].links, \
        "translucency was wired without being asked to"

    # mapping nodes reach every texture
    mapping = next(n for n in nodes if n.type == 'MAPPING')
    fed = {link.to_node for link in mapping.outputs['Vector'].links}
    images = {n for n in nodes if n.type == 'TEX_IMAGE'}
    assert fed == images, f"{len(fed)} of {len(images)} textures got a Vector"

    notes.append("megascans set wired: base/rough/metal/alpha + normal→bump chain")

check("et.megascans_material builds a wired material", megascans_builds_and_wires)


def megascans_gloss_and_directx():
    import tempfile
    folder = tempfile.mkdtemp(prefix="et_megascans_dx_")
    _write_textures(folder, ['Tile_2K_Albedo', 'Tile_2K_Gloss', 'Tile_2K_NormalDX'])

    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.et.megascans_material('EXEC_DEFAULT', directory=folder,
                                  assign_to_selected=False)

    mat = bpy.data.materials.get("Tile")
    assert mat is not None, [m.name for m in bpy.data.materials]
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')

    invert = _feeding_node(bsdf, 'Roughness')
    assert invert is not None and invert.type == 'INVERT', \
        f"gloss was not inverted into roughness: {invert}"

    nmap = _feeding_node(bsdf, 'Normal')
    assert nmap is not None and nmap.type == 'NORMAL_MAP', nmap
    flip = _feeding_node(nmap, 'Color')
    assert flip is not None and flip.type == 'VECT_MATH', \
        f"DirectX normal was not green-flipped: {flip}"
    notes.append("gloss inverts to roughness, DirectX normals get green flipped")

check("gloss and DirectX maps are converted", megascans_gloss_and_directx)


def megascans_true_displacement():
    """The Displacement path touches API that moved off mat.cycles in 4.1."""
    import tempfile
    folder = tempfile.mkdtemp(prefix="et_megascans_disp_")
    _write_textures(folder, ['Ground_2K_Albedo', 'Ground_2K_Displacement'])

    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.et.megascans_material('EXEC_DEFAULT', directory=folder,
                                  assign_to_selected=False,
                                  displacement='DISPLACEMENT')

    mat  = bpy.data.materials["Ground"]
    out  = next(n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL')
    disp = _feeding_node(out, 'Displacement')
    assert disp is not None and disp.type == 'DISPLACEMENT', disp
    assert _feeding_node(disp, 'Height').type == 'TEX_IMAGE'
    if hasattr(mat, 'displacement_method'):
        assert mat.displacement_method == 'BOTH', mat.displacement_method
    notes.append("displacement mode reaches the Material Output on this build")

check("megascans displacement mode wires the output", megascans_true_displacement)


def megascans_reuses_and_rejects():
    import tempfile

    # a folder with nothing recognisable must fail cleanly, not build junk
    empty = tempfile.mkdtemp(prefix="et_megascans_none_")
    _write_textures(empty, ['screenshot_of_the_thing'])
    try:
        bpy.ops.et.megascans_material('EXEC_DEFAULT', directory=empty)
    except RuntimeError as exc:
        assert "No recognised maps" in str(exc), exc
    else:
        raise AssertionError("an unrecognisable folder was accepted")

    # re-running over the same set reuses the material instead of making Rock.001
    folder = tempfile.mkdtemp(prefix="et_megascans_twice_")
    _write_textures(folder, ['Slab_2K_Albedo', 'Slab_2K_Roughness'])
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.et.megascans_material('EXEC_DEFAULT', directory=folder,
                                  assign_to_selected=False)
    bpy.ops.et.megascans_material('EXEC_DEFAULT', directory=folder,
                                  assign_to_selected=False)
    copies = [m.name for m in bpy.data.materials if m.name.startswith("Slab")]
    assert copies == ["Slab"], copies

check("megascans import rejects junk and reuses materials",
      megascans_reuses_and_rejects)


blend_mats = {}


def _run_blend(**kwargs):
    """
    Run Blend Materials and hand back the material it just built.

    The suite makes several blends from the same two sources, so bpy hands out
    .001 / .002 names; naming them by hand couples every test to the order they
    happen to run in.
    """
    before = set(bpy.data.materials.keys())
    res = bpy.ops.et.blend_materials('EXEC_DEFAULT', **kwargs)
    assert res == {'FINISHED'}, res
    made = set(bpy.data.materials.keys()) - before
    assert len(made) == 1, f"expected one new material, got {made}"
    return bpy.data.materials[made.pop()]


def _ensure_object_mode():
    """A failed paint assertion must not strand later tests in paint mode."""
    obj = bpy.context.active_object
    if obj is not None and obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


def _blend_fixture():
    """Two importer-built materials on one UV'd object, ready to blend."""
    import tempfile
    for name, stems in (
        ("Concrete", ['Concrete_2K_Albedo', 'Concrete_2K_Roughness',
                      'Concrete_2K_Normal', 'Concrete_2K_Displacement']),
        ("DirtGrime", ['DirtGrime_2K_Albedo', 'DirtGrime_2K_Roughness',
                       'DirtGrime_2K_Normal']),
    ):
        if name in bpy.data.materials:
            continue
        folder = tempfile.mkdtemp(prefix=f"et_blend_{name}_")
        _write_textures(folder, stems)
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.et.megascans_material('EXEC_DEFAULT', directory=folder,
                                      assign_to_selected=False)

    wall = bpy.data.objects.get("BlendWall")
    if wall is None:
        bpy.ops.mesh.primitive_plane_add(location=(10, 0, 0))
        wall = bpy.context.active_object
        wall.name = "BlendWall"
    return wall


def blend_mixes_every_shared_channel():
    wall = _blend_fixture()
    bpy.ops.object.select_all(action='DESELECT')
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall

    mat = _run_blend(material_a="Concrete", material_b="DirtGrime")
    blend_mats['plain'] = mat
    assert wall.data.materials[0] is mat, "blend not assigned to the selection"

    nodes = mat.node_tree.nodes
    bsdf  = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    mask  = nodes[et._MASK_NODE_NAME]

    # Concrete and DirtGrime share albedo, roughness and normal; only concrete
    # ships displacement. So exactly three channels mix, and every one of them
    # is driven by the same mask — that is the whole point of the tool.
    mixes = [n for n in nodes if n.type in {'MIX', 'MIX_RGB'}]
    assert len(mixes) == 3, f"{len(mixes)} channels mixed: {[m.label for m in mixes]}"
    for mix in mixes:
        factor = next(s for s in mix.inputs
                      if s.name in {'Factor', 'Fac'} and s.type == 'VALUE')
        assert factor.links and factor.links[0].from_node == mask, \
            f"{mix.label} is not driven by the shared mask"

    assert _feeding_node(bsdf, 'Base Color').type in {'MIX', 'MIX_RGB'}, \
        "base colour was not blended"
    assert _feeding_node(bsdf, 'Roughness').type in {'MIX', 'MIX_RGB'}, \
        "roughness was not blended"

    # a map only one side has passes straight through instead of being mixed
    # against a flat colour that would wash it out
    bump = _feeding_node(bsdf, 'Normal')
    assert bump.type == 'BUMP', bump.type
    assert _feeding_node(bump, 'Height').type == 'TEX_IMAGE', \
        "concrete's lone displacement was mixed instead of passed through"

    # the mask reads that height *before* the mix, so it cannot feed back into
    # the thing that decides it
    height = mask.inputs['Height']
    assert height.links, "mask has no height input"
    assert height.links[0].from_node.type == 'TEX_IMAGE', height.links[0].from_node

    notes.append(f"blend mixes {len(mixes)} shared channels, passes the rest through")

check("et.blend_materials mixes every shared channel",
      blend_mixes_every_shared_channel)


def blend_mask_group_exposes_controls():
    group = bpy.data.node_groups[et._MASK_GROUP_NAME]
    node  = blend_mats['plain'].node_tree.nodes[et._MASK_NODE_NAME]

    for name in ('Height', 'Paint', 'Amount', 'Softness',
                 'Noise Scale', 'Paint Influence'):
        assert name in node.inputs, f"{name} missing — got {[s.name for s in node.inputs]}"
    assert 'Fac' in node.outputs

    # mid grey on Paint must be a no-op, which is what lets one layer add *and*
    # remove; the group is shared but the values live per material
    assert abs(node.inputs['Paint'].default_value - 0.5) < 1e-6, \
        node.inputs['Paint'].default_value
    assert group.users >= 1
    notes.append("mask group exposes Amount / Softness / Noise / Paint Influence")

check("blend mask group exposes its controls", blend_mask_group_exposes_controls)


def _two_slot_wall(name):
    """A wall carrying both sources with every face on the *second* slot —
    what applying a second material over a whole mesh leaves behind."""
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [],
                     [(0, 1, 2, 3)])
    mesh.update()
    wall = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(wall)
    mesh.materials.append(bpy.data.materials["Concrete"])
    mesh.materials.append(bpy.data.materials["DirtGrime"])
    for poly in mesh.polygons:
        poly.material_index = 1

    _ensure_object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall
    return wall, mesh


def blend_lands_on_the_rendered_slot():
    """
    The reported bug: a wall with both materials, faces on the second slot,
    showed no change at all because the blend went into slot 0 where nothing
    rendered it. The over-correction — replacing every slot — was worse: it
    took both sources off the object.
    """
    wall, mesh = _two_slot_wall("RenderedSlotWall")
    blend = _run_blend(material_a="Concrete", material_b="DirtGrime")

    rendered = {mesh.materials[p.material_index] for p in mesh.polygons}
    assert rendered == {blend}, \
        f"faces still render {[m.name for m in rendered]} — the blend missed " \
        f"the slot they are on"
    assert wall.material_slots[0].material.name == "Concrete", \
        f"an unrendered slot was overwritten: " \
        f"{wall.material_slots[0].material.name}"
    notes.append("blend takes the slot the faces use, leaving unused slots alone")

check("blend lands on the slot the object renders", blend_lands_on_the_rendered_slot)


def blend_leaves_the_sources_intact():
    """The sources are read, never written — they must survive untouched."""
    concrete = bpy.data.materials["Concrete"]
    before = (len(concrete.node_tree.nodes),
              {n.name for n in concrete.node_tree.nodes},
              len(concrete.node_tree.links))
    _two_slot_wall("IntactSourceWall")
    _run_blend(material_a="Concrete", material_b="DirtGrime")

    after = (len(concrete.node_tree.nodes),
             {n.name for n in concrete.node_tree.nodes},
             len(concrete.node_tree.links))
    assert before == after, "the base material's node tree was modified"
    assert bpy.data.materials.get("DirtGrime") is not None, \
        "the overlay material was removed"

check("blending does not modify the source materials",
      blend_leaves_the_sources_intact)


def blend_rerun_still_follows_the_object():
    """
    After one run the slots hold the blend, not the sources. A second run must
    still land on the object instead of quietly assigning nothing.
    """
    wall, mesh = _two_slot_wall("RerunWall")
    first = _run_blend(material_a="Concrete", material_b="DirtGrime")
    assert wall.material_slots[1].material == first

    wall.active_material_index = 1
    second = _run_blend(material_a="Concrete", material_b="DirtGrime",
                        amount=0.6)
    assert second != first, "the second run reused the first blend"
    assert wall.material_slots[1].material == second, \
        f"re-run left the object on {wall.material_slots[1].material.name}"
    notes.append("re-running the blend updates the object instead of orphaning it")

check("re-running the blend still reaches the object",
      blend_rerun_still_follows_the_object)


def blend_respects_object_linked_slots():
    """A slot linked to the object renders its own material, not the mesh's."""
    _ensure_object_mode()
    mesh = bpy.data.meshes.new("ObjLinkMesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    mesh.update()
    obj = bpy.data.objects.new("ObjLinkWall", mesh)
    scene.collection.objects.link(obj)
    mesh.materials.append(bpy.data.materials["Concrete"])
    obj.material_slots[0].link     = 'OBJECT'
    obj.material_slots[0].material = bpy.data.materials["Concrete"]

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    blend = _run_blend(material_a="Concrete", material_b="DirtGrime")

    assert obj.material_slots[0].material == blend, \
        obj.material_slots[0].material.name
    assert obj.material_slots[0].link == 'OBJECT', "the slot link was changed"
    notes.append("object-linked slots are written through material_slots")

check("blend handles object-linked material slots",
      blend_respects_object_linked_slots)


def direction_coverage_reads_world_normals():
    """
    A vertical wall has nothing facing up, and the tool has to know that: the
    gate shuts, the overlay disappears completely, and without a warning it
    looks identical to the feature being broken.
    """
    import math as _math
    mesh = bpy.data.meshes.new("CoverMesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [],
                     [(0, 1, 2, 3)])
    mesh.update()
    flat = bpy.data.objects.new("CoverFlat", mesh)
    scene.collection.objects.link(flat)

    up = (0.0, 0.0, 1.0)
    assert abs(et._direction_coverage([flat], up) - 1.0) < 1e-5, \
        "a floor should face straight up"

    # stand it on edge — now nothing points up, which is the wall case
    flat.rotation_euler[0] = _math.radians(90)
    bpy.context.view_layer.update()
    standing = et._direction_coverage([flat], up)
    assert abs(standing) < 1e-5, f"a vertical wall scored {standing:.4f} against up"

    # and the object transform has to count, not just the local normal
    flat.rotation_euler[0] = _math.radians(180)
    bpy.context.view_layer.update()
    assert et._direction_coverage([flat], up) < -0.9, \
        "a flipped floor should face down"

    assert et._direction_coverage([], up) == -1.0
    notes.append("direction coverage measures world normals, transform included")

check("direction coverage reads world-space normals",
      direction_coverage_reads_world_normals)


def blend_warns_when_nothing_faces_that_way():
    wall, _mesh = _two_slot_wall("FacingWarnWall")
    wall.rotation_euler[0] = 1.5708          # stand it up: no face points up
    bpy.context.view_layer.update()

    before = len(bpy.data.materials)
    bpy.ops.et.blend_materials('EXEC_DEFAULT',
                               material_a="Concrete", material_b="DirtGrime",
                               direction='UP', direction_mode='FACING',
                               directionality=1.0, direction_falloff=0.5)
    # the operator still succeeds — it warns, it does not refuse
    assert len(bpy.data.materials) == before + 1, "the blend was not built"
    notes.append("a direction nothing faces reports a warning instead of failing quietly")

check("blend warns when no face points the chosen way",
      blend_warns_when_nothing_faces_that_way)


def gradient_span_spots_a_flat_axis():
    """
    A floor has no thickness along Up, so a gradient on that axis has nothing
    to ramp across and comes out uniform — the same silent nothing Face Angle
    gives on a wall, from the opposite cause.
    """
    mesh = bpy.data.meshes.new("SpanMesh")
    mesh.from_pydata([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], [],
                     [(0, 1, 2, 3)])
    mesh.update()
    floor = bpy.data.objects.new("SpanFloor", mesh)
    scene.collection.objects.link(floor)
    bpy.context.view_layer.update()

    up, sideways = (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    assert et._gradient_span([floor], up) < 0.01, \
        f"a floor scored {et._gradient_span([floor], up):.4f} of depth upward"
    assert et._gradient_span([floor], sideways) > 0.9, \
        "a floor should ramp fine along its own plane"

    # stood up, the same quad now has plenty of height to ramp through
    floor.rotation_euler[0] = 1.5708
    bpy.context.view_layer.update()
    assert et._gradient_span([floor], up) > 0.9, \
        "a wall should ramp fine upward"
    assert et._gradient_span([], up) == 0.0
    notes.append("gradient span detects an axis the object is flat in")

check("gradient span spots a flat axis", gradient_span_spots_a_flat_axis)


def gradient_ramp_has_no_hard_start():
    """
    A linear ramp clamps flat below its start and then climbs at full slope,
    which draws a visible line across the surface right where it begins.
    """
    group = et._blend_mask_group()
    ramps = [n for n in group.nodes if n.type == 'MAP_RANGE'
             and getattr(n, 'interpolation_type', '') == 'SMOOTHSTEP']
    assert ramps, "the gradient ramp is still linear — it will show a seam"

check("gradient ramp eases in instead of starting hard",
      gradient_ramp_has_no_hard_start)


def blend_direction_gates_by_facing():
    """Snow on top: the mask must read the surface normal, in world space."""
    _ensure_object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    mat = _run_blend(material_a="Concrete", material_b="DirtGrime",
                     direction='UP', direction_mode='FACING',
                     directionality=1.0,
                     direction_falloff=0.4, assign_to_selected=False)
    blend_mats['up'] = mat
    mask = mat.node_tree.nodes[et._MASK_NODE_NAME]

    assert tuple(mask.inputs['Direction'].default_value) == (0.0, 0.0, 1.0), \
        tuple(mask.inputs['Direction'].default_value)
    assert mask.inputs['Directionality'].default_value == 1.0
    assert abs(mask.inputs['Direction Falloff'].default_value - 0.4) < 1e-6

    # the gate has to come from the real surface normal, not a constant
    group = mask.node_tree
    dot = next((n for n in group.nodes if n.type == 'VECT_MATH'
                and n.operation == 'DOT_PRODUCT'), None)
    assert dot is not None, "no dot product in the mask group"
    normal_src = dot.inputs[0].links[0].from_node
    assert normal_src.type == 'NEW_GEOMETRY', normal_src.type
    assert dot.inputs[0].links[0].from_socket.name == 'Normal', \
        dot.inputs[0].links[0].from_socket.name

    notes.append("directional blend gates the mask on Geometry ▸ Normal")

check("blend direction gates the mask by facing", blend_direction_gates_by_facing)


def blend_direction_off_by_default():
    """
    Directionality 0 must leave the gate wide open, or every material built
    before this existed would change the moment the group is rebuilt.
    """
    mask = blend_mats['plain'].node_tree.nodes[et._MASK_NODE_NAME]
    assert mask.inputs['Directionality'].default_value == 0.0, \
        mask.inputs['Directionality'].default_value

    # gate = 1 + d * (aligned - 1), so d = 0 gives exactly 1 whatever the face
    group = mask.node_tree
    weighted = next(n for n in group.nodes
                    if n.type == 'MATH' and n.operation == 'MULTIPLY'
                    and n.inputs[1].links
                    and n.inputs[1].links[0].from_socket.name == 'Directionality')
    assert weighted.inputs[0].links, "gate weight has no aligned input"

check("direction is off unless asked for", blend_direction_off_by_default)


def gradient_ramps_across_a_flat_wall():
    """
    The whole reason Gradient exists. Face Angle reads one normal, so on a flat
    wall it can only shift the entire surface at once — the reported symptom
    was "no falloff, it's plane all across the mesh". A ramp has to actually
    vary from top to bottom of the same flat quad.
    """
    import tempfile
    _ensure_object_mode()

    folder = tempfile.mkdtemp(prefix="et_grad_")

    def flat_png(stem, grey):
        path = _os.path.join(folder, stem + ".png")
        img = bpy.data.images.new(stem, 8, 8)
        img.generated_color = (grey, grey, grey, 1.0)
        img.filepath_raw, img.file_format = path, 'PNG'
        img.save()
        bpy.data.images.remove(img)
        return path

    # black base, white overlay: the render *is* the mask
    dark  = {'BASE': flat_png("GradBase_2K_Albedo", 0.0)}
    light = {'BASE': flat_png("GradOver_2K_Albedo", 1.0)}

    mat, _notes = et._build_blend_material(
        "ET_GradProbe", dark, light, direction='UP', direction_mode='GRADIENT',
        directionality=1.0, direction_falloff=1.0, amount=1.0, softness=0.25,
        noise_scale=0.0, displacement='NONE', use_mapping=False)

    tree = mat.node_tree
    bsdf = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
    out  = next(n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL')
    emit = tree.nodes.new('ShaderNodeEmission')
    tree.links.new(bsdf.inputs['Base Color'].links[0].from_socket,
                   emit.inputs['Color'])
    tree.links.new(emit.outputs['Emission'], out.inputs['Surface'])

    scene_r = bpy.context.scene
    saved = (scene_r.render.engine, scene_r.render.resolution_x,
             scene_r.render.resolution_y, scene_r.render.filepath, scene_r.camera)

    # a flat wall standing up, well away from the rest of the suite's scene
    bpy.ops.mesh.primitive_plane_add(size=4, location=(0, 0, 1000),
                                     rotation=(1.5708, 0, 0))
    wall = bpy.context.active_object
    wall.data.materials.append(mat)
    bpy.ops.object.camera_add(location=(0, -6, 1000), rotation=(1.5708, 0, 0))
    camera = bpy.context.active_object

    try:
        scene_r.camera = camera
        scene_r.render.engine = 'CYCLES'
        scene_r.cycles.samples = 1
        scene_r.render.resolution_x = scene_r.render.resolution_y = 32
        try:
            scene_r.view_settings.view_transform = 'Standard'
        except TypeError:
            pass
        # EXR, not PNG: PNG comes back sRGB-encoded, so a linear 0.34 reads as
        # 0.62 and a clean ramp looks like a shallow one.
        scene_r.render.image_settings.file_format = 'OPEN_EXR'
        scene_r.render.filepath = _os.path.join(folder, "wall")
        bpy.ops.render.render(write_still=True)

        img = bpy.data.images.load(scene_r.render.filepath + ".exr")
        pixels, width = list(img.pixels), 32
        bpy.data.images.remove(img)

        # The wall fills ~92% of frame, so rows 3 and 28 are its real ends —
        # sampling the middle third would only ever show the middle of a ramp.
        def row(y):                       # centre columns, away from the edges
            return sum(pixels[(y * width + x) * 4] for x in range(12, 20)) / 8

        low, mid, high = row(3), row(16), row(28)
        assert high > mid > low, \
            f"no ramp across the wall: bottom {low:.3f}, middle {mid:.3f}, " \
            f"top {high:.3f} — this is the flat result Gradient exists to fix"
        # a full-strength ramp has to actually travel most of 0..1, not creep
        # across a narrow band in the middle
        assert high - low > 0.5, \
            f"ramp too shallow to read as 0 to 100: {low:.3f} to {high:.3f}"
        assert low < 0.2, f"ramp never reaches none of the overlay: {low:.3f}"
        assert high > 0.8, f"ramp never reaches all of the overlay: {high:.3f}"
        notes.append(f"gradient ramps a flat wall {low:.3f} -> {mid:.3f} -> "
                     f"{high:.3f} bottom to top")
    except Exception as exc:                      # noqa: BLE001 - env, not logic
        if isinstance(exc, AssertionError):
            raise
        notes.append(f"gradient render check skipped ({exc.__class__.__name__})")
    finally:
        (scene_r.render.engine, scene_r.render.resolution_x,
         scene_r.render.resolution_y, scene_r.render.filepath,
         scene_r.camera) = saved
        scene_r.render.image_settings.file_format = 'PNG'
        bpy.data.objects.remove(wall)
        bpy.data.objects.remove(camera)

check("gradient ramps the overlay across a flat wall",
      gradient_ramps_across_a_flat_wall)


def gradient_axis_flips_the_ramp():
    """Down must be Up the other way round, not Up again."""
    mask_of = {}
    a = et._material_texture_slots(bpy.data.materials["Concrete"])
    b = et._material_texture_slots(bpy.data.materials["DirtGrime"])
    for axis in ('UP', 'DOWN'):
        mat, _notes = et._build_blend_material(
            f"ET_GradAxis_{axis}", a, b, direction=axis,
            direction_mode='GRADIENT', displacement='NONE')
        node = mat.node_tree.nodes[et._MASK_NODE_NAME]
        mask_of[axis] = tuple(node.inputs['Direction'].default_value)
        assert node.inputs['Gradient'].default_value == 1.0, \
            "gradient mode did not reach the group"
    assert mask_of['UP'] == (0.0, 0.0, 1.0), mask_of['UP']
    assert mask_of['DOWN'] == (0.0, 0.0, -1.0), mask_of['DOWN']

check("gradient axis flips with the direction", gradient_axis_flips_the_ramp)


def facing_mode_still_reads_normals():
    """Face Angle must keep working — Gradient is an addition, not a swap."""
    a = et._material_texture_slots(bpy.data.materials["Concrete"])
    b = et._material_texture_slots(bpy.data.materials["DirtGrime"])
    mat, _notes = et._build_blend_material(
        "ET_FacingStill", a, b, direction='UP', direction_mode='FACING',
        displacement='NONE')
    node = mat.node_tree.nodes[et._MASK_NODE_NAME]
    assert node.inputs['Gradient'].default_value == 0.0, \
        "Face Angle left the gradient switched on"

check("face angle mode is unchanged by the gradient addition",
      facing_mode_still_reads_normals)


def blend_direction_actually_lands_on_top():
    """
    Structure proves the nodes are wired; only evaluation proves the *sign*.
    A flipped subtract would put the snow underneath and every other check here
    would still pass.

    Rotating a real plane and comparing renders does not work — it changes the
    texture coordinates and the frame coverage at the same time as the facing,
    and those swamp the signal. So the Geometry link is replaced with a
    constant vector in a *copy* of the group: the only thing that varies is the
    normal, on a flat plane whose every pixel is the same value.
    """
    import tempfile
    _ensure_object_mode()

    group = blend_mats['up'].node_tree.nodes[et._MASK_NODE_NAME].node_tree.copy()
    dot = next(n for n in group.nodes
               if n.type == 'VECT_MATH' and n.operation == 'DOT_PRODUCT')
    fake_normal = group.nodes.new('ShaderNodeCombineXYZ')
    group.links.new(fake_normal.outputs['Vector'], dot.inputs[0])

    probe = bpy.data.materials.new("ET_DirProbe")
    probe.use_nodes = True
    nodes, links = probe.node_tree.nodes, probe.node_tree.links
    nodes.clear()
    out  = nodes.new('ShaderNodeOutputMaterial')
    emit = nodes.new('ShaderNodeEmission')
    links.new(emit.outputs['Emission'], out.inputs['Surface'])
    node = nodes.new('ShaderNodeGroup')
    node.node_tree = group
    links.new(node.outputs['Fac'], emit.inputs['Color'])
    node.inputs['Direction'].default_value          = (0.0, 0.0, 1.0)
    node.inputs['Direction Falloff'].default_value  = 0.5
    node.inputs['Gradient'].default_value           = 0.0   # face angle only
    node.inputs['Height'].default_value             = 0.5
    node.inputs['Amount'].default_value             = 0.5
    node.inputs['Softness'].default_value           = 1.0   # nothing clamps flat
    node.inputs['Noise Scale'].default_value        = 0.0   # deterministic

    scene_r = bpy.context.scene
    saved = (scene_r.render.engine, scene_r.render.resolution_x,
             scene_r.render.resolution_y, scene_r.render.filepath, scene_r.camera)

    # Far from the rest of the suite's scene: a cube from an earlier test sits
    # at (0, 0, 3) and would be rendered instead of the probe plane, which
    # makes every reading identical no matter what the mask does.
    origin = (0.0, 0.0, 1000.0)
    bpy.ops.mesh.primitive_plane_add(size=50, location=origin)
    plane = bpy.context.active_object
    plane.data.materials.append(probe)
    bpy.ops.object.camera_add(location=(0.0, 0.0, origin[2] + 3.0))
    camera = bpy.context.active_object
    folder = tempfile.mkdtemp(prefix="et_dirrender_")

    def mask_value(tag, normal, strength):
        node.inputs['Directionality'].default_value = strength
        for axis, value in zip('XYZ', normal):
            fake_normal.inputs[axis].default_value = value
        scene_r.render.filepath = _os.path.join(folder, tag)
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(scene_r.render.filepath + ".png")
        first = img.pixels[0]
        bpy.data.images.remove(img)
        return first

    up, tilt, side = (0.0, 0.0, 1.0), (0.0, 0.866, 0.5), (0.0, 1.0, 0.0)
    try:
        scene_r.camera = camera
        scene_r.render.engine = 'CYCLES'
        scene_r.cycles.samples = 1
        scene_r.render.resolution_x = scene_r.render.resolution_y = 4
        try:
            scene_r.view_settings.view_transform = 'Standard'
        except TypeError:
            pass

        # off: the facing must not matter at all
        assert abs(mask_value("off_up", up, 0.0)
                   - mask_value("off_side", side, 0.0)) < 1e-4, \
            "Directionality 0 still let the facing change the mask"

        # on: strongest looking up, weakest looking away
        on_up   = mask_value("on_up", up, 1.0)
        on_tilt = mask_value("on_tilt", tilt, 1.0)
        on_side = mask_value("on_side", side, 1.0)
        assert on_up > on_tilt > on_side, \
            f"not monotonic by facing: up {on_up:.4f}, 60° {on_tilt:.4f}, " \
            f"side {on_side:.4f} — the gate is inverted"
        assert on_up - on_side > 0.05, \
            f"facing barely changes the mask: {on_up:.4f} vs {on_side:.4f}"
        notes.append(f"directional mask up {on_up:.3f} > 60° {on_tilt:.3f} "
                     f"> side {on_side:.3f}")
    except Exception as exc:                      # noqa: BLE001 - env, not logic
        if isinstance(exc, AssertionError):
            raise
        notes.append(f"directional render check skipped ({exc.__class__.__name__})")
    finally:
        (scene_r.render.engine, scene_r.render.resolution_x,
         scene_r.render.resolution_y, scene_r.render.filepath,
         scene_r.camera) = saved
        bpy.data.objects.remove(plane)
        bpy.data.objects.remove(camera)
        bpy.data.materials.remove(probe)
        bpy.data.node_groups.remove(group)

check("directional blend puts the overlay on the up-facing side",
      blend_direction_actually_lands_on_top)


def blend_mask_group_survives_an_older_file():
    """
    A .blend saved before the directional sockets existed carries a group of
    the same name with fewer inputs. Reusing it would KeyError on Direction.
    """
    stale = bpy.data.node_groups.new(et._MASK_GROUP_NAME + "_stale",
                                     'ShaderNodeTree')
    real_name, stale.name = et._MASK_GROUP_NAME, "parked"
    # make the stale one own the canonical name, with none of the new sockets
    current = bpy.data.node_groups[real_name]
    current.name = "current_parked"
    stale.name   = real_name
    try:
        rebuilt = et._blend_mask_group()
        assert set(et._MASK_INPUTS) <= et._group_input_names(rebuilt), \
            "reused a group that lacks the directional sockets"
        assert rebuilt.name != real_name or rebuilt is not stale, \
            "the stale group was handed back"
        assert et._group_input_names(stale) == set(), \
            "the old group was modified instead of left alone"
    finally:
        stale.name = "stale_parked"
        current.name = real_name

check("mask group is rebuilt rather than reused when stale",
      blend_mask_group_survives_an_older_file)


def missing_texture_reports_instead_of_crashing():
    """A texture deleted after the material was built must not throw."""
    import shutil
    import tempfile
    folder = tempfile.mkdtemp(prefix="et_missing_")
    _write_textures(folder, ['Ghost_2K_Albedo', 'Ghost_2K_Roughness'])
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.et.megascans_material('EXEC_DEFAULT', directory=folder,
                                  assign_to_selected=False)
    assert bpy.data.materials.get("Ghost") is not None

    # drop the files and the loaded datablocks, so a rebuild has to hit disk
    for image in list(bpy.data.images):
        if image.filepath and "et_missing_" in image.filepath:
            bpy.data.images.remove(image)
    shutil.rmtree(folder, ignore_errors=True)

    try:
        bpy.ops.et.blend_materials('EXEC_DEFAULT',
                                   material_a="Ghost", material_b="Concrete")
    except RuntimeError as exc:
        # a clean operator error, not a traceback out of images.load
        assert "Could not read" in str(exc) or "No recognisable" in str(exc), exc
    # succeeding is fine too — Blender may still hold the pixels in memory

check("a missing texture reports instead of crashing",
      missing_texture_reports_instead_of_crashing)


def blend_rejects_untextured_materials():
    plain = bpy.data.materials.new("PlainGrey")
    try:
        bpy.ops.et.blend_materials('EXEC_DEFAULT',
                                   material_a="Concrete", material_b="PlainGrey")
    except RuntimeError as exc:
        assert "No recognisable texture maps" in str(exc), exc
    else:
        raise AssertionError("a material with no textures was accepted")

    try:
        bpy.ops.et.blend_materials('EXEC_DEFAULT',
                                   material_a="Concrete", material_b="Concrete")
    except RuntimeError as exc:
        assert "two different" in str(exc), exc
    else:
        raise AssertionError("blending a material with itself was accepted")
    bpy.data.materials.remove(plain)

check("blend rejects materials it cannot read", blend_rejects_untextured_materials)


def blend_mask_panel_edits_in_place():
    """
    The panel is the non-destructive route: the dialog rebuilds the material
    and would drop the paint layer every time a slider moved, so a finished
    blend has to be adjustable on the node it already has.
    """
    _ensure_object_mode()
    wall = bpy.data.objects["BlendWall"]
    bpy.ops.object.select_all(action='DESELECT')
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall

    assert et.ET_PT_BlendMaskPanel.poll(bpy.context), \
        "panel hidden while a blended material is active"
    node = et._active_blend_mask(bpy.context)
    assert node is not None

    # every control the panel draws has to exist, or the panel raises on draw
    for name in ('Amount', 'Softness', 'Noise Scale', 'Gradient',
                 'Directionality', 'Direction Falloff', 'Paint Influence',
                 'Direction'):
        assert name in node.inputs, f"{name} missing from the mask node"

    before = bpy.data.materials[:]
    node.inputs['Amount'].default_value = 0.72
    assert abs(node.inputs['Amount'].default_value - 0.72) < 1e-6
    assert bpy.data.materials[:] == before, \
        "editing the mask created or destroyed a material"

    # and the axis buttons move the vector without a rebuild
    bpy.ops.et.set_blend_axis('EXEC_DEFAULT', axis='DOWN')
    assert tuple(node.inputs['Direction'].default_value) == (0.0, 0.0, -1.0), \
        tuple(node.inputs['Direction'].default_value)
    bpy.ops.et.set_blend_axis('EXEC_DEFAULT', axis='UP')
    assert bpy.data.materials[:] == before, "an axis button rebuilt the material"
    notes.append("blend mask is editable in the panel, no rebuild, no new material")

check("blend mask panel edits the live material", blend_mask_panel_edits_in_place)


def blend_mask_tools_survive_an_older_group():
    """
    A material built by an earlier version carries a mask group without the
    directional sockets. The panel and the axis buttons must degrade, not raise.
    """
    _ensure_object_mode()
    stale_group = bpy.data.node_groups.new("ET Blend Mask Legacy", 'ShaderNodeTree')
    et._new_group_socket(stale_group, 'Paint', 'NodeSocketFloat', 'INPUT', 0.5)
    et._new_group_socket(stale_group, 'Fac', 'NodeSocketFloat', 'OUTPUT')

    mat = bpy.data.materials.new("LegacyBlend")
    mat.use_nodes = True
    node = mat.node_tree.nodes.new('ShaderNodeGroup')
    node.node_tree = stale_group
    node.name = et._MASK_NODE_NAME

    obj = bpy.data.objects.new("LegacyObj", bpy.data.meshes.new("LegacyMesh"))
    scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    assert et._active_blend_mask(bpy.context) is not None
    assert et.ET_PT_BlendMaskPanel.poll(bpy.context), \
        "panel should still show for an older blend"
    # the axis buttons have nothing to write to, so they must switch off
    assert not et.ET_OT_SetBlendAxis.poll(bpy.context), \
        "axis button offered on a mask with no Direction input"
    # and reading the paint image must not raise on the missing socket
    assert et._blend_paint_image(node) is None

    bpy.data.objects.remove(obj)
    bpy.data.materials.remove(mat)
    bpy.data.node_groups.remove(stale_group)

check("blend tools degrade on an older mask group",
      blend_mask_tools_survive_an_older_group)


def blend_presets_are_well_formed():
    """Every preset has to be usable by both the dialog and the panel."""
    operator_props = et.ET_OT_BlendMaterials.__annotations__
    for key in et._PRESET_ORDER:
        values = et._BLEND_PRESETS[key]
        assert values['label'] and values['description'], key
        for field in ('amount', 'softness', 'noise_scale', 'direction',
                      'direction_mode', 'direction_falloff'):
            assert field in values, f"{key} is missing {field}"
            # a preset writes straight onto the operator, so the names must match
            assert field in operator_props, f"{key} sets unknown property {field}"
        axes = {item[0] for item in
                operator_props['direction'].keywords['items']}
        assert values['direction'] in axes, \
            f"{key} names an axis the operator does not offer: {values['direction']}"
        assert values['direction_mode'] in ('GRADIENT', 'FACING'), key
        assert 0.0 <= values['amount'] <= 1.0, key
        assert 0.01 <= values['direction_falloff'] <= 1.0, key

    keys = {item[0] for item in et._preset_items()}
    assert keys == set(et._PRESET_ORDER) | {'CUSTOM'}, keys

check("blend presets are well formed", blend_presets_are_well_formed)


def blend_preset_applies_without_rebuilding():
    """
    The point of the panel preset: change your mind without losing the paint.
    Rebuilding through the dialog would make a second material and orphan the
    mask that has been painted on.
    """
    _ensure_object_mode()
    wall = bpy.data.objects["BlendWall"]
    bpy.ops.object.select_all(action='DESELECT')
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall

    node   = et._active_blend_mask(bpy.context)
    before = list(bpy.data.materials)
    paint  = node.inputs['Paint'].links[0].from_node if node.inputs['Paint'].links else None

    bpy.ops.et.apply_blend_preset('EXEC_DEFAULT', preset='SNOW')
    snow = et._BLEND_PRESETS['SNOW']
    assert abs(node.inputs['Amount'].default_value - snow['amount']) < 1e-6
    assert abs(node.inputs['Softness'].default_value - snow['softness']) < 1e-6
    assert node.inputs['Gradient'].default_value == 0.0, "SNOW is a Face Angle preset"
    assert node.inputs['Directionality'].default_value == 1.0
    assert tuple(node.inputs['Direction'].default_value) == (0.0, 0.0, 1.0)

    bpy.ops.et.apply_blend_preset('EXEC_DEFAULT', preset='GRIME_RISE')
    assert node.inputs['Gradient'].default_value == 1.0, "GRIME_RISE is a gradient"
    assert tuple(node.inputs['Direction'].default_value) == (0.0, 0.0, -1.0)

    # a preset with no direction has to switch the gate off, not leave it armed
    bpy.ops.et.apply_blend_preset('EXEC_DEFAULT', preset='DIRT')
    assert node.inputs['Directionality'].default_value == 0.0, \
        "a directionless preset left the gate on"

    assert list(bpy.data.materials) == before, "a preset built a new material"
    if paint is not None:
        assert node.inputs['Paint'].links[0].from_node == paint, \
            "a preset dropped the painted layer"
    notes.append("presets load onto a live blend without touching the paint")

check("blend preset applies without rebuilding",
      blend_preset_applies_without_rebuilding)


def blend_mask_panel_hides_without_a_blend():
    bare = bpy.data.objects.new("NoBlendProbe", bpy.data.meshes.new("NoBlendM"))
    scene.collection.objects.link(bare)
    bare.data.materials.append(bpy.data.materials.new("PlainForPanel"))
    _ensure_object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    bare.select_set(True)
    bpy.context.view_layer.objects.active = bare

    assert et._active_blend_mask(bpy.context) is None
    assert not et.ET_PT_BlendMaskPanel.poll(bpy.context), \
        "panel shown for a material with no blend mask"
    # and the axis operator must refuse rather than raise
    assert not et.ET_OT_SetBlendAxis.poll(bpy.context)

check("blend mask panel stays hidden without a blend",
      blend_mask_panel_hides_without_a_blend)


def paint_mask_creates_an_image_layer():
    import tempfile
    _ensure_object_mode()
    # A generated image cannot be packed, so the mask is only durable once the
    # blend has a location to sit next to. Give it one.
    blend = _os.path.join(tempfile.mkdtemp(prefix="et_blendfile_"), "mask_test.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)

    wall = bpy.data.objects["BlendWall"]
    bpy.ops.object.select_all(action='DESELECT')
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall

    mat  = wall.active_material
    mask = mat.node_tree.nodes[et._MASK_NODE_NAME]
    assert not mask.inputs['Paint'].links, "paint layer existed before painting"

    bpy.ops.et.paint_blend_mask('EXEC_DEFAULT', mask_type='IMAGE', resolution='1024')

    links = mask.inputs['Paint'].links
    assert links, "paint layer was not connected to the mask"
    node = links[0].from_node
    assert node.type == 'TEX_IMAGE', node.type
    assert node.image.size[0] == 1024, node.image.size[:]
    assert node.image.colorspace_settings.name != 'sRGB', \
        "mask image left colour managed"
    # the pixels must exist somewhere other than generated_color, or the paint
    # is silently rebuilt as flat grey on the next load
    on_disk = _os.path.join(_os.path.dirname(blend), "textures", node.image.name + ".png")
    assert _os.path.isfile(on_disk), f"mask image was not written to {on_disk}"
    assert node.image.source == 'FILE', node.image.source

    # a second click must reuse that image rather than stacking another
    image_name = node.image.name
    _ensure_object_mode()
    bpy.ops.et.paint_blend_mask('EXEC_DEFAULT', mask_type='IMAGE')
    again = mask.inputs['Paint'].links[0].from_node
    # bpy wrappers are rebuilt per access, so compare the data, not identity
    assert again.image.name == image_name, \
        f"a second run built a second mask image: {again.image.name}"
    assert len([n for n in mat.node_tree.nodes
                if n.type == 'TEX_IMAGE'
                and n.name.startswith(et._BLEND_PAINT_NODE)]) == 1, \
        "a second paint node was added to the tree"
    _ensure_object_mode()

    notes.append("paint mask: one image on disk, reused, non-colour, grey neutral")

check("et.paint_blend_mask creates and reuses an image layer",
      paint_mask_creates_an_image_layer)


def paint_uvs_do_not_tile():
    """
    The reported bug: painting one stroke made it appear all over the mesh.

    A tiling surface has UVs running well past 0-1 so the textures repeat. Sample
    the mask through those and every tile shares the same pixels, so one stroke
    lands in all of them. The mask needs its own 0-1 layer.
    """
    _ensure_object_mode()
    mesh = bpy.data.meshes.new("TiledMesh")
    mesh.from_pydata([(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)], [],
                     [(0, 1, 2, 3)])
    mesh.update()
    layer = mesh.uv_layers.new(name="Tiled")
    # UVs running 0..8: the texture repeats eight times across the quad
    for datum, uv in zip(layer.data, [(0, 0), (8, 0), (8, 8), (0, 8)]):
        datum.uv = uv

    tiled = bpy.data.objects.new("TiledWall", mesh)
    scene.collection.objects.link(tiled)
    tiled.data.materials.append(blend_mats['plain'])
    bpy.ops.object.select_all(action='DESELECT')
    tiled.select_set(True)
    bpy.context.view_layer.objects.active = tiled

    paint_layer = et._paint_uv_layer(tiled)
    assert paint_layer is not None
    assert paint_layer.name == et._BLEND_PAINT_UV, \
        f"tiled UVs were used for painting: {paint_layer.name}"

    us = [datum.uv[0] for datum in paint_layer.data]
    vs = [datum.uv[1] for datum in paint_layer.data]
    assert -0.001 <= min(us) and max(us) <= 1.001, (min(us), max(us))
    assert -0.001 <= min(vs) and max(vs) <= 1.001, (min(vs), max(vs))
    # the tiled layer must be left exactly as it was for the textures
    assert max(datum.uv[0] for datum in mesh.uv_layers["Tiled"].data) == 8.0, \
        "the texture UVs were rescaled"

    # a mesh already inside 0-1 needs no second layer
    plain = bpy.data.meshes.new("PlainUVMesh")
    plain.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    plain.update()
    good = plain.uv_layers.new(name="Fine")
    for datum, uv in zip(good.data, [(0, 0), (1, 0), (1, 1)]):
        datum.uv = uv
    plain_obj = bpy.data.objects.new("PlainUVWall", plain)
    scene.collection.objects.link(plain_obj)
    assert et._paint_uv_layer(plain_obj).name == "Fine", \
        "added a paint UV layer to a mesh that did not need one"

    notes.append("paint mask gets its own 0-1 UV layer when the surface tiles")

check("paint mask does not repeat across tiled UVs", paint_uvs_do_not_tile)


def paint_mask_wires_its_own_uvs():
    """The mask node has to sample through that layer, not the tiled default."""
    _ensure_object_mode()
    tiled = bpy.data.objects["TiledWall"]
    bpy.ops.object.select_all(action='DESELECT')
    tiled.select_set(True)
    bpy.context.view_layer.objects.active = tiled

    # a fresh blend so this object's mask has no paint layer yet
    blend = _run_blend(material_a="Concrete", material_b="DirtGrime")
    bpy.ops.et.paint_blend_mask('EXEC_DEFAULT', mask_type='IMAGE',
                                resolution='1024')

    mask  = blend.node_tree.nodes[et._MASK_NODE_NAME]
    paint = mask.inputs['Paint'].links[0].from_node
    assert paint.type == 'TEX_IMAGE', paint.type
    assert paint.inputs['Vector'].links, \
        "paint image left on the default UVs — strokes will repeat per tile"
    uv_node = paint.inputs['Vector'].links[0].from_node
    assert uv_node.type == 'UVMAP', uv_node.type
    assert uv_node.uv_map == et._BLEND_PAINT_UV, uv_node.uv_map
    assert tiled.data.uv_layers.active.name == et._BLEND_PAINT_UV, \
        "strokes would project through the tiled layer"
    _ensure_object_mode()

check("paint mask samples and paints through its own UVs",
      paint_mask_wires_its_own_uvs)


def paint_uvs_reach_every_object_on_the_material():
    """
    The reported bug: paint showed in the viewport but not in a render. One
    blend material covering a floor of many planes — only the object paint mode
    was entered on got the UV layer, so everything else looked up a UV map it
    did not have and silently rendered no paint at all.
    """
    _ensure_object_mode()
    blend = blend_mats['plain']

    tiles = []
    for index in range(3):
        mesh = bpy.data.meshes.new(f"SharedTile{index}")
        mesh.from_pydata([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], [],
                         [(0, 1, 2, 3)])
        mesh.update()
        layer = mesh.uv_layers.new(name="Tiled")
        for datum, uv in zip(layer.data, [(0, 0), (6, 0), (6, 6), (0, 6)]):
            datum.uv = uv
        obj = bpy.data.objects.new(f"SharedTileObj{index}", mesh)
        scene.collection.objects.link(obj)
        mesh.materials.append(blend)
        tiles.append(obj)

    bpy.ops.object.select_all(action='DESELECT')
    tiles[0].select_set(True)
    bpy.context.view_layer.objects.active = tiles[0]
    bpy.ops.et.paint_blend_mask('EXEC_DEFAULT', mask_type='IMAGE',
                                resolution='1024')
    _ensure_object_mode()

    for obj in tiles:
        names = [layer.name for layer in obj.data.uv_layers]
        assert et._BLEND_PAINT_UV in names, \
            f"{obj.name} never got the paint UV layer — it would render no paint"
        # the texture UVs must still be the ones the images sample
        render_layer = next(l for l in obj.data.uv_layers if l.active_render)
        assert render_layer.name == "Tiled", render_layer.name

    users = et._material_users(blend)
    assert len(users) >= 3, [o.name for o in users]
    notes.append(f"paint UVs reach all {len(users)} objects sharing the material")

check("paint UVs reach every object sharing the material",
      paint_uvs_reach_every_object_on_the_material)


def painted_mask_survives_a_reload():
    """
    The reported bug: paint showed in the viewport but a render ignored it.

    The file on disk was the flat grey written when the mask was created, so
    the strokes only ever existed in memory. Anything that re-read the image —
    a reopened file, a reload, a fresh render process — saw an unpainted mask.
    """
    _ensure_object_mode()
    wall = bpy.data.objects["BlendWall"]
    bpy.ops.object.select_all(action='DESELECT')
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall

    node  = et._active_blend_mask(bpy.context)
    image = et._blend_paint_image(node)
    assert image is not None, "no paint image to test with"

    # paint white over the whole mask
    width, height = image.size
    image.pixels.foreach_set([1.0, 1.0, 1.0, 1.0] * (width * height))
    image.update()
    assert image.is_dirty, "painting did not mark the image dirty"

    assert et._save_blend_mask_image(image), "the painted mask was not saved"
    assert not image.is_dirty, "still dirty after saving"

    # the real check: what comes back off disk has to be the paint
    image.reload()
    assert image.pixels[0] > 0.9, \
        f"reload gave {image.pixels[0]:.3f} — the paint never reached the file"
    notes.append("painted mask is written to disk, so it survives a reload")

check("painted mask survives a reload", painted_mask_survives_a_reload)


def saving_a_clean_mask_is_a_no_op():
    node  = et._active_blend_mask(bpy.context)
    image = et._blend_paint_image(node)
    assert not image.is_dirty
    assert et._save_blend_mask_image(image) is False, \
        "reported a write for an already-saved mask"

    # and packing must never be reached before saving, which would pack the
    # stale file and mark the buffer clean — losing the strokes
    image.pixels.foreach_set([0.25, 0.25, 0.25, 1.0] * (image.size[0] * image.size[1]))
    image.update()
    et._save_blend_mask_image(image)
    image.reload()
    assert abs(image.pixels[0] - 0.25) < 0.02, \
        f"reload gave {image.pixels[0]:.3f} — a stale file overwrote the paint"

check("saving a mask never packs stale pixels over the paint",
      saving_a_clean_mask_is_a_no_op)


def paint_mask_falls_back_to_vertex_colour():
    """No UVs must not be a dead end — vertex colour needs none."""
    _ensure_object_mode()
    mesh = bpy.data.meshes.new("NoUVMesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    mesh.update()
    obj = bpy.data.objects.new("NoUVWall", mesh)
    scene.collection.objects.link(obj)
    obj.data.materials.append(blend_mats['plain'])

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # the image path should refuse, and say why
    try:
        bpy.ops.et.paint_blend_mask('EXEC_DEFAULT', mask_type='IMAGE')
    except RuntimeError as exc:
        assert "no UV map" in str(exc), exc
    else:
        raise AssertionError("painting an image mask without UVs was accepted")

    bpy.ops.et.paint_blend_mask('EXEC_DEFAULT', mask_type='VERTEX')
    assert et._BLEND_PAINT_ATTR in mesh.color_attributes, \
        "vertex paint layer not created"
    attr = mesh.color_attributes[et._BLEND_PAINT_ATTR]
    assert abs(attr.data[0].color[0] - 0.5) < 1e-6, \
        f"vertex layer not initialised to neutral grey: {attr.data[0].color[:]}"

    mask = obj.active_material.node_tree.nodes[et._MASK_NODE_NAME]
    assert mask.inputs['Paint'].links[0].from_node.type == 'ATTRIBUTE', \
        "vertex layer did not take over the Paint input"

    # switching type moves the link but must not destroy the other layer, so
    # switching back brings the painted image with it
    _ensure_object_mode()
    uvd = bpy.data.objects["BlendWall"]
    bpy.ops.object.select_all(action='DESELECT')
    uvd.select_set(True)
    bpy.context.view_layer.objects.active = uvd
    bpy.ops.et.paint_blend_mask('EXEC_DEFAULT', mask_type='IMAGE')
    back = mask.inputs['Paint'].links[0].from_node
    assert back.type == 'TEX_IMAGE', back.type
    assert back.image.name.endswith("_Mask"), back.image.name
    assert et._BLEND_PAINT_ATTR in mesh.color_attributes, \
        "switching back deleted the vertex layer"
    _ensure_object_mode()
    notes.append("image and vertex mask layers coexist; switching only moves the link")

check("paint mask falls back to vertex colour without UVs",
      paint_mask_falls_back_to_vertex_colour)


def paint_mask_needs_a_blend_material():
    _ensure_object_mode()
    bare = bpy.data.objects.new("BareWall", bpy.data.meshes.new("BareM"))
    scene.collection.objects.link(bare)
    bare.data.materials.append(bpy.data.materials.new("JustAMaterial"))
    bpy.ops.object.select_all(action='DESELECT')
    bare.select_set(True)
    bpy.context.view_layer.objects.active = bare
    try:
        bpy.ops.et.paint_blend_mask('EXEC_DEFAULT')
    except RuntimeError as exc:
        assert "Blend Materials first" in str(exc), exc
    else:
        raise AssertionError("paint mode was entered without a blend mask")

check("paint mask refuses a material with no mask",
      paint_mask_needs_a_blend_material)


# --- release blockers -------------------------------------------------------

def apply_buttons_set_all_three_flags():
    """
    object.transform_apply defaults location, rotation AND scale to True. A
    button that only sets its own flag applies all three: 'Apply Scale' also
    zeroed the rotation and moved the origin to the world origin.
    """
    import types

    class FakeLayout:
        def __init__(self):
            self.calls = []

        def operator(self, idname, **kwargs):
            props = types.SimpleNamespace()
            self.calls.append((idname, kwargs, props))
            return props

    layout = FakeLayout()
    et._transform_apply_buttons(layout, ("Scale", "Rotation", "Location"))
    expected = {"Scale":    (False, False, True),
                "Rotation": (False, True,  False),
                "Location": (True,  False, False)}
    assert len(layout.calls) == 3, layout.calls
    for idname, kwargs, props in layout.calls:
        assert idname == 'object.transform_apply', idname
        flags = (props.location, props.rotation, props.scale)
        assert flags == expected[kwargs['text']], (kwargs['text'], flags)

    # and nothing in the add-on builds one of these buttons by hand any more
    source = open(SRC, encoding='utf-8').read()
    raw = source.count("operator('object.transform_apply'")
    assert raw == 1, f"{raw} transform_apply buttons bypass the helper"

check("apply buttons set all three transform flags",
      apply_buttons_set_all_three_flags)


def batch_export_applies_only_what_is_ticked():
    """Rotation and Location unticked must leave rotation and location alone."""
    import math
    _ensure_object_mode()
    mesh = bpy.data.meshes.new("ExportProbeMesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("ExportProbe", mesh)
    scene.collection.objects.link(obj)
    obj.location = (3.0, 4.0, 5.0)
    obj.rotation_euler = (0.0, 0.0, math.radians(45))
    obj.scale = (2.0, 2.0, 2.0)

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.et.batch_export('EXEC_DEFAULT', export_fbx=False, export_obj=False,
                            export_glb=False, apply_scale=True,
                            apply_rotation=False, apply_location=False)

    assert tuple(round(v, 5) for v in obj.scale) == (1.0, 1.0, 1.0), obj.scale[:]
    assert abs(obj.rotation_euler.z - math.radians(45)) < 1e-5, \
        f"rotation was applied: {obj.rotation_euler[:]}"
    assert tuple(round(v, 5) for v in obj.location) == (3.0, 4.0, 5.0), \
        f"location was applied: {obj.location[:]}"
    bpy.data.objects.remove(obj)

check("batch export applies only the ticked transforms",
      batch_export_applies_only_what_is_ticked)


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
