# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

bl_info = {
    "name": "Easy Tasks",
    "author": "Alberto Cordero",
    "description": "Scene organisation, mesh cleanup, modelling analysis, and "
                   "Megascans import with paintable material blending.",
    "blender": (3, 0, 0),
    "version": (2, 6, 0),
    "location": "View3D > Sidebar > EasyTasks, Ctrl+Shift+X, Shift+Alt+X, Q",
    "warning": "",
    "doc_url": "https://github.com/AlbertoCGArt/EasyTasks",
    "tracker_url": "https://github.com/AlbertoCGArt/EasyTasks/issues",
    "category": "3D View",
}

import os
import re
import datetime
import numpy as np
import mathutils
import bmesh
import bpy

addon_keymaps    = {}
_isolation_state = {}      # {lc_name: hide_viewport} when in isolation mode

# Auto-route bookkeeping. Keyed per scene so switching scenes doesn't make every
# object in the new scene look "newly added".
_known_objects  = {}       # {scene_name: set(obj_name)}
_route_pending  = False    # a routing timer is already queued

# Viewport shading previews (silhouette / cavity / face stretch). Keyed by space
# pointer so two viewports can't clobber each other's saved shading.
_shading_state  = {}       # {space_ptr: (mode, {prop: value})}

# Face-stretch analyzer. Records which meshes were given the temporary colour
# attribute and which colour attribute was active before, so both can be put
# back. Nothing about the mesh's materials is touched.
_stretch_state  = {}       # {mesh_name: (active_index, render_index)}

# session-only visibility bookmarks
_vis_bookmarks  = {}       # {slot_name: {lc_name: hide_viewport}}

_VCOL_STRETCH   = "ET_Stretch"

# Blender's duplicate suffix, e.g. the '.001' in 'Crate.001'.
_SUFFIX_SPLIT_RE = re.compile(r'\.\d+$')


# ---------------------------------------------------------------------------
# Shared routing helpers  (Arrange Scene + auto-route handler both use these)
# ---------------------------------------------------------------------------

_KEYWORD_ALIASES = {
    'lamp':  'LIGHTS',
    'cam':   'CAMERAS',
    'ceil':  'CEILING',
}


def _obj_name_parts(obj):
    """Return lowercase word tokens from an object name, split on _ . - space."""
    name = obj.name.lower()
    for sep in ('_', '.', '-', ' '):
        name = name.replace(sep, '\x00')
    return set(filter(None, name.split('\x00')))


_TYPE_COLLECTION = {'LIGHT': 'LIGHTS', 'CAMERA': 'CAMERAS'}


def _editable(coll):
    """A collection whose object list can be changed: not from a library."""
    return coll.library is None and getattr(coll, 'override_library', None) is None


def _scene_collections(scene):
    """
    Every editable collection in this scene's tree, in tree order.

    Routing used to search all of bpy.data.collections, which includes other
    scenes' collections, orphans, and library-linked ones. Linking an object
    into the first two takes it out of this scene; the third refuses the link
    after the object had already been unlinked, leaving it in no collection at
    all — and an object in no collection is gone on save.
    """
    found, seen, stack = [], set(), list(scene.collection.children)
    while stack:
        coll = stack.pop(0)
        key = coll.as_pointer()
        if key in seen:
            continue
        seen.add(key)
        if _editable(coll):
            found.append(coll)
        stack.extend(coll.children)
    return found


def _move_object(obj, target, scene):
    """
    Move obj into target, leaving it there and only there within this scene.

    Link first, unlink after, so a link that fails cannot leave the object
    homeless. Memberships outside this scene's tree — another scene, the rigid
    body world — are left alone: they are not this scene's to rearrange.
    """
    if obj.name not in target.objects:
        target.objects.link(obj)
    local = {c.as_pointer() for c in _scene_collections(scene)}
    local.add(scene.collection.as_pointer())
    for current in list(obj.users_collection):
        if (current != target and current.as_pointer() in local
                and _editable(current)):
            current.objects.unlink(obj)


def _build_route_index(scene=None):
    """
    Build {keyword: Collection} once, so routing N objects costs O(N) name-token
    lookups instead of O(N × collections) string comparisons.

    Keys are the lowercased collection name plus its naive singular, so a
    collection 'ROCKS' matches an object named 'Rock_Pile_01'. Only collections
    in this scene's tree are candidates — see _scene_collections.
    """
    scene = scene or bpy.context.scene
    index = {}
    for coll in _scene_collections(scene):
        lower = coll.name.lower()
        index.setdefault(lower, coll)
        if lower.endswith('s') and len(lower) > 2:
            index.setdefault(lower[:-1], coll)
    return index


def _find_target_collection(obj, index=None, scene=None):
    """
    Return a collection in this scene to route obj into, or None.

    Priority:
      1. Object type  (LIGHT → LIGHTS, CAMERA → CAMERAS)
      2. Hardcoded aliases  (cam → CAMERAS, lamp → LIGHTS, ceil → CEILING)
      3. Any existing collection matched by name / simple singular

    Pass a prebuilt `index` from _build_route_index() when routing in bulk.
    Every lookup goes through the index, so nothing outside the scene's own
    editable tree can be chosen.
    """
    if index is None:
        index = _build_route_index(scene)

    coll_name = _TYPE_COLLECTION.get(obj.type)
    if coll_name:
        coll = index.get(coll_name.lower())
        if coll:
            return coll

    parts = _obj_name_parts(obj)

    for part in parts:
        alias = _KEYWORD_ALIASES.get(part)
        if alias:
            coll = index.get(alias.lower())
            if coll:
                return coll

    for part in parts:
        coll = index.get(part)
        if coll:
            return coll

    return None


def _filed_by_addon(obj):
    """
    Whether obj sits in a collection another add-on keeps for itself.

    A generator (cables, scatter, fracture) files what it makes in a collection
    of its own and finds it there again on the next rebuild. Routed out of it,
    the generator no longer sees its output: it builds a fresh set and leaves
    the routed one behind, so every rebuild multiplied the objects (Conduit's
    Wall to Floor cables piled up in WALLS, Nov 2026). Such a collection carries
    the add-on's bookkeeping as custom properties; a collection the user made,
    or one Arrange Scene made, carries none.
    """
    return any(len(c.keys()) for c in obj.users_collection)


def _where_new_objects_land(scene):
    """The collections Blender adds a new object to: each view layer's active
    collection, and the scene's own."""
    homes = {scene.collection.as_pointer()}
    for layer in scene.view_layers:
        active = layer.active_layer_collection
        if active is not None:
            homes.add(active.collection.as_pointer())
    return homes


def _find_layer_collection(root_lc, coll):
    """Recursively find the LayerCollection wrapping a given Collection."""
    if root_lc.collection == coll:
        return root_lc
    for child in root_lc.children:
        result = _find_layer_collection(child, coll)
        if result:
            return result
    return None


# ---------------------------------------------------------------------------
# Auto-route handler — silently moves newly added objects to matching
# collections without the user having to run Arrange Scene manually.
# ---------------------------------------------------------------------------

def _prefs():
    """Addon preferences, or None if the addon isn't registered as expected."""
    try:
        return bpy.context.preferences.addons[__name__].preferences
    except (KeyError, AttributeError):
        return None


@bpy.app.handlers.persistent
def _reset_route_snapshot(*_args):
    """
    Forget which objects auto-route has seen. Runs after load, undo and redo.

    'New' only means 'not in the last snapshot'. A file opened afterwards
    usually has a scene called 'Scene' too, so its objects were compared against
    the previous file's names and silently moved on the first click; an object
    brought back by Ctrl+Z was re-routed the same way. Clearing the snapshot
    makes the next depsgraph tick re-seed it instead of routing.
    """
    global _route_pending
    _known_objects.clear()
    _route_pending = False


@bpy.app.handlers.persistent
def _reset_session_state(*_args):
    """
    Drop everything held for the previous file. Runs after load only.

    Isolation, preview shading, Face Stretch and visibility bookmarks are all
    keyed by names or pointers from the file they were made in; carried into
    the next file they 'restore' state onto unrelated collections and meshes.
    """
    _reset_route_snapshot()
    _isolation_state.clear()
    _shading_state.clear()
    _stretch_state.clear()
    _vis_bookmarks.clear()


@bpy.app.handlers.persistent
def _auto_route_new_objects(scene, depsgraph):
    # This fires on *every* depsgraph update — dragging a vertex, scrubbing the
    # timeline, every click. It must stay near-free in the common case, so the
    # first gate is an integer compare and nothing else.
    global _route_pending

    known = _known_objects.get(scene.name)
    count = len(scene.objects)

    if known is None:
        _known_objects[scene.name] = {obj.name for obj in scene.objects}
        return

    if count <= len(known):
        # Objects were deleted or nothing changed. Resync only on shrink, which
        # is rare, so the steady state costs one len() call.
        if count < len(known):
            _known_objects[scene.name] = {obj.name for obj in scene.objects}
        return

    prefs = _prefs()
    if prefs is not None and not prefs.auto_route:
        _known_objects[scene.name] = {obj.name for obj in scene.objects}
        return

    current   = {obj.name for obj in scene.objects}
    new_names = current - known
    _known_objects[scene.name] = current

    if not new_names or _route_pending:
        return

    scene_name    = scene.name
    _route_pending = True

    def _route():
        # Runs outside the depsgraph callback: relinking collections from inside
        # the handler itself re-triggers the handler and can recurse.
        global _route_pending
        _route_pending = False
        s = bpy.data.scenes.get(scene_name)
        if s is None:
            return
        index = _build_route_index(s)
        landing = _where_new_objects_land(s)
        for name in new_names:
            obj = s.objects.get(name)
            if obj is None:
                continue
            current_colls = list(obj.users_collection)
            if len(current_colls) != 1:
                continue
            # Only what was added the way a user adds things: into the active
            # collection or the scene's. An object an add-on put somewhere on
            # purpose stays there (see _filed_by_addon).
            if (current_colls[0].as_pointer() not in landing
                    or _filed_by_addon(obj)):
                continue
            target = _find_target_collection(obj, index, s)
            if target is None or current_colls[0] == target:
                continue
            try:
                _move_object(obj, target, s)
            except RuntimeError:
                # One object refusing (linked data, say) must not abort the
                # rest of the batch from inside a timer nobody sees fail.
                continue

    bpy.app.timers.register(_route, first_interval=0.0)


# ---------------------------------------------------------------------------
# Viewport shading preview state
#
# Silhouette and Cavity both hijack the same shading properties. Storing one
# global blob meant turning on Cavity while Silhouette was active saved the
# *black* silhouette settings as the "original", so restoring left the viewport
# stuck. State is now per-viewport and the two modes are mutually exclusive.
# ---------------------------------------------------------------------------

_SHADING_PROPS = (
    'type', 'light', 'color_type', 'single_color',
    'show_cavity', 'cavity_type',
    'cavity_ridge_factor', 'cavity_valley_factor',
)


def _save_shading(shading):
    saved = {}
    for prop in _SHADING_PROPS:
        value = getattr(shading, prop, None)
        saved[prop] = tuple(value) if prop == 'single_color' else value
    return saved


def _apply_shading(shading, values):
    for prop, value in values.items():
        try:
            setattr(shading, prop, value)
        except (AttributeError, TypeError):
            pass


def _active_preview(space):
    """Name of the preview mode active on this viewport, or None."""
    entry = _shading_state.get(space.as_pointer()) if space else None
    return entry[0] if entry else None


def _iter_view3d_spaces():
    """Every open 3D viewport across every window."""
    try:
        windows = bpy.context.window_manager.windows
    except AttributeError:
        return
    for window in windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                yield area.spaces.active


def _restore_preview_mode(mode):
    """Undo a named preview in every viewport showing it. Returns the count."""
    restored = 0
    for space in _iter_view3d_spaces():
        ptr = space.as_pointer()
        entry = _shading_state.get(ptr)
        if entry and entry[0] == mode:
            _apply_shading(space.shading, entry[1])
            del _shading_state[ptr]
            restored += 1
    # Anything left under this mode belongs to a viewport that has since been
    # closed; drop it so a stale entry cannot block the toggle later.
    for ptr in [p for p, e in _shading_state.items() if e[0] == mode]:
        del _shading_state[ptr]
    return restored


def _clear_stretch_attributes():
    """Drop the face-stretch colour attribute from every mesh carrying it."""
    if not _stretch_state:
        return 0
    return ET_OT_FaceStretchAnalyzer.restore_attributes()


def _resolve_view3d(space):
    """
    The 3D viewport a preview should act on.

    context.space_data is None in background mode, and is some other editor
    when an operator is reached from a menu outside the 3D view, so fall back
    to the first open viewport rather than dereferencing None.
    """
    if space is not None and getattr(space, 'type', None) == 'VIEW_3D':
        return space
    return next(_iter_view3d_spaces(), None)


def _toggle_shading_preview(space, mode, settings):
    """Toggle a named preview on one viewport. Returns True if it is now on."""
    space = _resolve_view3d(space)
    if space is None:
        return True

    ptr   = space.as_pointer()
    entry = _shading_state.pop(ptr, None)

    if entry is not None:
        _apply_shading(space.shading, entry[1])
        if entry[0] == mode:
            return False
        if entry[0] == 'STRETCH':
            # Another preview is taking this viewport over. Without this the
            # colour attributes would linger and the panel would keep claiming
            # Face Stretch was on while the viewport showed something else.
            _clear_stretch_attributes()

    _shading_state[ptr] = (mode, _save_shading(space.shading))
    _apply_shading(space.shading, settings)
    return True


# ---------------------------------------------------------------------------
# Keymap helper
# ---------------------------------------------------------------------------

def find_user_keyconfig(key):
    entry = addon_keymaps.get(key)
    if entry is None:
        return None
    km, kmi = entry
    user_km = bpy.context.window_manager.keyconfigs.user.keymaps.get(km.name)
    if user_km is None:
        return kmi
    for item in user_km.keymap_items:
        found = False
        if kmi.idname == item.idname:
            found = True
            for name in dir(kmi.properties):
                if not name.startswith('_') and name not in ('bl_rna', 'rna_type'):
                    if (name in kmi.properties and name in item.properties
                            and kmi.properties[name] != item.properties[name]):
                        found = False
        if found:
            return item
    # No print here: this runs from a panel draw, so a miss would spam the
    # console once per redraw.
    return kmi


# ---------------------------------------------------------------------------
# Header / panel injections
# ---------------------------------------------------------------------------

# Named icons, not icon_value integers: the numeric IDs shift between Blender
# releases, so the old hardcoded values drew the wrong glyphs (or none).
def draw_header_add_menu(self, context):
    if context.mode != 'OBJECT':
        return
    layout = self.layout
    layout.separator()
    layout.label(text='Add New')
    layout.operator('mesh.primitive_plane_add',     text='', icon='MESH_PLANE')
    layout.operator('mesh.primitive_cube_add',      text='', icon='MESH_CUBE')
    layout.operator('mesh.primitive_circle_add',    text='', icon='MESH_CIRCLE')
    layout.operator('mesh.primitive_uv_sphere_add', text='', icon='MESH_UVSPHERE')
    layout.operator('mesh.primitive_cylinder_add',  text='', icon='MESH_CYLINDER')
    layout.operator('object.text_add',              text='', icon='OUTLINER_OB_FONT')
    layout.operator('object.empty_add',             text='', icon='OUTLINER_OB_EMPTY')


def draw_modifier_panel_buttons(self, context):
    row = self.layout.box().row()
    row.operator('et.apply_modifiers', text='Apply Modifiers', icon='CHECKMARK')
    row.operator('et.clear_modifiers', text='Clear Modifiers', icon='TRASH')


# ---------------------------------------------------------------------------
# Modifier operators
# ---------------------------------------------------------------------------

class ET_OT_ApplyModifiers(bpy.types.Operator):
    bl_idname  = "et.apply_modifiers"
    bl_label   = "Apply Modifiers"
    bl_description = "Apply all modifiers on the active object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and bool(context.active_object.modifiers)

    def execute(self, context):
        obj = context.active_object
        if obj.data is not None and obj.data.users > 1:
            self.report({'ERROR'},
                        f"'{obj.name}' has multi-user data — make it single-user first")
            return {'CANCELLED'}

        applied = 0
        for name in [m.name for m in obj.modifiers]:
            try:
                bpy.ops.object.modifier_apply(modifier=name)
                applied += 1
            except RuntimeError as exc:
                self.report({'WARNING'}, f"{name}: {exc}")

        self.report({'INFO'}, f"Applied {applied} modifier(s) on '{obj.name}'")
        return {"FINISHED"}


class ET_OT_ClearModifiers(bpy.types.Operator):
    bl_idname  = "et.clear_modifiers"
    bl_label   = "Clear Modifiers"
    bl_description = "Remove all modifiers from the active object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        context.active_object.modifiers.clear()
        return {"FINISHED"}


class ET_OT_AddModifier(bpy.types.Operator):
    bl_idname  = "et.add_modifier"
    bl_label   = "Add Modifier"
    bl_description = "Add a modifier to the active object"
    bl_options = {"REGISTER", "UNDO"}

    modifier_type: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        context.active_object.modifiers.new(name=self.modifier_type, type=self.modifier_type)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Quick Mirror
# ---------------------------------------------------------------------------
#
# Select the part, then the object to mirror it across (that one is active),
# and press X, Y or Z: the part gets a Mirror modifier whose Mirror Object is
# the pivot. Nothing is applied and no origin moves, so the copy follows when
# either object is moved, and pressing the same axis again takes it off.
#
# The pivot is one of three:
#   ACTIVE  the active object itself: its origin and its own axes.
#   WORLD   a helper empty at the active object's origin, on world axes, and
#           parented to it so it follows. For a rotated body whose own axes
#           are not the ones to mirror on.
#   CURSOR  a helper empty at the 3D cursor, turned with it.
# A Mirror modifier mirrors around an object, never a bare point, so the two
# helper pivots are real empties. They stay in the scene, small and named
# '<part>_MirrorPivot', so moving one moves the mirror; one that no Mirror
# uses any more is removed.

_MIRROR_AXES   = ('X', 'Y', 'Z')
_MIRROR_PIVOTS = (
    ('ACTIVE', 'Active',        "Mirror across the active object's origin, on its own axes",
     'OBJECT_DATA'),
    ('WORLD',  'World Axes',    "Mirror across the active object's origin, on world axes "
                                "(a helper empty that follows the active object)",
     'ORIENTATION_GLOBAL'),
    ('CURSOR', '3D Cursor',     "Mirror across the 3D cursor (a helper empty left at the "
                                "cursor, which can be moved afterwards)",
     'PIVOT_CURSOR'),
)
_MIRROR_PIVOT_ICON = {key: icon for key, _l, _d, icon in _MIRROR_PIVOTS}
# Custom property on a helper empty: which pivot it stands for.
_MIRROR_HELPER_PROP = "et_mirror_pivot"
# Modifiers that belong after a mirror: they read the finished shape.
_MIRROR_FINISHERS = {'WEIGHTED_NORMAL', 'TRIANGULATE'}
# Object types a Mirror modifier can go on.
_MIRRORABLE = {'MESH', 'CURVE', 'SURFACE', 'FONT'}


def _mirror_targets(context):
    """(targets, active): what gets the modifier, and the pivot object.

    With two or more selected, every selected object but the active one is a
    target. With only the active one selected it is its own target, which
    only makes sense around the 3D cursor.
    """
    active = context.active_object
    if active is None:
        return [], None
    others = [o for o in context.selected_objects
              if o != active and o.type in _MIRRORABLE]
    if others:
        return others, active
    if active.type in _MIRRORABLE and active.select_get():
        return [active], active
    return [], active


def _is_mirror_helper(obj, pivot=None):
    if obj is None or obj.type != 'EMPTY':
        return False
    kind = obj.get(_MIRROR_HELPER_PROP)
    return kind is not None and (pivot is None or kind == pivot)


def _cursor_matrix(scene):
    # cursor.matrix already follows whichever rotation mode the cursor uses.
    return mathutils.Matrix(scene.cursor.matrix).normalized()


def _helper_fits(helper, pivot, active, scene):
    """Is `helper` still the pivot this click asks for?"""
    if not _is_mirror_helper(helper, pivot):
        return False
    if pivot == 'WORLD':
        return helper.parent == active
    if pivot == 'CURSOR':
        here = helper.matrix_world.translation
        return (here - scene.cursor.location).length < 1e-4
    return False


def _mirror_modifier_for(target, pivot, active, scene):
    """The Mirror on `target` that already mirrors across this pivot, or None."""
    for mod in target.modifiers:
        if mod.type != 'MIRROR':
            continue
        if pivot == 'ACTIVE':
            if mod.mirror_object == active and active != target:
                return mod
        elif _helper_fits(mod.mirror_object, pivot, active, scene):
            return mod
    return None


def _pivot_matrix(pivot, active, scene):
    """World matrix of the pivot, rotation and location only."""
    if pivot == 'CURSOR':
        return _cursor_matrix(scene)
    loc = active.matrix_world.translation
    if pivot == 'WORLD':
        return mathutils.Matrix.Translation(loc)
    rot = active.matrix_world.to_quaternion().to_matrix().to_4x4()
    return mathutils.Matrix.Translation(loc) @ rot


def _suggested_mirror_axis(target, pivot, active, scene):
    """The axis the target sits furthest along, seen from the pivot: the one
    whose mirror lands on the far side rather than overlapping the part."""
    if target is None or active is None:
        return None
    corners = [target.matrix_world @ mathutils.Vector(c) for c in target.bound_box]
    centre = sum(corners, mathutils.Vector()) / len(corners)
    local = _pivot_matrix(pivot, active, scene).inverted() @ centre
    offs = [abs(v) for v in local]
    if max(offs) < 1e-6:
        return None
    return _MIRROR_AXES[offs.index(max(offs))]


def _new_mirror_helper(pivot, active, target, scene):
    name = f"{target.name}_MirrorPivot"
    helper = bpy.data.objects.new(name, None)
    helper.empty_display_type = 'PLAIN_AXES'
    size = max(max(target.dimensions), 0.1) * 0.5
    helper.empty_display_size = size
    helper[_MIRROR_HELPER_PROP] = pivot
    # Beside the part it mirrors, so it is found, hidden and moved with it.
    colls = [c for c in target.users_collection if _editable(c)]
    (colls[0] if colls else scene.collection).objects.link(helper)
    helper.matrix_world = _pivot_matrix(pivot, active, scene)
    if pivot == 'WORLD':
        world = helper.matrix_world.copy()
        helper.parent = active
        helper.matrix_parent_inverse = active.matrix_world.inverted()
        helper.matrix_world = world
    return helper


def _move_modifier(obj, mod, index):
    """Move `mod` to `index` in obj's stack, on every supported Blender."""
    current = list(obj.modifiers).index(mod)
    if current == index:
        return
    if hasattr(obj.modifiers, 'move'):
        obj.modifiers.move(current, index)
        return
    override = {'object': obj, 'active_object': obj}
    if hasattr(bpy.context, 'temp_override'):
        with bpy.context.temp_override(**override):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=index)
    else:
        bpy.ops.object.modifier_move_to_index(override, modifier=mod.name, index=index)


def _place_mirror(obj, mod):
    """Last in the stack, but before Weighted Normal / Triangulate, which
    should see the mirrored shape."""
    mods = list(obj.modifiers)
    index = len(mods) - 1
    while index > 0 and mods[index - 1].type in _MIRROR_FINISHERS:
        index -= 1
    _move_modifier(obj, mod, index)


def _drop_unused_helpers(candidates):
    """Remove helper empties no Mirror modifier points at any more."""
    used = {mod.mirror_object for obj in bpy.data.objects
            for mod in obj.modifiers if mod.type == 'MIRROR'}
    for helper in candidates:
        if helper is not None and _is_mirror_helper(helper) and helper not in used:
            bpy.data.objects.remove(helper)


def _mirror_axes_on(context, pivot):
    """{'X': bool, ...} for the first target's Mirror across this pivot."""
    targets, active = _mirror_targets(context)
    if not targets:
        return {}
    mod = _mirror_modifier_for(targets[0], pivot, active, context.scene)
    if mod is None:
        return {}
    return {a: bool(mod.use_axis[i]) for i, a in enumerate(_MIRROR_AXES)}


def mirror_toggle(context, axis, pivot):
    """Turn `axis` on or off on every target's Mirror across `pivot`.

    Returns (turned_on: bool | None, changed targets). On is decided once,
    from the first target, so a mixed selection all ends up the same way.
    """
    scene = context.scene
    targets, active = _mirror_targets(context)
    if pivot == 'ACTIVE':
        targets = [t for t in targets if t != active]
    if not targets:
        return None, []
    i = _MIRROR_AXES.index(axis)
    first = _mirror_modifier_for(targets[0], pivot, active, scene)
    turn_on = first is None or not first.use_axis[i]

    shared_helper = None
    dropped = []
    changed = []
    for target in targets:
        mod = _mirror_modifier_for(target, pivot, active, scene)
        if mod is None:
            if not turn_on:
                continue
            if pivot == 'ACTIVE':
                mirror_obj = active
            else:
                if shared_helper is None:
                    shared_helper = _new_mirror_helper(pivot, active, target, scene)
                mirror_obj = shared_helper
            try:
                mod = target.modifiers.new(name="Mirror", type='MIRROR')
            except (TypeError, RuntimeError):
                mod = None
            if mod is None:
                continue
            mod.use_axis = (False, False, False)
            mod.mirror_object = mirror_obj
            # Separate parts: nothing on the mirror plane should weld or clip.
            mod.use_mirror_merge = False
            mod.use_clip = False
            _place_mirror(target, mod)
        mod.use_axis[i] = turn_on
        if not any(mod.use_axis):
            dropped.append(mod.mirror_object)
            target.modifiers.remove(mod)
        changed.append(target)
    if dropped:
        _drop_unused_helpers(set(dropped))
    return turn_on, changed


class ET_OT_QuickMirror(bpy.types.Operator):
    bl_idname  = "et.quick_mirror"
    bl_label   = "Quick Mirror"
    bl_description = ("Mirror the selected objects across the active one (or the "
                      "3D cursor) with a Mirror modifier. Press the same axis "
                      "again to take it off")
    bl_options = {"REGISTER", "UNDO"}

    axis: bpy.props.EnumProperty(
        name="Axis",
        items=[(a, a, f"Mirror on {a}") for a in _MIRROR_AXES],
        default='X')
    pivot: bpy.props.EnumProperty(
        name="Pivot",
        items=[(k, l, d, ic, n) for n, (k, l, d, ic) in enumerate(_MIRROR_PIVOTS)],
        default='ACTIVE')

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            return False
        targets, _active = _mirror_targets(context)
        return bool(targets)

    def execute(self, context):
        targets, active = _mirror_targets(context)
        if self.pivot == 'ACTIVE' and targets == [active]:
            self.report({'WARNING'},
                        "Select the part first, then the object to mirror it across")
            return {'CANCELLED'}
        turned_on, changed = mirror_toggle(context, self.axis, self.pivot)
        if not changed:
            self.report({'WARNING'}, "Nothing to mirror")
            return {'CANCELLED'}
        what = (f"'{changed[0].name}'" if len(changed) == 1
                else f"{len(changed)} objects")
        if turned_on:
            around = {'ACTIVE': f"'{active.name}'",
                      'WORLD':  f"'{active.name}' on world axes",
                      'CURSOR': "the 3D cursor"}[self.pivot]
            self.report({'INFO'}, f"Mirrored {what} on {self.axis} across {around}")
        else:
            self.report({'INFO'}, f"Took {self.axis} off the mirror of {what}")
        return {"FINISHED"}


def draw_quick_mirror(layout, context):
    """Pivot choice and the three axis buttons, lit where an axis is on."""
    scene = context.scene
    pivot = scene.et_mirror_pivot
    row = layout.row(align=True)
    row.prop(scene, 'et_mirror_pivot', expand=True)
    on = _mirror_axes_on(context, pivot) if context.mode == 'OBJECT' else {}
    row = layout.row(align=True)
    row.scale_y = 1.2
    for axis in _MIRROR_AXES:
        op = row.operator('et.quick_mirror', text=axis, depress=on.get(axis, False))
        op.axis = axis
        op.pivot = pivot


class ET_MT_QuickMirrorMenu(bpy.types.Menu):
    bl_idname = "ET_MT_quick_mirror_menu"
    bl_label  = "Quick Mirror"

    def draw(self, context):
        layout = self.layout
        layout.label(text='Part first, then the object to mirror across',
                     icon='INFO')
        layout.separator()
        draw_quick_mirror(layout, context)


# --- floating buttons in the 3D view ---------------------------------------

_QM_BACK   = (0.18, 0.18, 0.2)
_QM_HINT   = (0.34, 0.34, 0.38)
_QM_LIT    = (0.85, 0.5, 0.12)
_QM_AMBER  = (1.0, 0.62, 0.15)
_QM_SIZE   = 15.0       # button radius in pixels, before UI scale
_QM_GAP    = 6.0
_QM_BELOW  = 70.0       # how far under the active object's origin the row sits


