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
    "description": "A collection of Easy access Tools.",
    "blender": (3, 0, 0),
    "version": (2, 4, 0),
    "location": "",
    "warning": "",
    "doc_url": "https://www.artstation.com/albertocordero",
    "tracker_url": "",
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

# Viewport shading previews (silhouette / cavity). Keyed by space pointer so two
# viewports can't clobber each other's saved shading.
_shading_state  = {}       # {space_ptr: (mode, {prop: value})}

# Face-stretch analyzer. Material *names* are stored, not ID pointers â€” holding
# bpy IDs across an undo step can leave stale pointers behind.
_stretch_state  = {}       # {obj_name: [material_name | None]}

# session-only visibility bookmarks
_vis_bookmarks  = {}       # {slot_name: {lc_name: hide_viewport}}

_MAT_STRETCH    = "ET_FaceStretch"
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


def _build_route_index():
    """
    Build {keyword: Collection} once, so routing N objects costs O(N) name-token
    lookups instead of O(N Ã— collections) string comparisons.

    Keys are the lowercased collection name plus its naive singular, so a
    collection 'ROCKS' matches an object named 'Rock_Pile_01'.
    """
    index = {}
    for coll in bpy.data.collections:
        lower = coll.name.lower()
        index.setdefault(lower, coll)
        if lower.endswith('s') and len(lower) > 2:
            index.setdefault(lower[:-1], coll)
    return index


def _find_target_collection(obj, index=None):
    """
    Return an existing bpy.data collection to route obj into, or None.

    Priority:
      1. Object type  (LIGHT â†’ LIGHTS, CAMERA â†’ CAMERAS)
      2. Hardcoded aliases  (cam â†’ CAMERAS, lamp â†’ LIGHTS, ceil â†’ CEILING)
      3. Any existing collection matched by name / simple singular

    Pass a prebuilt `index` from _build_route_index() when routing in bulk.
    """
    coll_name = _TYPE_COLLECTION.get(obj.type)
    if coll_name:
        coll = bpy.data.collections.get(coll_name)
        if coll:
            return coll

    if index is None:
        index = _build_route_index()

    parts = _obj_name_parts(obj)

    for part in parts:
        alias = _KEYWORD_ALIASES.get(part)
        if alias:
            coll = bpy.data.collections.get(alias)
            if coll:
                return coll

    for part in parts:
        coll = index.get(part)
        if coll:
            return coll

    return None


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
# Auto-route handler â€” silently moves newly added objects to matching
# collections without the user having to run Arrange Scene manually.
# ---------------------------------------------------------------------------

def _prefs():
    """Addon preferences, or None if the addon isn't registered as expected."""
    try:
        return bpy.context.preferences.addons[__name__].preferences
    except (KeyError, AttributeError):
        return None


@bpy.app.handlers.persistent
def _auto_route_new_objects(scene, depsgraph):
    # This fires on *every* depsgraph update â€” dragging a vertex, scrubbing the
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
        index = _build_route_index()
        for name in new_names:
            obj = s.objects.get(name)
            if obj is None:
                continue
            target = _find_target_collection(obj, index)
            if target is None:
                continue
            current_colls = list(obj.users_collection)
            if len(current_colls) != 1 or current_colls[0] == target:
                continue
            current_colls[0].objects.unlink(obj)
            target.objects.link(obj)

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


