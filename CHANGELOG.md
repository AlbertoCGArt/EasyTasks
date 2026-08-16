# Changelog

All notable changes to Easy Tasks are recorded here.

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