def _quick_mirror_buttons_on():
    prefs = _prefs()
    return prefs is None or getattr(prefs, 'quick_mirror_buttons', True)


def _transforming(context):
    """A move, rotate or scale running in this window."""
    win = getattr(context, 'window', None)
    for op in (getattr(win, 'modal_operators', None) or ()):
        if getattr(op, 'bl_idname', '').startswith('TRANSFORM_OT'):
            return True
    return False


def _quick_mirror_row_origin(context, count, scale):
    """Left button centre of a row of `count` buttons under the active object,
    kept inside the view and clear of the sidebar, toolbar and headers."""
    from bpy_extras import view3d_utils
    region, rv3d = context.region, context.region_data
    obj = context.active_object
    p = view3d_utils.location_3d_to_region_2d(region, rv3d,
                                              obj.matrix_world.translation)
    if p is None:
        return None
    r = _QM_SIZE * scale
    step = 2 * r + _QM_GAP * scale
    width = step * (count - 1)
    x0, y0, x1, y1 = r + 4, r + 4, region.width - r - 4, region.height - r - 4
    area = context.area
    if area is not None:
        for reg in area.regions:
            if reg.width <= 1 or reg.height <= 1:
                continue
            left = reg.x - region.x
            bottom = reg.y - region.y
            if reg.type == 'UI' and left > region.width / 2:
                x1 = min(x1, left - r - 4)
            elif reg.type == 'TOOLS' and left < region.width / 2:
                x0 = max(x0, left + reg.width + r + 4)
            elif reg.type in {'HEADER', 'TOOL_HEADER'}:
                if bottom > region.height / 2:
                    y1 = min(y1, bottom - r - 4)
                else:
                    y0 = max(y0, bottom + reg.height + r + 4)
    x = p.x - width / 2
    y = p.y - _QM_BELOW * scale
    x = min(max(x, x0), max(x0, x1 - width))
    y = min(max(y, y0), max(y0, y1))
    return x, y, step


class ET_GGT_QuickMirror(bpy.types.GizmoGroup):
    """Pivot + X / Y / Z under the active object while two or more objects
    are selected. The pivot button cycles Active / World Axes / 3D Cursor."""
    bl_idname      = "ET_GGT_quick_mirror"
    bl_label       = "Easy Tasks Quick Mirror"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options     = {'PERSISTENT', 'SCALE'}

    @classmethod
    def poll(cls, context):
        if not _quick_mirror_buttons_on() or context.mode != 'OBJECT':
            return False
        space = context.space_data
        if space is None or not getattr(space, 'show_gizmo', True):
            return False
        obj = context.active_object
        if obj is None or not obj.select_get():
            return False
        if len(context.selected_objects) < 2 or _transforming(context):
            return False
        targets, _active = _mirror_targets(context)
        return bool(targets) and targets != [obj]

    def _button(self, icon):
        gz = self.gizmos.new('GIZMO_GT_button_2d')
        gz.icon = icon
        gz.draw_options = {'BACKDROP', 'OUTLINE'}
        gz.color = _QM_BACK
        gz.color_highlight = _QM_AMBER
        gz.alpha = gz.alpha_highlight = 0.9
        gz.scale_basis = _QM_SIZE
        return gz

    def setup(self, context):
        # One pivot button per pivot, only the current one shown: a
        # button_2d reads its icon once and does not redraw a changed one.
        self.pivots = {}
        for key, _l, _d, icon in _MIRROR_PIVOTS:
            gz = self._button(icon)
            props = gz.target_set_operator('wm.context_cycle_enum')
            props.data_path = 'scene.et_mirror_pivot'
            props.wrap = True
            self.pivots[key] = gz
        self.axes = {}
        for axis in _MIRROR_AXES:
            gz = self._button(f'EVENT_{axis}')
            self.axes[axis] = gz
        self._pivot = None

    def draw_prepare(self, context):
        if not hasattr(self, 'axes'):
            return
        scene = context.scene
        pivot = scene.et_mirror_pivot
        if pivot != self._pivot:
            for axis, gz in self.axes.items():
                props = gz.target_set_operator('et.quick_mirror')
                props.axis = axis
                props.pivot = pivot
            self._pivot = pivot

        scale = context.preferences.system.ui_scale
        spot = _quick_mirror_row_origin(context, 1 + len(self.axes), scale)
        every = list(self.pivots.values()) + list(self.axes.values())
        if spot is None:
            for gz in every:
                gz.hide = True
            return
        x, y, step = spot

        for key, gz in self.pivots.items():
            gz.hide = key != pivot
            gz.matrix_basis = mathutils.Matrix.Translation((x, y, 0.0))

        targets, active = _mirror_targets(context)
        on = _mirror_axes_on(context, pivot)
        hint = (None if any(on.values()) else
                _suggested_mirror_axis(targets[0] if targets else None,
                                       pivot, active, scene))
        for n, axis in enumerate(_MIRROR_AXES, start=1):
            gz = self.axes[axis]
            gz.hide = False
            gz.color = (_QM_LIT if on.get(axis) else
                        _QM_HINT if axis == hint else _QM_BACK)
            gz.matrix_basis = mathutils.Matrix.Translation((x + n * step, y, 0.0))


# ---------------------------------------------------------------------------
# Batch export
# ---------------------------------------------------------------------------

def _export_objects(selection, basedir, fmt):
    """Export each object to its own file. fmt = 'fbx', 'obj', or 'glb'."""
    view_layer = bpy.context.view_layer
    original_active = view_layer.objects.active
    bpy.ops.object.select_all(action='DESELECT')
    for obj in selection:
        obj.select_set(True)
        view_layer.objects.active = obj
        filepath = os.path.join(basedir, bpy.path.clean_name(obj.name))
        if fmt == 'fbx':
            bpy.ops.export_scene.fbx(filepath=filepath + ".fbx", use_selection=True)
        elif fmt == 'glb':
            bpy.ops.export_scene.gltf(
                filepath=filepath + ".glb",
                use_selection=True,
                export_format='GLB',
            )
        else:
            if bpy.app.version >= (3, 3, 0):
                bpy.ops.wm.obj_export(filepath=filepath + ".obj", export_selected_objects=True)
            else:
                bpy.ops.export_scene.obj(filepath=filepath + ".obj", use_selection=True)
        print("Written:", filepath)
        obj.select_set(False)
    view_layer.objects.active = original_active
    for obj in selection:
        obj.select_set(True)


class ET_OT_BatchExport(bpy.types.Operator):
    bl_idname  = "et.batch_export"
    bl_label   = "Batch Export Tool"
    bl_description = "Export selected objects individually to FBX / OBJ / GLB with optional prefix/suffix"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = 'prefix'

    name:           bpy.props.StringProperty(name='Name',       default='')
    prefix:         bpy.props.StringProperty(name='Prefix',     default='')
    suffix:         bpy.props.StringProperty(name='Suffix',     default='')
    export_fbx:     bpy.props.BoolProperty(name='Export FBX',   default=True)
    export_obj:     bpy.props.BoolProperty(name='Export OBJ',   default=True)
    export_glb:     bpy.props.BoolProperty(name='Export GLB',   default=False)
    apply_scale:    bpy.props.BoolProperty(name='Scale',        default=True)
    apply_rotation: bpy.props.BoolProperty(name='Rotation',     default=False)
    apply_location: bpy.props.BoolProperty(name='Location',     default=True)

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        for obj in context.view_layer.objects.selected:
            sep_pre = '_' if self.prefix else ''
            sep_suf = '_' if self.suffix else ''
            base    = self.name if self.name else obj.name
            obj.name = f"{self.prefix}{sep_pre}{base}{sep_suf}{self.suffix}"

        # One call with every flag explicit: transform_apply defaults all three
        # to True, so transform_apply(scale=True) also baked rotation and
        # location and the Rotation / Location checkboxes did nothing.
        if self.apply_scale or self.apply_rotation or self.apply_location:
            bpy.ops.object.transform_apply(location=self.apply_location,
                                           rotation=self.apply_rotation,
                                           scale=self.apply_scale)

        if self.export_fbx or self.export_obj or self.export_glb:
            basedir = os.path.dirname(bpy.data.filepath)
            if not basedir:
                self.report({'ERROR'}, "Save the blend file before exporting!")
                return {"CANCELLED"}
            selection = list(context.selected_objects)
            if self.export_fbx:
                _export_objects(selection, basedir, 'fbx')
            if self.export_obj:
                _export_objects(selection, basedir, 'obj')
            if self.export_glb:
                _export_objects(selection, basedir, 'glb')

        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text='Apply Transformation')
        row = box.row(align=True)
        row.prop(self, 'apply_scale')
        row.prop(self, 'apply_rotation')
        row.prop(self, 'apply_location')

        row = layout.row()
        col = row.box().column()
        col.label(text='Export Format:')
        col.prop(self, 'export_fbx')
        col.prop(self, 'export_obj')
        col.prop(self, 'export_glb')

        col = row.box().column()
        col.prop(self, 'name')
        col.prop(self, 'prefix')
        col.prop(self, 'suffix')

        layout.box().label(text='Save your file before exporting!', icon='ERROR')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)


# ---------------------------------------------------------------------------
# Interaction Mode Pie  (Shift+Alt+X)
# ---------------------------------------------------------------------------

class ET_MT_InteractionModePie(bpy.types.Menu):
    bl_idname = "ET_MT_interaction_mode_pie"
    bl_label  = "Interaction Mode"

    def draw(self, context):
        pie = self.layout.menu_pie()

        box = pie.box()
        col = box.box().column()
        col.operator('mesh.select_mode', text='Vertex', icon='VERTEXSEL').type = 'VERT'
        col.operator('mesh.select_mode', text='Edge',   icon='EDGESEL').type  = 'EDGE'
        col.operator('mesh.select_mode', text='Face',   icon='FACESEL').type  = 'FACE'

        box = pie.box()
        col = box.box().column()
        col.operator('object.mode_set', text='Object Mode',  icon='OBJECT_DATAMODE').mode  = 'OBJECT'
        col.operator('object.mode_set', text='Edit Mode',    icon='EDITMODE_HLT').mode     = 'EDIT'
        col.operator('object.mode_set', text='Sculpt Mode',  icon='SCULPTMODE_HLT').mode   = 'SCULPT'


# ---------------------------------------------------------------------------
# Menus (Modifiers, UV/Sharp, Asset, Link/Transfer, FavTools)
# ---------------------------------------------------------------------------

def _transform_apply_buttons(layout, texts):
    """
    Apply Scale / Rotation / Location buttons, labelled with `texts` in that order.

    object.transform_apply defaults location, rotation and scale all to True and
    remembers whatever was used last, so a button that only sets its own flag
    applies all three — 'Apply Scale' also zeroed rotation and moved the origin
    to the world origin. Blender's own Object > Apply menu sets every flag for
    the same reason, and so does this.
    """
    for text, flags in zip(texts, ((False, False, True),
                                   (False, True,  False),
                                   (True,  False, False))):
        op = layout.operator('object.transform_apply', text=text)
        op.location, op.rotation, op.scale = flags


class ET_MT_ModifiersMenu(bpy.types.Menu):
    bl_idname = "ET_MT_modifiers_menu"
    bl_label  = "Modifiers"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        layout.operator('object.modifier_add', text='Add Modifier', icon='MODIFIER')
        layout.separator()
        layout.label(text='FAVORITE', icon='SOLO_ON')
        layout.separator()
        MODS = [
            ('ARRAY',           'Array',               'MOD_ARRAY'),
            ('BEVEL',           'Bevel',               'MOD_BEVEL'),
            ('MIRROR',          'Mirror',              'MOD_MIRROR'),
            ('EDGE_SPLIT',      'Edge Split',          'MOD_EDGESPLIT'),
            ('SOLIDIFY',        'Solidify',            'MOD_SOLIDIFY'),
            ('WEIGHTED_NORMAL', 'Weighted Normal',     'MOD_NORMALEDIT'),
            ('SUBSURF',         'Subdivision Surface', 'MOD_SUBSURF'),
            ('SHRINKWRAP',      'Shrinkwrap',          'MOD_SHRINKWRAP'),
        ]
        for mod_type, label, icon in MODS:
            layout.operator('et.add_modifier', text=label, icon=icon).modifier_type = mod_type
        layout.separator()
        layout.operator('et.clear_modifiers', text='Clear Modifiers', icon='X')


class ET_MT_UVSharpMenu(bpy.types.Menu):
    bl_idname = "ET_MT_uv_sharp_menu"
    bl_label  = "UVs | Sharp Edge"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        layout.label(text='Seams', icon='UV')
        layout.separator()
        layout.operator('mesh.mark_seam', text='Mark Seam', icon='UV_SYNC_SELECT')
        layout.operator('mesh.mark_seam', text='Clear Seam', icon='X').clear = True
        layout.separator()
        layout.label(text='Sharp Edges', icon='NORMALS_FACE')
        layout.separator()
        layout.operator('mesh.mark_sharp', text='Mark Sharp', icon='SHARPCURVE')
        layout.operator('mesh.mark_sharp', text='Clear Sharp', icon='X').clear = True


class ET_MT_AssetMenu(bpy.types.Menu):
    bl_idname = "ET_MT_asset_menu"
    bl_label  = "Asset"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        layout.operator('asset.mark',  text='Mark as Asset', icon='ASSET_MANAGER')
        layout.operator('asset.clear', text='Clear Asset',   icon='X')


class ET_MT_LinkTransferMenu(bpy.types.Menu):
    bl_idname = "ET_MT_link_transfer_menu"
    bl_label  = "Link / Transfer Data"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        layout.operator('object.make_links_data', text='Link Object Data', icon='MESH_DATA').type  = 'OBDATA'
        layout.operator('object.make_links_data', text='Link Material',    icon='MATERIAL').type   = 'MATERIAL'
        layout.separator()
        layout.operator('object.make_links_data', text='Copy Modifiers',   icon='MODIFIER').type   = 'MODIFIERS'
        layout.operator('object.join_uvs',         text='Copy UV Maps',    icon='UV')


class ET_MT_FavToolsMenu(bpy.types.Menu):
    bl_idname = "ET_MT_fav_tools_menu"
    bl_label  = "FavTools"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        layout.label(text='FavTools', icon='SOLO_ON')
        layout.separator()
        layout.menu('ET_MT_modifiers_menu', text='Modifiers', icon='MODIFIER')
        layout.menu('ET_MT_quick_mirror_menu', text='Quick Mirror', icon='MOD_MIRROR')
        layout.separator()
        layout.operator('et.batch_export', text='Batch Export', icon='EXPORT')
        layout.separator()
        layout.menu('ET_MT_uv_sharp_menu', text='UVs | Sharp Edge', icon='UV')
        layout.separator()
        _transform_apply_buttons(layout, ('Apply Scale', 'Apply Rotation',
                                          'Apply Location'))
        layout.separator()
        layout.operator('export_scene.fbx',  text='Export FBX', icon='EXPORT').use_selection = True
        layout.operator('wm.obj_export',     text='Export OBJ', icon='EXPORT')
        layout.operator('export_scene.gltf', text='Export GLB', icon='EXPORT')
        layout.operator('wm.append',         text='Append',     icon='APPEND_BLEND')
        layout.separator()
        layout.menu('ET_MT_origin_menu', text='Origin Tools', icon='OBJECT_ORIGIN')
        layout.separator()
        layout.menu('ET_MT_asset_menu',         text='Asset',              icon='ASSET_MANAGER')
        layout.separator()
        layout.menu('ET_MT_link_transfer_menu', text='Link/Transfer Data', icon='LINKED')
        layout.separator()
        layout.operator('et.scene_snapshot',        text='Scene Snapshot',        icon='FILE_TICK')
        layout.operator('et.scene_stats',           text='Scene Statistics',      icon='INFO')
        layout.separator()
        layout.operator('et.generate_lods',         text='Generate LODs',         icon='MOD_DECIM')
        layout.operator('et.megascans_material',    text='Megascans Material',    icon='NODE_MATERIAL')
        layout.operator('et.blend_materials',       text='Blend Materials',       icon='MOD_MASK')
        layout.operator('et.paint_blend_mask',      text='Paint Blend Mask',      icon='BRUSH_DATA')
        layout.operator('et.consolidate_materials', text='Consolidate Materials', icon='MATERIAL')
        layout.operator('et.place_reference_image', text='Place Reference Image', icon='IMAGE_REFERENCE')
        layout.separator()
        # These two blocks used to be inlined here *and* duplicated in
        # ET_MT_OrganizeMenu / ET_MT_AnalysisMenu, which were only reachable
        # from two menus the pie stopped calling. Pointing at the submenus keeps
        # one definition of each group and shortens this menu by eleven rows.
        layout.menu('ET_MT_semantic_menu',  text='Assign to Category', icon='OUTLINER_COLLECTION')
        layout.menu('ET_MT_organize_menu',  text='Organize',           icon='FILEBROWSER')
        layout.menu('ET_MT_analysis_menu',  text='Analysis',           icon='VIEWZOOM')


# ---------------------------------------------------------------------------
# Scene organization
# ---------------------------------------------------------------------------

# (name, parent_name_or_None, color_tag)
COLLECTION_STRUCTURE = [
    ("PRODUCTION", None,         "COLOR_01"),
    ("STUDIO",     "PRODUCTION", "COLOR_02"),
    ("LIGHTS",     "STUDIO",     "COLOR_03"),
    ("CAMERAS",    "STUDIO",     "COLOR_03"),
    ("MODULES",    "PRODUCTION", "COLOR_06"),
    ("FLOOR",      "MODULES",    "COLOR_04"),
    ("WALLS",      "MODULES",    "COLOR_04"),
    ("CEILING",    "MODULES",    "COLOR_04"),
    ("PROPS",      "MODULES",    "COLOR_04"),
    ("DECALS",     "MODULES",    "COLOR_04"),
    ("BLOCKING",   "PRODUCTION", "COLOR_07"),
]


def _structure_children():
    """{parent_name_or_None: [(index, name), ...]} built from COLLECTION_STRUCTURE."""
    children = {}
    for index, (name, parent, _color) in enumerate(COLLECTION_STRUCTURE):
        children.setdefault(parent, []).append((index, name))
    return children


class ET_OT_OrganizeScene(bpy.types.Operator):
    bl_idname  = "et.organize_scene"
    bl_label   = "Project Structure"
    bl_description = "Choose which collections to create in the scene hierarchy"
    bl_options = {"REGISTER", "UNDO"}

    # One flag per entry in COLLECTION_STRUCTURE, indexed the same way. This
    # replaces eleven hand-written BoolProperties plus a hand-written draw tree
    # and a hand-written selections dict — three places that all had to agree
    # about the hierarchy, and did not: the dialog drew every depth-2 collection
    # after BLOCKING, so LIGHTS and CAMERAS appeared to be children of BLOCKING
    # rather than of STUDIO.
    use_colls: bpy.props.BoolVectorProperty(
        name="Collections",
        size=len(COLLECTION_STRUCTURE),
        default=(True,) * len(COLLECTION_STRUCTURE),
    )

    new_coll_name:   bpy.props.StringProperty(
        name="Name", default="",
        description="Name of a new collection to add")
    new_coll_parent: bpy.props.StringProperty(
        name="Parent", default="",
        description="Parent collection name. Leave empty to add at scene root")

    def _existing(self):
        return {c.name for c in bpy.data.collections}

    def invoke(self, context, event):
        existing = self._existing()
        # Pre-tick only what is missing, so re-running is a no-op by default.
        self.use_colls = tuple(name not in existing
                               for name, _parent, _color in COLLECTION_STRUCTURE)
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout   = self.layout
        existing = self._existing()
        children = _structure_children()

        layout.label(text="Select collections to create:", icon='OUTLINER_COLLECTION')
        layout.separator(factor=0.5)

        col = layout.column(align=True)

        def draw_branch(parent, depth, parent_ok):
            """Depth-first, so each collection is drawn directly under its parent."""
            for index, name in children.get(parent, []):
                is_existing = name in existing

                row = col.row(align=True)
                row.enabled = parent_ok
                if depth:
                    row.separator(factor=depth * 2.0)
                row.prop(self, 'use_colls', index=index, text=name, toggle=True,
                         icon='OUTLINER_OB_GROUP_INSTANCE' if is_existing
                              else 'OUTLINER_COLLECTION')
                if is_existing:
                    row.label(text='exists')

                # A child is only reachable if its parent will be there afterwards.
                draw_branch(name, depth + 1,
                            parent_ok and (self.use_colls[index] or is_existing))

        draw_branch(None, 0, True)

        layout.separator()
        box = layout.box()
        box.label(text='Add Custom Collection:', icon='ADD')
        col = box.column(align=True)
        col.prop(self, 'new_coll_name', text='Name')
        col.prop_search(self, 'new_coll_parent', bpy.data, 'collections',
                        text='Parent')

    def execute(self, context):
        scene = context.scene

        existing = {c.name: c for c in bpy.data.collections}
        created  = 0

        # COLLECTION_STRUCTURE lists parents before children, so a single pass
        # guarantees a parent is present by the time its children are handled.
        for index, (name, parent_name, color) in enumerate(COLLECTION_STRUCTURE):
            if not self.use_colls[index]:
                continue
            # Skip orphans rather than crashing on a missing parent.
            if parent_name is not None and parent_name not in existing:
                continue

            if name not in existing:
                coll = bpy.data.collections.new(name)
                existing[name] = coll
                parent = (scene.collection if parent_name is None
                          else existing[parent_name])
                parent.children.link(coll)
                created += 1
            existing[name].color_tag = color

        custom = self.new_coll_name.strip()
        if custom and custom not in existing:
            new_coll = bpy.data.collections.new(custom)
            existing[custom] = new_coll
            parent_name = self.new_coll_parent.strip()
            parent = existing.get(parent_name) if parent_name else None
            (parent or scene.collection).children.link(new_coll)
            created += 1

        self.report({'INFO'}, f"Created {created} collection(s)")
        return {"FINISHED"}


