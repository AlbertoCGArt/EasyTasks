# Changelog

All notable changes to Easy Tasks are recorded here.

## [2.4.0]

### Added
- **Project root collection.** Point Assign to Category at a root collection and
  it works with the hierarchy that is already there. Categories resolve to
  collections inside it, Quick Assign is built from what it actually contains,
  and a picker chooses which of those appear as buttons. Clearing the root falls
  back to the preset categories.
- **Right-click ▸ Add to Collection** in the 3D viewport and the outliner. It
  suggests a destination using the same matcher Arrange Scene uses, lists
  existing collections with object counts, creates new ones with a parent and
  colour tag, and offers a search field past twenty collections instead of
  truncating the menu.
- `tests/redundancy_test.py` — static audit that fails if a button is drawn
  twice on the sidebar, if a container repeats an entry, or if an operator or
  menu is registered but reachable from no UI and no keymap.

### Fixed
- **Quick Assign created a duplicate collection instead of using the project's
  own.** With `PRODUCTION > MODULES > FLOOR` already built, clicking Floor made
  a new `Floor_<project>` at the scene root beside it. Categories now resolve to
  an existing collection under the project root first.
- **The Project Structure dialog drew the wrong hierarchy.** It laid the tree out
  flat by depth rather than depth-first, so LIGHTS and CAMERAS rendered below
  BLOCKING and appeared to be its children instead of STUDIO's, and FLOOR–DECALS
  appeared under those rather than under MODULES. Both `draw()` and `execute()`
  now walk `COLLECTION_STRUCTURE`, replacing eleven hand-written BoolProperties
  and a duplicated selections dict with one indexed BoolVectorProperty.
- Collection Stats was drawn in both the Scene and Analysis panels, which share
  the EasyTasks tab, so it appeared twice on screen at once. It now lives only
  in Analysis, beside Scene Stats.
- The context-menu suggestion compared a collection name against a list of
  collection objects, so it never recognised that the selection was already in
  the collection it was suggesting.

### Removed
- `ET_OT_Convert` — registered but present in no menu or panel, and a bare
  passthrough to `bpy.ops.object.convert` since the context workaround was
  dropped.
- `ET_MT_SceneMenu` and `ET_MT_MeshMenu` — orphaned when the pie was rewritten to
  use boxes; nothing referenced them, and they were the only route to
  `ET_MT_OrganizeMenu` and `ET_MT_AnalysisMenu`, which FavTools now points at
  directly instead of inlining the same eleven rows.

### Changed
- The sidebar's Organize box gained Isolate Collection, Swap Collections and
  Rename by Collection, which were previously reachable only from the pie and
  FavTools.

## [2.3.0]

### Added
- **Assign to Category** — selection-driven collection assignment. Pick a
  category (11 presets or a custom name), hit Add, and the selection moves into
  a collection named from a configurable `{category}` / `{project}` pattern,
  created on first use and reused after. Move-or-link toggle, automatic colour
  tags, configurable parent collection, and a live preview of the resolved
  name. Available in the sidebar, the pie, the Organize menu and FavTools.
- Preference toggle for the auto-route handler.
- Options on existing tools: bevel width and segments, merge-doubles on Clean Up
  Mesh, linked Smart Duplicate, UV match tolerance, orphan-material promotion.
- Headless Blender test suite under `tests/`.

### Fixed
- **Add-on preferences never appeared.** `bl_idname` was hardcoded to
  `easy_tasks` while the package installs as `EasyTasks`, so Blender never
  matched the preferences to the add-on — taking the three shortcut rebinding
  fields with it.
- **Face Stretch Analyzer could destroy material assignments.** It restored from
  the current selection, so changing selection while it was active wiped the
  material slots of anything no longer selected, and cleared its own state so the
  loss was permanent. It also could not be switched off with nothing selected.
- **Silhouette and Cavity clobbered each other.** Enabling one while the other
  was active saved the *modified* shading as the original, leaving the viewport
  stuck. State is now per-viewport and the two are mutually exclusive.
- Generate LODs produced `Foo_LOD0_LOD1` names and duplicate LOD objects on
  re-runs.
- Smart Duplicate put copies in the wrong collection when the source object was
  already named with a `.001` suffix.
- The pie menu failed to draw when opened outside a 3D viewport.
- Shortcuts were bound in the global `Window` keymap, firing in every editor.
  They are now scoped to the 3D View.
- Toolbar and modifier-panel buttons used hardcoded numeric icon IDs, which are
  not stable across Blender versions.
- Collection Statistics counted objects once per collection they belonged to.
- Consolidate Materials skipped families where the un-suffixed original was gone.
- Apply Modifiers now reports failures instead of silently aborting, and refuses
  multi-user data rather than erroring mid-loop.
- Auto-route no longer treats a scene switch as a scene full of new objects.

### Changed
- Source moved into `EasyTasks/` so the folder name matches the module name.
- `build.ps1` packages the add-on and reads the version from `bl_info`.
- Origin to Base uses matrix maths instead of per-object operator calls, no
  longer moving the 3D cursor, and skips multi-user data.
- Select by Type selects directly instead of temporarily rewriting
  `context.area.type`.
- Clean Up Mesh works on every selected mesh and skips shared datablocks.
- WNormals + Bevel reuses existing modifiers instead of stacking new ones.
- Color Sync walks the collection tree so nested collections inherit correctly,
  and can switch the viewport to Object colour.

### Performance
Measured on a 160k-polygon mesh and a 1,200-object scene:

| Path | Before | After |
| --- | --- | --- |
| Face stretch — write vertex colours | 1037 ms | 0.9 ms |
| Stacked UV — build signature | 980 ms | 25 ms |
| Face stretch — compute colours | 808 ms | 127 ms |
| Shade smooth flags | 141 ms | 14 ms |
| Arrange Scene — match all objects | 18 ms | 1.8 ms |

The auto-route handler rebuilt a full set of scene object names on *every*
depsgraph update. It now gates on an integer comparison, so its idle cost no
longer scales with scene size.

## [2.2.0]

Initial version tracked in this changelog.
