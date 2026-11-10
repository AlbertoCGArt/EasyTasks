# Changelog

All notable changes to Easy Tasks are recorded here.

## [Unreleased]

### Fixed
- **Auto-route no longer takes objects away from other add-ons.** It routed
  every new object by name, including the ones a generator had just filed in
  its own collection: Conduit's Wall to Floor cables went to WALLS, Conduit no
  longer found them, built a fresh set on the next move and left the old one
  behind, so the cables multiplied. Auto-route now moves only what lands where
  Blender adds a new object (the active collection or the scene's), and never
  anything in a collection an add-on keeps for itself (one with custom
  properties). Arrange Scene leaves those alone too and says how many it left.

## [2.6.0]

Material tools join the scene tools: a Megascans importer, two-material
blending with a hand-painted mask, presets, and a UV health check — plus fixes
for three long-standing problems that could change or lose objects in your
scene (see **Fixed**).

### Added
- **Blend presets.** Seven starting points for the mask — Dirt in Crevices,
  Puddles, Grime up a Wall, Snow on Top, Settled Dust, Moss, Streaks from Above
  — covering amount, softness, noise and the whole direction setup. The useful
  values here are not guessable; each of these took a round of trial and error
  to find. Available in the Blend Materials dialog, and from the Blend Mask
  panel as a dropdown that loads onto the material already on the object, so
  changing your mind costs nothing and keeps whatever you have painted.

- **UV Health Check.** Measures the selection for the things that quietly stop
  a texture or a paint mask behaving: objects with no UV map, zero-area UV
  faces, layouts running past 0-1, and **texel density** in pixels per metre.
  Density is the one worth having — two walls at different densities show
  visibly different texture detail side by side and nothing in Blender says so
  until you look at a render. Area is measured from world-space vertices, so a
  scaled or non-uniformly stretched object reports the density it actually
  renders at; it reads the render UV layer, not the paint mask's; the popup's
  Texture Size re-quotes the figures; and it works from Edit Mode. Objects
  more than 2x off the median are named.
- **Blend Materials + Paint Mask.** Two PBR materials mixed through one shared
  mask, every channel at once. The mask starts from the base material's own
  height map, inverted and broken up by noise, so the overlay settles into the
  low spots the way dirt actually does — believable before you touch a slider.
  Amount, Softness, Noise Scale and Paint Influence live on one node group,
  shared logic with per-material values.

  **Paint Mask** is the manual half: white adds the overlay, black scrubs it
  back, `X` swaps. Mid grey is the neutral value, which is what lets a single
  painted layer both add and remove rather than needing two masks. It paints an
  image by default, or a vertex colour layer when the object has no UVs, and
  switching between them only moves the link — the layer you already painted
  stays in the tree, so switching back brings the work with it.

  Painting goes through a UV layer of its own. A tiling surface carries UVs
  running well past 0-1 so the textures repeat, which is right for them and
  fatal for a mask: every tile shares the same wrapped pixels, so one stroke
  appears in all of them at once. A copy of the layout scaled into 0-1 is added
  as `ET_BlendPaintUV`, wired to the mask through a UV Map node and made the
  active layer for painting. The textures keep the tiled layer untouched, since
  they read the active *render* UV map and painting uses the active one. A mesh
  whose UVs already sit inside 0-1 gets the same layer as a plain copy: the UV
  Map node lives in a material many objects share, so the name has to mean the
  same thing on all of them.

  Because that layer is looked up by name, every object on the material gets
  it, not only the one paint mode was entered on. Otherwise a floor built from
  many planes sharing one blend rendered paint on exactly one of them and
  silently nothing on the rest. Sharing still means one mask image between
  them all, so painting one paints all — the operator now says so rather than
  leaving it to be discovered.

  The painted mask is made durable when you leave paint mode through the panel
  and every time the .blend is saved, however paint mode was left. Strokes live
  in memory until something writes them, and a generated image is rebuilt from
  its flat grey on every reload — so an unsaved mask renders blank and the
  blend looks like it ignored the painting. In a saved .blend the mask goes to
  `textures/`, named after both the .blend and the material so two files in
  one folder cannot overwrite each other's painting; in a .blend that has never
  been saved it is packed into the file. Saving happens before packing, never
  after: packing a dirty file-backed image packs the stale file and marks the
  buffer clean, silently destroying the strokes. The panel says "Unsaved paint
  — renders blank" with a Save Painted Mask button whenever there is any.

  Painting is pinned to that one image rather than the material's slot list,
  so a stray stroke cannot land on the Megascans albedo and overwrite the
  library file itself.

  Maps only one side has stay where they belong. The base's own height and
  normal pass straight through when the overlay has none. Anything else only
  one side has is mixed against the value the BSDF would use without it —
  metal 0, opacity 1, a flat normal — because wired straight through, an
  overlay's metalness or opacity made the whole surface metallic or
  see-through rather than just the masked area.

  The blend is a new material; the two sources are read and never written. It
  lands on the slots the object's faces actually use, and only those still
  holding a source — writing to slot 0 meant an object carrying both materials
  kept rendering whichever slot its faces were on, so the blend appeared to do
  nothing, while replacing every slot stripped both sources off the object.
  Unused slots keep their material, and a re-run takes the active slot so the
  object follows the new blend instead of being left on the old one.
  Assignment goes through `material_slots`, which also respects a slot linked
  to the object rather than the mesh.

- **ET: Blend Mask panel.** Appears in the sidebar whenever the active material
  carries a blend, with every mask control live on the node itself — Amount,
  Softness, Noise, axis buttons, Gradient/Face Angle, Strength, Falloff, Paint
  Influence, and the paint toggle. Re-opening the dialog to change your mind
  built a second material and orphaned the mask you had painted; this edits the
  material already in front of you and creates nothing.

- **Directional blending**, in two modes. Pick an axis — the end the overlay
  builds toward — then how the direction is read:

  **Gradient** (the default) ramps the overlay along that axis, a full 0 to 100
  across the mesh: grime climbing a wall, moss up a trunk. It uses normalised
  bounding-box coordinates, so it holds at any scale and follows the object
  when moved or duplicated. **Face Angle** restricts the overlay to faces
  pointing that way — snow on a rock — reading the true world-space normal.

  Face Angle cannot produce a transition on a flat surface: every face there
  shares one normal, so it can only shift the whole surface at once. That is
  what Gradient exists for, and why it is the default. Choosing Face Angle
  where nothing points the chosen way now reports a warning rather than leaving
  an invisible overlay.

  The direction scales the *finished* mask rather than the raw height feeding
  it. Gating before the Amount/Softness threshold squeezed the ramp into
  whatever band the threshold left, so it could reach neither none of the
  overlay nor all of it — a gradient that could not actually run 0 to 100.
  Paint is applied after the gate, so a hand stroke still wins. Left at Any
  nothing changes, which is what keeps earlier materials identical.

  The mask group is rebuilt beside an older one rather than reused when the
  sockets do not match, so opening a file saved by the previous version leaves
  its materials exactly as they were.
- **Megascans Material.** A button, a file browser, and every texture set in the
  folder comes back as a wired Principled BSDF. It parses the naming Bridge
  actually writes — `Rock_Cliff_ug0jaipaw_4K_Normal_LOD0` — so the asset id,
  resolution and LOD tag stay out of the material name, and it reads ordinary
  `BaseColor`/`Normal`/`Rough` sets from anywhere else too.

  Base colour, roughness, metallic, specular and alpha go to their sockets with
  data maps set to Non-Color; the normal goes through a Normal Map node with
  height chained *after* it in a Bump node, so detail rides on the normal map
  rather than replacing it. Gloss is inverted into roughness, DirectX normals
  get their green channel flipped, and packed ORM/RMA maps are split into three
  channels. Height can go to the Material Output as true displacement instead.

  Two things it declines to do quietly: AO is loaded but only multiplied into
  base colour on request, since a renderer that computes occlusion would then
  double-darken; and translucency, cavity and curvature are loaded and labelled
  but left unconnected, because what they should drive is a per-shader call.

  Where a folder ships 2K and 4K side by side it picks one deliberately —
  highest by default, lowest on request — rather than whichever the OS listed
  last, and re-running over the same folder reuses the material instead of
  leaving a `.001` behind.

### Fixed
- **Apply Scale / Rotation / Location applied all three transforms.**
  `object.transform_apply` defaults every flag to True, and the pie and
  FavTools buttons set only their own — so Apply Scale also zeroed rotation and
  moved the origin to the world origin. Every button now sets all three flags,
  the way Blender's own Object ▸ Apply menu does.
- **Batch Export ignored its Rotation and Location checkboxes** for the same
  reason: unticked transforms were baked into your objects anyway.
- **Auto-route reorganised files you opened.** It never forgot the previous
  file's objects, so a file opened afterwards with a scene also called "Scene"
  had its objects silently moved into matching collections on the first click,
  in a step undo could not reverse. Objects brought back by Ctrl+Z were
  re-routed the same way. The snapshot is now reset on load, undo and redo, and
  a file load also drops isolation, preview, Face Stretch and bookmark state
  kept for the previous file.
- **Auto-route, Arrange Scene and Add to Collection could strand objects.**
  Routing considered every collection in the file — another scene's, orphaned
  ones, library-linked ones — and unlinked an object before linking it. A
  refused link left the object in no collection, lost on save; an off-scene
  target took it out of the scene. Only editable collections in the current
  scene are candidates now, objects are linked before they are unlinked, and a
  move no longer pulls an object out of other scenes or the rigid body world.
- **Blender 3.0 is verified, not just claimed.** The full test suite now runs
  green on 3.0 as well as 3.5, 4.0, 4.2, 4.5, 5.1 and 5.2.


## [2.5.0]

### Changed
- **Face Stretch Analyzer no longer touches materials.** It used to clear
  `mesh.materials`, assign a temporary shader and hold the originals in a
  module-level dict — so a crash, or the add-on being disabled, while the
  analyzer was on lost the user's material assignments with no way back. No test
  can cover that, because the failure mode is the process dying.

  It now writes the `ET_Stretch` colour attribute, makes it the active colour
  layer and switches the viewport to display it. Nothing destructive happens to
  the data, it is instant because no material is built, and it drops the
  temporary material and its node tree entirely.

  Two consequences worth knowing: the viewport shows *every* object by its
  colour attribute while the analyzer is on, not only the selection; and the
  analyzer is now mutually exclusive with Silhouette and Cavity, which share the
  same shading machinery, so enabling one cleans up the other.

### Fixed
- Preview toggles no longer raise when there is no 3D viewport in context —
  in background mode, or when reached from a menu in another editor.
- Meshes shared by several objects are coloured once rather than per object.


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