class ET_OT_ArrangeScene(bpy.types.Operator):
    bl_idname  = "et.arrange_scene"
    bl_label   = "Arrange Scene"
    bl_description = (
        "Move objects into collections. Routes by object type and name keywords "
        "when matching collections exist, then falls back to name-prefix grouping."
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        local = {c.name: c for c in _scene_collections(scene)}

        def ensure_collection(name):
            # Only reuse a collection that is already in this scene and
            # editable; anything else of that name gets a fresh local one.
            coll = local.get(name)
            if coll is None:
                coll = bpy.data.collections.new(name)
                scene.collection.children.link(coll)
                local[coll.name] = coll
            return coll

        index = _build_route_index(scene)

        routed, skipped, kept = 0, 0, 0
        prefix_objects  = []
        # Snapshot first: moving objects while iterating scene.objects
        # reorders what the loop is walking.
        for obj in list(scene.objects):
            if _filed_by_addon(obj):
                # An add-on's own output: moving it makes the add-on lose it.
                kept += 1
                continue
            target = _find_target_collection(obj, index, scene)
            if target:
                try:
                    _move_object(obj, target, scene)
                    routed += 1
                except RuntimeError:
                    skipped += 1
            else:
                prefix_objects.append(obj)

        # Remaining objects are grouped by their pre-suffix name stem, so
        # Crate.001/Crate.002 land together in a 'Crate' collection.
        groups = {}
        for obj in prefix_objects:
            groups.setdefault(obj.name.split(".")[0], []).append(obj)

        for prefix, group in groups.items():
            coll = ensure_collection(prefix)
            for obj in group:
                try:
                    _move_object(obj, coll, scene)
                except RuntimeError:
                    skipped += 1

        message = f"Routed {routed} object(s), grouped {len(groups)} by name"
        if kept:
            message += f", left {kept} in add-on collections"
        if skipped:
            self.report({'WARNING'},
                        f"{message} — {skipped} could not be moved (linked data)")
        else:
            self.report({'INFO'}, message)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Collection Isolate Toggle
# ---------------------------------------------------------------------------

class ET_OT_IsolateCollection(bpy.types.Operator):
    bl_idname  = "et.isolate_collection"
    bl_label   = "Isolate Collection"
    bl_description = (
        "Hide all top-level collections except the one(s) containing the active "
        "object. Run again to restore."
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        global _isolation_state
        root_lc   = context.view_layer.layer_collection
        top_level = list(root_lc.children)

        if _isolation_state:
            for lc in top_level:
                if lc.name in _isolation_state:
                    lc.hide_viewport = _isolation_state[lc.name]
            _isolation_state.clear()
            self.report({'INFO'}, "Isolation restored")
        else:
            obj       = context.active_object
            obj_colls = {c.name for c in obj.users_collection}

            def contains_any(lc, names):
                if lc.collection.name in names:
                    return True
                return any(contains_any(c, names) for c in lc.children)

            _isolation_state = {lc.name: lc.hide_viewport for lc in top_level}
            for lc in top_level:
                lc.hide_viewport = not contains_any(lc, obj_colls)
            self.report({'INFO'}, "Collection isolated — run again to restore")

        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Collection Color Sync
# ---------------------------------------------------------------------------

_COLOR_TAG_RGB = {
    'COLOR_01': (0.918, 0.278, 0.278, 1.0),  # red
    'COLOR_02': (0.918, 0.600, 0.200, 1.0),  # orange
    'COLOR_03': (0.918, 0.847, 0.200, 1.0),  # yellow
    'COLOR_04': (0.388, 0.729, 0.314, 1.0),  # green
    'COLOR_05': (0.200, 0.729, 0.729, 1.0),  # teal
    'COLOR_06': (0.200, 0.506, 0.918, 1.0),  # blue
    'COLOR_07': (0.639, 0.278, 0.918, 1.0),  # violet
    'COLOR_08': (0.918, 0.278, 0.600, 1.0),  # pink
}


class ET_OT_CollectionColorSync(bpy.types.Operator):
    bl_idname  = "et.collection_color_sync"
    bl_label   = "Collection Color Sync"
    bl_description = "Apply each collection's color tag to its objects' viewport display color"
    bl_options = {"REGISTER", "UNDO"}

    show_colors: bpy.props.BoolProperty(
        name="Switch Viewport to Object Color", default=True,
        description="Set solid shading to Object colour so the result is visible")

    def execute(self, context):
        # Walk the tree once carrying the nearest tagged ancestor's colour down,
        # so a child's own tag wins and untagged children inherit their parent.
        # The old version looped all_objects per collection, which coloured
        # nested objects repeatedly in an arbitrary order.
        colors = {}

        def walk(coll, inherited):
            color = _COLOR_TAG_RGB.get(coll.color_tag, inherited)
            if color is not None:
                for obj in coll.objects:
                    colors[obj] = color
            for child in coll.children:
                walk(child, color)

        walk(context.scene.collection, None)

        for obj, color in colors.items():
            obj.color = color

        if self.show_colors:
            space = context.space_data
            if space is not None and space.type == 'VIEW_3D':
                space.shading.color_type = 'OBJECT'

        self.report({'INFO'}, f"Color synced {len(colors)} object(s)")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Semantic Collection Assignment
#
# Complements Arrange Scene rather than replacing it. Arrange Scene sorts the
# whole scene automatically by object type and name; this assigns whatever is
# *selected* to a category the user names by hand — select the floor meshes,
# pick Floor, hit Add. The collection is created on first use and reused after.
# ---------------------------------------------------------------------------

# (key, label, icon, color_tag)
SEMANTIC_CATEGORIES = [
    ('FLOOR',    'Floor',    'MESH_PLANE',     'COLOR_04'),
    ('WALLS',    'Walls',    'MOD_BUILD',      'COLOR_04'),
    ('CEILING',  'Ceiling',  'MESH_GRID',      'COLOR_04'),
    ('PROPS',    'Props',    'MESH_MONKEY',    'COLOR_05'),
    ('DECALS',   'Decals',   'TEXTURE',        'COLOR_06'),
    ('TRIM',     'Trim',     'MOD_BEVEL',      'COLOR_06'),
    ('MODULAR',  'Modular',  'MOD_ARRAY',      'COLOR_02'),
    ('FX',       'FX',       'PARTICLES',      'COLOR_08'),
    ('LIGHTS',   'Lights',   'LIGHT',          'COLOR_03'),
    ('CAMERAS',  'Cameras',  'CAMERA_DATA',    'COLOR_03'),
    ('BLOCKING', 'Blocking', 'MESH_CUBE',      'COLOR_07'),
]

_SEMANTIC_LOOKUP = {key: (label, icon, color)
                    for key, label, icon, color in SEMANTIC_CATEGORIES}

# Built once as a module-level list. A dynamic items callback would have to keep
# its own reference to the strings it returns or Blender frees them mid-draw.
_SEMANTIC_ENUM = [
    (key, label, f"Assign the selection to {label}", icon, i)
    for i, (key, label, icon, _color) in enumerate(SEMANTIC_CATEGORIES)
]


def _project_name(scene):
    """Resolve the {project} token."""
    settings = scene.et_semantic

    if settings.project_source == 'CUSTOM':
        name = settings.custom_project.strip()
        if name:
            return name
    elif settings.project_source == 'SCENE':
        return scene.name

    if bpy.data.filepath:
        return os.path.splitext(os.path.basename(bpy.data.filepath))[0]
    return scene.name


def _semantic_collection_name(scene, label):
    """Apply the user's naming pattern to a category label."""
    settings = scene.et_semantic
    try:
        name = settings.pattern.format(category=label,
                                       project=_project_name(scene))
    except (KeyError, IndexError, ValueError):
        # Unknown token in the pattern — fall back rather than raising in draw().
        name = f"{label}_{_project_name(scene)}"
    return name.strip() or label


def _collection_icon(coll):
    """
    Icon matching a collection's own colour tag.

    Blender ships COLLECTION_COLOR_01..08 to mirror the eight tags, so the
    buttons can carry the same colour the user sees in the outliner instead of
    a uniform grey box. Untagged collections keep the plain outliner icon.
    """
    if coll is None:
        return 'OUTLINER_COLLECTION'
    tag = coll.color_tag
    if tag and tag != 'NONE' and tag.startswith('COLOR_'):
        return 'COLLECTION_' + tag
    return 'OUTLINER_COLLECTION'


def _collection_is_linked(coll, scene):
    """True if coll already sits somewhere in a collection tree."""
    if coll.name in scene.collection.children:
        return True
    return any(coll.name in other.children for other in bpy.data.collections)


def _link_collection(coll, scene, parent_name=""):
    """Put a freshly made collection into the tree, under parent_name if it exists."""
    if _collection_is_linked(coll, scene):
        return
    parent = bpy.data.collections.get(parent_name.strip()) if parent_name else None
    if parent is not None and parent is not coll:
        parent.children.link(coll)
    else:
        scene.collection.children.link(coll)


def _assign_to_collection(objects, coll, move):
    """
    Link objects into coll. When `move`, also unlink them from everywhere else.

    Links before it unlinks, so an object is never left without a collection
    when a link is refused, and a move only rearranges this scene's tree — the
    object keeps its place in other scenes and in the rigid body world.
    """
    scene = bpy.context.scene
    count = 0
    for obj in objects:
        if move:
            _move_object(obj, coll, scene)
        elif obj.name not in coll.objects:
            coll.objects.link(obj)
        count += 1
    return count


def _iter_child_collections(root, depth=0, _seen=None):
    """Yield (collection, depth) for everything nested under root."""
    # Blender allows a collection to be linked in more than one place, so guard
    # against walking the same branch twice (and against a cycle).
    if _seen is None:
        _seen = set()
    for child in root.children:
        key = child.name
        if key in _seen:
            continue
        _seen.add(key)
        yield child, depth
        yield from _iter_child_collections(child, depth + 1, _seen)


def _project_root_collection(scene):
    name = scene.et_semantic.project_root.strip()
    return bpy.data.collections.get(name) if name else None


def _find_in_project(scene, *names):
    """
    First collection under the project root whose name matches any of `names`
    (case-insensitively).

    This is what makes a category resolve to the FLOOR that Project Structure
    already built, instead of creating a second Floor_<project> beside it.
    """
    root = _project_root_collection(scene)
    if root is None:
        return None
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    if not wanted:
        return None
    if root.name.lower() in wanted:
        return root
    for coll, _depth in _iter_child_collections(root):
        if coll.name.lower() in wanted:
            return coll
    return None


def _rebuild_quick_slots(scene):
    """Repopulate the Quick Assign slots from the project root's hierarchy."""
    slots = scene.et_quick_slots
    previous = {slot.name: slot.use for slot in slots}
    slots.clear()

    root = _project_root_collection(scene)
    if root is None:
        return 0

    for coll, depth in _iter_child_collections(root):
        slot = slots.add()
        slot.name = coll.name
        slot.depth = depth
        # Remember what the user ticked last time; default new ones to visible.
        slot.use = previous.get(coll.name, True)
    return len(slots)


def _on_project_root_changed(settings, context):
    scene = context.scene
    count = _rebuild_quick_slots(scene)
    # Picking a root is a clear signal the user wants the real hierarchy.
    settings.source = 'PROJECT' if count else 'PRESET'


class ET_QuickSlot(bpy.types.PropertyGroup):
    """One detected collection offered as a Quick Assign button."""
    name:  bpy.props.StringProperty()
    use:   bpy.props.BoolProperty(default=True)
    depth: bpy.props.IntProperty(default=0)


class ET_SemanticSettings(bpy.types.PropertyGroup):
    category: bpy.props.EnumProperty(
        name="Category", items=_SEMANTIC_ENUM, default='FLOOR')

    use_custom: bpy.props.BoolProperty(
        name="Custom Category", default=False,
        description="Type a category name instead of picking a preset")
    custom_category: bpy.props.StringProperty(
        name="Category", default="",
        description="Category name used in place of a preset")

    pattern: bpy.props.StringProperty(
        name="Pattern", default="{category}_{project}",
        description="Collection naming pattern. Tokens: {category}, {project}")

    project_source: bpy.props.EnumProperty(
        name="Project Name From",
        items=[
            ('FILE',   'Blend File', 'Use the .blend file name'),
            ('SCENE',  'Scene',      'Use the scene name'),
            ('CUSTOM', 'Custom',     'Type the project name below'),
        ],
        default='FILE')
    custom_project: bpy.props.StringProperty(name="Project", default="")

    project_root: bpy.props.StringProperty(
        name="Project", default="",
        description="Root collection for this project. Categories resolve to "
                    "collections that already exist inside it, and anything "
                    "new is created inside it. Blank = scene root",
        update=lambda self, context: _on_project_root_changed(self, context))

    source: bpy.props.EnumProperty(
        name="Quick Assign From",
        items=[
            ('PROJECT', 'Project', 'Collections found under the project root'),
            ('PRESET',  'Presets', 'The built-in category list'),
        ],
        default='PRESET')


    move: bpy.props.BoolProperty(
        name="Move (unlink from others)", default=True,
        description="Move objects into the category collection. Turn off to "
                    "link them in while leaving their current collections intact")

    color_tag: bpy.props.BoolProperty(
        name="Apply Colour Tag", default=True,
        description="Tag the collection with the category colour, so "
                    "Collection Color Sync picks it up")

    show_options: bpy.props.BoolProperty(name="Options", default=False)


class ET_OT_AssignSemantic(bpy.types.Operator):
    bl_idname  = "et.assign_semantic"
    bl_label   = "Assign to Category"
    bl_description = ("Put the selected objects into a named category collection, "
                      "creating it if it does not exist yet")
    bl_options = {"REGISTER", "UNDO"}

    category: bpy.props.StringProperty(
        name="Category", default="",
        description="Preset category key. Empty uses the panel's current choice")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def execute(self, context):
        scene    = context.scene
        settings = scene.et_semantic

        if self.category:
            entry = _SEMANTIC_LOOKUP.get(self.category)
            if entry is None:
                self.report({'ERROR'}, f"Unknown category '{self.category}'")
                return {'CANCELLED'}
            label, _icon, color = entry
        elif settings.use_custom:
            label = settings.custom_category.strip()
            color = 'COLOR_01'
            if not label:
                self.report({'ERROR'}, "Type a custom category name first")
                return {'CANCELLED'}
        else:
            label, _icon, color = _SEMANTIC_LOOKUP[settings.category]

        patterned = _semantic_collection_name(scene, label)

        # Resolution order matters. Reusing what the project already has beats
        # inventing a new collection beside it — clicking Floor should drop the
        # selection into PRODUCTION > MODULES > FLOOR, not create Floor_<project>
        # at the scene root next to it.
        coll = _find_in_project(scene, label, patterned)
        if coll is None:
            coll = bpy.data.collections.get(patterned)

        created = coll is None
        if created:
            coll = bpy.data.collections.new(patterned)
            if settings.color_tag:
                coll.color_tag = color
        elif settings.color_tag and coll.color_tag == 'NONE':
            # Only tag an existing collection that has no colour of its own,
            # so we never overwrite a choice the user already made.
            coll.color_tag = color

        _link_collection(coll, scene, settings.project_root)

        assigned = _assign_to_collection(context.selected_objects, coll,
                                         settings.move)

        verb = "Created" if created else "Added to"
        self.report({'INFO'}, f"{verb} '{coll.name}' — {assigned} object(s)")
        return {"FINISHED"}


class ET_OT_ScanProject(bpy.types.Operator):
    bl_idname  = "et.scan_project"
    bl_label   = "Detect Collections"
    bl_description = ("Rescan the project root and refresh the Quick Assign "
                      "buttons from the collections it actually contains")
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        if _project_root_collection(scene) is None:
            self.report({'WARNING'}, "Set a project collection first")
            return {'CANCELLED'}

        count = _rebuild_quick_slots(scene)
        scene.et_semantic.source = 'PROJECT' if count else 'PRESET'
        self.report({'INFO'}, f"Detected {count} collection(s)")
        return {"FINISHED"}


class ET_OT_PickQuickSlots(bpy.types.Operator):
    bl_idname  = "et.pick_quick_slots"
    bl_label   = "Choose Quick Assign Buttons"
    bl_description = "Pick which detected collections appear in Quick Assign"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        if not context.scene.et_quick_slots:
            _rebuild_quick_slots(context.scene)
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        scene  = context.scene
        slots  = scene.et_quick_slots

        root = _project_root_collection(scene)
        layout.label(text=f"Inside '{root.name}':" if root else "No project root set",
                     icon='OUTLINER_COLLECTION')

        if not slots:
            layout.label(text="Nothing found — is the root empty?", icon='INFO')
            return

        col = layout.column(align=True)
        for slot in slots:
            row = col.row(align=True)
            if slot.depth:
                row.separator(factor=slot.depth * 2.0)
            row.prop(slot, 'use', text="", toggle=True,
                     icon='CHECKBOX_HLT' if slot.use else 'CHECKBOX_DEHLT')
            row.label(text=slot.name,
                      icon=_collection_icon(bpy.data.collections.get(slot.name)))

        layout.separator(factor=0.5)
        row = layout.row(align=True)
        row.operator('et.set_all_quick_slots', text="All").state = True
        row.operator('et.set_all_quick_slots', text="None").state = False

    def execute(self, context):
        used = sum(1 for slot in context.scene.et_quick_slots if slot.use)
        self.report({'INFO'}, f"{used} collection(s) in Quick Assign")
        return {"FINISHED"}


class ET_OT_SetAllQuickSlots(bpy.types.Operator):
    bl_idname  = "et.set_all_quick_slots"
    bl_label   = "Set All Quick Slots"
    bl_description = "Tick or untick every detected collection"
    bl_options = {"REGISTER", "INTERNAL"}

    state: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        for slot in context.scene.et_quick_slots:
            slot.use = self.state
        return {"FINISHED"}


def draw_semantic_assign(layout, context):
    scene    = context.scene
    settings = scene.et_semantic
    root     = _project_root_collection(scene)

    box = layout.box()
    header = box.row(align=True)
    header.label(text="Assign to Category", icon='OUTLINER_COLLECTION')
    header.prop(settings, 'show_options', text='', icon='PREFERENCES', emboss=False)

    # --- project root ------------------------------------------------------
    row = box.row(align=True)
    row.prop_search(settings, 'project_root', bpy.data, 'collections',
                    text="Project", icon='OUTLINER_COLLECTION')
    row.operator('et.scan_project', text="", icon='FILE_REFRESH')

    if root is None:
        box.label(text="Pick a project root to use its hierarchy", icon='INFO')

    use_project = settings.source == 'PROJECT' and root is not None

    # --- target + add ------------------------------------------------------
    # In project mode there is deliberately no second collection field here.
    # It duplicated the root picker directly above it, and the two were
    # independent, so it was ambiguous whether Add Selection followed the root
    # or that field. The Quick Assign buttons below are the whole interface:
    # pick the root once, then one click per assignment.
    col = box.column(align=True)
    if not use_project:
        if settings.use_custom:
            col.prop(settings, 'custom_category', text='',
                     icon='OUTLINER_COLLECTION')
            label = settings.custom_category.strip() or "Category"
        else:
            col.prop(settings, 'category', text='')
            label = _SEMANTIC_LOOKUP[settings.category][0]

        # Show where this will actually land, so it is never a guess at the
        # moment of clicking — an existing collection when one matches, or the
        # pattern-built name when a new one has to be created.
        existing = _find_in_project(scene, label,
                                    _semantic_collection_name(scene, label))
        if existing is not None:
            col.label(text=f"{existing.name}  (existing)", icon='RIGHTARROW_THIN')
        else:
            col.label(text=_semantic_collection_name(scene, label),
                      icon='RIGHTARROW_THIN')

        row = col.row(align=True)
        row.scale_y = 1.3
        row.operator('et.assign_semantic', text='Add Selection',
                     icon='ADD').category = ''

    # --- quick assign ------------------------------------------------------
    box.separator(factor=0.4)
    header = box.row(align=True)
    header.label(text="Quick Assign", icon='PRESET')
    if root is not None:
        header.prop(settings, 'source', text="")
        header.operator('et.pick_quick_slots', text="", icon='CHECKBOX_HLT')

    if use_project:
        slots = [s for s in scene.et_quick_slots if s.use]
        if not slots:
            box.label(text="No collections picked — use the tick icon",
                      icon='INFO')
        else:
            # Two columns, not three: collection names here are the user's own
            # and can be long, so a third column truncates them to "MODU…".
            # The presets below keep three because those names are short and fixed.
            grid = box.grid_flow(row_major=True, columns=2, align=True)
            missing = 0
            for slot in slots:
                coll = bpy.data.collections.get(slot.name)
                if coll is None:
                    # Deleted or renamed since the last scan.
                    missing += 1
                    continue
                grid.operator('et.add_to_collection', text=slot.name,
                              icon=_collection_icon(coll)
                              ).collection_name = slot.name
            if missing:
                box.label(text=f"{missing} collection(s) gone — rescan",
                          icon='ERROR')

        # Anything not on a button is still one click away, without a second
        # always-visible field competing with the root picker.
        row = box.row(align=True)
        row.operator('et.add_to_collection', text="Other…",
                     icon='VIEWZOOM').collection_name = ""
        row.operator('et.new_collection_for_selection', text="New…", icon='ADD')
    else:
        grid = box.grid_flow(row_major=True, columns=3, align=True)
        for key, cat_label, icon, _color in SEMANTIC_CATEGORIES:
            grid.operator('et.assign_semantic', text=cat_label,
                          icon=icon).category = key

    # --- options -----------------------------------------------------------
    if settings.show_options:
        opts = box.box()
        opts.label(text="Naming", icon='SORTALPHA')
        opts.prop(settings, 'pattern')
        opts.prop(settings, 'project_source')
        if settings.project_source == 'CUSTOM':
            opts.prop(settings, 'custom_project')

        opts.separator(factor=0.4)
        opts.label(text="Behaviour", icon='OUTLINER')
        opts.prop(settings, 'move')
        opts.prop(settings, 'color_tag')
        opts.prop(settings, 'use_custom')


# ---------------------------------------------------------------------------
# Right-click ▸ Add to Collection
#
# Blender's own Move/Link to Collection (M / Shift+M) is a bare hierarchy
# browser. This adds the thing it cannot do: it looks at what you selected and
# suggests where it belongs, using the same matching Arrange Scene uses, and it
# can create the collection with the project naming pattern and colour tag
# already applied.
# ---------------------------------------------------------------------------

# Menus are drawn every time they open; past this many entries the list stops
# being scannable and the search dialog is the better route.
_MENU_COLLECTION_LIMIT = 20


def _suggested_collection(context):
    """Best-guess destination for the selection, or None."""
    obj = context.active_object
    if obj is None:
        selected = context.selected_objects
        if not selected:
            return None
        obj = selected[0]

    target = _find_target_collection(obj, scene=context.scene)
    if target is None:
        return None
    # Pointless to suggest the collection everything is already in.
    # users_collection holds Collection objects, not names — comparing against
    # target.name here silently never matched.
    if all(target in o.users_collection for o in context.selected_objects):
        return None
    return target


class ET_OT_AddToCollection(bpy.types.Operator):
    bl_idname  = "et.add_to_collection"
    bl_label   = "Add to Collection"
    bl_description = ("Move or link the selected objects into a collection. "
                      "Leave the name empty to search")
    bl_options = {"REGISTER", "UNDO"}

    collection_name: bpy.props.StringProperty(name="Collection")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def invoke(self, context, event):
        # Called from a menu entry with a name -> act immediately.
        # Called as "Search all…" with no name -> offer the search field.
        if self.collection_name:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop_search(self, 'collection_name', bpy.data, 'collections',
                           text="", icon='OUTLINER_COLLECTION')
        layout.prop(context.scene.et_semantic, 'move')

    def execute(self, context):
        coll = bpy.data.collections.get(self.collection_name)
        if coll is None:
            self.report({'ERROR'},
                        f"Collection '{self.collection_name}' not found")
            return {'CANCELLED'}

        move  = context.scene.et_semantic.move
        count = _assign_to_collection(context.selected_objects, coll, move)
        self.report({'INFO'},
                    f"{'Moved' if move else 'Linked'} {count} object(s) "
                    f"to '{coll.name}'")
        return {"FINISHED"}


class ET_OT_NewCollectionForSelection(bpy.types.Operator):
    bl_idname  = "et.new_collection_for_selection"
    bl_label   = "New Collection"
    bl_description = "Create a collection and put the selected objects in it"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty(name="Name", default="Collection")
    parent: bpy.props.StringProperty(
        name="Parent",
        description="Existing collection to nest under. Blank = scene root")
    color: bpy.props.EnumProperty(
        name="Colour",
        items=[('NONE', 'None', 'No colour tag')] +
              [(tag, tag.replace('COLOR_', 'Colour '), '')
               for tag in sorted(_COLOR_TAG_RGB)],
        default='NONE')

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def invoke(self, context, event):
        # Seed the field from the active object so the common case is one Enter.
        obj = context.active_object
        if obj is not None:
            self.name = _SUFFIX_SPLIT_RE.sub('', obj.name) or "Collection"
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'name')
        layout.prop_search(self, 'parent', bpy.data, 'collections')
        layout.prop(self, 'color')
        layout.prop(context.scene.et_semantic, 'move')

    def execute(self, context):
        name = self.name.strip()
        if not name:
            self.report({'ERROR'}, "Give the collection a name")
            return {'CANCELLED'}

        scene   = context.scene
        coll    = bpy.data.collections.get(name)
        created = coll is None
        if created:
            coll = bpy.data.collections.new(name)
        if self.color != 'NONE':
            coll.color_tag = self.color

        _link_collection(coll, scene, self.parent)
        count = _assign_to_collection(context.selected_objects, coll,
                                      scene.et_semantic.move)

        self.report({'INFO'},
                    f"{'Created' if created else 'Reused'} '{coll.name}' "
                    f"— {count} object(s)")
        return {"FINISHED"}


class ET_MT_AddToCollectionMenu(bpy.types.Menu):
    bl_idname = "ET_MT_add_to_collection"
    bl_label  = "Add to Collection"

    def draw(self, context):
        layout   = self.layout
        settings = context.scene.et_semantic

        layout.prop(settings, 'move', text="Move (uncheck to link)")
        layout.separator()

        suggested = _suggested_collection(context)
        if suggested is not None:
            layout.label(text="Suggested", icon='OUTLINER_OB_LIGHT')
            layout.operator('et.add_to_collection', text=suggested.name,
                            icon=_collection_icon(suggested)
                            ).collection_name = suggested.name
            layout.separator()

        layout.operator('et.new_collection_for_selection',
                        text="New Collection…", icon='ADD')
        layout.menu('ET_MT_semantic_menu', text="By Category", icon='PRESET')
        layout.separator()

        collections = sorted(bpy.data.collections, key=lambda c: c.name)
        if not collections:
            layout.label(text="No collections yet", icon='INFO')
            return

        layout.label(text="Existing", icon='OUTLINER_COLLECTION')
        for coll in collections[:_MENU_COLLECTION_LIMIT]:
            layout.operator('et.add_to_collection',
                            text=f"{coll.name}   ({len(coll.all_objects)})",
                            icon=_collection_icon(coll)
                            ).collection_name = coll.name

        # Never silently truncate — say how many are hidden and offer search.
        hidden = len(collections) - _MENU_COLLECTION_LIMIT
        if hidden > 0:
            layout.separator()
            layout.operator('et.add_to_collection',
                            text=f"Search all… (+{hidden} more)",
                            icon='VIEWZOOM').collection_name = ""


def draw_object_context_menu(self, context):
    if context.mode != 'OBJECT' or not context.selected_objects:
        return
    layout = self.layout
    layout.separator()
    layout.menu('ET_MT_add_to_collection', text="Add to Collection",
                icon='OUTLINER_COLLECTION')


# ---------------------------------------------------------------------------
# Organization tools (new)
# ---------------------------------------------------------------------------

class ET_OT_VisibilityBookmark(bpy.types.Operator):
    bl_idname  = "et.visibility_bookmark"
    bl_label   = "Visibility Bookmark"
    bl_description = "Save or restore named collection visibility states"
    bl_options = {"REGISTER"}

    action: bpy.props.EnumProperty(
        name="Action",
        items=[
            ('SAVE',   'Save',   'Save current visibility to a slot'),
            ('LOAD',   'Load',   'Restore visibility from a slot'),
            ('DELETE', 'Delete', 'Remove a saved slot'),
        ],
        default='SAVE',
    )
    slot: bpy.props.StringProperty(name="Slot Name", default="Slot 1")

    def _collect_states(self, lc, result):
        result[lc.name] = lc.hide_viewport
        for child in lc.children:
            self._collect_states(child, result)

    def _apply_states(self, lc, states):
        if lc.name in states:
            lc.hide_viewport = states[lc.name]
        for child in lc.children:
            self._apply_states(child, states)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=280)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Snapshot which collections are visible/hidden,", icon='MARKER')
        layout.label(text="then restore that state later by name.")
        layout.separator(factor=0.5)
        row = layout.row(align=True)
        row.prop(self, 'action', expand=True)
        layout.prop(self, 'slot')
        if _vis_bookmarks:
            box = layout.box()
            box.label(text="Saved slots:", icon='CHECKMARK')
            for name in _vis_bookmarks:
                box.label(text=f"  • {name}")

    def execute(self, context):
        root_lc = context.view_layer.layer_collection

        if self.action == 'SAVE':
            states = {}
            self._collect_states(root_lc, states)
            _vis_bookmarks[self.slot] = states
            self.report({'INFO'}, f"Visibility saved to '{self.slot}'")

        elif self.action == 'LOAD':
            states = _vis_bookmarks.get(self.slot)
            if not states:
                self.report({'ERROR'}, f"No bookmark named '{self.slot}'")
                return {'CANCELLED'}
            self._apply_states(root_lc, states)
            self.report({'INFO'}, f"Visibility restored from '{self.slot}'")

        elif self.action == 'DELETE':
            if self.slot in _vis_bookmarks:
                del _vis_bookmarks[self.slot]
                self.report({'INFO'}, f"Deleted bookmark '{self.slot}'")
            else:
                self.report({'WARNING'}, f"No bookmark named '{self.slot}'")

        return {"FINISHED"}


class ET_OT_RenameByCollection(bpy.types.Operator):
    bl_idname  = "et.rename_by_collection"
    bl_label   = "Rename by Collection"
    bl_description = (
        "Rename selected objects to CollectionName_01, _02 … "
        "grouped by their first collection"
    )
    bl_options = {"REGISTER", "UNDO"}

    prefix:      bpy.props.StringProperty(
        name="Prefix Override", default="",
        description="Leave empty to use the collection name")
    padding:     bpy.props.IntProperty(name="Digits",      default=2, min=1, max=4)
    start_index: bpy.props.IntProperty(name="Start Index", default=1, min=0)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'prefix')
        row = layout.row(align=True)
        row.prop(self, 'start_index')
        row.prop(self, 'padding')

    def execute(self, context):
        # Group selected objects by their primary (first) collection
        groups = {}
        for obj in context.selected_objects:
            colls = obj.users_collection
            key = colls[0].name if colls else "__none__"
            groups.setdefault(key, []).append(obj)

        renamed = 0
        for coll_name, objs in groups.items():
            base = self.prefix if self.prefix else coll_name
            for i, obj in enumerate(sorted(objs, key=lambda o: o.name),
                                    start=self.start_index):
                new_name = f"{base}_{str(i).zfill(self.padding)}"
                obj.name = new_name
                if obj.data:
                    obj.data.name = new_name
                renamed += 1

        self.report({'INFO'}, f"Renamed {renamed} object(s)")
        return {"FINISHED"}


class ET_OT_SelectByCollection(bpy.types.Operator):
    bl_idname  = "et.select_by_collection"
    bl_label   = "Select by Collection"
    bl_description = "Select all visible objects in a chosen collection"
    bl_options = {"REGISTER", "UNDO"}

    collection_name: bpy.props.StringProperty(name="Collection")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, context):
        self.layout.prop_search(self, 'collection_name', bpy.data, 'collections')

    def execute(self, context):
        coll = bpy.data.collections.get(self.collection_name)
        if not coll:
            self.report({'ERROR'}, f"Collection '{self.collection_name}' not found")
            return {'CANCELLED'}

        bpy.ops.object.select_all(action='DESELECT')
        count = 0
        first = None
        for obj in coll.all_objects:
            if obj.visible_get():
                obj.select_set(True)
                count += 1
                if first is None:
                    first = obj

        if first:
            context.view_layer.objects.active = first

        self.report({'INFO'}, f"Selected {count} object(s) in '{self.collection_name}'")
        return {"FINISHED"}


class ET_OT_SwapCollections(bpy.types.Operator):
    bl_idname  = "et.swap_collections"
    bl_label   = "Swap Collections"
    bl_description = "Move selected objects to a target collection, removing them from their current ones"
    bl_options = {"REGISTER", "UNDO"}

    target_collection: bpy.props.StringProperty(name="Target Collection")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, context):
        self.layout.prop_search(self, 'target_collection', bpy.data, 'collections')

    def execute(self, context):
        target = bpy.data.collections.get(self.target_collection)
        if not target:
            self.report({'ERROR'}, f"Collection '{self.target_collection}' not found")
            return {'CANCELLED'}

        moved = 0
        for obj in context.selected_objects:
            current = list(obj.users_collection)
            for c in current:
                if c != target:
                    c.objects.unlink(obj)
            if obj.name not in target.objects:
                target.objects.link(obj)
            moved += 1

        self.report({'INFO'}, f"Moved {moved} object(s) to '{self.target_collection}'")
        return {"FINISHED"}


class ET_OT_CollectionStats(bpy.types.Operator):
    bl_idname  = "et.collection_stats"
    bl_label   = "Collection Statistics"
    bl_description = "Show face and object counts per collection"
    bl_options = {"REGISTER"}

    # draw() runs on every redraw of the popup — once per mouse move. Counting
    # polygons there re-walked the whole scene each frame, so the numbers are
    # gathered once in invoke() and the draw just formats them.
    _rows  = []
    _total = (0, 0)

    def invoke(self, context, event):
        rows       = []
        seen       = set()
        total_objs = 0
        total_faces = 0

        for coll in sorted(bpy.data.collections, key=lambda c: c.name):
            objs  = coll.objects
            faces = sum(len(o.data.polygons) for o in objs if o.type == 'MESH')
            rows.append((coll.name, len(objs), faces))
            # Objects linked into several collections must only count once in
            # the total, otherwise TOTAL exceeds the real scene contents.
            for obj in objs:
                if obj.name in seen:
                    continue
                seen.add(obj.name)
                total_objs += 1
                if obj.type == 'MESH':
                    total_faces += len(obj.data.polygons)

        self._rows  = rows
        self._total = (total_objs, total_faces)
        return context.window_manager.invoke_popup(self, width=380)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.label(text="Collection Statistics", icon='OUTLINER_COLLECTION')
        layout.separator(factor=0.3)

        col = layout.column(align=True)
        col.label(text=f"{'Collection':<24} {'Objs':>5}  {'Faces':>12}")
        col.separator(factor=0.3)

        for name, objs, faces in self._rows:
            col.label(text=f"{name:<24} {objs:>5}  {faces:>12,}")

        total_objs, total_faces = self._total
        col.separator(factor=0.5)
        col.label(text=f"{'TOTAL (unique)':<24} {total_objs:>5}  {total_faces:>12,}")


# ---------------------------------------------------------------------------
# Scene Statistics
# ---------------------------------------------------------------------------

