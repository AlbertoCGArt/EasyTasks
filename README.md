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

### Assign to Category
Selection-driven scene organisation. Select the floor meshes, pick **Floor**,
hit **Add** — they go straight into the right collection.

Point it at a **project root collection** and it works with the hierarchy you
already have: categories resolve to collections that exist inside that root, so
clicking Floor drops the selection into `PRODUCTION ▸ MODULES ▸ FLOOR` rather
than inventing a `Floor_MyProject` beside it. Quick Assign is rebuilt from the
collections actually found under the root, and a picker chooses which of them
get a button.

With no project root set it falls back to 11 presets — Floor, Walls, Ceiling,
Props, Decals, Trim, Modular, FX, Lights, Cameras, Blocking — plus free-text
custom categories, naming new collections from a `{category}` / `{project}`
pattern you control, with `{project}` read from the blend file name, the scene
name, or a value you type.

- **Move** or **link** — take objects out of their old collections, or leave
  them in both.
- Colour tags applied to collections that don't already have one, so **Color
  Sync** picks them up without overwriting your own choices.
- The destination is shown in the panel before you click, marked `(existing)`
  when it resolves to a collection you already have.

### Right-click ▸ Add to Collection
In the 3D viewport and the outliner. Suggests a destination for the selection
using the same matcher **Arrange Scene** uses, lists existing collections with
object counts, creates new ones with a parent and colour tag, and offers a
search field past twenty collections instead of truncating the menu.

### Scene organisation
- **Project Structure** — build a PRODUCTION ▸ STUDIO / MODULES / BLOCKING
  hierarchy, picking which collections you want.
- **Arrange Scene** — sort every object into matching collections by type and
  name, falling back to grouping by name stem.
- **Auto-route** — new objects drop into a matching collection as you create
  them. Toggle it off in preferences.
- **Isolate Collection**, **Swap Collections**, **Select by Collection**,
  **Rename by Collection**, **Visibility Bookmarks**, **Color Sync**.

### Modelling and cleanup
Origin tools including **Origin to Base**, **Drop It**, **Smart Duplicate**
(copies stay in their collection), **WNormals + Bevel**, **Correct Normals**,
**Clean Up Mesh**, **Generate LODs**, **Consolidate Materials**.

### Analysis
- **Silhouette Check** and **Cavity Preview** — per-viewport shading toggles
  that restore exactly what you had.
- **Face Stretch Analyzer** — colour-codes UV distortion across the selection.
- **Stacked UV Detector** — finds objects sharing an identical UV layout.
- **Scene** and **Collection Statistics**.

### Export
**Batch Export** — each selected object to its own FBX / OBJ / GLB, with
optional prefix/suffix renaming and transform apply.

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