def _toggle_shading_preview(space, mode, settings):
    """Toggle a named preview on one viewport. Returns True if it is now on."""
    ptr   = space.as_pointer()
    entry = _shading_state.pop(ptr, None)

    if entry is not None:
        _apply_shading(space.shading, entry[1])
        if entry[0] == mode:
            return False

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
                        f"'{obj.name}' has multi-user data â€” make it single-user first")
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

        if self.apply_scale:
            bpy.ops.object.transform_apply(scale=True)
        if self.apply_rotation:
            bpy.ops.object.transform_apply(rotation=True)
        if self.apply_location:
            bpy.ops.object.transform_apply(location=True)

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
        layout.separator()
        layout.operator('et.batch_export', text='Batch Export', icon='EXPORT')
        layout.separator()
        layout.menu('ET_MT_uv_sharp_menu', text='UVs | Sharp Edge', icon='UV')
        layout.separator()
        layout.operator('object.transform_apply', text='Apply Scale').scale       = True
        layout.operator('object.transform_apply', text='Apply Rotation').rotation = True
        layout.operator('object.transform_apply', text='Apply Location').location = True
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
    # and a hand-written selections dict â€” three places that all had to agree
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

        def unlink_all(obj):
            for c in list(obj.users_collection):
                c.objects.unlink(obj)

        def ensure_collection(name):
            coll = bpy.data.collections.get(name)
            if not coll:
                coll = bpy.data.collections.new(name)
            if coll.name not in {c.name for c in scene.collection.children}:
                scene.collection.children.link(coll)
            return coll

        index = _build_route_index()

        routed         = 0
        prefix_objects = []
        for obj in scene.objects:
            target = _find_target_collection(obj, index)
            if target:
                unlink_all(obj)
                target.objects.link(obj)
                routed += 1
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
                unlink_all(obj)
                coll.objects.link(obj)

        self.report({'INFO'},
                    f"Routed {routed} object(s), grouped {len(groups)} by name")
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
            self.report({'INFO'}, "Collection isolated â€” run again to restore")

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
# *selected* to a category the user names by hand â€” select the floor meshes,
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
        # Unknown token in the pattern â€” fall back rather than raising in draw().
        name = f"{label}_{_project_name(scene)}"
    return name.strip() or label


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

    Linking happens after unlinking within the same operator call, so an object
    is never left without a collection.
    """
    count = 0
    for obj in objects:
        if move:
            for current in list(obj.users_collection):
                if current is not coll:
                    current.objects.unlink(obj)
        if obj.name not in coll.objects:
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

    target_collection: bpy.props.StringProperty(
        name="Collection",
        description="Collection to assign the selection to")

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
        # inventing a new collection beside it â€” clicking Floor should drop the
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
        self.report({'INFO'}, f"{verb} '{coll.name}' â€” {assigned} object(s)")
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
            layout.label(text="Nothing found â€” is the root empty?", icon='INFO')
            return

        col = layout.column(align=True)
        for slot in slots:
            row = col.row(align=True)
            if slot.depth:
                row.separator(factor=slot.depth * 2.0)
            row.prop(slot, 'use', text=slot.name, toggle=True,
                     icon='CHECKBOX_HLT' if slot.use else 'CHECKBOX_DEHLT')

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
                    text="", icon='OUTLINER_COLLECTION')
    row.operator('et.scan_project', text="", icon='FILE_REFRESH')

    if root is None:
        box.label(text="Set a project collection to use its hierarchy",
                  icon='INFO')

    use_project = settings.source == 'PROJECT' and root is not None

    # --- target + add ------------------------------------------------------
    col = box.column(align=True)
    if use_project:
        col.prop_search(settings, 'target_collection', bpy.data, 'collections',
                        text="")
        target = settings.target_collection.strip()
        row = col.row(align=True)
        row.scale_y = 1.3
        row.enabled = bool(target)
        row.operator('et.add_to_collection', text='Add Selection',
                     icon='ADD').collection_name = target
    else:
        if settings.use_custom:
            col.prop(settings, 'custom_category', text='',
                     icon='OUTLINER_COLLECTION')
            label = settings.custom_category.strip() or "Category"
        else:
            col.prop(settings, 'category', text='')
            label = _SEMANTIC_LOOKUP[settings.category][0]

        # Show where this will actually land, so it is never a guess at the
        # moment of clicking â€” an existing collection when one matches, or the
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
            box.label(text="No collections picked â€” use the tick icon",
                      icon='INFO')
        else:
            grid = box.grid_flow(row_major=True, columns=3, align=True)
            for slot in slots:
                coll = bpy.data.collections.get(slot.name)
                if coll is None:
                    continue
                grid.operator('et.add_to_collection', text=slot.name,
                              icon='OUTLINER_COLLECTION'
                              ).collection_name = slot.name
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
# Right-click â–¸ Add to Collection
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

    target = _find_target_collection(obj)
    if target is None:
        return None
    # Pointless to suggest the collection everything is already in.
    # users_collection holds Collection objects, not names â€” comparing against
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
        # Called as "Search allâ€¦" with no name -> offer the search field.
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
                    f"â€” {count} object(s)")
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
                            icon='OUTLINER_COLLECTION'
                            ).collection_name = suggested.name
            layout.separator()

        layout.operator('et.new_collection_for_selection',
                        text="New Collectionâ€¦", icon='ADD')
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
                            icon='OUTLINER_COLLECTION'
                            ).collection_name = coll.name

        # Never silently truncate â€” say how many are hidden and offer search.
        hidden = len(collections) - _MENU_COLLECTION_LIMIT
        if hidden > 0:
            layout.separator()
            layout.operator('et.add_to_collection',
                            text=f"Search allâ€¦ (+{hidden} more)",
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
                box.label(text=f"  â€¢ {name}")

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
        "Rename selected objects to CollectionName_01, _02 â€¦ "
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

    # draw() runs on every redraw of the popup â€” once per mouse move. Counting
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
        col.label(text=f"Objects   â€”  Selection: {s['sel_objs']:>6}    Scene: {s['all_objs']}")
        col.label(text=f"Faces     â€”  Selection: {s['sel_faces']:>6,}    Scene: {s['all_faces']:,}")
        col.label(text=f"Vertices  â€”  Selection: {s['sel_verts']:>6,}    Scene: {s['all_verts']:,}")
        col.label(text=f"Materials â€”  Selection: {s['sel_mats']:>6}    Scene: {s['all_mats']}")


# ---------------------------------------------------------------------------
# Material Consolidate
# ---------------------------------------------------------------------------

class ET_OT_ConsolidateMaterials(bpy.types.Operator):
    bl_idname  = "et.consolidate_materials"
    bl_label   = "Consolidate Materials"
    bl_description = "Merge duplicate materials (Mat.001, Mat.002 â†’ Mat) and optionally remove unused"
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
                # No original left â€” promote the first copy so the rest still merge.
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
            msg += f" â€” {shared} skipped (multi-user data)"
        if skipped:
            msg += f" â€” {skipped} unsupported"
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
        # to run bpy.ops.object.select_by_type â€” that swap redraws the editor
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
                    "Silhouette on â€” click again to restore" if on
                    else "Silhouette off â€” shading restored")
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
                    "Cavity preview on â€” click again to restore" if on
                    else "Cavity preview off â€” shading restored")
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
        # selected â€” otherwise deselecting traps the user with the analyzer
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
        # blue â†’ green: UV island larger than the 3D face
        rgb[compressed, 0] = 0.0
        rgb[compressed, 1] = t[compressed]
        rgb[compressed, 2] = 1.0 - t[compressed]
        # green â†’ red: 3D face larger than the UV island
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

    def _remove_vcol(self, mesh):
        if hasattr(mesh, 'color_attributes'):
            vcol = mesh.color_attributes.get(_VCOL_STRETCH)
            if vcol:
                mesh.color_attributes.remove(vcol)
        else:
            vcol = mesh.vertex_colors.get(_VCOL_STRETCH)
            if vcol:
                mesh.vertex_colors.remove(vcol)

    def _build_mat(self):
        mat = bpy.data.materials.get(_MAT_STRETCH)
        if mat:
            return mat
        mat = bpy.data.materials.new(_MAT_STRETCH)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        out  = nodes.new('ShaderNodeOutputMaterial'); out.location  = (300, 0)
        diff = nodes.new('ShaderNodeBsdfDiffuse');    diff.location = (100, 0)
        vcol = nodes.new('ShaderNodeVertexColor');    vcol.location = (-100, 0)
        vcol.layer_name = _VCOL_STRETCH
        links.new(vcol.outputs['Color'], diff.inputs['Color'])
        links.new(diff.outputs['BSDF'],  out.inputs['Surface'])
        return mat

    def _restore(self):
        """
        Put every object's original materials back.

        Restoring used to iterate context.selected_objects, so changing the
        selection while the analyzer was on wiped the material slots of any
        object that had scrolled out of the selection â€” and cleared the state
        dict anyway, making the loss permanent.
        """
        restored = 0
        for obj_name, mat_names in _stretch_state.items():
            obj = bpy.data.objects.get(obj_name)
            if obj is None or obj.type != 'MESH':
                continue
            obj.data.materials.clear()
            for mat_name in mat_names:
                obj.data.materials.append(
                    bpy.data.materials.get(mat_name) if mat_name else None)
            self._remove_vcol(obj.data)
            restored += 1

        _stretch_state.clear()

        mat = bpy.data.materials.get(_MAT_STRETCH)
        if mat is not None:
            bpy.data.materials.remove(mat)
        return restored

    def execute(self, context):
        if _stretch_state:
            restored = self._restore()
            self.report({'INFO'}, f"Face stretch off â€” restored {restored} object(s)")
            return {"FINISHED"}

        mat     = self._build_mat()
        skipped = 0
        shown   = 0

        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue

            loop_colors = self._loop_colors(obj)
            if loop_colors is None:
                skipped += 1
                continue

            mesh = obj.data
            vcol = self._get_or_create_vcol(mesh)
            vcol.data.foreach_set('color', loop_colors)
            mesh.update()

            # Names, not material pointers: a pointer held across an undo step
            # can dangle and crash Blender when it is dereferenced later.
            _stretch_state[obj.name] = [m.name if m else None
                                        for m in mesh.materials]
            mesh.materials.clear()
            mesh.materials.append(mat)
            shown += 1

        if not shown:
            if mat is not None:
                bpy.data.materials.remove(mat)
            self.report({'WARNING'}, "No selected mesh has usable UVs")
            return {'CANCELLED'}

        msg = f"Face stretch on for {shown} mesh(es) â€” blue=compressed, green=ok, red=stretched"
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
        layout.operator('et.stacked_uv_detector',   text='Stacked UV Detector', icon='UV_SYNC_SELECT')


class ET_MT_EasyTasksPie(bpy.types.Menu):
    bl_idname = "ET_MT_easy_tasks_pie"
    bl_label  = "Easy Tasks"

    def draw(self, context):
        pie = self.layout.menu_pie()

        # Slot 1 â€“ left: Overlay (largest box â€” left slot has the most outward room)
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

        # Slot 2 â€“ right: Collections + Select by Type
        # (no diagonal slots â€” all 4 cardinals hold boxes so diagonals always clip)
        box = pie.box()
        col = box.column(align=True)
        col.label(text="Collections", icon='OUTLINER_COLLECTION')
        col.menu('ET_MT_semantic_menu',         text='Assign to Category',   icon='OUTLINER_COLLECTION')
        col.operator('et.isolate_collection',   text='Isolate Collection',   icon='HIDE_OFF')
        col.operator('et.swap_collections',     text='Swap Collections',     icon='ARROW_LEFTRIGHT')
        col.operator('et.rename_by_collection', text='Rename by Collection', icon='FONT_DATA')
        col.separator()
        col.menu('ET_MT_select_menu', text='Select by Type', icon='RESTRICT_SELECT_OFF')

        # Slot 3 â€“ bottom: Apply Transform + Shading combined
        box = pie.box()
        col = box.column(align=True)
        col.label(text="Apply Transform", icon='OBJECT_DATA')
        col.operator('object.transform_apply', text='Scale').scale       = True
        col.operator('object.transform_apply', text='Rotation').rotation = True
        col.operator('object.transform_apply', text='Location').location = True
        col.separator()
        col.label(text="Shading", icon='NORMALS_FACE')
        col.operator('object.shade_smooth', text='Shade Smooth', icon='MESH_CIRCLE')
        col.operator('object.shade_flat',   text='Shade Flat',   icon='MESH_PLANE')

        # Slot 4 â€“ top: Setup
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
    # matched the preferences to the addon and this whole panel â€” including the
    # three shortcut rebinding fields â€” never appeared.
    bl_idname = __name__

    auto_route: bpy.props.BoolProperty(
        name="Auto-route New Objects",
        default=True,
        description="Silently move newly created objects into a matching "
                    "collection as soon as they are added")

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text='Behaviour', icon='PREFERENCES')
        box.prop(self, 'auto_route')

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
        box.label(text="Scene", icon='SCENE_DATA')
        col = box.column(align=True)
        col.operator('et.generate_lods',         text='Generate LODs',         icon='MOD_DECIM')
        col.operator('et.consolidate_materials', text='Consolidate Materials', icon='MATERIAL')
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
                     text='Silhouette  â—  ON' if sil_on else 'Silhouette Check',
                     icon='SHADING_SOLID',
                     depress=sil_on)
        col.operator('et.cavity_preview',
                     text='Cavity  â—  ON' if cavity_on else 'Cavity Preview',
                     icon='SHADING_RENDERED',
                     depress=cavity_on)
        col.operator('et.face_stretch_analyzer',
                     text='Face Stretch  â—  ON' if stretch_on else 'Face Stretch',
                     icon='MOD_UVPROJECT',
                     depress=stretch_on)

        box = layout.box()
        box.label(text="UV", icon='UV')
        box.operator('et.stacked_uv_detector', text='Stacked UV Detector', icon='UV_SYNC_SELECT')

        box = layout.box()
        box.label(text="Statistics", icon='INFO')
        row = box.row(align=True)
        row.operator('et.scene_stats',      text='Scene Stats',      icon='INFO')
        row.operator('et.collection_stats', text='Collection Stats', icon='SPREADSHEET')


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
    # Right-click â–¸ Add to Collection
    ET_OT_AddToCollection,
    ET_OT_NewCollectionForSelection,
    ET_MT_AddToCollectionMenu,
    ET_OT_ApplyModifiers,
    ET_OT_ClearModifiers,
    ET_OT_AddModifier,
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
)


# (addon_keymaps key, keymap name, space_type, operator, key, modifiers, menu)
# Bound in the '3D View' keymap rather than 'Window'/EMPTY: the old scope made
# Q and Ctrl+Shift+X global, so they fired in the shader editor, the outliner,
# the video sequencer â€” every editor in Blender.
_KEYMAP_SPEC = (
    ('easy_tasks_pie',       'wm.call_menu_pie', 'X', {'ctrl': True, 'shift': True},
     'ET_MT_easy_tasks_pie'),
    ('fav_tools',            'wm.call_menu',     'Q', {},
     'ET_MT_fav_tools_menu'),
    ('interaction_mode_pie', 'wm.call_menu_pie', 'X', {'alt': True, 'shift': True},
     'ET_MT_interaction_mode_pie'),
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.et_semantic = bpy.props.PointerProperty(type=ET_SemanticSettings)
    bpy.types.Scene.et_quick_slots = bpy.props.CollectionProperty(type=ET_QuickSlot)

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


def _route_pending_reset():
    global _route_pending
    _route_pending = False


def unregister():
    if _auto_route_new_objects in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_auto_route_new_objects)

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

    del bpy.types.Scene.et_quick_slots
    del bpy.types.Scene.et_semantic

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