class ET_OT_SceneStats(bpy.types.Operator):
    bl_idname  = "et.scene_stats"
    bl_label   = "Scene Statistics"
    bl_description = "Show face, object and material counts for selection and full scene"
    bl_options = {"REGISTER"}

    # Gathered once in invoke(); see the note on ET_OT_CollectionStats.
    _stats = {}

    def invoke(self, context, event):
        scene = context.scene
        sel   = context.selected_objects

        sel_meshes = [o for o in sel           if o.type == 'MESH']
        all_meshes = [o for o in scene.objects if o.type == 'MESH']

        self._stats = {
            'sel_objs':  len(sel),
            'all_objs':  len(scene.objects),
            'sel_faces': sum(len(o.data.polygons) for o in sel_meshes),
            'all_faces': sum(len(o.data.polygons) for o in all_meshes),
            'sel_verts': sum(len(o.data.vertices) for o in sel_meshes),
            'all_verts': sum(len(o.data.vertices) for o in all_meshes),
            'sel_mats':  len({m for o in sel_meshes for m in o.data.materials if m}),
            'all_mats':  len(bpy.data.materials),
        }
        return context.window_manager.invoke_popup(self, width=300)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        s = self._stats
        box = self.layout.box()
        box.label(text="Scene Statistics", icon='INFO')
        col = box.column(align=True)
        col.label(text=f"Objects   —  Selection: {s['sel_objs']:>6}    Scene: {s['all_objs']}")
        col.label(text=f"Faces     —  Selection: {s['sel_faces']:>6,}    Scene: {s['all_faces']:,}")
        col.label(text=f"Vertices  —  Selection: {s['sel_verts']:>6,}    Scene: {s['all_verts']:,}")
        col.label(text=f"Materials —  Selection: {s['sel_mats']:>6}    Scene: {s['all_mats']}")


# ---------------------------------------------------------------------------
# Material Consolidate
# ---------------------------------------------------------------------------

class ET_OT_ConsolidateMaterials(bpy.types.Operator):
    bl_idname  = "et.consolidate_materials"
    bl_label   = "Consolidate Materials"
    bl_description = "Merge duplicate materials (Mat.001, Mat.002 → Mat) and optionally remove unused"
    bl_options = {"REGISTER", "UNDO"}

    remove_unused: bpy.props.BoolProperty(name="Remove Unused After Merge", default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, context):
        self.layout.prop(self, 'remove_unused')
        self.layout.prop(self, 'rename_orphans')

    rename_orphans: bpy.props.BoolProperty(
        name="Rename Orphan Copies", default=True,
        description="If only Mat.001/Mat.002 exist and Mat does not, promote the "
                    "lowest-numbered copy to the base name")

    def execute(self, context):
        suffix_re = re.compile(r'^(.+)\.\d{3,}$')

        # Group every suffixed material under its base name in one pass, instead
        # of hitting bpy.data.materials.get() per material.
        families = {}
        for mat in bpy.data.materials:
            m = suffix_re.match(mat.name)
            if m:
                families.setdefault(m.group(1), []).append(mat)

        merged = 0
        for base_name, copies in families.items():
            base = bpy.data.materials.get(base_name)
            copies.sort(key=lambda m: m.name)

            if base is None:
                if not self.rename_orphans:
                    continue
                # No original left — promote the first copy so the rest still merge.
                base = copies.pop(0)
                base.name = base_name

            for mat in copies:
                mat.user_remap(base)
                bpy.data.materials.remove(mat)
                merged += 1

        removed = 0
        if self.remove_unused:
            for mat in list(bpy.data.materials):
                if mat.users == 0:
                    bpy.data.materials.remove(mat)
                    removed += 1

        self.report({'INFO'}, f"Merged {merged} duplicates, removed {removed} unused")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# PBR material builder  (Quixel Megascans and any standard map naming)
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.tga',
               '.bmp', '.webp'}

# Filename tokens that name the map, separators stripped and lowercased, so
# 'Base_Color', 'BaseColor' and 'basecolor' are all one lookup.
_TEX_KEYWORDS = {
    'albedo': 'BASE', 'basecolor': 'BASE', 'basecolour': 'BASE',
    'diffuse': 'BASE', 'diff': 'BASE', 'color': 'BASE', 'colour': 'BASE',
    'col': 'BASE', 'albedotransparency': 'BASE',
    'roughness': 'ROUGH', 'rough': 'ROUGH', 'rgh': 'ROUGH',
    'gloss': 'GLOSS', 'glossiness': 'GLOSS', 'glossy': 'GLOSS',
    'normal': 'NORMAL', 'normalgl': 'NORMAL', 'normalopengl': 'NORMAL',
    'normalmap': 'NORMAL', 'nrm': 'NORMAL', 'nor': 'NORMAL',
    'normaldx': 'NORMAL_DX', 'normaldirectx': 'NORMAL_DX',
    'metalness': 'METAL', 'metallic': 'METAL', 'metal': 'METAL',
    'displacement': 'DISP', 'disp': 'DISP', 'height': 'DISP',
    'bump': 'BUMP', 'normalbump': 'BUMP',
    'ao': 'AO', 'ambientocclusion': 'AO', 'occlusion': 'AO',
    'opacity': 'ALPHA', 'alpha': 'ALPHA', 'transparency': 'ALPHA',
    'specular': 'SPEC', 'spec': 'SPEC',
    'orm': 'ORM', 'arm': 'ORM', 'occlusionroughnessmetallic': 'ORM',
    'rma': 'RMA',
    # Loaded and labelled, but deliberately left unconnected: what these should
    # drive is a per-shader judgement call (translucency into subsurface,
    # cavity into a mask) that a one-click importer should not make silently.
    'translucency': 'EXTRA', 'transmission': 'EXTRA', 'thickness': 'EXTRA',
    'cavity': 'EXTRA', 'curvature': 'EXTRA', 'fuzz': 'EXTRA',
    'scatter': 'EXTRA', 'brush': 'EXTRA',
}

# What each channel of a packed map means, in R, G, B order.
_PACKED_CHANNELS = {
    'ORM': ('AO', 'ROUGH', 'METAL'),
    'RMA': ('ROUGH', 'METAL', 'AO'),
}

# Tokens that sit *behind* the map name and have to come off before matching:
# Megascans writes 'Rock_ug0jaipaw_4K_Normal_LOD0'.
_TRAILING_JUNK = {'srgb', 'raw', 'preview'}
_RES_RE     = re.compile(r'^(\d+)k$', re.I)
_LOD_RE     = re.compile(r'^lod\d+$', re.I)
_SKIP_WORDS = ('preview', 'thumb', 'billboard')

# Tie-break when a folder ships the same map twice: prefer the encoding that
# survives an 8-bit gradient, which matters most for displacement and normals.
_EXT_RANK = {'.exr': 4, '.tif': 3, '.tiff': 3, '.png': 2, '.tga': 1,
             '.bmp': 1, '.webp': 0, '.jpg': 0, '.jpeg': 0}

# Top-to-bottom order of the texture column, so every material this builds
# reads the same way.
_SLOT_ORDER = ('BASE', 'ORM', 'RMA', 'AO', 'ROUGH', 'GLOSS', 'METAL', 'SPEC',
               'NORMAL', 'NORMAL_DX', 'BUMP', 'DISP', 'ALPHA')


def _split_texture_name(stem):
    """
    Split a texture filename stem into (base_name, slot, resolution).

    Megascans puts the map keyword last, after the resolution and sometimes
    before an LOD tag — 'Rock_Cliff_ug0jaipaw_4K_Normal_LOD0'. So walk in from
    the right: drop the tags nothing keys off, then try the last two tokens
    joined before the last token alone, which is what makes 'Base_Color' and
    'BaseColor' the same lookup.

    slot is None when no keyword matched, i.e. not a texture we can place.
    """
    tokens = [t for t in re.split(r'[^A-Za-z0-9]+', stem) if t]

    while tokens and (_LOD_RE.match(tokens[-1])
                      or tokens[-1].lower() in _TRAILING_JUNK):
        tokens.pop()

    slot = None
    for span in (2, 1):
        if len(tokens) < span:
            continue
        key   = ''.join(tokens[-span:]).lower()
        found = _TEX_KEYWORDS.get(key)
        if found:
            # Extras keep their own key so two of them can't overwrite each other.
            slot = f'EXTRA_{key}' if found == 'EXTRA' else found
            del tokens[-span:]
            break

    res  = 0
    kept = []
    for token in tokens:
        match = _RES_RE.match(token)
        if match:
            res = int(match.group(1))
        else:
            kept.append(token)

    return '_'.join(kept), slot, res


def _collect_texture_sets(paths, prefer_high=True):
    """
    Group texture files into {material_name: {slot: filepath}}.

    A Bridge export folder often holds 2K and 4K side by side; both reduce to
    the same base name, so the resolution decides which one wins rather than
    whichever the OS happened to list last.
    """
    sets = {}
    for path in paths:
        stem, ext = os.path.splitext(os.path.basename(path))
        ext = ext.lower()
        if ext not in _IMAGE_EXTS:
            continue
        if any(word in stem.lower() for word in _SKIP_WORDS):
            continue

        base, slot, res = _split_texture_name(stem)
        if slot is None:
            continue
        if not base:
            # 'Albedo.jpg' — the folder is the only name we have.
            base = os.path.basename(os.path.dirname(path)) or 'Material'

        slots   = sets.setdefault(base, {})
        rank    = (res if prefer_high else -res, _EXT_RANK.get(ext, 0))
        current = slots.get(slot)
        if current is None or rank > current[1]:
            slots[slot] = (path, rank)

    return {base: {slot: entry[0] for slot, entry in slots.items()}
            for base, slots in sets.items()}


def _image_key(path):
    """Comparable absolute form of a texture path."""
    try:
        return os.path.normcase(os.path.abspath(bpy.path.abspath(path)))
    except (ValueError, RuntimeError):
        return None


def _index_images():
    """{path: image name} over everything currently loaded."""
    index = {}
    for img in bpy.data.images:
        if not img.filepath:
            continue
        key = _image_key(img.filepath)
        if key is not None:
            index.setdefault(key, img.name)
    return index


def _load_image(path, index=None):
    """
    Load a texture, reusing an image datablock already pointing at it.

    Scanning bpy.data.images per texture is quadratic once a library import is
    loading hundreds of files into a scene that already holds hundreds, so the
    builders pass an index in and it is kept up to date as images arrive.
    """
    key = _image_key(path)
    if index is not None and key is not None:
        existing = bpy.data.images.get(index.get(key, ""))
        # A name can go stale if the datablock was renamed or removed.
        if existing is not None and _image_key(existing.filepath) == key:
            return existing
        image = bpy.data.images.load(path)
        index[key] = image.name
        return image

    for img in bpy.data.images:
        if img.filepath and _image_key(img.filepath) == key:
            return img
    return bpy.data.images.load(path)


def _set_non_color(image):
    """Data maps must not be colour managed. The space name varies by config."""
    for name in ('Non-Color', 'Non-Colour', 'Raw', 'Generic Data', 'Utility - Raw'):
        try:
            image.colorspace_settings.name = name
            return
        except TypeError:
            continue


def _socket(node, *names):
    """First input socket that exists under any of these names, or None."""
    for name in names:
        sock = node.inputs.get(name)
        if sock is not None:
            return sock
    return None


def _new_mix(nodes, blend_type='MIX'):
    """
    A colour mix node, across the 3.4 Mix rework.

    Returns (node, factor, input_a, input_b, output). Sockets are found by name
    and type rather than index because 4.0 added rotation sockets to
    ShaderNodeMix, which shifted every index after the float pair.
    """
    if hasattr(bpy.types, 'ShaderNodeMix'):
        node = nodes.new('ShaderNodeMix')
        node.data_type  = 'RGBA'
        node.blend_type = blend_type
        factor    = next(s for s in node.inputs
                         if s.name == 'Factor' and s.type == 'VALUE')
        colour_in = [s for s in node.inputs if s.type == 'RGBA']
        result    = next(s for s in node.outputs if s.type == 'RGBA')
        return node, factor, colour_in[0], colour_in[1], result

    node = nodes.new('ShaderNodeMixRGB')
    node.blend_type = blend_type
    return node, node.inputs[0], node.inputs[1], node.inputs[2], node.outputs[0]


def _new_separate_color(nodes):
    """Separate Color, or the pre-3.3 Separate RGB. Returns (node, [r, g, b])."""
    if hasattr(bpy.types, 'ShaderNodeSeparateColor'):
        node = nodes.new('ShaderNodeSeparateColor')
    else:
        node = nodes.new('ShaderNodeSeparateRGB')
    return node, [node.outputs[i] for i in range(3)]


def _flip_normal_green(nodes, links, source, location):
    """
    A DirectX normal map is an OpenGL one with green inverted.

    Two Vector Math nodes do it — (r, -g, b) then + (0, 1, 0) — and both exist
    unchanged in every Blender this add-on supports, where Separate/Combine
    Color were renamed in 3.3.
    """
    flip = nodes.new('ShaderNodeVectorMath')
    flip.operation = 'MULTIPLY'
    flip.location  = location
    flip.inputs[1].default_value = (1.0, -1.0, 1.0)
    links.new(source, flip.inputs[0])

    shift = nodes.new('ShaderNodeVectorMath')
    shift.operation = 'ADD'
    shift.location  = (location[0] + 190, location[1])
    shift.inputs[1].default_value = (0.0, 1.0, 0.0)
    links.new(flip.outputs['Vector'], shift.inputs[0])
    return shift.outputs['Vector']


def _set_alpha_blend(mat):
    """Turn on transparency, across the 4.2 EEVEE Next render-method rework."""
    if hasattr(mat, 'surface_render_method'):
        mat.surface_render_method = 'BLENDED'
        return
    for value in ('HASHED', 'BLEND'):
        try:
            mat.blend_method = value
            break
        except TypeError:
            continue
    if hasattr(mat, 'shadow_method'):
        try:
            mat.shadow_method = 'HASHED'
        except TypeError:
            pass


def _set_displacement_method(mat, method):
    """displacement_method moved off mat.cycles onto the material in 4.1."""
    try:
        if hasattr(mat, 'displacement_method'):
            mat.displacement_method = method
        elif hasattr(mat, 'cycles'):
            mat.cycles.displacement_method = method
    except (AttributeError, TypeError):
        pass


def _add_mapping(nodes, links, x=-2000):
    """Texture Coordinate ▸ Mapping, so a whole set tiles from one node."""
    coord = nodes.new('ShaderNodeTexCoord')
    coord.location = (x - 200, 0)
    mapping = nodes.new('ShaderNodeMapping')
    mapping.location = (x, 0)
    links.new(coord.outputs['UV'], mapping.inputs['Vector'])
    return mapping


def _texture_column(nodes, links, slots, x, y_top, mapping=None, index=None):
    """Lay one texture set out as a column. Returns {slot: image node}."""
    order  = [s for s in _SLOT_ORDER if s in slots]
    order += sorted(s for s in slots if s not in _SLOT_ORDER)

    tex = {}
    y   = y_top
    for slot in order:
        node = nodes.new('ShaderNodeTexImage')
        node.image    = _load_image(slots[slot], index)
        node.label    = slot.replace('EXTRA_', '').replace('_', ' ').title()
        node.location = (x, y)
        node.width    = 260
        if slot != 'BASE':
            _set_non_color(node.image)
        if mapping is not None:
            links.new(mapping.outputs['Vector'], node.inputs['Vector'])
        tex[slot] = node
        y -= 300
    return tex


def _canonical_sources(nodes, links, tex):
    """
    Reduce a texture set to canonical slots, each feeding a real socket.

    Packed maps are split, gloss is inverted into roughness and DirectX
    normals are flipped *here* rather than at wiring time, so that a blend of
    two sets mixes like with like — a gloss-based set against a roughness-based
    one still lines up. Returns ({slot: output socket}, [what was converted]).
    """
    source, notes = {}, []

    for packed, meaning in _PACKED_CHANNELS.items():
        node = tex.get(packed)
        if node is None:
            continue
        sep, outs = _new_separate_color(nodes)
        sep.location = (node.location.x + 320, node.location.y)
        links.new(node.outputs['Color'], sep.inputs[0])
        for out, target in zip(outs, meaning):
            source.setdefault(target, out)
        notes.append(f'{packed} split')

    for slot, node in tex.items():
        source.setdefault(slot, node.outputs['Color'])

    if 'ROUGH' not in source and 'GLOSS' in tex:
        node   = tex['GLOSS']
        invert = nodes.new('ShaderNodeInvert')
        invert.location = (node.location.x + 320, node.location.y)
        invert.label    = "Gloss to Roughness"
        links.new(node.outputs['Color'],
                  _socket(invert, 'Color') or invert.inputs[1])
        source['ROUGH'] = invert.outputs[0]
        notes.append('gloss inverted')

    if 'NORMAL' not in source and 'NORMAL_DX' in tex:
        node = tex['NORMAL_DX']
        source['NORMAL'] = _flip_normal_green(
            nodes, links, node.outputs['Color'],
            (node.location.x + 320, node.location.y))
        notes.append('DirectX normal flipped')

    # A Megascans '_Bump' map stands in for height when no displacement shipped.
    if 'DISP' not in source and 'BUMP' in source:
        source['DISP'] = source['BUMP']

    return source, notes


def _wire_principled(nodes, links, mat, bsdf, output, source,
                     use_ao=False, displacement='BUMP', displacement_scale=0.1):
    """Connect canonical sources to a Principled BSDF. Returns what it wired."""
    wired = []

    base_out = source.get('BASE')
    if base_out is not None:
        ao_out = source.get('AO')
        if use_ao and ao_out is not None:
            mix, fac, mix_a, mix_b, mix_out = _new_mix(nodes, 'MULTIPLY')
            mix.location = (-20, 300)
            mix.label    = "AO"
            fac.default_value = 1.0
            links.new(base_out, mix_a)
            links.new(ao_out, mix_b)
            base_out = mix_out
            wired.append('AO')
        links.new(base_out, bsdf.inputs['Base Color'])
        wired.append('base colour')

    for slot, socket_name, label in (('ROUGH', 'Roughness', 'roughness'),
                                     ('METAL', 'Metallic',  'metallic')):
        out = source.get(slot)
        if out is not None:
            links.new(out, bsdf.inputs[socket_name])
            wired.append(label)

    spec_out = source.get('SPEC')
    spec_in  = _socket(bsdf, 'Specular IOR Level', 'Specular')
    if spec_out is not None and spec_in is not None:
        links.new(spec_out, spec_in)
        wired.append('specular')

    alpha_out = source.get('ALPHA')
    if alpha_out is not None:
        links.new(alpha_out, bsdf.inputs['Alpha'])
        _set_alpha_blend(mat)
        wired.append('alpha')

    # Normal first, then bump on top of it: the Bump node takes the normal as
    # its own input, so height detail rides on the normal map rather than
    # replacing it.
    normal_out = None
    normal_src = source.get('NORMAL')
    if normal_src is not None:
        nmap = nodes.new('ShaderNodeNormalMap')
        nmap.location = (0, -520)
        links.new(normal_src, nmap.inputs['Color'])
        normal_out = nmap.outputs['Normal']
        wired.append('normal')

    height_out = source.get('DISP')

    if height_out is not None and displacement == 'BUMP':
        bump = nodes.new('ShaderNodeBump')
        bump.location = (140, -560)
        bump.inputs['Distance'].default_value = displacement_scale
        links.new(height_out, bump.inputs['Height'])
        if normal_out is not None:
            links.new(normal_out, bump.inputs['Normal'])
        normal_out = bump.outputs['Normal']
        wired.append('height as bump')

    elif height_out is not None and displacement == 'DISPLACEMENT':
        disp = nodes.new('ShaderNodeDisplacement')
        disp.location = (300, -700)
        disp.inputs['Midlevel'].default_value = 0.5
        disp.inputs['Scale'].default_value    = displacement_scale
        links.new(height_out, disp.inputs['Height'])
        links.new(disp.outputs['Displacement'], output.inputs['Displacement'])
        _set_displacement_method(mat, 'BOTH')
        wired.append('true displacement')

    if normal_out is not None:
        links.new(normal_out, bsdf.inputs['Normal'])

    return wired


