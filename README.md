# Easy Tasks

A Blender add-on that puts scene organisation, mesh cleanup and modelling
analysis tools one keystroke away.

Built for environment and prop work: keep a scene tidy while you build it,
check your forms and UVs without leaving the viewport, and batch out assets
when you are done.

**Blender 3.0 – 5.x** · GPL-3.0-or-later · by [Alberto Cordero](https://www.artstation.com/albertocordero)

---

## Install

1. Download `easy_tasks_<version>.zip` from the
   [Releases](https://github.com/AlbertoCGArt/EasyTasks/releases) page.
2. In Blender: **Edit ▸ Preferences ▸ Add-ons ▸ Install…**, pick the zip.
3. Tick **Easy Tasks** to enable it.

Tools appear in the **EasyTasks** tab of the 3D viewport sidebar (`N`), and on
the shortcuts below.

> Install the zip, not the source folder. Blender takes the module name from the
> folder inside the archive, and the add-on's preferences are keyed to it.

### From source

Clone and symlink (or copy) the inner `EasyTasks/` folder into your Blender
add-ons directory:

```
%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\EasyTasks
```

Or build a zip yourself:

```powershell
.\build.ps1
```

`build.ps1 -Install "<path to scripts\addons>"` builds and installs in one step.

---

## Shortcuts

| Keys | Opens |
| --- | --- |
| `Ctrl` `Shift` `X` | Easy Tasks pie — overlays, collections, transforms, setup |
| `Q` | FavTools menu — the full tool list |
| `Shift` `Alt` `X` | Interaction mode pie — object/edit/sculpt, vert/edge/face |

All three are rebindable in **Preferences ▸ Add-ons ▸ Easy Tasks**. They are
bound in the 3D View keymap, so they do not shadow keys in other editors.

---

## What's in it

Two halves, and it's worth being straight about which is which. The tools below
have no built-in equivalent — they're the reason to install it. Further down are
the batch operations and shortcuts that make it pleasant day to day.

---

## The distinctive part

### Assign to Category
Select the floor meshes, pick **Floor**, click. They go straight into the right
collection.

Point it at a **project root** and it works with the hierarchy you already have,
rather than inventing one beside it — clicking Floor drops the selection into
`PRODUCTION ▸ MODULES ▸ FLOOR`. Quick Assign rebuilds its buttons from the
collections actually found under that root, and a picker chooses which get one.

With no root set it falls back to 11 presets — Floor, Walls, Ceiling, Props,
Decals, Trim, Modular, FX, Lights, Cameras, Blocking — plus free-text
categories, naming new collections from a `{category}` / `{project}` pattern you
control, with `{project}` read from the blend file, the scene, or a value you type.

**Move** or **link**; colour tags applied only to collections that don't already
have one; and the destination shown in the panel before you click, marked
`(existing)` when it resolves to something you already have.

### Arrange Scene, and auto-routing
**Arrange Scene** sorts every object into matching collections by type and name,
falling back to grouping by name stem. **Auto-route** does the same for new
objects the moment you create them — a light or a camera lands in LIGHTS or
CAMERAS without you doing anything. Toggle it off in preferences.

### Right-click ▸ Add to Collection
In the 3D viewport and the outliner. It **suggests** a destination for the
selection using the same matcher Arrange Scene uses, lists existing collections
with object counts, creates new ones with a parent and colour tag, and offers a
search field past twenty collections instead of truncating the menu.

### Face Stretch Analyzer
Colour-codes UV distortion as vertex colours across the whole selection, in the
3D viewport — blue compressed, green even, red stretched. Run it again to
restore your materials exactly.

### Stacked UV Detector
Finds objects sharing an identical UV layout and selects them, which is how
stacked or accidentally-duplicated UVs surface before they reach a bake.

### Silhouette Check and Cavity Preview
One click each, per-viewport, restoring precisely the shading you had. They are
mutually exclusive, so neither can strand the other's settings in your viewport.

### Visibility Bookmarks
Snapshot which collections are visible under a name, then restore that state
later — useful when a scene has several working configurations.

### Collection Color Sync
Pushes each collection's colour tag onto its objects' viewport colour, walking
the tree so nested collections inherit and a child's own tag wins.

---

## Batch work

Things Blender can do, but not in one step.

- **Batch Export** — each selected object to its own FBX / OBJ / GLB, with
  optional prefix/suffix renaming and transform apply.
- **Project Structure** — build a PRODUCTION ▸ STUDIO / MODULES / BLOCKING
  hierarchy, picking which collections you want.
- **Generate LODs** — Decimate-reduced copies into an LODs collection.
- **Consolidate Materials** — merge `Mat.001`/`Mat.002` back into `Mat`.
- **Origin to Base** — origin to the bottom centre of the bounding box.
- **Smart Duplicate** — copies stay in their original collection.
- **Clean Up Mesh** — loose vertices and edges, optionally weld doubles.
- **WNormals + Bevel** — bevel, weighted normals and shade smooth in one.
- **Scene Snapshot** — timestamped copy of the .blend into `snapshots/`.
- **Scene** and **Collection Statistics**.

## Menu shortcuts

One-key access to things Blender already does — a pie and menus covering
modifiers, UV seams and sharp edges, asset marking, link/transfer data, origin
modes, interaction modes, apply transform, shading, select by type, and
isolate / swap / select / rename by collection.

---

## Development

The add-on is a single module, `EasyTasks/__init__.py`.

Tests run headlessly in real Blender — no mocking:

```powershell
.\tests\run_tests.ps1
```

| Suite | Covers |
| --- | --- |
| `smoke_test.py` | Registration, operator behaviour, regression cases |
| `icon_test.py` | Every icon name, settings property and operator id the UI references |
| `install_test.py` | Installs the built zip and checks preferences resolve |
| `redundancy_test.py` | Fails on a button drawn twice in the sidebar, or an operator/menu no UI can reach |
| `bench.py` | Old vs new timings for the optimised paths (`-IncludeBench`) |

`run_tests.ps1` sweeps every Blender it finds under
`C:\Program Files\Blender Foundation`; pass `-Blender <path>` to target one.

---

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