def _new_shader_material(name):
    """An empty material with just a Principled and an Output. (mat, nodes, links, bsdf, output)"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (640, 0)
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (280, 0)
    links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
    return mat, nodes, links, bsdf, output


def _build_pbr_material(name, slots, use_ao=False, use_mapping=True,
                        displacement='BUMP', displacement_scale=0.1, index=None):
    """
    Wire one texture set into a Principled BSDF.

    Returns (material, [what was connected]) — the second half is what the
    operator prints, so a set that arrives half-named is visible rather than
    silently producing a flat grey material.
    """
    mat, nodes, links, bsdf, output = _new_shader_material(name)

    mapping = _add_mapping(nodes, links, -1180) if use_mapping else None
    tex     = _texture_column(nodes, links, slots, -820,
                              300 * (len(slots) - 1) // 2, mapping, index)
    source, notes = _canonical_sources(nodes, links, tex)

    wired = _wire_principled(nodes, links, mat, bsdf, output, source,
                             use_ao=use_ao, displacement=displacement,
                             displacement_scale=displacement_scale)
    return mat, notes + wired


def _assign_material(obj, mat):
    """
    Put mat in the object's first slot. True when the object can hold one.

    Through material_slots, like _replace_materials: a slot linked to the
    object renders its own material and ignores the mesh's, so writing to
    data.materials there changes nothing anyone can see.
    """
    data = getattr(obj, 'data', None)
    if data is None or not hasattr(data, 'materials'):
        return False
    if obj.material_slots:
        obj.material_slots[0].material = mat
    else:
        data.materials.append(mat)
    return True


def _replace_materials(obj, sources, blend):
    """
    Put the blend on the material this object actually renders.

    Which slot that is matters. Applying a second material over a whole mesh
    leaves every face on slot 1, so writing to slot 0 renders nowhere and the
    object looks completely untouched. Replacing *every* slot is no better: it
    collapses a deliberate two-material object into one and strips both sources
    off it.

    So: only the slots the faces actually use, and among those only the ones
    still holding one of the two sources. A slot nothing renders keeps its
    material. When nothing matches — a re-run, where the slots already hold the
    previous blend — the active slot is taken instead, so the object follows
    along rather than being silently left behind.

    Assignment goes through material_slots rather than mesh.materials: a slot
    linked to the object renders its own material and ignores the mesh's.

    Returns how many slots changed. The source materials themselves are never
    modified — only which slots point at them.
    """
    data = getattr(obj, 'data', None)
    if data is None or not hasattr(data, 'materials'):
        return 0

    slots = obj.material_slots
    if not slots:
        data.materials.append(blend)
        return 1

    polygons = getattr(data, 'polygons', None)
    if polygons:
        in_use = {poly.material_index for poly in polygons}
    else:
        in_use = set(range(len(slots)))     # curves and text have no polygons

    replaced = 0
    for index in sorted(in_use):
        if index >= len(slots):
            continue
        current = slots[index].material
        if current is not None and current.name in sources:
            slots[index].material = blend
            replaced += 1

    if replaced == 0:
        index = max(0, min(obj.active_material_index, len(slots) - 1))
        slots[index].material = blend
        replaced = 1
    return replaced


def _best_object_for(objects, base):
    """
    Pick the selected object whose name shares the most words with a texture
    set's name, so importing several sets at once doesn't stack them all onto
    one object. None when nothing shares a word.
    """
    wanted = {t for t in re.split(r'[^A-Za-z0-9]+', base.lower()) if len(t) > 2}
    best, best_score = None, 0
    for obj in objects:
        tokens = {t for t in re.split(r'[^A-Za-z0-9]+', obj.name.lower())
                  if len(t) > 2}
        score = len(wanted & tokens)
        if score > best_score:
            best, best_score = obj, score
    return best


class ET_OT_MegascansMaterial(bpy.types.Operator):
    bl_idname  = "et.megascans_material"
    bl_label   = "Megascans Material"
    bl_description = ("Pick a Megascans texture folder, or its files, and build a "
                      "fully wired PBR material for every set found")
    bl_options = {"REGISTER", "UNDO"}

    filepath:      bpy.props.StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    directory:     bpy.props.StringProperty(subtype='DIR_PATH',  options={'SKIP_SAVE'})
    files:         bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement,
                                                options={'SKIP_SAVE'})
    filter_image:  bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    scan_subfolders: bpy.props.BoolProperty(
        name="Scan Subfolders", default=False,
        description="With no files selected, walk the whole folder tree — one run "
                    "for a library of assets")
    resolution: bpy.props.EnumProperty(
        name="Resolution",
        items=[('HIGHEST', "Highest", "Take the largest when 2K and 4K sit side by side"),
               ('LOWEST',  "Lowest",  "Take the smallest, to keep the viewport light")],
        default='HIGHEST')
    displacement: bpy.props.EnumProperty(
        name="Height",
        items=[('BUMP',         "Bump",         "Bump node — works in EEVEE, no subdivision"),
               ('DISPLACEMENT', "Displacement", "Displacement output — Cycles, needs subdivision"),
               ('NONE',         "Load Only",    "Load the map but leave it unconnected")],
        default='BUMP')
    displacement_scale: bpy.props.FloatProperty(
        name="Height Scale", default=0.1, min=0.0, soft_max=1.0,
        description="Bump distance, or displacement scale, in metres")
    use_ao: bpy.props.BoolProperty(
        name="Multiply AO into Base Color", default=False,
        description="Off by default: baking occlusion into albedo double-darkens "
                    "under a renderer that already computes it")
    use_mapping: bpy.props.BoolProperty(
        name="Add Mapping Nodes", default=True,
        description="Texture Coordinate and Mapping in front of every texture, so "
                    "the whole set tiles and rotates from one place")
    assign_to_selected: bpy.props.BoolProperty(
        name="Assign to Selected", default=True,
        description="Put the material in the first slot of the selected objects, "
                    "replacing what is there")
    reuse_existing: bpy.props.BoolProperty(
        name="Reuse Existing", default=True,
        description="When a material of the same name already exists, use it "
                    "rather than building a second copy")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        box = layout.box()
        box.label(text="Source", icon='FILE_FOLDER')
        box.prop(self, 'scan_subfolders')
        box.prop(self, 'resolution')

        box = layout.box()
        box.label(text="Build", icon='NODETREE')
        box.prop(self, 'displacement')
        row = box.row()
        row.enabled = self.displacement != 'NONE'
        row.prop(self, 'displacement_scale')
        box.prop(self, 'use_mapping')
        box.prop(self, 'use_ao')

        box = layout.box()
        box.label(text="Result", icon='MATERIAL')
        box.prop(self, 'reuse_existing')
        box.prop(self, 'assign_to_selected')

    def _selected_paths(self):
        directory = self.directory
        if not directory and self.filepath:
            directory = os.path.dirname(self.filepath)

        names = [f.name for f in self.files if f.name]
        if names:
            paths = [os.path.join(directory, name) for name in names]
            return [p for p in paths if os.path.isfile(p)]

        if not directory or not os.path.isdir(directory):
            return []

        if self.scan_subfolders:
            found = []
            for root, _dirs, files in os.walk(directory):
                found.extend(os.path.join(root, name) for name in files)
            return found

        return [os.path.join(directory, name) for name in os.listdir(directory)
                if os.path.isfile(os.path.join(directory, name))]

    def execute(self, context):
        paths = self._selected_paths()
        if not paths:
            self.report({'ERROR'}, "Select texture files, or a folder holding them")
            return {'CANCELLED'}

        sets = _collect_texture_sets(paths, self.resolution == 'HIGHEST')
        if not sets:
            self.report({'ERROR'},
                        f"No recognised maps among {len(paths)} file(s) — expected "
                        f"names like Rock_4K_Albedo.jpg")
            return {'CANCELLED'}

        built, reused = [], 0
        # One index for the whole run: a library import loads hundreds of
        # textures, and rescanning bpy.data.images for each is quadratic.
        index = _index_images()
        for base, slots in sorted(sets.items()):
            existing = bpy.data.materials.get(base)
            if existing is not None and self.reuse_existing:
                built.append((base, existing))
                reused += 1
                continue

            try:
                mat, wired = _build_pbr_material(
                    base, slots,
                    use_ao=self.use_ao,
                    use_mapping=self.use_mapping,
                    displacement=self.displacement,
                    displacement_scale=self.displacement_scale,
                    index=index)
            except RuntimeError as exc:
                # A texture that vanished between listing and loading. Report
                # it rather than throwing a traceback at the user.
                self.report({'ERROR'}, f"Could not build '{base}': {exc}")
                return {'CANCELLED'}
            built.append((base, mat))
            print(f"[EasyTasks] {mat.name}: {len(slots)} map(s), wired "
                  f"{', '.join(wired) if wired else 'nothing'}")

        assigned = 0
        if self.assign_to_selected:
            # execute() runs with the File Browser in context, where
            # context.selected_objects is not guaranteed to exist. The view
            # layer carries the same selection and is always there.
            objects = [o for o in context.view_layer.objects
                       if o.select_get()
                       and getattr(o.data, 'materials', None) is not None]
            if len(built) == 1:
                for obj in objects:
                    assigned += _assign_material(obj, built[0][1])
            else:
                for base, mat in built:
                    match = _best_object_for(objects, base)
                    if match is not None:
                        assigned += _assign_material(match, mat)

        names   = ', '.join(name for name, _mat in built[:3])
        more    = f" +{len(built) - 3}" if len(built) > 3 else ""
        summary = f"{len(built)} material(s): {names}{more}"
        if reused:
            summary += f" ({reused} reused)"
        if self.assign_to_selected:
            summary += f", assigned to {assigned} object(s)"
        self.report({'INFO'}, summary)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Blend two PBR materials through one mask, paintable by hand
# ---------------------------------------------------------------------------

_MASK_GROUP_NAME  = "ET Blend Mask"
_MASK_NODE_NAME   = "ET_BlendMask"
_BLEND_PAINT_ATTR = "ET_BlendPaint"
_BLEND_PAINT_UV   = "ET_BlendPaintUV"
_BLEND_PAINT_NODE = "ET_BlendPaintSource"

# Every input the mask group is expected to carry. A .blend saved by an older
# version has a group without the directional sockets, so the builder checks
# this before reusing one it found by name.
_MASK_INPUTS = ('Height', 'Paint', 'Amount', 'Softness', 'Noise Scale',
                'Paint Influence', 'Direction', 'Directionality',
                'Direction Falloff', 'Gradient')

# Which way "up" is for a directional blend. World space, so rotating the asset
# does not carry the snow around with it.
_DIRECTION_VECTORS = {
    'UP':    (0.0, 0.0, 1.0),
    'DOWN':  (0.0, 0.0, -1.0),
    'X':     (1.0, 0.0, 0.0),
    'NEG_X': (-1.0, 0.0, 0.0),
    'Y':     (0.0, 1.0, 0.0),
    'NEG_Y': (0.0, -1.0, 0.0),
}

# Only canonical slots take part in a blend. The raw GLOSS / NORMAL_DX keys are
# already folded into ROUGH / NORMAL by _canonical_sources, and the extras are
# unconnected by design.
_BLEND_SLOTS = ('BASE', 'ROUGH', 'METAL', 'SPEC', 'ALPHA', 'NORMAL', 'DISP', 'AO')

# What a missing map means, as the colour a Mix input takes in its place: the
# value the Principled BSDF uses when nothing is plugged in (or, for the
# normal, a flat tangent-space normal and, for height, the displacement
# mid-level). Mixing against these keeps a map only one side has inside the
# mask instead of spreading it across the whole surface.
_NEUTRAL_RGBA = {
    'BASE':   (0.8, 0.8, 0.8, 1.0),
    'ROUGH':  (0.5, 0.5, 0.5, 1.0),
    'METAL':  (0.0, 0.0, 0.0, 1.0),
    'SPEC':   (0.5, 0.5, 0.5, 1.0),
    'ALPHA':  (1.0, 1.0, 1.0, 1.0),
    'NORMAL': (0.5, 0.5, 1.0, 1.0),
    'DISP':   (0.5, 0.5, 0.5, 1.0),
    'AO':     (1.0, 1.0, 1.0, 1.0),
}

# Base-only detail maps pass straight through: the overlay has nothing to say
# about them, and flattening the base's height or normals everywhere would be
# a loss, not a blend.
_KEEP_BASE_DETAIL = {'NORMAL', 'DISP'}

# Starting points for the mask, because the useful values are not guessable and
# every one of these took a round of trial and error to find. Keys match the
# operator's own property names so a preset can be applied to either the dialog
# or a finished material.
_BLEND_PRESETS = {
    'DIRT': dict(
        label="Dirt in Crevices", amount=0.40, softness=0.30, noise_scale=4.0,
        direction='NONE', direction_mode='GRADIENT', direction_falloff=1.0,
        description="Settles into the low spots of the base — the default use"),
    'PUDDLES': dict(
        label="Puddles / Wet", amount=0.55, softness=0.12, noise_scale=2.0,
        direction='NONE', direction_mode='GRADIENT', direction_falloff=1.0,
        description="Tight edges pooling in the deepest parts, like standing water"),
    'GRIME_RISE': dict(
        label="Grime up a Wall", amount=0.70, softness=0.40, noise_scale=5.0,
        direction='DOWN', direction_mode='GRADIENT', direction_falloff=0.55,
        description="Strongest at the base and fading upward — splash and damp"),
    'SNOW': dict(
        label="Snow on Top", amount=0.60, softness=0.25, noise_scale=3.0,
        direction='UP', direction_mode='FACING', direction_falloff=0.60,
        description="Upward faces only, with a crisp edge. Needs real form, "
                    "not a flat surface"),
    'DUST': dict(
        label="Settled Dust", amount=0.35, softness=0.55, noise_scale=6.0,
        direction='UP', direction_mode='FACING', direction_falloff=1.0,
        description="A soft veil on anything facing up"),
    'MOSS': dict(
        label="Moss / Damp Side", amount=0.50, softness=0.35, noise_scale=3.0,
        direction='UP', direction_mode='FACING', direction_falloff=0.85,
        description="Broken-up growth on upward and angled faces"),
    'LEAK_DOWN': dict(
        label="Streaks from Above", amount=0.55, softness=0.35, noise_scale=8.0,
        direction='UP', direction_mode='GRADIENT', direction_falloff=0.70,
        description="Strongest at the top, running down — rust and water stains"),
}

_PRESET_ORDER = ('DIRT', 'PUDDLES', 'GRIME_RISE', 'SNOW', 'DUST', 'MOSS',
                 'LEAK_DOWN')


def _preset_items(_self=None, _context=None):
    """Enum entries for the presets, plus a Custom that changes nothing."""
    items = [('CUSTOM', "Custom", "Leave the settings as they are")]
    items += [(key, _BLEND_PRESETS[key]['label'], _BLEND_PRESETS[key]['description'])
              for key in _PRESET_ORDER]
    return items


def _active_blend_mask(context):
    """
    The Blend Mask node on the active object's active material, or None.

    Everything the mask does lives on this one node's inputs, so the panel can
    edit a finished blend in place instead of the dialog having to rebuild the
    material — which would throw away the paint layer every time you nudged a
    slider.
    """
    obj = getattr(context, 'active_object', None)
    mat = getattr(obj, 'active_material', None)
    if mat is None or not getattr(mat, 'use_nodes', False):
        return None
    return mat.node_tree.nodes.get(_MASK_NODE_NAME)


def _direction_coverage(objects, vector):
    """
    Best alignment between any face on these objects and a world direction.

    Face-angle blending is silent when it fails: point it Up at a vertical wall
    and every face scores zero, the gate shuts, and the overlay vanishes
    entirely — which reads as the tool being broken rather than as the answer
    being "nothing here faces that way". Sampling lets the operator say so.

    Returns -1.0 when there are no faces to judge.
    """
    axis = mathutils.Vector(vector).normalized()
    best = -1.0
    for obj in objects:
        mesh = getattr(obj, 'data', None)
        polygons = getattr(mesh, 'polygons', None)
        if not polygons:
            continue
        # normals transform by the inverse transpose, not the matrix itself
        rotation = obj.matrix_world.to_3x3().inverted_safe().transposed()
        step = max(1, len(polygons) // 200)
        for index in range(0, len(polygons), step):
            normal = rotation @ polygons[index].normal
            if normal.length_squared > 0.0:
                best = max(best, normal.normalized().dot(axis))
    return best


def _paint_uv_layer(obj):
    """
    A UV layer the mask can be painted on without the strokes repeating.

    Tiling surfaces carry UVs that run well past 0-1 so the textures repeat —
    exactly what you want for the Megascans set, and fatal for a paint mask.
    Every surface point then shares wrapped coordinates with every other tile,
    so one stroke appears in all of them at once.

    Scaling a copy of the layout down into 0-1 keeps the arrangement and makes
    each point unique. The textures are unaffected: they read the active
    *render* UV map, while painting goes through the active one, and those are
    separate flags.

    Every object gets the layer under the same name, even one whose UVs are
    already inside 0-1 (it gets a plain copy). The mask is sampled through a UV
    Map node in the material, which every object on that material shares; a
    name that was one object's own layer meant a tiled layer, or nothing, on
    the others — so their paint repeated or vanished.

    Returns the layer to paint through, or None if the mesh has no UVs. At
    Blender's eight-layer limit it returns None too: there is no consistent
    layer to give, and painting through a mismatched one is the bug above.
    """
    mesh   = getattr(obj, 'data', None)
    layers = getattr(mesh, 'uv_layers', None)
    if not layers:
        return None

    existing = layers.get(_BLEND_PAINT_UV)
    if existing is not None:
        return existing

    # Copy the layout the textures use, not whatever happens to be selected.
    source = next((l for l in layers if l.active_render), None) \
        or layers.active or layers[0]
    count  = len(source.data)
    if not count:
        return None

    # foreach_get, not a comprehension: a dense mesh has hundreds of thousands
    # of loops and stepping through them in Python takes seconds.
    flat = [0.0] * (count * 2)
    source.data.foreach_get('uv', flat)

    low_u,  high_u = min(flat[0::2]), max(flat[0::2])
    low_v,  high_v = min(flat[1::2]), max(flat[1::2])
    in_range = (-0.001 <= low_u and high_u <= 1.001
                and -0.001 <= low_v and high_v <= 1.001)

    previous_active = layers.active
    try:
        layer = layers.new(name=_BLEND_PAINT_UV, do_init=False)
    except (RuntimeError, TypeError):
        return None                        # at the 8 layer limit
    if layer is None:
        return None

    if not in_range:
        # One scale for both axes, so the layout keeps its proportions instead
        # of being squashed to fit the square.
        span = max(high_u - low_u, high_v - low_v) or 1.0
        flat[0::2] = [(u - low_u) / span for u in flat[0::2]]
        flat[1::2] = [(v - low_v) / span for v in flat[1::2]]
    layer.data.foreach_set('uv', flat)

    # new() makes itself active, which would silently switch the UV layer under
    # every object we set up alongside the painted one. _enter_paint selects it
    # deliberately for the object actually being painted.
    if previous_active is not None:
        layers.active = previous_active
    return layer


# Linear 0.5 stored as an sRGB byte. Legacy vertex colours hold sRGB bytes that
# the shader linearises on read, so writing a plain 0.5 would arrive as ~0.21 —
# an offset of -0.58 that silently drags the whole blend toward the base.
_SRGB_HALF = 0.7353569830524495


def _paint_color_layer(mesh):
    """
    Find or create the vertex-colour paint layer, initialised to neutral grey,
    and make it the one vertex paint writes to.

    Mesh.color_attributes arrived in Blender 3.2; on 3.0 and 3.1 the layer has
    to be a legacy vertex_colors one. The shader reads both by name through the
    same Attribute node, so the rest of the blend does not care which it got.
    """
    attributes = getattr(mesh, 'color_attributes', None)
    if attributes is not None:
        layer = attributes.get(_BLEND_PAINT_ATTR)
        if layer is None:
            layer = attributes.new(_BLEND_PAINT_ATTR, 'FLOAT_COLOR', 'CORNER')
            # float colour attributes are linear, so mid grey is simply 0.5
            layer.data.foreach_set('color', [0.5, 0.5, 0.5, 1.0] * len(layer.data))
        attributes.active_color_index = attributes.find(layer.name)
        return layer

    layer = mesh.vertex_colors.get(_BLEND_PAINT_ATTR)
    if layer is None:
        layer = mesh.vertex_colors.new(name=_BLEND_PAINT_ATTR)
        grey  = [_SRGB_HALF, _SRGB_HALF, _SRGB_HALF, 1.0]
        layer.data.foreach_set('color', grey * len(layer.data))
    mesh.vertex_colors.active = layer
    return layer


def _current_paint_type(node):
    """'IMAGE' or 'VERTEX' for the layer feeding the mask's Paint input, or None."""
    socket = node.inputs.get('Paint') if node is not None else None
    if socket is None or not socket.links:
        return None
    kind = socket.links[0].from_node.type
    return {'TEX_IMAGE': 'IMAGE', 'ATTRIBUTE': 'VERTEX'}.get(kind)


def _default_paint_type(obj, node):
    """
    Which layer a plain click on Paint Blend Mask should use.

    The one the material already paints with, so the toggle never quietly
    swaps layers; otherwise an image, unless the object has no UVs to paint
    an image through — then vertex colour, rather than an error.
    """
    current = _current_paint_type(node)
    if current is not None:
        return current
    has_uvs = bool(getattr(getattr(obj, 'data', None), 'uv_layers', None))
    return 'IMAGE' if has_uvs else 'VERTEX'


def _paint_needs_setup(node, mask_type):
    """
    True the first time an image mask is made on this material: the one moment
    there is a real choice to offer (its resolution). Vertex colour has none,
    and an existing image layer is simply reused.
    """
    if mask_type != 'IMAGE' or node is None:
        return False
    return not any(n.type == 'TEX_IMAGE' and n.name.startswith(_BLEND_PAINT_NODE)
                   for n in node.id_data.nodes)


def _blend_paint_image(node):
    """The image feeding a Blend Mask node's Paint input, if it is one."""
    socket = node.inputs.get('Paint') if node is not None else None
    links  = socket.links if socket is not None else ()
    if links and links[0].from_node.type == 'TEX_IMAGE':
        return links[0].from_node.image
    return None


def _blend_dir():
    """Folder of the open .blend, or '' if it has never been saved."""
    return os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""


def _blend_stem():
    """File name of the open .blend without its extension, or ''."""
    return os.path.splitext(os.path.basename(bpy.data.filepath))[0] \
        if bpy.data.filepath else ""


def _mask_file_path(image):
    """
    Where a painted mask lives on disk: textures/ beside the .blend.

    The file name carries the .blend's own name as well as the image's. Keyed
    on the image alone — which is the material's name — a second .blend in the
    same folder with a material of the same name silently overwrote the first
    one's painting. bpy.path.clean_name keeps characters a material name may
    hold but a file name may not out of the path.
    """
    stem = bpy.path.clean_name(f"{_blend_stem()}_{image.name}" if _blend_stem()
                               else image.name)
    return os.path.join(_blend_dir(), "textures", f"{stem}.png")


def _save_blend_mask_image(image):
    """
    Make a painted mask's pixels durable. Returns True when something was kept.

    Paint lives in memory until something writes it, and a generated image is
    rebuilt from its flat grey on every reload — so an unsaved mask renders
    blank and the blend looks like it ignored the painting entirely.

      * already has a file      -> save over it
      * .blend saved, no file   -> give it one in textures/ and save
      * .blend never saved      -> pack it, so the pixels travel in the .blend

    A painted generated image packs its current buffer. (An *unpainted* one
    does not, which is what made this look impossible earlier.) Order matters
    for file images: packing a dirty one packs the stale file and marks the
    buffer clean, throwing the paint away — save first, then refresh the pack.
    """
    if image is None or not image.is_dirty:
        return False
    try:
        if not image.filepath_raw and _blend_dir():
            path = _mask_file_path(image)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            image.filepath_raw = path
            image.file_format  = 'PNG'
        if image.filepath_raw:
            image.save()
            if image.packed_file is not None:
                image.pack()          # refresh the packed copy from the file
            return True
        image.pack()
        return image.packed_file is not None
    except (RuntimeError, OSError):
        return False


@bpy.app.handlers.persistent
def _save_masks_before_file_save(*_args):
    """
    Keep painted masks whenever the .blend is saved.

    Leaving paint mode through the Blend Mask button saves the mask, but leaving
    any other way — Tab, the mode menu, another tool — did not, and Ctrl+S
    then wrote a file whose mask reloads as flat grey.
    """
    for mat in bpy.data.materials:
        if mat.library is not None or not getattr(mat, 'use_nodes', False):
            continue
        node = mat.node_tree.nodes.get(_MASK_NODE_NAME)
        if node is not None:
            _save_blend_mask_image(_blend_paint_image(node))


class ET_OT_SaveBlendMask(bpy.types.Operator):
    bl_idname  = "et.save_blend_mask"
    bl_label   = "Save Painted Mask"
    bl_description = ("Write the painted mask to disk. Until it is saved the "
                      "paint exists only in memory and a fresh render or a "
                      "reopened file sees an unpainted mask")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _blend_paint_image(_active_blend_mask(context)) is not None

    def execute(self, context):
        image = _blend_paint_image(_active_blend_mask(context))
        if not image.is_dirty:
            self.report({'INFO'}, f"{image.name} is already saved")
            return {"FINISHED"}
        if not _save_blend_mask_image(image):
            self.report({'ERROR'},
                        f"Could not write {image.name} — check the textures folder "
                        f"beside the .blend is writable")
            return {'CANCELLED'}
        if image.filepath_raw:
            self.report({'INFO'}, f"Saved {image.name} to textures/")
        else:
            self.report({'INFO'},
                        f"Packed {image.name} into the .blend — save the file to keep it")
        return {"FINISHED"}


def _material_users(mat):
    """Mesh objects carrying this material in any slot."""
    return [obj for obj in bpy.data.objects
            if getattr(obj, 'type', None) == 'MESH'
            and any(slot.material == mat for slot in obj.material_slots)]


def _gradient_span(objects, vector):
    """
    How much depth these objects have along an axis, as a share of their size.

    The ramp reads generated coordinates, which normalise to the bounding box.
    An axis the object is flat in — Up on a floor plane, whose local Z extent
    is zero — has nothing to ramp across, so the gradient comes out uniform and
    looks exactly like the feature doing nothing.

    Returns 0..1 across the objects; near zero means no usable ramp.
    """
    axis = mathutils.Vector(vector).normalized()
    best = 0.0
    for obj in objects:
        corners = [mathutils.Vector(corner) for corner in
                   getattr(obj, 'bound_box', ()) or ()]
        if not corners:
            continue
        try:
            local = obj.matrix_world.to_3x3().inverted() @ axis
        except ValueError:
            continue
        if local.length_squared == 0.0:
            continue
        local.normalize()

        along = [corner.dot(local) for corner in corners]
        widest = max((max(c[i] for c in corners) - min(c[i] for c in corners))
                     for i in range(3))
        if widest > 0.0:
            best = max(best, (max(along) - min(along)) / widest)
    return best


def _new_group_socket(group, name, socket_type, in_out, default=None,
                      min_value=None, max_value=None):
    """Node group sockets moved from group.inputs to group.interface in 4.0."""
    if hasattr(group, 'interface'):
        sock = group.interface.new_socket(name=name, in_out=in_out,
                                          socket_type=socket_type)
    else:
        collection = group.inputs if in_out == 'INPUT' else group.outputs
        sock = collection.new(socket_type, name)
    if min_value is not None:
        sock.min_value = min_value
    if max_value is not None:
        sock.max_value = max_value
    if default is not None:
        sock.default_value = default
    return sock


def _group_input_names(group):
    """Input socket names of a node group, across the 4.0 interface rework."""
    if hasattr(group, 'interface'):
        return {item.name for item in group.interface.items_tree
                if getattr(item, 'in_out', None) == 'INPUT'}
    return {sock.name for sock in group.inputs}


def _blend_mask_group():
    """
    The node group behind Blend Materials, built once and shared.

    Three things decide the mask, in order:

      * height — the overlay collects in the low spots, inverted from the base
        set's own displacement and broken up by noise;
      * direction — a gate from the angle between the surface and a world axis,
        so snow lands on top and streaks run down verticals;
      * paint — a *signed* offset around mid grey: 0.5 changes nothing, white
        forces the overlay in, black scrubs it back to the base.

    Paint is applied last, after the gate, so a hand stroke always wins — you
    can still put snow on the underside of a ledge if the shot needs it.

    Sharing one group keeps the maths in a single place while the sliders stay
    per-material, because the values live on the group node instance.
    """
    group = bpy.data.node_groups.get(_MASK_GROUP_NAME)
    if group is not None and set(_MASK_INPUTS) <= _group_input_names(group):
        return group
    # Anything else is a group from an older version of the add-on. Leave it
    # exactly as it is so materials already pointing at it keep working, and
    # build beside it — bpy uniquifies the name.

    group = bpy.data.node_groups.new(_MASK_GROUP_NAME, 'ShaderNodeTree')
    _new_group_socket(group, 'Height',      'NodeSocketFloat', 'INPUT', 0.5)
    _new_group_socket(group, 'Paint',       'NodeSocketFloat', 'INPUT', 0.5)
    _new_group_socket(group, 'Amount',      'NodeSocketFloat', 'INPUT', 0.35, 0.0, 1.0)
    _new_group_socket(group, 'Softness',    'NodeSocketFloat', 'INPUT', 0.25, 0.01, 1.0)
    _new_group_socket(group, 'Noise Scale', 'NodeSocketFloat', 'INPUT', 4.0, 0.0, 1000.0)
    _new_group_socket(group, 'Paint Influence',
                      'NodeSocketFloat', 'INPUT', 1.0, 0.0, 1.0)
    _new_group_socket(group, 'Direction',
                      'NodeSocketVector', 'INPUT', (0.0, 0.0, 1.0))
    _new_group_socket(group, 'Directionality',
                      'NodeSocketFloat', 'INPUT', 0.0, 0.0, 1.0)
    _new_group_socket(group, 'Direction Falloff',
                      'NodeSocketFloat', 'INPUT', 1.0, 0.01, 1.0)
    _new_group_socket(group, 'Gradient', 'NodeSocketFloat', 'INPUT', 1.0, 0.0, 1.0)
    _new_group_socket(group, 'Fac', 'NodeSocketFloat', 'OUTPUT')

    nodes, links = group.nodes, group.links
    gin  = nodes.new('NodeGroupInput')
    gin.location = (-900, 0)
    gout = nodes.new('NodeGroupOutput')
    gout.location = (700, 0)

    def math(operation, x, y, label=None, clamp=False):
        node = nodes.new('ShaderNodeMath')
        node.operation = operation
        node.location  = (x, y)
        node.use_clamp = clamp
        if label:
            node.label = label
        return node

    # 1 - height, so the mask is strongest in the crevices
    invert = math('SUBTRACT', -680, 140, "Invert Height")
    invert.inputs[0].default_value = 1.0
    links.new(gin.outputs['Height'], invert.inputs[1])

    noise = nodes.new('ShaderNodeTexNoise')
    noise.location = (-680, -180)
    links.new(gin.outputs['Noise Scale'], noise.inputs['Scale'])

    total = math('ADD', -460, 40)
    links.new(invert.outputs[0], total.inputs[0])
    links.new(noise.outputs['Fac'], total.inputs[1])
    average = math('MULTIPLY', -280, 40, "Average")
    links.new(total.outputs[0], average.inputs[0])
    average.inputs[1].default_value = 0.5

    # Amount slides the cut, Softness widens the gradient either side of it.
    centre = math('SUBTRACT', -460, 400, "Threshold")
    centre.inputs[0].default_value = 1.0
    links.new(gin.outputs['Amount'], centre.inputs[1])
    low = math('SUBTRACT', -280, 460)
    links.new(centre.outputs[0], low.inputs[0])
    links.new(gin.outputs['Softness'], low.inputs[1])
    high = math('ADD', -280, 300)
    links.new(centre.outputs[0], high.inputs[0])
    links.new(gin.outputs['Softness'], high.inputs[1])

    # Direction: how much this face points the way we care about. The Geometry
    # node's normal is world space, so rotating the asset does not carry the
    # snow around with it.
    geometry = nodes.new('ShaderNodeNewGeometry')
    geometry.location = (-900, -760)

    axis = nodes.new('ShaderNodeVectorMath')
    axis.operation = 'NORMALIZE'
    axis.location  = (-680, -940)
    links.new(gin.outputs['Direction'], axis.inputs[0])

    facing = nodes.new('ShaderNodeVectorMath')
    facing.operation = 'DOT_PRODUCT'
    facing.location  = (-460, -800)
    facing.label     = "Face vs Direction"
    links.new(geometry.outputs['Normal'], facing.inputs[0])
    links.new(axis.outputs['Vector'], facing.inputs[1])

    # Falloff near 0 keeps only faces pointing almost exactly that way; near 1
    # spreads the gradient across the whole sphere. MULTIPLY then ADD rather
    # than one MULTIPLY_ADD: its three sockets are all named 'Value', so the
    # operand order is an assumption, and these two are symmetric.
    scaled_fall = math('MULTIPLY', -460, -1060, "Falloff Window")
    links.new(gin.outputs['Direction Falloff'], scaled_fall.inputs[0])
    scaled_fall.inputs[1].default_value = -2.0
    window = math('ADD', -280, -1060)
    links.new(scaled_fall.outputs[0], window.inputs[0])
    window.inputs[1].default_value = 1.0

    aligned = nodes.new('ShaderNodeMapRange')
    aligned.location = (-80, -820)
    links.new(facing.outputs['Value'], aligned.inputs['Value'])
    links.new(window.outputs[0], aligned.inputs['From Min'])
    aligned.inputs['From Max'].default_value = 1.0

    # Gradient: where a point sits along the axis, rather than which way it
    # faces. Generated coordinates are already normalised 0-1 over the object's
    # bounds, so this is a true 0-to-100 ramp across any mesh at any scale —
    # and unlike face angle it still means something on a flat wall, where
    # every normal is identical and the angle can only shift the whole surface
    # up or down at once.
    coord = nodes.new('ShaderNodeTexCoord')
    coord.location = (-900, -1320)

    centred = nodes.new('ShaderNodeVectorMath')
    centred.operation = 'SUBTRACT'
    centred.location  = (-680, -1320)
    centred.label     = "Centre Bounds"
    links.new(coord.outputs['Generated'], centred.inputs[0])
    centred.inputs[1].default_value = (0.5, 0.5, 0.5)

    # Generated coordinates are *object* space; Direction is world space. A
    # wall modelled flat and stood up by its object rotation has its height on
    # local Y, so dotting world Z straight against it reads the plane's
    # degenerate axis and the ramp comes out perfectly flat. Bring the axis
    # into the same space first.
    axis_local = nodes.new('ShaderNodeVectorTransform')
    axis_local.vector_type  = 'VECTOR'
    axis_local.convert_from = 'WORLD'
    axis_local.convert_to   = 'OBJECT'
    axis_local.location     = (-900, -1500)
    links.new(gin.outputs['Direction'], axis_local.inputs[0])

    # Object scale changes the transformed length, so normalise after.
    axis_norm = nodes.new('ShaderNodeVectorMath')
    axis_norm.operation = 'NORMALIZE'
    axis_norm.location  = (-680, -1500)
    links.new(axis_local.outputs[0], axis_norm.inputs[0])

    # Centring means a negative axis reads 0-1 the same way a positive one
    # does, so Down is simply Up with the ramp the other way round.
    along = nodes.new('ShaderNodeVectorMath')
    along.operation = 'DOT_PRODUCT'
    along.location  = (-460, -1320)
    along.label     = "Position Along Axis"
    links.new(centred.outputs['Vector'], along.inputs[0])
    links.new(axis_norm.outputs['Vector'], along.inputs[1])

    where = math('ADD', -280, -1320)
    links.new(along.outputs['Value'], where.inputs[0])
    where.inputs[1].default_value = 0.5

    # Falloff is the share of the mesh the ramp spans: 1 covers all of it,
    # 0.3 keeps the transition to the last third before the axis runs out.
    ramp_start = math('SUBTRACT', -280, -1500, "Ramp Start")
    ramp_start.inputs[0].default_value = 1.0
    links.new(gin.outputs['Direction Falloff'], ramp_start.inputs[1])

    gradient = nodes.new('ShaderNodeMapRange')
    gradient.location = (-80, -1320)
    links.new(where.outputs[0], gradient.inputs['Value'])
    links.new(ramp_start.outputs[0], gradient.inputs['From Min'])
    gradient.inputs['From Max'].default_value = 1.0
    # Linear clamps flat below the start and then climbs at full slope, which
    # leaves a hard line across the surface exactly where the ramp begins.
    # Smoothstep eases in and out, so the ramp has no seam at either end.
    try:
        gradient.interpolation_type = 'SMOOTHSTEP'
    except (AttributeError, TypeError):
        pass

    # Gradient picks which of the two drives the gate: 0 face angle, 1 ramp.
    spread = math('SUBTRACT', 120, -1180)
    links.new(gradient.outputs['Result'], spread.inputs[0])
    links.new(aligned.outputs['Result'], spread.inputs[1])
    chosen = math('MULTIPLY', 300, -1180)
    links.new(spread.outputs[0], chosen.inputs[0])
    links.new(gin.outputs['Gradient'], chosen.inputs[1])
    combined = math('ADD', 480, -1180, "Facing or Gradient")
    links.new(aligned.outputs['Result'], combined.inputs[0])
    links.new(chosen.outputs[0], combined.inputs[1])

    # Directionality 0 must leave the gate wide open, so a material built
    # before this existed behaves exactly as it always did: 1 + d * (a - 1).
    swing = math('SUBTRACT', 660, -900, "Gate")
    links.new(combined.outputs[0], swing.inputs[0])
    swing.inputs[1].default_value = 1.0
    weighted = math('MULTIPLY', 300, -900)
    links.new(swing.outputs[0], weighted.inputs[0])
    links.new(gin.outputs['Directionality'], weighted.inputs[1])
    gate = math('ADD', 480, -900)
    links.new(weighted.outputs[0], gate.inputs[0])
    gate.inputs[1].default_value = 1.0

    shape = nodes.new('ShaderNodeMapRange')
    shape.location = (160, 180)
    links.new(average.outputs[0], shape.inputs['Value'])
    links.new(low.outputs[0],  shape.inputs['From Min'])
    links.new(high.outputs[0], shape.inputs['From Max'])

    # The gate multiplies the *shaped* mask, not the raw height. Gating first
    # and thresholding after squeezes the ramp into whatever narrow band the
    # threshold leaves — it could reach neither 0 nor 1, so a gradient could
    # never actually run from none of the overlay to all of it. This way the
    # direction scales the finished mask: 0 where the axis runs out, full
    # strength where it points.
    gated = math('MULTIPLY', 400, 120, "Mask x Direction")
    links.new(shape.outputs['Result'], gated.inputs[0])
    links.new(gate.outputs[0], gated.inputs[1])

    # paint, as a signed offset around mid grey
    offset = math('SUBTRACT', -460, -420, "Paint Offset")
    links.new(gin.outputs['Paint'], offset.inputs[0])
    offset.inputs[1].default_value = 0.5
    doubled = math('MULTIPLY', -280, -420)
    links.new(offset.outputs[0], doubled.inputs[0])
    doubled.inputs[1].default_value = 2.0
    scaled = math('MULTIPLY', -60, -420)
    links.new(doubled.outputs[0], scaled.inputs[0])
    links.new(gin.outputs['Paint Influence'], scaled.inputs[1])

    # Paint goes on last, after the gate, so a hand stroke always wins.
    final = math('ADD', 600, 0, "Mask", clamp=True)
    links.new(gated.outputs[0], final.inputs[0])
    links.new(scaled.outputs[0], final.inputs[1])
    links.new(final.outputs[0], gout.inputs[0])
    return group


def _material_texture_slots(mat):
    """
    Recover {slot: filepath} from a material's image nodes.

    Runs the same filename parser the importer uses, so it reads materials this
    add-on built and any other sanely-named set equally. Images with no file
    behind them (generated, packed-only) are skipped — the builder loads from
    disk.
    """
    if mat is None or not getattr(mat, 'use_nodes', False):
        return {}

    found = {}
    for node in mat.node_tree.nodes:
        if node.type != 'TEX_IMAGE' or node.image is None or not node.image.filepath:
            continue
        path = bpy.path.abspath(node.image.filepath)
        stem = os.path.splitext(os.path.basename(path))[0]
        _base, slot, _res = _split_texture_name(stem)
        if slot is not None:
            found.setdefault(slot, path)
    return found


def _build_blend_material(name, slots_a, slots_b, use_ao=False, use_mapping=True,
                          displacement='BUMP', displacement_scale=0.1,
                          amount=0.35, noise_scale=4.0, softness=0.25,
                          direction='NONE', directionality=1.0,
                          direction_falloff=1.0, direction_mode='GRADIENT'):
    """Mix two texture sets through one shared mask. Returns (material, notes)."""
    mat, nodes, links, bsdf, output = _new_shader_material(name)
    mapping = _add_mapping(nodes, links, -2300) if use_mapping else None

    index = _index_images()
    tex_a = _texture_column(nodes, links, slots_a, -1900, 400, mapping, index)
    tex_b = _texture_column(nodes, links, slots_b, -1900,
                            100 - 300 * len(slots_a), mapping, index)
    source_a, notes_a = _canonical_sources(nodes, links, tex_a)
    source_b, notes_b = _canonical_sources(nodes, links, tex_b)

    mask_node = nodes.new('ShaderNodeGroup')
    mask_node.node_tree = _blend_mask_group()
    mask_node.name      = _MASK_NODE_NAME
    mask_node.label     = "Blend Mask"
    mask_node.location  = (-1180, 620)
    mask_node.width     = 230
    mask_node.inputs['Amount'].default_value      = amount
    mask_node.inputs['Softness'].default_value    = softness
    mask_node.inputs['Noise Scale'].default_value = noise_scale

    vector = _DIRECTION_VECTORS.get(direction)
    mask_node.inputs['Gradient'].default_value = \
        1.0 if direction_mode == 'GRADIENT' else 0.0
    if vector is not None:
        mask_node.inputs['Direction'].default_value         = vector
        mask_node.inputs['Directionality'].default_value    = directionality
        mask_node.inputs['Direction Falloff'].default_value = direction_falloff
    else:
        mask_node.inputs['Directionality'].default_value = 0.0

    # The mask reads the base set's height *before* the mix, so the blend can
    # never feed back into the thing that decides it.
    height_a = source_a.get('DISP')
    if height_a is not None:
        links.new(height_a, mask_node.inputs['Height'])
    mask = mask_node.outputs['Fac']

    blended, mixed = {}, []
    y = 500
    for slot in _BLEND_SLOTS:
        a, b = source_a.get(slot), source_b.get(slot)
        if a is None and b is None:
            continue
        # The base's own detail maps survive untouched when the overlay simply
        # has none — there is nothing to mix them with. Everything else that
        # only one side has is mixed against the value the BSDF would have used
        # without it: wired straight through, an overlay's metalness or opacity
        # made the whole surface metallic or see-through, not just the mask.
        if b is None and slot in _KEEP_BASE_DETAIL:
            blended[slot] = a
            continue
        mix, fac, in_a, in_b, out = _new_mix(nodes, 'MIX')
        mix.location = (-760, y)
        mix.label    = slot.title()
        links.new(mask, fac)
        for socket, source in ((in_a, a), (in_b, b)):
            if source is not None:
                links.new(source, socket)
            else:
                socket.default_value = _NEUTRAL_RGBA[slot]
        blended[slot] = out
        mixed.append(slot.lower() if a is not None and b is not None
                     else f"{slot.lower()} (vs neutral)")
        y -= 220

    wired = _wire_principled(nodes, links, mat, bsdf, output, blended,
                             use_ao=use_ao, displacement=displacement,
                             displacement_scale=displacement_scale)

    notes = notes_a + notes_b
    notes.append(f"mixed {', '.join(mixed) if mixed else 'nothing'}")
    return mat, notes + wired


class ET_OT_BlendMaterials(bpy.types.Operator):
    bl_idname  = "et.blend_materials"
    bl_label   = "Blend Materials"
    bl_description = ("Mix two PBR materials through one mask — the overlay settles "
                      "into the base's low spots, and Paint Mask edits it by hand")
    bl_options = {"REGISTER", "UNDO"}

    def _load_preset(self, context):
        """Write a preset's values across the mask properties."""
        values = _BLEND_PRESETS.get(self.preset)
        if values is None:                      # 'CUSTOM' leaves everything be
            return
        for key, value in values.items():
            if key not in ('label', 'description'):
                setattr(self, key, value)

    preset: bpy.props.EnumProperty(
        name="Preset", items=_preset_items, update=_load_preset,
        description="A starting point for the mask. Everything below stays "
                    "editable afterwards")
    material_a: bpy.props.StringProperty(
        name="Base", description="The clean material underneath")
    material_b: bpy.props.StringProperty(
        name="Overlay", description="The material blended on top — dirt, moss, snow")
    amount: bpy.props.FloatProperty(
        name="Overlay Amount", default=0.35, min=0.0, max=1.0,
        description="How much of the overlay shows before you paint anything")
    softness: bpy.props.FloatProperty(
        name="Softness", default=0.25, min=0.01, max=1.0,
        description="Width of the transition — low is a crisp edge, high a slow fade")
    noise_scale: bpy.props.FloatProperty(
        name="Noise Scale", default=4.0, min=0.0, soft_max=50.0,
        description="Breaks the height mask up so the transition isn't mathematically even")
    direction: bpy.props.EnumProperty(
        name="Axis",
        items=[('NONE',  "Any",   "No direction — height and noise only"),
               ('UP',    "Up",    "Overlay strongest at the top / on upward faces"),
               ('DOWN',  "Down",  "Overlay strongest at the bottom / underneath"),
               ('X',     "+X",    "Strongest toward +X"),
               ('NEG_X', "-X",    "Strongest toward -X"),
               ('Y',     "+Y",    "Strongest toward +Y"),
               ('NEG_Y', "-Y",    "Strongest toward -Y")],
        default='NONE',
        description="Which way the overlay builds up. The axis points at the "
                    "end where it is strongest")
    direction_mode: bpy.props.EnumProperty(
        name="Mode",
        items=[('GRADIENT', "Gradient",
                "Ramp the overlay along the axis, 0 to 100 across the mesh — "
                "grime climbing a wall. Works on any shape, flat ones included"),
               ('FACING', "Face Angle",
                "Restrict the overlay to faces pointing that way — snow on top "
                "of a rock. Does nothing on a flat surface, where every face "
                "points the same way")],
        default='GRADIENT',
        description="Whether the direction reads where a point is, or which "
                    "way its surface faces")
    directionality: bpy.props.FloatProperty(
        name="Strength", default=1.0, min=0.0, max=1.0,
        description="How hard the direction bites. 0 ignores it entirely")
    direction_falloff: bpy.props.FloatProperty(
        name="Falloff", default=1.0, min=0.01, max=1.0,
        description="Gradient: how much of the mesh the ramp spans — 1 all of "
                    "it, 0.3 just the last third. Face Angle: how tightly it "
                    "hugs the exact direction")
    displacement: bpy.props.EnumProperty(
        name="Height",
        items=[('BUMP',         "Bump",         "Bump node — works in EEVEE, no subdivision"),
               ('DISPLACEMENT', "Displacement", "Displacement output — Cycles, needs subdivision"),
               ('NONE',         "Load Only",    "Leave height unconnected")],
        default='BUMP')
    displacement_scale: bpy.props.FloatProperty(
        name="Height Scale", default=0.1, min=0.0, soft_max=1.0)
    use_mapping: bpy.props.BoolProperty(name="Add Mapping Nodes", default=True)
    use_ao: bpy.props.BoolProperty(name="Multiply AO into Base Color", default=False)
    assign_to_selected: bpy.props.BoolProperty(
        name="Assign to Selected", default=True,
        description="Put the blend in the first slot of the selected objects")

    def invoke(self, context, event):
        obj = context.active_object
        mats = [m for m in getattr(obj.data, 'materials', []) or [] if m is not None] \
            if obj is not None else []
        if mats and not self.material_a:
            self.material_a = mats[0].name
        if len(mats) > 1 and not self.material_b:
            self.material_b = mats[1].name
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        box = layout.box()
        box.label(text="Materials", icon='MATERIAL')
        box.prop_search(self, 'material_a', bpy.data, 'materials')
        box.prop_search(self, 'material_b', bpy.data, 'materials')

        box = layout.box()
        box.label(text="Preset", icon='PRESET')
        box.prop(self, 'preset', text="")

        box = layout.box()
        box.label(text="Mask", icon='MOD_MASK')
        box.prop(self, 'amount')
        box.prop(self, 'softness')
        box.prop(self, 'noise_scale')

        box = layout.box()
        box.label(text="Direction", icon='NORMALS_FACE')
        box.prop(self, 'direction')
        col = box.column()
        col.enabled = self.direction != 'NONE'
        col.prop(self, 'direction_mode')
        col.prop(self, 'directionality')
        col.prop(self, 'direction_falloff')
        if self.direction != 'NONE' and self.direction_mode == 'FACING':
            box.label(text="Face Angle does nothing on a flat surface",
                      icon='ERROR')
        box.label(text="Paint it by hand afterwards with Paint Mask", icon='BRUSH_DATA')

        box = layout.box()
        box.label(text="Build", icon='NODETREE')
        box.prop(self, 'displacement')
        row = box.row()
        row.enabled = self.displacement != 'NONE'
        row.prop(self, 'displacement_scale')
        if self.displacement == 'DISPLACEMENT':
            box.label(text="Cycles only — EEVEE ignores this output",
                      icon='INFO')
        box.prop(self, 'use_mapping')
        box.prop(self, 'use_ao')
        box.prop(self, 'assign_to_selected')

    def execute(self, context):
        mat_a = bpy.data.materials.get(self.material_a)
        mat_b = bpy.data.materials.get(self.material_b)
        if mat_a is None or mat_b is None:
            self.report({'ERROR'}, "Pick two materials to blend")
            return {'CANCELLED'}
        if mat_a is mat_b:
            self.report({'ERROR'}, "Pick two different materials")
            return {'CANCELLED'}

        slots_a = _material_texture_slots(mat_a)
        slots_b = _material_texture_slots(mat_b)
        for mat, slots in ((mat_a, slots_a), (mat_b, slots_b)):
            if not slots:
                self.report({'ERROR'},
                            f"No recognisable texture maps in '{mat.name}' — the blend "
                            f"reads image nodes named like Rock_4K_Albedo.jpg")
                return {'CANCELLED'}

        try:
            blend, notes = _build_blend_material(
                f"{mat_a.name}_{mat_b.name}_Blend", slots_a, slots_b,
                use_ao=self.use_ao, use_mapping=self.use_mapping,
                displacement=self.displacement,
                displacement_scale=self.displacement_scale,
                amount=self.amount, noise_scale=self.noise_scale,
                softness=self.softness, direction=self.direction,
                directionality=self.directionality,
                direction_falloff=self.direction_falloff,
                direction_mode=self.direction_mode)
        except RuntimeError as exc:
            # A source material pointing at a texture that is no longer on disk.
            self.report({'ERROR'},
                        f"Could not read the textures behind these materials: {exc}")
            return {'CANCELLED'}
        if self.direction != 'NONE':
            notes.append(f"{self.direction_mode.lower()} "
                         f"{self.direction.lower().replace('neg_', '-')}")
        print(f"[EasyTasks] {blend.name}: {', '.join(notes)}")

        objects, filled_slots = 0, 0
        targets = []
        if self.assign_to_selected:
            sources = {mat_a.name, mat_b.name}
            for obj in context.view_layer.objects:
                if obj.select_get() and getattr(obj.data, 'materials', None) is not None:
                    filled = _replace_materials(obj, sources, blend)
                    filled_slots += filled
                    objects += bool(filled)
                    if filled:
                        targets.append(obj)

        # Both modes have a way of silently producing nothing, and they are not
        # the same way: Face Angle when no face points that way, Gradient when
        # the object has no depth along the axis to ramp across.
        caution = None
        vector  = _DIRECTION_VECTORS.get(self.direction)
        label   = self.direction.lower().replace('neg_', '-')
        if vector is not None and targets and self.directionality > 0.0:
            if self.direction_mode == 'FACING':
                # Below this the gate is shut and the overlay cannot show.
                cutoff = 1.0 - 2.0 * self.direction_falloff
                if _direction_coverage(targets, vector) <= cutoff:
                    caution = (f"nothing faces {label}, so the overlay will not "
                               f"show — use Gradient, raise Falloff, or pick a "
                               f"direction the geometry points")
            elif _gradient_span(targets, vector) < 0.01:
                caution = (f"flat along {label}, so the gradient has nothing to "
                           f"ramp across — a floor ramps along X or Y, not up")

        summary = (f"{blend.name} built, {filled_slots} slot(s) on "
                   f"{objects} object(s)")
        # One report, not two: a later call replaces the banner, so a warning
        # followed by a summary is a warning the user never sees.
        if caution:
            self.report({'WARNING'}, f"{summary} — but {caution}")
        else:
            self.report({'INFO'},
                        f"{summary} — use Paint Mask to add or remove by hand")
        return {"FINISHED"}


class ET_OT_ApplyBlendPreset(bpy.types.Operator):
    bl_idname  = "et.apply_blend_preset"
    bl_label   = "Apply Blend Preset"
    bl_description = ("Load a preset onto the blend already on this object, "
                      "without rebuilding the material or losing painted work")
    bl_options = {"REGISTER", "UNDO"}

    preset: bpy.props.EnumProperty(name="Preset", items=_preset_items)

    @classmethod
    def poll(cls, context):
        return _active_blend_mask(context) is not None

    def execute(self, context):
        values = _BLEND_PRESETS.get(self.preset)
        if values is None:
            return {'CANCELLED'}

        node = _active_blend_mask(context)
        # Property names on the operator, socket names on the group: one map
        # rather than two parallel sets of magic strings.
        sockets = {
            'amount':            'Amount',
            'softness':          'Softness',
            'noise_scale':       'Noise Scale',
            'direction_falloff': 'Direction Falloff',
        }
        for key, socket_name in sockets.items():
            socket = node.inputs.get(socket_name)
            if socket is not None:
                socket.default_value = values[key]

        vector = _DIRECTION_VECTORS.get(values['direction'])
        axis   = node.inputs.get('Direction')
        if axis is not None:
            axis.default_value = vector or (0.0, 0.0, 1.0)
        strength = node.inputs.get('Directionality')
        if strength is not None:
            strength.default_value = 0.0 if vector is None else 1.0
        gradient = node.inputs.get('Gradient')
        if gradient is not None:
            gradient.default_value = \
                1.0 if values['direction_mode'] == 'GRADIENT' else 0.0

        self.report({'INFO'}, f"{values['label']} applied — painting untouched")
        return {"FINISHED"}


class ET_OT_SetBlendAxis(bpy.types.Operator):
    bl_idname  = "et.set_blend_axis"
    bl_label   = "Blend Axis"
    bl_description = "Point the blend's direction along a world axis"
    bl_options = {"REGISTER", "UNDO"}

    axis: bpy.props.EnumProperty(
        name="Axis",
        items=[(key, key.replace('NEG_', '-'), f"Build the overlay toward {key}")
               for key in ('UP', 'DOWN', 'X', 'NEG_X', 'Y', 'NEG_Y')],
        default='UP')

    @classmethod
    def poll(cls, context):
        node = _active_blend_mask(context)
        # A material built by an older version has a mask group without the
        # directional sockets, and reaching for one would raise on click.
        return node is not None and 'Direction' in node.inputs

    def execute(self, context):
        node   = _active_blend_mask(context)
        socket = node.inputs.get('Direction') if node is not None else None
        if socket is None:
            self.report({'ERROR'},
                        "This blend's mask has no Direction input — rebuild it "
                        "with Blend Materials to pick up the newer mask")
            return {'CANCELLED'}
        socket.default_value = _DIRECTION_VECTORS[self.axis]
        return {"FINISHED"}


class ET_OT_PaintBlendMask(bpy.types.Operator):
    bl_idname  = "et.paint_blend_mask"
    bl_label   = "Paint Blend Mask"
    bl_description = ("Paint the blend by hand — white adds the overlay, black scrubs "
                      "it back to the base. Click again to leave paint mode")
    bl_options = {"REGISTER"}

    mask_type: bpy.props.EnumProperty(
        name="Paint On",
        items=[('IMAGE',  "Image",        "A mask image — full resolution on any mesh, needs UVs"),
               ('VERTEX', "Vertex Color", "A colour attribute — no UVs needed, "
                                          "but only as fine as the geometry")],
        default='IMAGE')
    resolution: bpy.props.EnumProperty(
        name="Resolution",
        items=[('1024', "1K", "Light, fine for broad grime"),
               ('2048', "2K", "A good default for most surfaces"),
               ('4096', "4K", "Crisp edges on large, close-up surfaces")],
        default='2048')

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def invoke(self, context, event):
        """
        A click toggles paint mode — except the first time an image mask is
        made on a material, when there is a real choice to make (its
        resolution) and a short dialog asks for it. Without this, Paint On and
        Resolution could only be changed from Python.
        """
        obj = context.active_object
        if obj.mode in {'TEXTURE_PAINT', 'VERTEX_PAINT'}:
            return self.execute(context)

        node = _active_blend_mask(context)
        # The panel's Image / Vertex Color buttons set the type; a plain click
        # keeps whatever the material already paints with.
        if not self.properties.is_property_set('mask_type'):
            self.mask_type = _default_paint_type(obj, node)

        if _paint_needs_setup(node, self.mask_type) and obj.data.uv_layers:
            return context.window_manager.invoke_props_dialog(self, width=320)
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, 'mask_type')
        row = layout.row()
        row.enabled = self.mask_type == 'IMAGE'
        row.prop(self, 'resolution')
        layout.label(text="White adds the overlay, black scrubs it — X swaps",
                     icon='INFO')

    def execute(self, context):
        obj = context.active_object
        # Blender's banner shows only the last report, so anything worth saying
        # is gathered here and said once at the end.
        self._cautions = []

        if obj.mode in {'TEXTURE_PAINT', 'VERTEX_PAINT'}:
            bpy.ops.object.mode_set(mode='OBJECT')
            # Leaving is the moment to make the strokes durable. Without this
            # the paint is only ever in memory, and a render or a reopened file
            # reads the flat grey the mask was created with.
            image = _blend_paint_image(_active_blend_mask(context))
            if _save_blend_mask_image(image):
                where = "saved to textures/" if image.filepath_raw \
                    else "packed into the .blend"
                self.report({'INFO'}, f"Left paint mode, {image.name} {where}")
            elif image is not None and image.is_dirty:
                self.report({'WARNING'},
                            f"Left paint mode, but {image.name} could not be "
                            f"written — check the textures folder is writable")
            else:
                self.report({'INFO'}, "Left paint mode")
            return {'FINISHED'}

        mat  = obj.active_material
        node = None
        if mat is not None and mat.use_nodes:
            node = mat.node_tree.nodes.get(_MASK_NODE_NAME)
        if node is None:
            self.report({'ERROR'},
                        "No blend mask on this material — run Blend Materials first")
            return {'CANCELLED'}

        # Checked here rather than at creation time: an object can reach an
        # already-painted material and still have nothing to paint onto.
        if self.mask_type == 'IMAGE' and not obj.data.uv_layers:
            self.report({'ERROR'},
                        f"{obj.name} has no UV map — unwrap it, or pick Vertex "
                        f"Color under Paint in the Blend Mask panel")
            return {'CANCELLED'}

        source = self._resolve_source(obj, mat, node)
        if source is None:
            return {'CANCELLED'}

        if self.mask_type == 'IMAGE':
            self._share_paint_uvs(mat, obj)

        return self._enter_paint(context, obj, source)

    def _share_paint_uvs(self, mat, painted):
        """
        Every object on this material needs the paint UV layer, and the user
        needs telling what sharing means.

        The mask is sampled through a UV map looked up *by name*. An object that
        does not have it falls back silently and shows no paint at all — which
        is what happens the moment one material covers a floor made of twenty
        planes and only the active one was set up.

        Giving them all the layer stops the silent failure, but it cannot make
        the paint independent: one material means one mask image, and each mesh
        normalises its own layout onto the whole of it, so a stroke on one lands
        on all of them. Only separate materials can separate the masks.

        Anything worth saying goes on the caution list rather than being
        reported here: the banner only shows the last report, and entering
        paint mode reports after this.
        """
        users = _material_users(mat)
        for other in users:
            if other is not painted:
                _paint_uv_layer(other)

        if len(users) > 1:
            self._cautions.append(
                f"{mat.name} is on {len(users)} objects sharing one mask, so "
                f"painting one paints them all — make it single-user (the "
                f"number beside the material name) to paint this one alone")

    def _resolve_source(self, obj, mat, node):
        """
        Find or build the paint layer feeding the mask.

        Switching between an image and a vertex mask only moves the link. The
        layer you already painted stays in the tree, so switching back brings
        that work with it rather than starting from grey again.
        """
        wanted   = 'TEX_IMAGE' if self.mask_type == 'IMAGE' else 'ATTRIBUTE'
        tree     = mat.node_tree
        existing = node.inputs['Paint'].links

        if existing:
            if existing[0].from_node.type == wanted:
                return existing[0].from_node
            tree.links.remove(existing[0])

        # By name, not label: a label is free text the user may well retitle,
        # and losing track of it would silently start a second paint layer.
        previous = next((n for n in tree.nodes
                         if n.name.startswith(_BLEND_PAINT_NODE)
                         and n.type == wanted), None)
        if previous is not None:
            tree.links.new(self._source_output(previous), node.inputs['Paint'])
            return previous

        return self._create_source(obj, mat, node)

    @staticmethod
    def _source_output(source):
        return source.outputs['Color'] if source.type == 'TEX_IMAGE' \
            else source.outputs['Fac']

    def _create_source(self, obj, mat, node):
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        if self.mask_type == 'VERTEX':
            _paint_color_layer(obj.data)

            source = nodes.new('ShaderNodeAttribute')
            source.attribute_name = _BLEND_PAINT_ATTR
            source.name     = _BLEND_PAINT_NODE
            source.label    = "Blend Paint"
            source.location = (node.location.x - 320, node.location.y - 300)
            links.new(source.outputs['Fac'], node.inputs['Paint'])
            return source

        size  = int(self.resolution)
        image = bpy.data.images.new(f"{mat.name}_Mask", size, size, alpha=False)
        image.generated_color = (0.5, 0.5, 0.5, 1.0)
        _set_non_color(image)
        self._persist(image)

        source = nodes.new('ShaderNodeTexImage')
        source.image    = image
        source.name     = _BLEND_PAINT_NODE
        source.label    = "Blend Paint"
        source.location = (node.location.x - 360, node.location.y - 300)
        links.new(source.outputs['Color'], node.inputs['Paint'])

        # Sample the mask through its own 0-1 layer, never the tiled one the
        # textures use, or a single stroke shows up in every tile at once. The
        # name is the constant, not whatever this object's layer is called:
        # the node belongs to a material other objects share too.
        if _paint_uv_layer(obj) is None:
            self._cautions.append(
                f"{obj.name} already has 8 UV maps, so there was no room for the "
                f"paint layer — strokes may repeat; remove a UV map and paint again")
        uv_node = nodes.new('ShaderNodeUVMap')
        uv_node.uv_map   = _BLEND_PAINT_UV
        uv_node.label    = "Paint UVs"
        uv_node.location = (source.location.x - 220, source.location.y - 60)
        links.new(uv_node.outputs['UV'], source.inputs['Vector'])
        return source

    def _persist(self, image):
        """
        Give the mask a file on disk, beside the .blend, as soon as it exists.

        In a .blend that has never been saved there is nowhere to put it yet;
        that is fine — once painted, the mask is packed into the .blend when it
        is saved (see _save_blend_mask_image and _save_masks_before_file_save).
        """
        if not _blend_dir():
            return
        path = _mask_file_path(image)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            image.filepath_raw = path
            image.file_format  = 'PNG'
            image.save()
        except (RuntimeError, OSError) as exc:
            self._cautions.append(f"could not save {image.name}: {exc}")

    def _enter_paint(self, context, obj, source):
        ts = context.scene.tool_settings

        if source.type == 'ATTRIBUTE':
            mode  = 'VERTEX_PAINT'
            paint = getattr(ts, 'vertex_paint', None)
        else:
            mode  = 'TEXTURE_PAINT'
            # Strokes are projected through the mesh's active UV layer, so it
            # has to be the same non-tiling one the mask node samples.
            layer = _paint_uv_layer(obj)
            if layer is not None:
                obj.data.uv_layers.active = layer

            paint = getattr(ts, 'image_paint', None)
            if paint is not None:
                # IMAGE mode paints exactly one target. In MATERIAL mode every
                # image node in the tree is a paint slot, so a stray stroke
                # could land on the Megascans albedo — and saving images would
                # then write over the library file itself.
                paint.mode   = 'IMAGE'
                paint.canvas = source.image

        try:
            bpy.ops.object.mode_set(mode=mode)
        except RuntimeError as exc:
            self.report({'WARNING'}, f"Mask ready, but paint mode refused: {exc}")
            return {'FINISHED'}

        self._set_brush_colors(context, paint)
        # One report: the banner shows the last one, so a caution followed by
        # the usual hint would be a caution nobody reads.
        if self._cautions:
            self.report({'WARNING'}, "Painting — " + "; ".join(self._cautions))
        else:
            self.report({'INFO'},
                        "Painting the blend mask — white adds, black removes, "
                        "X swaps")
        return {'FINISHED'}

    @staticmethod
    def _set_brush_colors(context, paint):
        """
        White to add the overlay, black to scrub it — on whichever holder this
        Blender actually paints with.

        Unified paint settings lived on ToolSettings until 5.0 and on each
        paint mode after it; unified colour is on by default, so writing only
        to the old place left 5.x painting with whatever colour was there.
        Brushes became assets in 5.0 too, so any of these may be absent.
        """
        ts = context.scene.tool_settings
        holders = (getattr(paint, 'brush', None),
                   getattr(paint, 'unified_paint_settings', None),   # 5.0+
                   getattr(ts, 'unified_paint_settings', None))      # before 5.0
        for holder in holders:
            if holder is None:
                continue
            for attr, value in (('color', (1.0, 1.0, 1.0)),
                                ('secondary_color', (0.0, 0.0, 0.0))):
                try:
                    setattr(holder, attr, value)
                except (AttributeError, TypeError):
                    pass


# ---------------------------------------------------------------------------
# LOD Generator
# ---------------------------------------------------------------------------

class ET_OT_GenerateLODs(bpy.types.Operator):
    bl_idname  = "et.generate_lods"
    bl_label   = "Generate LODs"
    bl_description = "Create Decimate-reduced LOD copies of selected mesh objects"
    bl_options = {"REGISTER", "UNDO"}

    ratio_1: bpy.props.FloatProperty(name="LOD1 Ratio", default=0.75, min=0.01, max=0.99, subtype='FACTOR')
    ratio_2: bpy.props.FloatProperty(name="LOD2 Ratio", default=0.50, min=0.01, max=0.99, subtype='FACTOR')
    ratio_3: bpy.props.FloatProperty(name="LOD3 Ratio", default=0.25, min=0.01, max=0.99, subtype='FACTOR')
    apply_decimate: bpy.props.BoolProperty(name="Apply Decimate", default=True)

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, context):
        col = self.layout.column(align=True)
        col.prop(self, 'ratio_1', slider=True)
        col.prop(self, 'ratio_2', slider=True)
        col.prop(self, 'ratio_3', slider=True)
        self.layout.separator(factor=0.5)
        self.layout.prop(self, 'apply_decimate')

    def execute(self, context):
        scene = context.scene

        lods_coll = bpy.data.collections.get('LODs')
        if not lods_coll:
            lods_coll = bpy.data.collections.new('LODs')
            scene.collection.children.link(lods_coll)

        prev_active = context.view_layer.objects.active
        made = 0

        for obj in [o for o in context.selected_objects if o.type == 'MESH']:
            # Strip any existing _LOD<n> so re-running doesn't yield Foo_LOD0_LOD1.
            base_name = re.sub(r'_LOD\d+$', '', obj.name)
            obj.name  = f"{base_name}_LOD0"

            for i, ratio in enumerate((self.ratio_1, self.ratio_2, self.ratio_3), start=1):
                lod_name = f"{base_name}_LOD{i}"

                # Replace a previous LOD of the same name rather than piling up
                # Foo_LOD1.001, Foo_LOD1.002 on every run.
                stale = bpy.data.objects.get(lod_name)
                if stale is not None and stale is not obj:
                    bpy.data.objects.remove(stale, do_unlink=True)

                new_obj      = obj.copy()
                new_obj.data = obj.data.copy()
                new_obj.name = lod_name
                lods_coll.objects.link(new_obj)

                dec       = new_obj.modifiers.new("LOD_Decimate", "DECIMATE")
                dec.ratio = ratio

                if self.apply_decimate:
                    context.view_layer.objects.active = new_obj
                    try:
                        bpy.ops.object.modifier_apply(modifier="LOD_Decimate")
                    except RuntimeError as exc:
                        self.report({'WARNING'}, f"{lod_name}: {exc}")
                made += 1

        context.view_layer.objects.active = prev_active
        self.report({'INFO'}, f"Generated {made} LOD mesh(es)")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Quick Origin Tools
# ---------------------------------------------------------------------------

class ET_OT_OriginToBase(bpy.types.Operator):
    bl_idname  = "et.origin_to_base"
    bl_label   = "Origin to Base"
    bl_description = "Move origin to the bottom center of the bounding box (floor level)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def execute(self, context):
        # Done with matrix maths rather than a select/deselect + origin_set loop:
        # the old path ran two operators per object and hijacked the 3D cursor,
        # which made it crawl on large selections and dirtied the undo stack.
        moved   = 0
        shared  = 0
        skipped = 0

        for obj in context.selected_objects:
            data = obj.data
            if data is None or not hasattr(data, 'transform'):
                skipped += 1
                continue
            if data.users > 1:
                # Transforming the datablock would drag every other user with it.
                shared += 1
                continue

            matrix  = obj.matrix_world
            corners = [matrix @ mathutils.Vector(c) for c in obj.bound_box]
            target  = mathutils.Vector((
                sum(v.x for v in corners) / 8.0,
                sum(v.y for v in corners) / 8.0,
                min(v.z for v in corners),
            ))

            local_target = matrix.inverted() @ target
            data.transform(mathutils.Matrix.Translation(-local_target))

            new_matrix             = matrix.copy()
            new_matrix.translation = target
            obj.matrix_world       = new_matrix
            moved += 1

        msg = f"Origin to base on {moved} object(s)"
        if shared:
            msg += f" — {shared} skipped (multi-user data)"
        if skipped:
            msg += f" — {skipped} unsupported"
        self.report({'INFO'} if moved else {'WARNING'}, msg)
        return {"FINISHED"}


class ET_MT_OriginMenu(bpy.types.Menu):
    bl_idname = "ET_MT_origin_menu"
    bl_label  = "Origin Tools"

    def draw(self, context):
        layout = self.layout
        layout.operator('object.origin_set', text='Origin to Geometry',  icon='OBJECT_ORIGIN').type = 'ORIGIN_GEOMETRY'
        layout.operator('object.origin_set', text='Origin to 3D Cursor', icon='PIVOT_CURSOR').type  = 'ORIGIN_CURSOR'
        layout.operator('object.origin_set', text='Origin to Center of Mass', icon='PIVOT_MEDIAN').type = 'ORIGIN_CENTER_OF_MASS'
        layout.operator('et.origin_to_base', text='Origin to Base',      icon='TRIA_DOWN')
        layout.separator()
        layout.operator('object.origin_set', text='Geometry to Origin',  icon='OBJECT_DATAMODE').type = 'GEOMETRY_ORIGIN'


# ---------------------------------------------------------------------------
# Reference Image Placer
# ---------------------------------------------------------------------------

class ET_OT_PlaceReferenceImage(bpy.types.Operator):
    bl_idname  = "et.place_reference_image"
    bl_label   = "Place Reference Image"
    bl_description = "Load an image and place it aligned to the current viewport view"
    bl_options = {"REGISTER", "UNDO"}

    filepath:      bpy.props.StringProperty(subtype='FILE_PATH')
    filter_image:  bpy.props.BoolProperty(default=True,  options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: bpy.props.BoolProperty(default=True,  options={'HIDDEN', 'SKIP_SAVE'})
    distance:      bpy.props.FloatProperty(name="Distance", default=5.0, min=0.1, max=100.0)

    @classmethod
    def poll(cls, context):
        return context.space_data is not None and context.space_data.type == 'VIEW_3D'

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath or not os.path.isfile(self.filepath):
            self.report({'ERROR'}, "No valid image file selected")
            return {'CANCELLED'}

        # Works for any viewport: perspective, ortho, or camera view
        view_mat_inv = context.region_data.view_matrix.inverted()
        view_origin  = view_mat_inv.translation
        view_forward = (view_mat_inv.to_3x3() @ mathutils.Vector((0, 0, -1))).normalized()

        img   = bpy.data.images.load(self.filepath)
        name  = os.path.splitext(os.path.basename(self.filepath))[0]
        empty = bpy.data.objects.new(name, None)
        empty.empty_display_type = 'IMAGE'
        empty.data               = img
        empty.location           = view_origin + view_forward * self.distance
        empty.rotation_euler     = view_mat_inv.to_euler()

        context.scene.collection.objects.link(empty)
        self.report({'INFO'}, f"Reference image placed: {img.name}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Scene Snapshot
# ---------------------------------------------------------------------------

class ET_OT_SceneSnapshot(bpy.types.Operator):
    bl_idname  = "et.scene_snapshot"
    bl_label   = "Scene Snapshot"
    bl_description = "Save a timestamped copy of the .blend to a /snapshots/ subfolder"
    bl_options = {"REGISTER"}

    def execute(self, context):
        blend_path = bpy.data.filepath
        if not blend_path:
            self.report({'ERROR'}, "Save the file first!")
            return {'CANCELLED'}

        blend_dir  = os.path.dirname(blend_path)
        blend_name = os.path.splitext(os.path.basename(blend_path))[0]
        snap_dir   = os.path.join(blend_dir, 'snapshots')
        os.makedirs(snap_dir, exist_ok=True)

        stamp     = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        snap_path = os.path.join(snap_dir, f"{blend_name}_{stamp}.blend")

        bpy.ops.wm.save_as_mainfile(filepath=snap_path, copy=True)
        self.report({'INFO'}, f"Snapshot saved: {os.path.basename(snap_path)}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Utility operators
# ---------------------------------------------------------------------------

class ET_OT_DropIt(bpy.types.Operator):
    bl_idname  = "et.drop_it"
    bl_label   = "Drop It"
    bl_description = "Move selected objects to Z = 0"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def execute(self, context):
        for obj in context.selected_objects:
            obj.location.z = 0
        return {"FINISHED"}


class ET_OT_SelectByType(bpy.types.Operator):
    bl_idname  = "et.select_by_type"
    bl_label   = "Select by Type"
    bl_description = "Select all objects of a given type"
    bl_options = {"REGISTER", "UNDO"}

    object_type: bpy.props.StringProperty()
    extend:      bpy.props.BoolProperty(name="Extend", default=False)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        # Selecting directly instead of temporarily rewriting context.area.type
        # to run bpy.ops.object.select_by_type — that swap redraws the editor
        # under the user and is not a supported way to build an operator context.
        view_layer = context.view_layer
        count = 0
        first = None

        for obj in view_layer.objects:
            if obj.type == self.object_type and obj.visible_get():
                obj.select_set(True)
                count += 1
                if first is None:
                    first = obj
            elif not self.extend:
                obj.select_set(False)

        if first is not None:
            view_layer.objects.active = first

        self.report({'INFO'}, f"Selected {count} {self.object_type.lower()} object(s)")
        return {"FINISHED"}


# ET_OT_Convert was removed in 2.4.0. It existed only to work around a context
# problem by swapping context.area.type before calling bpy.ops.object.convert;
# once that hack was dropped it was a bare passthrough, and it appeared in no
# menu or panel. Use bpy.ops.object.convert directly.


class ET_OT_WNormalsBevel(bpy.types.Operator):
    bl_idname  = "et.wnormals_bevel"
    bl_label   = "WNormals + Bevel"
    bl_description = "Apply Bevel + Weighted Normal + Shade Smooth on selected mesh objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    width: bpy.props.FloatProperty(
        name="Bevel Width", default=0.15, min=0.0, soft_max=1.0, subtype='DISTANCE')
    segments: bpy.props.IntProperty(name="Segments", default=2, min=1, max=12)

    def execute(self, context):
        done = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            mesh = obj.data

            # foreach_set writes the whole array in one call; the per-polygon
            # Python loop was the slow part of this operator on dense meshes.
            if mesh.polygons:
                mesh.polygons.foreach_set(
                    'use_smooth', np.ones(len(mesh.polygons), dtype=np.int8))
                mesh.update()

            # Re-running used to add a second Bevel and a second Weighted Normal
            # every time. Reuse the existing ones instead.
            bevel = next((m for m in obj.modifiers if m.type == 'BEVEL'), None)
            if bevel is None:
                bevel = obj.modifiers.new("Bevel", "BEVEL")
            bevel.offset_type    = 'WIDTH'
            bevel.harden_normals = True
            bevel.width          = self.width
            bevel.segments       = self.segments

            if not any(m.type == 'WEIGHTED_NORMAL' for m in obj.modifiers):
                obj.modifiers.new("Weighted Normal", "WEIGHTED_NORMAL")

            if bpy.app.version < (4, 1, 0):
                mesh.use_auto_smooth = True
            done += 1

        self.report({'INFO'}, f"Bevel + weighted normals on {done} mesh(es)")
        return {"FINISHED"}


class ET_OT_CorrectNormals(bpy.types.Operator):
    bl_idname  = "et.correct_normals"
    bl_label   = "Correct Normals"
    bl_description = "Add Weighted Normal modifier and Shade Smooth on the active mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        if bpy.app.version < (4, 1, 0):
            obj = context.active_object
            obj.data.use_auto_smooth   = True
            obj.data.auto_smooth_angle = 0.523599
        bpy.ops.object.modifier_add(type='WEIGHTED_NORMAL')
        bpy.ops.object.shade_smooth()
        return {"FINISHED"}


class ET_OT_CleanUpMesh(bpy.types.Operator):
    bl_idname  = "et.clean_up_mesh"
    bl_label   = "Clean Up Mesh"
    bl_description = "Remove loose vertices and loose edges from the active mesh"
    bl_options = {"REGISTER", "UNDO"}

    merge_doubles: bpy.props.BoolProperty(
        name="Merge Doubles", default=False,
        description="Also weld vertices closer than the merge distance")
    merge_distance: bpy.props.FloatProperty(
        name="Merge Distance", default=0.0001, min=0.0, precision=5, subtype='DISTANCE')

    @classmethod
    def poll(cls, context):
        return (context.mode == 'OBJECT'
                and any(o.type == 'MESH' for o in context.selected_objects))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'merge_doubles')
        row = layout.row()
        row.enabled = self.merge_doubles
        row.prop(self, 'merge_distance')

    def execute(self, context):
        # Runs on every selected mesh, and skips shared datablocks so cleaning
        # one instance doesn't silently rewrite geometry under the others.
        meshes = {}
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                meshes.setdefault(obj.data.name, obj.data)

        removed_verts = 0
        removed_edges = 0

        for mesh in meshes.values():
            bm = bmesh.new()
            bm.from_mesh(mesh)

            if self.merge_doubles:
                bmesh.ops.remove_doubles(bm, verts=bm.verts[:],
                                         dist=self.merge_distance)

            loose_edges = [e for e in bm.edges if not e.link_faces]
            removed_edges += len(loose_edges)
            bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')

            loose_verts = [v for v in bm.verts if not v.link_faces and not v.link_edges]
            removed_verts += len(loose_verts)
            bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')

            bm.to_mesh(mesh)
            bm.free()
            mesh.update()

        self.report({'INFO'},
                    f"Cleaned {len(meshes)} mesh(es): "
                    f"-{removed_verts} vert(s), -{removed_edges} edge(s)")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Modeling analysis / preview tools (new)
# ---------------------------------------------------------------------------

class ET_OT_SilhouetteCheck(bpy.types.Operator):
    bl_idname  = "et.silhouette_check"
    bl_label   = "Silhouette Check"
    bl_description = (
        "Switch the viewport to flat black to judge silhouette quality. "
        "Run again to restore your shading."
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.space_data is not None and context.space_data.type == 'VIEW_3D'

    def execute(self, context):
        on = _toggle_shading_preview(context.space_data, 'SILHOUETTE', {
            'type':         'SOLID',
            'light':        'FLAT',
            'color_type':   'SINGLE',
            'single_color': (0.0, 0.0, 0.0),
            'show_cavity':  False,
        })
        self.report({'INFO'},
                    "Silhouette on — click again to restore" if on
                    else "Silhouette off — shading restored")
        return {"FINISHED"}


class ET_OT_CavityPreview(bpy.types.Operator):
    bl_idname  = "et.cavity_preview"
    bl_label   = "Cavity Preview"
    bl_description = (
        "Switch the viewport to a high-contrast clay cavity view to evaluate surface form. "
        "Run again to restore your shading."
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.space_data is not None and context.space_data.type == 'VIEW_3D'

    def execute(self, context):
        on = _toggle_shading_preview(context.space_data, 'CAVITY', {
            'type':                 'SOLID',
            'light':                'FLAT',
            'color_type':           'SINGLE',
            'single_color':         (0.6, 0.6, 0.6),
            'show_cavity':          True,
            'cavity_type':          'BOTH',
            'cavity_ridge_factor':  2.5,
            'cavity_valley_factor': 2.5,
        })
        self.report({'INFO'},
                    "Cavity preview on — click again to restore" if on
                    else "Cavity preview off — shading restored")
        return {"FINISHED"}


class ET_OT_FaceStretchAnalyzer(bpy.types.Operator):
    bl_idname  = "et.face_stretch_analyzer"
    bl_label   = "Face Stretch Analyzer"
    bl_description = (
        "Color-code faces by UV stretch on selected meshes "
        "(blue=compressed, green=good, red=stretched). Run again to restore."
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # While the analyzer is on it must stay clickable even with nothing
        # selected — otherwise deselecting traps the user with the analyzer
        # material still assigned and no way to switch it back off.
        if _stretch_state:
            return True
        return any(o.type == 'MESH' for o in context.selected_objects)

    def _loop_colors(self, obj):
        """
        Per-loop RGBA colours for a mesh's UV stretch, as a flat float32 array
        ready for foreach_set, or None if the mesh has no usable UVs.

        Vectorised with numpy: the previous version walked every polygon and
        every loop in Python, which dominated the runtime on dense meshes.
        """
        mesh     = obj.data
        uv_layer = mesh.uv_layers.active
        n_polys  = len(mesh.polygons)
        n_loops  = len(mesh.loops)
        if uv_layer is None or n_polys == 0 or n_loops == 0:
            return None

        area_3d = np.empty(n_polys, dtype=np.float64)
        mesh.polygons.foreach_get('area', area_3d)

        loop_start = np.empty(n_polys, dtype=np.int32)
        loop_total = np.empty(n_polys, dtype=np.int32)
        mesh.polygons.foreach_get('loop_start', loop_start)
        mesh.polygons.foreach_get('loop_total', loop_total)

        uvs = np.empty(n_loops * 2, dtype=np.float64)
        uv_layer.data.foreach_get('uv', uvs)
        uvs = uvs.reshape(n_loops, 2)

        # Shoelace area per UV face, computed for all loops at once. `nxt` is the
        # following loop within the same polygon, wrapping at the polygon end.
        poly_of_loop = np.repeat(np.arange(n_polys), loop_total)
        start_of_loop = np.repeat(loop_start, loop_total)
        total_of_loop = np.repeat(loop_total, loop_total)
        local = np.arange(n_loops, dtype=np.int64) - start_of_loop
        nxt   = start_of_loop + (local + 1) % total_of_loop

        cross   = uvs[:, 0] * uvs[nxt, 1] - uvs[nxt, 0] * uvs[:, 1]
        area_uv = np.abs(np.bincount(poly_of_loop, weights=cross,
                                     minlength=n_polys)) * 0.5

        valid = area_uv > 1e-10
        if not valid.any():
            return None

        ratio = np.zeros(n_polys, dtype=np.float64)
        np.divide(area_3d, area_uv, out=ratio, where=valid)

        avg = ratio[valid].mean()
        if avg <= 0.0:
            return None

        t = ratio / avg
        rgb = np.empty((n_polys, 3), dtype=np.float64)

        compressed = t <= 1.0
        # blue → green: UV island larger than the 3D face
        rgb[compressed, 0] = 0.0
        rgb[compressed, 1] = t[compressed]
        rgb[compressed, 2] = 1.0 - t[compressed]
        # green → red: 3D face larger than the UV island
        stretched = ~compressed
        f = np.minimum(t[stretched] - 1.0, 1.0)
        rgb[stretched, 0] = f
        rgb[stretched, 1] = 1.0 - f
        rgb[stretched, 2] = 0.0
        # faces with a degenerate UV area read as neutral grey
        rgb[~valid] = 0.5

        loop_colors = np.ones((n_loops, 4), dtype=np.float32)
        loop_colors[:, :3] = rgb[poly_of_loop]
        return loop_colors.ravel()

    def _get_or_create_vcol(self, mesh):
        if hasattr(mesh, 'color_attributes'):
            vcol = mesh.color_attributes.get(_VCOL_STRETCH)
            if not vcol:
                vcol = mesh.color_attributes.new(_VCOL_STRETCH, 'FLOAT_COLOR', 'CORNER')
        else:
            vcol = mesh.vertex_colors.get(_VCOL_STRETCH)
            if not vcol:
                vcol = mesh.vertex_colors.new(name=_VCOL_STRETCH)
        return vcol

    @staticmethod
    def _remove_vcol(mesh):
        if hasattr(mesh, 'color_attributes'):
            vcol = mesh.color_attributes.get(_VCOL_STRETCH)
            if vcol:
                mesh.color_attributes.remove(vcol)
        else:
            vcol = mesh.vertex_colors.get(_VCOL_STRETCH)
            if vcol:
                mesh.vertex_colors.remove(vcol)

    @staticmethod
    def _colour_indices(mesh):
        """(active, render) colour-attribute indices, or (None, None)."""
        group = getattr(mesh, 'color_attributes', None)
        if group is None:
            legacy = getattr(mesh, 'vertex_colors', None)
            return (getattr(legacy, 'active_index', None), None)
        return (group.active_color_index, group.render_color_index)

    @staticmethod
    def _select_colour_layer(mesh, vcol):
        """
        Make vcol the layer solid shading displays.

        Both indices are set because which one drives the Attribute display has
        differed between Blender versions; setting both is correct either way.
        """
        group = getattr(mesh, 'color_attributes', None)
        if group is None:
            legacy = getattr(mesh, 'vertex_colors', None)
            if legacy is not None:
                legacy.active_index = legacy.find(vcol.name)
            return
        index = group.find(vcol.name)
        if index < 0:
            return
        group.active_color_index = index
        try:
            group.render_color_index = index
        except (AttributeError, TypeError):
            pass

    @staticmethod
    def _restore_colour_indices(mesh, indices):
        active, render = indices
        group = getattr(mesh, 'color_attributes', None)
        if group is None:
            legacy = getattr(mesh, 'vertex_colors', None)
            if legacy is not None and active is not None:
                try:
                    legacy.active_index = active
                except (TypeError, RuntimeError):
                    pass
            return
        for attr, value in (('active_color_index', active),
                            ('render_color_index', render)):
            if value is None:
                continue
            try:
                setattr(group, attr, min(value, max(len(group) - 1, 0)))
            except (AttributeError, TypeError, RuntimeError):
                pass

    @classmethod
    def restore_attributes(cls):
        """Remove the temporary colour attribute and put the old one back."""
        restored = 0
        for mesh_name, indices in _stretch_state.items():
            mesh = bpy.data.meshes.get(mesh_name)
            if mesh is None:
                continue
            cls._remove_vcol(mesh)
            cls._restore_colour_indices(mesh, indices)
            mesh.update()
            restored += 1
        _stretch_state.clear()
        return restored

    def _restore(self):
        restored = self.restore_attributes()
        _restore_preview_mode('STRETCH')
        return restored

    def execute(self, context):
        # Nothing here touches materials. The previous version cleared
        # mesh.materials and held the originals in a module-level dict, so a
        # crash or a disabled add-on while the analyzer was on lost the user's
        # material assignments with no way back. Writing a colour attribute and
        # switching the viewport to display it is entirely non-destructive, and
        # it is also instant because no material has to be rebuilt.
        if _stretch_state:
            restored = self._restore()
            self.report({'INFO'},
                        f"Face stretch off — {restored} mesh(es) restored")
            return {"FINISHED"}

        skipped = 0
        seen    = set()

        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            mesh = obj.data
            # Several objects can share one mesh; colour it once.
            if mesh.name in seen:
                continue

            loop_colors = self._loop_colors(obj)
            if loop_colors is None:
                skipped += 1
                continue

            seen.add(mesh.name)
            _stretch_state[mesh.name] = self._colour_indices(mesh)

            vcol = self._get_or_create_vcol(mesh)
            vcol.data.foreach_set('color', loop_colors)
            self._select_colour_layer(mesh, vcol)
            mesh.update()

        if not _stretch_state:
            self.report({'WARNING'}, "No selected mesh has usable UVs")
            return {'CANCELLED'}

        # Mutually exclusive with the other previews, via the shared machinery.
        _toggle_shading_preview(context.space_data, 'STRETCH', {
            'type':       'SOLID',
            'light':      'STUDIO',
            'color_type': 'VERTEX',
            'show_cavity': False,
        })

        msg = (f"Face stretch on for {len(_stretch_state)} mesh(es) — "
               "blue=compressed, green=ok, red=stretched")
        if skipped:
            msg += f" ({skipped} skipped, no UV)"
        self.report({'INFO'}, msg)
        return {"FINISHED"}

class ET_OT_SmartDuplicate(bpy.types.Operator):
    bl_idname  = "et.smart_duplicate"
    bl_label   = "Smart Duplicate"
    bl_description = "Duplicate selected objects and keep the copies in the same collection(s)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    _TAG = '_et_dup_src'

    linked: bpy.props.BoolProperty(
        name="Linked", default=False,
        description="Share object data with the original instead of copying it")

    def execute(self, context):
        # Tag each original with an index, which the duplicate carries over.
        # Matching on the name with the .001 suffix stripped broke whenever the
        # source was itself named Crate.001, silently leaving copies in the
        # wrong collection.
        originals = list(context.selected_objects)
        source_colls = {}
        for i, obj in enumerate(originals):
            obj[self._TAG] = i
            source_colls[i] = list(obj.users_collection)

        try:
            bpy.ops.object.duplicate(linked=self.linked)

            moved = 0
            for new_obj in context.selected_objects:
                index = new_obj.get(self._TAG)
                if index is None:
                    continue
                targets = source_colls.get(index)
                if not targets:
                    continue

                current = list(new_obj.users_collection)
                for coll in targets:
                    if coll not in current:
                        coll.objects.link(new_obj)
                for coll in current:
                    if coll not in targets:
                        coll.objects.unlink(new_obj)
                moved += 1
        finally:
            for obj in list(originals) + list(context.selected_objects):
                if self._TAG in obj:
                    del obj[self._TAG]

        self.report({'INFO'}, f"Duplicated {moved} object(s) in-collection")
        return {"FINISHED"}


class ET_OT_StackedUVDetector(bpy.types.Operator):
    bl_idname  = "et.stacked_uv_detector"
    bl_label   = "Stacked UV Detector"
    bl_description = (
        "Find objects with identical UV layouts (stacked UVs). "
        "Selects the offending objects and reports the groups."
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    tolerance: bpy.props.IntProperty(
        name="Decimals", default=3, min=1, max=6,
        description="How precisely UVs must match to count as stacked")

    def _uv_signature(self, obj):
        # foreach_get pulls the whole UV layer into one buffer; the old version
        # built a Python tuple of per-loop tuples, allocating two objects per
        # loop before hashing. Quantising and hashing the raw bytes is the same
        # comparison at a fraction of the cost.
        mesh     = obj.data
        uv_layer = mesh.uv_layers.active
        n_loops  = len(mesh.loops)
        if uv_layer is None or n_loops == 0:
            return None

        uvs = np.empty(n_loops * 2, dtype=np.float32)
        uv_layer.data.foreach_get('uv', uvs)

        scale = 10 ** self.tolerance
        quantised = np.rint(uvs * scale).astype(np.int32)
        # Loop count is part of the key so different topologies cannot collide.
        return (n_loops, hash(quantised.tobytes()))

    def execute(self, context):
        sig_map = {}
        no_uv   = []

        for obj in context.scene.objects:
            if obj.type != 'MESH':
                continue
            sig = self._uv_signature(obj)
            if sig is None:
                no_uv.append(obj.name)
                continue
            sig_map.setdefault(sig, []).append(obj)

        stacked = [group for group in sig_map.values() if len(group) > 1]
        flat    = [obj for group in stacked for obj in group]

        bpy.ops.object.select_all(action='DESELECT')
        for obj in flat:
            obj.select_set(True)
        if flat:
            context.view_layer.objects.active = flat[0]

        if stacked:
            summary = '; '.join(
                '(' + ', '.join(o.name for o in g) + ')' for g in stacked
            )
            self.report({'WARNING'},
                        f"{len(stacked)} stacked UV group(s): {summary[:120]}")
        else:
            self.report({'INFO'}, "No stacked UVs found")

        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Main EasyTasks Pie  (Ctrl+Shift+X)
# ---------------------------------------------------------------------------

class ET_MT_SelectMenu(bpy.types.Menu):
    bl_idname = "ET_MT_select_menu"
    bl_label  = "Select by Type"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        layout.operator('et.select_by_type', text='Mesh',   icon='MESH_DATA').object_type   = 'MESH'
        layout.operator('et.select_by_type', text='Curve',  icon='CURVE_DATA').object_type  = 'CURVE'
        layout.operator('et.select_by_type', text='Lights', icon='LIGHT').object_type        = 'LIGHT'
        layout.operator('et.select_by_type', text='Camera', icon='CAMERA_DATA').object_type = 'CAMERA'


class ET_MT_SemanticMenu(bpy.types.Menu):
    bl_idname = "ET_MT_semantic_menu"
    bl_label  = "Assign to Category"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "EXEC_DEFAULT"
        for key, label, icon, _color in SEMANTIC_CATEGORIES:
            layout.operator('et.assign_semantic', text=label,
                            icon=icon).category = key


class ET_MT_OrganizeMenu(bpy.types.Menu):
    bl_idname = "ET_MT_organize_menu"
    bl_label  = "Organize"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        layout.operator('et.isolate_collection',   text='Isolate Collection',   icon='HIDE_OFF')
        layout.operator('et.select_by_collection', text='Select by Collection', icon='RESTRICT_SELECT_OFF')
        layout.operator('et.swap_collections',     text='Swap Collections',     icon='ARROW_LEFTRIGHT')
        layout.operator('et.rename_by_collection', text='Rename by Collection', icon='FONT_DATA')
        layout.operator('et.visibility_bookmark',  text='Visibility Bookmark',  icon='MARKER')
        layout.operator('et.collection_stats',     text='Collection Stats',     icon='SPREADSHEET')


class ET_MT_AnalysisMenu(bpy.types.Menu):
    bl_idname = "ET_MT_analysis_menu"
    bl_label  = "Analysis"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        layout.operator('et.silhouette_check',      text='Silhouette Check',    icon='SHADING_SOLID')
        layout.operator('et.cavity_preview',        text='Cavity Preview',      icon='SHADING_RENDERED')
        layout.operator('et.face_stretch_analyzer', text='Face Stretch',        icon='MOD_UVPROJECT')
        layout.operator('et.smart_duplicate',       text='Smart Duplicate',     icon='DUPLICATE')
        layout.operator('et.uv_health',             text='UV Health Check',     icon='UV')
        layout.operator('et.stacked_uv_detector',   text='Stacked UV Detector', icon='UV_SYNC_SELECT')


class ET_MT_EasyTasksPie(bpy.types.Menu):
    bl_idname = "ET_MT_easy_tasks_pie"
    bl_label  = "Easy Tasks"

    def draw(self, context):
        pie = self.layout.menu_pie()

        # Slot 1 – left: Overlay (largest box — left slot has the most outward room)
        # spaces[0] is only a View3D when the pie is opened over a 3D viewport;
        # from any other editor the overlay/shading lookups raise and the whole
        # pie fails to draw.
        space = context.space_data
        box = pie.box()
        box.label(text='Overlay', icon='OVERLAY')
        if space is not None and space.type == 'VIEW_3D':
            box.prop(space.overlay, 'show_wireframes',       text='Wireframe')
            box.prop(space.shading, 'show_backface_culling', text='Backface Culling')
            box.prop(space.overlay, 'show_overlays',         text='Overlays')
            box.prop(space.shading, 'show_cavity',           text='Cavity')
            box.prop(space.shading, 'cavity_type',           text='')
            box.prop(space.shading, 'show_shadows',          text='Shadow')
        else:
            box.label(text='Open over a 3D viewport', icon='INFO')

        # Slot 2 – right: Collections + Select by Type
        # (no diagonal slots — all 4 cardinals hold boxes so diagonals always clip)
        box = pie.box()
        col = box.column(align=True)
        col.label(text="Collections", icon='OUTLINER_COLLECTION')
        col.menu('ET_MT_semantic_menu',         text='Assign to Category',   icon='OUTLINER_COLLECTION')
        col.operator('et.isolate_collection',   text='Isolate Collection',   icon='HIDE_OFF')
        col.operator('et.swap_collections',     text='Swap Collections',     icon='ARROW_LEFTRIGHT')
        col.operator('et.rename_by_collection', text='Rename by Collection', icon='FONT_DATA')
        col.separator()
        col.menu('ET_MT_select_menu', text='Select by Type', icon='RESTRICT_SELECT_OFF')

        # Slot 3 – bottom: Apply Transform + Shading combined
        box = pie.box()
        col = box.column(align=True)
        col.label(text="Apply Transform", icon='OBJECT_DATA')
        _transform_apply_buttons(col, ('Scale', 'Rotation', 'Location'))
        col.separator()
        col.label(text="Shading", icon='NORMALS_FACE')
        col.operator('object.shade_smooth', text='Shade Smooth', icon='MESH_CIRCLE')
        col.operator('object.shade_flat',   text='Shade Flat',   icon='MESH_PLANE')

        # Slot 4 – top: Setup
        box = pie.box()
        col = box.column(align=True)
        col.label(text="Setup", icon='OUTLINER_COLLECTION')
        col.operator('et.organize_scene', text='Project Structure', icon='OUTLINER_COLLECTION')
        col.operator('et.arrange_scene',  text='Arrange Scene',     icon='OUTLINER')


# ---------------------------------------------------------------------------
# Addon Preferences
# ---------------------------------------------------------------------------

class ET_AddonPreferences(bpy.types.AddonPreferences):
    # Must match the module this addon is registered under. It was hardcoded to
    # 'easy_tasks' while the package installs as 'EasyTasks', so Blender never
    # matched the preferences to the addon and this whole panel — including the
    # three shortcut rebinding fields — never appeared.
    bl_idname = __name__

    auto_route: bpy.props.BoolProperty(
        name="Auto-route New Objects",
        default=True,
        description="Silently move newly created objects into a matching "
                    "collection as soon as they are added")

    quick_mirror_buttons: bpy.props.BoolProperty(
        name="Quick Mirror Buttons in the Viewport",
        default=True,
        description="Show the pivot and X / Y / Z mirror buttons under the "
                    "active object while two or more objects are selected")

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text='Behaviour', icon='PREFERENCES')
        box.prop(self, 'auto_route')
        box.prop(self, 'quick_mirror_buttons')

        shortcuts = (
            ('easy_tasks_pie',      'EasyTasks Pie (Ctrl+Shift+X)', 'KEYINGSET'),
            ('fav_tools',           'FavTools (Q)',                 'SOLO_ON'),
            ('interaction_mode_pie','Interaction Mode Pie (Shift+Alt+X)', 'VIEW3D'),
        )
        for key, label, icon in shortcuts:
            box = layout.box()
            box.label(text=f'{label}:', icon=icon)
            kmi = find_user_keyconfig(key)
            if kmi is None:
                box.label(text='Keymap not registered', icon='ERROR')
            else:
                box.prop(kmi, 'type', text='', full_event=True)


# ---------------------------------------------------------------------------
# N-Panel: ET: Scene
# ---------------------------------------------------------------------------

class ET_PT_ScenePanel(bpy.types.Panel):
    bl_label       = "Scene"
    bl_idname      = "ET_PT_scene_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'EasyTasks'
    bl_order       = 1

    def draw(self, context):
        layout = self.layout

        draw_semantic_assign(layout, context)

        box = layout.box()
        box.label(text="Collections", icon='HIDE_OFF')
        col = box.column(align=True)
        col.operator('et.organize_scene',         text='Project Structure', icon='OUTLINER_COLLECTION')
        col.operator('et.arrange_scene',          text='Arrange Scene',     icon='OUTLINER')
        col.operator('et.collection_color_sync',  text='Color Sync',        icon='COLOR')

        box = layout.box()
        box.label(text="Organize", icon='OUTLINER_COLLECTION')
        col = box.column(align=True)
        col.operator('et.isolate_collection',    text='Isolate Collection',   icon='HIDE_OFF')
        col.operator('et.select_by_collection',  text='Select by Collection', icon='RESTRICT_SELECT_OFF')
        col.operator('et.swap_collections',      text='Swap Collections',     icon='ARROW_LEFTRIGHT')
        col.operator('et.rename_by_collection',  text='Rename by Collection', icon='FONT_DATA')
        col.operator('et.visibility_bookmark',   text='Visibility Bookmark',  icon='MARKER')
        # Collection Stats lives in the Analysis panel next to Scene Stats.
        # Both panels share the EasyTasks tab, so listing it here too put the
        # same button on screen twice.


# ---------------------------------------------------------------------------
# N-Panel: ET: Tools
# ---------------------------------------------------------------------------

class ET_PT_ToolsPanel(bpy.types.Panel):
    bl_label       = "Tools"
    bl_idname      = "ET_PT_tools_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'EasyTasks'
    bl_order       = 2

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Origin", icon='OBJECT_ORIGIN')
        row = box.row(align=True)
        row.operator('object.origin_set', text='To Geometry',   icon='OBJECT_ORIGIN').type  = 'ORIGIN_GEOMETRY'
        row.operator('et.origin_to_base', text='To Base',       icon='TRIA_DOWN')
        row = box.row(align=True)
        row.operator('object.origin_set', text='To Cursor',     icon='PIVOT_CURSOR').type   = 'ORIGIN_CURSOR'
        row.operator('object.origin_set', text='Geo to Origin', icon='OBJECT_DATAMODE').type = 'GEOMETRY_ORIGIN'

        box = layout.box()
        box.label(text="Object", icon='OBJECT_DATA')
        col = box.column(align=True)
        col.operator('et.drop_it',         text='Drop It',         icon='TRIA_DOWN')
        col.operator('et.smart_duplicate', text='Smart Duplicate', icon='DUPLICATE')
        col.separator()
        col.operator('et.wnormals_bevel',  text='WNormals + Bevel', icon='MOD_NORMALEDIT')
        col.operator('et.correct_normals', text='Correct Normals',  icon='NORMALS_VERTEX_FACE')
        col.operator('et.clean_up_mesh',   text='Clean Up Mesh',    icon='BRUSH_DATA')

        box = layout.box()
        box.label(text="Quick Mirror", icon='MOD_MIRROR')
        draw_quick_mirror(box.column(align=True), context)

        box = layout.box()
        box.label(text="Materials", icon='MATERIAL')
        col = box.column(align=True)
        col.operator('et.megascans_material',    text='Megascans Material',    icon='NODE_MATERIAL')
        col.operator('et.blend_materials',       text='Blend Materials',       icon='MOD_MASK')
        # Painting and the mask sliders live in the Blend Mask panel, which
        # appears by itself once a blended material is active. Repeating them
        # here would put the same button on screen twice in one tab.
        col.separator()
        col.operator('et.consolidate_materials', text='Consolidate Materials', icon='MATERIAL')

        box = layout.box()
        box.label(text="Scene", icon='SCENE_DATA')
        col = box.column(align=True)
        col.operator('et.generate_lods',         text='Generate LODs',         icon='MOD_DECIM')
        col.operator('et.place_reference_image', text='Place Reference Image', icon='IMAGE_REFERENCE')
        col.operator('et.scene_snapshot',        text='Scene Snapshot',        icon='FILE_TICK')

        box = layout.box()
        box.label(text="Export", icon='EXPORT')
        box.operator('et.batch_export', text='Batch Export', icon='EXPORT')


# ---------------------------------------------------------------------------
# N-Panel: ET: Analysis
# ---------------------------------------------------------------------------

class ET_PT_AnalysisPanel(bpy.types.Panel):
    bl_label       = "Analysis"
    bl_idname      = "ET_PT_analysis_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'EasyTasks'
    bl_order       = 3

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Viewport Preview", icon='SHADING_SOLID')
        col        = box.column(align=True)
        # Preview state is per-viewport, so the toggle reflects this panel's own
        # 3D view rather than whichever viewport was touched last.
        preview    = _active_preview(context.space_data)
        sil_on     = preview == 'SILHOUETTE'
        cavity_on  = preview == 'CAVITY'
        stretch_on = bool(_stretch_state)
        col.operator('et.silhouette_check',
                     text='Silhouette  ●  ON' if sil_on else 'Silhouette Check',
                     icon='SHADING_SOLID',
                     depress=sil_on)
        col.operator('et.cavity_preview',
                     text='Cavity  ●  ON' if cavity_on else 'Cavity Preview',
                     icon='SHADING_RENDERED',
                     depress=cavity_on)
        col.operator('et.face_stretch_analyzer',
                     text='Face Stretch  ●  ON' if stretch_on else 'Face Stretch',
                     icon='MOD_UVPROJECT',
                     depress=stretch_on)

        box = layout.box()
        box.label(text="UV", icon='UV')
        col = box.column(align=True)
        col.operator('et.uv_health',          text='UV Health Check',    icon='UV')
        col.operator('et.stacked_uv_detector', text='Stacked UV Detector', icon='UV_SYNC_SELECT')

        box = layout.box()
        box.label(text="Statistics", icon='INFO')
        row = box.row(align=True)
        row.operator('et.scene_stats',      text='Scene Stats',      icon='INFO')
        row.operator('et.collection_stats', text='Collection Stats', icon='SPREADSHEET')


# ---------------------------------------------------------------------------
# N-Panel: ET: Blend Mask  (only while a blended material is active)
# ---------------------------------------------------------------------------

class ET_PT_BlendMaskPanel(bpy.types.Panel):
    bl_label       = "Blend Mask"
    bl_idname      = "ET_PT_blend_mask_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'EasyTasks'
    bl_order       = 4

    @classmethod
    def poll(cls, context):
        return _active_blend_mask(context) is not None

    def draw(self, context):
        layout = self.layout
        node   = _active_blend_mask(context)
        if node is None:                       # material swapped mid-draw
            return

        obj = context.active_object
        layout.label(text=obj.active_material.name, icon='MATERIAL')

        box = layout.box()
        box.label(text="Preset", icon='PRESET')
        box.operator_menu_enum('et.apply_blend_preset', 'preset',
                               text="Load a Preset")

        def slider(parent, socket_name, label):
            socket = node.inputs.get(socket_name)
            if socket is not None:
                parent.prop(socket, 'default_value', text=label)

        box = layout.box()
        box.label(text="Mask", icon='MOD_MASK')
        col = box.column(align=True)
        slider(col, 'Amount',      'Overlay Amount')
        slider(col, 'Softness',    'Softness')
        slider(col, 'Noise Scale', 'Noise Scale')

        box = layout.box()
        box.label(text="Direction", icon='NORMALS_FACE')
        row = box.row(align=True)
        row.operator('et.set_blend_axis', text='Up').axis   = 'UP'
        row.operator('et.set_blend_axis', text='Down').axis = 'DOWN'
        row = box.row(align=True)
        row.operator('et.set_blend_axis', text='+X').axis = 'X'
        row.operator('et.set_blend_axis', text='-X').axis = 'NEG_X'
        row.operator('et.set_blend_axis', text='+Y').axis = 'Y'
        row.operator('et.set_blend_axis', text='-Y').axis = 'NEG_Y'
        col = box.column(align=True)
        slider(col, 'Gradient',          'Gradient / Face Angle')
        slider(col, 'Directionality',    'Strength')
        slider(col, 'Direction Falloff', 'Falloff')

        box = layout.box()
        box.label(text="Paint", icon='BRUSH_DATA')
        painting = obj.mode in {'TEXTURE_PAINT', 'VERTEX_PAINT'}
        box.operator('et.paint_blend_mask',
                     text='Painting  ●  ON' if painting else 'Paint Blend Mask',
                     icon='BRUSH_DATA',
                     depress=painting)
        if not painting:
            # Which layer to paint on. The highlighted one is what the plain
            # button above uses; the other starts (or returns to) that layer.
            current = _default_paint_type(obj, node)
            row = box.row(align=True)
            row.label(text="Paint On")
            for kind, text, icon in (('IMAGE', "Image", 'IMAGE_DATA'),
                                     ('VERTEX', "Vertex Color", 'VPAINT_HLT')):
                row.operator('et.paint_blend_mask', text=text, icon=icon,
                             depress=(current == kind)).mask_type = kind
        slider(box, 'Paint Influence', 'Paint Influence')

        # Unsaved paint renders as an unpainted mask, so say so where it will
        # be seen rather than leaving it to be discovered at render time.
        image = _blend_paint_image(node)
        if image is not None and image.is_dirty:
            box.label(text="Unsaved paint — renders blank", icon='ERROR')
            box.operator('et.save_blend_mask', text='Save Painted Mask',
                         icon='FILE_TICK')


# ---------------------------------------------------------------------------
# UV Health — what actually stops a texture or a paint mask behaving
# ---------------------------------------------------------------------------

def _uv_report(obj, texture_px=2048):
    """
    Measure one object's UVs. Returns a dict, or None if it has no geometry.

    Texel density is the number that matters across a set of assets: two walls
    at different densities show visibly different texture detail side by side,
    and nothing in Blender tells you until you look at a render.
    """
    mesh = getattr(obj, 'data', None)
    polygons = getattr(mesh, 'polygons', None)
    if not polygons:
        return None

    layer = _texture_uv_layer(mesh)
    entry = {'name': obj.name, 'faces': len(polygons), 'layers':
             len(mesh.uv_layers), 'uv': layer.name if layer else None,
             'density': 0.0, 'ratio': 0.0, 'tiles': False, 'degenerate': 0,
             'span': 0.0, 'area': 0.0, 'uv_area': 0.0}

    # Triangles once, then everything below is whole-array numpy: the old
    # per-polygon Python walk took seconds on a dense scan.
    mesh.calc_loop_triangles()
    tris = mesh.loop_triangles
    count = len(tris)
    if not count:
        return entry
    tri_verts = np.empty(count * 3, dtype=np.int32)
    tri_loops = np.empty(count * 3, dtype=np.int32)
    tri_poly  = np.empty(count,     dtype=np.int32)
    tris.foreach_get('vertices', tri_verts)
    tris.foreach_get('loops', tri_loops)
    tris.foreach_get('polygon_index', tri_poly)

    # World-space area from transformed vertices. Averaging the object's scale
    # read a (2, 1, 1) object as 1.33x everywhere instead of 2x along one axis,
    # so non-uniformly scaled objects got the wrong density and showed up as
    # outliers they were not.
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get('co', co)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    world = co.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    p = world[tri_verts].reshape(count, 3, 3)
    world_area = 0.5 * np.linalg.norm(
        np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1).sum()
    entry['area'] = float(world_area)

    # No layer, or one whose loop data is not available (Edit Mode keeps it in
    # the BMesh): report what is known rather than raising on an empty array.
    if layer is None or len(layer.data) != len(mesh.loops):
        return entry

    uv = np.empty(len(layer.data) * 2, dtype=np.float64)
    layer.data.foreach_get('uv', uv)
    uv = uv.reshape(-1, 2)
    low, high = uv.min(axis=0), uv.max(axis=0)
    entry['span']  = float((high - low).max())
    entry['tiles'] = bool((low < -0.001).any() or (high > 1.001).any())

    t  = uv[tri_loops].reshape(count, 3, 2)
    e1 = t[:, 1] - t[:, 0]
    e2 = t[:, 2] - t[:, 0]
    tri_uv = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    per_poly = np.bincount(tri_poly, weights=tri_uv, minlength=len(polygons))
    entry['degenerate'] = int((per_poly <= 1e-12).sum())
    uv_area = float(tri_uv.sum())
    entry['uv_area'] = uv_area

    if world_area > 1e-9 and uv_area > 1e-12:
        # Density per texture pixel, kept separately so the popup can rescale
        # it when Texture Size changes without measuring everything again.
        entry['ratio']   = (uv_area / world_area) ** 0.5
        entry['density'] = entry['ratio'] * texture_px
    return entry


def _texture_uv_layer(mesh):
    """
    The UV layer the textures render with: the active *render* layer, never
    the paint mask's own. Painting makes ET_BlendPaintUV the active layer, so
    measuring 'active' reported the paint layout instead of the texture one.
    """
    layers = mesh.uv_layers
    render = next((l for l in layers if l.active_render
                   and l.name != _BLEND_PAINT_UV), None)
    if render is not None:
        return render
    return next((l for l in layers if l.name != _BLEND_PAINT_UV), None) \
        or layers.active


def _uv_reports_at(reports, texture_px):
    """The same measurements with density quoted for another texture size."""
    return [dict(r, density=r['ratio'] * texture_px) for r in reports]


def _uv_findings(reports):
    """Turn per-object measurements into the things worth saying."""
    findings = []
    missing = [r['name'] for r in reports if r['uv'] is None]
    if missing:
        findings.append(('ERROR', f"{len(missing)} object(s) have no UV map: "
                                  f"{', '.join(missing[:3])}"
                                  f"{' …' if len(missing) > 3 else ''}"))

    degenerate = [r['name'] for r in reports if r['degenerate']]
    if degenerate:
        findings.append(('ERROR',
                         f"{len(degenerate)} object(s) have zero-area UV faces — "
                         f"they cannot be textured or painted"))

    tiling = [r for r in reports if r['tiles']]
    if tiling:
        worst = max(tiling, key=lambda r: r['span'])
        findings.append(('INFO',
                         f"{len(tiling)} object(s) tile past 0-1 (up to "
                         f"{worst['span']:.1f}x on {worst['name']}) — right for "
                         f"textures, but a paint mask needs its own UV layer"))

    densities = [r['density'] for r in reports if r['density'] > 0.0]
    if len(densities) > 1:
        ordered = sorted(densities)
        median  = ordered[len(ordered) // 2]
        spread  = max(densities) / max(min(densities), 1e-9)
        outliers = [r['name'] for r in reports
                    if r['density'] > 0.0
                    and (r['density'] > median * 2.0 or r['density'] < median / 2.0)]
        level = 'WARNING' if outliers else 'OK'
        message = (f"Texel density {min(densities):.0f}–{max(densities):.0f} px/m "
                   f"(median {median:.0f}, {spread:.1f}x spread)")
        if outliers:
            message += (f" — off by more than 2x: "
                        f"{', '.join(outliers[:3])}"
                        f"{' …' if len(outliers) > 3 else ''}")
        findings.append((level, message))
    elif densities:
        findings.append(('OK', f"Texel density {densities[0]:.0f} px/m"))

    if not findings:
        findings.append(('OK', "Nothing to flag"))
    return findings


class ET_OT_UVHealth(bpy.types.Operator):
    bl_idname  = "et.uv_health"
    bl_label   = "UV Health Check"
    bl_description = ("Check the selection for missing UVs, zero-area faces, "
                      "tiling, and texel density that varies between objects")
    bl_options = {"REGISTER"}

    texture_px: bpy.props.EnumProperty(
        name="Texture Size",
        items=[('1024', "1K", ""), ('2048', "2K", ""),
               ('4096', "4K", ""), ('8192', "8K", "")],
        default='2048',
        description="Density is quoted in pixels per metre, so it needs to know "
                    "what size texture you intend to use")

    _findings = []
    _reports  = []

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def invoke(self, context, event):
        self._gather(context)
        return context.window_manager.invoke_popup(self, width=460)

    def _gather(self, context):
        size    = int(self.texture_px)
        meshes  = [o for o in context.selected_objects if o.type == 'MESH']
        # In Edit Mode the geometry and UVs live in the BMesh; the mesh
        # datablock's loop UVs read back empty even after
        # update_from_editmode(), and measuring them raised. Step out for the
        # measurement and straight back in.
        was_editing = context.mode == 'EDIT_MESH'
        if was_editing:
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            reports = [_uv_report(o, size) for o in meshes]
        finally:
            if was_editing:
                bpy.ops.object.mode_set(mode='EDIT')
        self._reports  = [r for r in reports if r]
        self._findings = _uv_findings(self._reports)

    def execute(self, context):
        self._gather(context)
        worst = 'INFO'
        for level, _message in self._findings:
            if level == 'ERROR':
                worst = 'ERROR'
                break
            if level == 'WARNING':
                worst = 'WARNING'
        summary = "; ".join(m for _l, m in self._findings)
        self.report({'WARNING'} if worst != 'INFO' else {'INFO'},
                    summary[:400])
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="UV Health", icon='UV')
        box.prop(self, 'texture_px')

        # Re-quote at whatever size is picked now. The measurements were taken
        # once in invoke; only the pixels-per-metre figure depends on the size,
        # and before this the dropdown changed nothing on screen.
        reports  = _uv_reports_at(self._reports, int(self.texture_px))
        findings = _uv_findings(reports)

        icons = {'ERROR': 'ERROR', 'WARNING': 'ERROR', 'INFO': 'INFO',
                 'OK': 'CHECKMARK'}
        col = box.column(align=True)
        for level, message in findings:
            col.label(text=message, icon=icons.get(level, 'INFO'))

        if not reports:
            return
        box = layout.box()
        box.label(text=f"{len(reports)} object(s)", icon='OBJECT_DATA')
        col = box.column(align=True)
        for entry in sorted(reports,
                            key=lambda r: r['density'], reverse=True)[:12]:
            uv = entry['uv'] or "no UV map"
            col.label(text=f"{entry['name']:<24} {entry['density']:>7.0f} px/m   "
                           f"{entry['faces']:>6} faces   {uv}")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    # PropertyGroups must register before anything that points at them.
    ET_QuickSlot,
    ET_SemanticSettings,
    ET_OT_AssignSemantic,
    ET_OT_ScanProject,
    ET_OT_PickQuickSlots,
    ET_OT_SetAllQuickSlots,
    ET_MT_SemanticMenu,
    # Right-click ▸ Add to Collection
    ET_OT_AddToCollection,
    ET_OT_NewCollectionForSelection,
    ET_MT_AddToCollectionMenu,
    ET_OT_ApplyModifiers,
    ET_OT_ClearModifiers,
    ET_OT_AddModifier,
    ET_OT_QuickMirror,
    ET_MT_QuickMirrorMenu,
    ET_GGT_QuickMirror,
    ET_OT_BatchExport,
    ET_MT_InteractionModePie,
    ET_MT_ModifiersMenu,
    ET_MT_UVSharpMenu,
    ET_MT_AssetMenu,
    ET_MT_LinkTransferMenu,
    ET_MT_FavToolsMenu,
    ET_OT_OrganizeScene,
    ET_OT_ArrangeScene,
    ET_OT_IsolateCollection,
    ET_OT_CollectionColorSync,
    # Organization tools
    ET_OT_VisibilityBookmark,
    ET_OT_RenameByCollection,
    ET_OT_SelectByCollection,
    ET_OT_SwapCollections,
    ET_OT_CollectionStats,
    # Scene stats / materials / LODs
    ET_OT_SceneStats,
    ET_OT_ConsolidateMaterials,
    ET_OT_MegascansMaterial,
    ET_OT_BlendMaterials,
    ET_OT_ApplyBlendPreset,
    ET_OT_SetBlendAxis,
    ET_OT_SaveBlendMask,
    ET_OT_PaintBlendMask,
    ET_OT_GenerateLODs,
    ET_OT_OriginToBase,
    ET_MT_OriginMenu,
    ET_OT_PlaceReferenceImage,
    ET_OT_SceneSnapshot,
    ET_OT_DropIt,
    ET_OT_SelectByType,
    ET_OT_WNormalsBevel,
    ET_OT_CorrectNormals,
    ET_OT_CleanUpMesh,
    # Modeling analysis tools
    ET_OT_SilhouetteCheck,
    ET_OT_CavityPreview,
    ET_OT_FaceStretchAnalyzer,
    ET_OT_SmartDuplicate,
    ET_OT_StackedUVDetector,
    ET_OT_UVHealth,
    # Menus / pie
    ET_MT_SelectMenu,
    ET_MT_OrganizeMenu,
    ET_MT_AnalysisMenu,
    ET_MT_EasyTasksPie,
    ET_AddonPreferences,
    # N-Panels
    ET_PT_ScenePanel,
    ET_PT_ToolsPanel,
    ET_PT_AnalysisPanel,
    ET_PT_BlendMaskPanel,
)


# (addon_keymaps key, keymap name, space_type, operator, key, modifiers, menu)
# Bound in the '3D View' keymap rather than 'Window'/EMPTY: the old scope made
# Q and Ctrl+Shift+X global, so they fired in the shader editor, the outliner,
# the video sequencer — every editor in Blender.
_KEYMAP_SPEC = (
    ('easy_tasks_pie',       'wm.call_menu_pie', 'X', {'ctrl': True, 'shift': True},
     'ET_MT_easy_tasks_pie'),
    ('fav_tools',            'wm.call_menu',     'Q', {},
     'ET_MT_fav_tools_menu'),
    ('interaction_mode_pie', 'wm.call_menu_pie', 'X', {'alt': True, 'shift': True},
     'ET_MT_interaction_mode_pie'),
)


# Undo and redo only invalidate the auto-route snapshot; loading a file
# invalidates every piece of state kept for the previous one; saving a file
# makes any painted masks durable first.
_STATE_HANDLERS = (
    ('load_post', _reset_session_state),
    ('undo_post', _reset_route_snapshot),
    ('redo_post', _reset_route_snapshot),
    ('save_pre',  _save_masks_before_file_save),
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.et_semantic = bpy.props.PointerProperty(type=ET_SemanticSettings)
    bpy.types.Scene.et_quick_slots = bpy.props.CollectionProperty(type=ET_QuickSlot)
    bpy.types.Scene.et_mirror_pivot = bpy.props.EnumProperty(
        name="Mirror Pivot",
        description="What Quick Mirror mirrors across",
        items=[(k, l, d, ic, n) for n, (k, l, d, ic) in enumerate(_MIRROR_PIVOTS)],
        default='ACTIVE')

    bpy.types.VIEW3D_MT_editor_menus.append(draw_header_add_menu)
    bpy.types.DATA_PT_modifiers.append(draw_modifier_panel_buttons)
    bpy.types.VIEW3D_MT_object_context_menu.append(draw_object_context_menu)
    bpy.types.OUTLINER_MT_object.append(draw_object_context_menu)

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
        for key, idname, letter, mods, menu in _KEYMAP_SPEC:
            kmi = km.keymap_items.new(idname, letter, 'PRESS', **mods)
            kmi.properties.name = menu
            addon_keymaps[key] = (km, kmi)

    _known_objects.clear()
    _route_pending_reset()
    bpy.app.handlers.depsgraph_update_post.append(_auto_route_new_objects)
    for list_name, handler in _STATE_HANDLERS:
        handlers = getattr(bpy.app.handlers, list_name)
        if handler not in handlers:
            handlers.append(handler)


def _route_pending_reset():
    global _route_pending
    _route_pending = False


def unregister():
    if _auto_route_new_objects in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_auto_route_new_objects)
    for list_name, handler in _STATE_HANDLERS:
        handlers = getattr(bpy.app.handlers, list_name)
        if handler in handlers:
            handlers.remove(handler)

    for km, kmi in addon_keymaps.values():
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError):
            pass
    addon_keymaps.clear()

    for draw_fn, target in (
        (draw_header_add_menu,        bpy.types.VIEW3D_MT_editor_menus),
        (draw_modifier_panel_buttons, bpy.types.DATA_PT_modifiers),
        (draw_object_context_menu,    bpy.types.VIEW3D_MT_object_context_menu),
        (draw_object_context_menu,    bpy.types.OUTLINER_MT_object),
    ):
        try:
            target.remove(draw_fn)
        except (ValueError, AttributeError):
            pass

    _known_objects.clear()
    _shading_state.clear()
    _stretch_state.clear()
    _isolation_state.clear()
    _vis_bookmarks.clear()

    del bpy.types.Scene.et_mirror_pivot
    del bpy.types.Scene.et_quick_slots
    del bpy.types.Scene.et_semantic

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
