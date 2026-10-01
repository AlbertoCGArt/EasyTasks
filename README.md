# Easy Tasks

A Blender add-on that puts scene organisation, mesh cleanup and modelling
analysis tools one keystroke away.

Built for environment and prop work: keep a scene tidy while you build it,
check your forms and UVs without leaving the viewport, and batch out assets
when you are done.

**Blender 3.0 – 5.x** · GPL-3.0-or-later · by [Alberto Cordero](https://www.artstation.com/albertocordero)

**Free and open source.** Use it, modify it, ship commercial work with it — no
licence to buy and nothing held back. If it saves you time and you'd like to
support the work, [3dartstuff.com](https://3dartstuff.com).

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

### Megascans Material
Click **Megascans Material**, point the file browser at a Quixel/Bridge texture
folder — or select the files yourself — and every texture set in it becomes a
wired Principled BSDF.

It reads the naming Bridge writes, `Rock_Cliff_ug0jaipaw_4K_Normal_LOD0` and
all, so the asset id, the resolution and the LOD tag don't end up in the
material name, and it handles ordinary `BaseColor` / `Normal` / `Rough` naming
from anywhere else just as well. Several sets in one folder become several
materials, matched to the selected objects by name.

What it connects, and what it won't:

- Base colour, roughness, metallic, specular, alpha — with data maps set to
  Non-Color and only the albedo left as sRGB.
- **Height** goes to a Bump node by default, which both renderers honour.
  Choosing true **Displacement** instead wires the Material Output's
  Displacement input, and EEVEE ignores that entirely — Cycles only. The dialog
  says so when you pick it.
- Normal into a Normal Map node, with height chained *after* it through a Bump
  node, so detail rides on the normal map instead of replacing it. Height can
  go to the Material Output as true displacement instead, or stay unconnected.
- **Gloss** inverted into roughness, **DirectX** normals green-flipped, and
  packed **ORM**/**RMA** maps split into their three channels.
- Texture Coordinate ▸ Mapping in front of the whole set, so it tiles from one
  node.
- Translucency, cavity, curvature and friends are loaded and labelled but left
  unconnected — what they should drive is your call, not the importer's.
- AO is loaded but *not* multiplied into base colour unless you ask, because
  under a renderer that already computes occlusion that double-darkens.

When a folder ships 2K and 4K side by side it takes one deliberately — highest
by default, lowest if you'd rather keep the viewport light — instead of
whichever the OS listed last.

### Blend Materials, and painting the blend
Two PBR materials, one mask, every channel mixed together — clean concrete
under dirt, rock under moss, anything under snow.

The mask starts from physics rather than from you: dirt collects in the low
spots, so the base material's **own height map** drives it, inverted and broken
up by noise. Point it at two materials and you get a believable result before
touching a slider. Four controls on the mask node tune it — Amount, Softness,
Noise Scale, Paint Influence.

**Presets** give you a starting point rather than a blank slider: Dirt in
Crevices, Puddles, Grime up a Wall, Snow on Top, Settled Dust, Moss, Streaks
from Above. Each sets the amount, softness, noise and direction together. In
the Blend Mask panel the same list loads onto the material already on the
object — no rebuild, and your painting survives.

**Direction** makes it directional, in one of two ways. Pick an **Axis** — the
end the overlay builds toward — then a Mode:

- **Gradient** ramps the overlay along that axis, a clean 0 to 100 across the
  mesh. Grime climbing a wall, moss up a trunk, dust settling down a column.
  It reads normalised bounding-box coordinates, so it works on any shape at any
  scale, flat walls included, and follows the object when you move or duplicate
  it.
- **Face Angle** restricts the overlay to faces pointing that way — snow on the
  top of a rock, damp under a ledge. It reads the true surface normal in world
  space, so rotating the asset doesn't carry the snow around with it.

Falloff sets how much of the mesh the ramp spans (Gradient) or how tightly it
hugs the exact direction (Face Angle); Strength dials the whole thing back.

Face Angle has one honest limitation: on a flat surface every face points the
same way, so it can only shift the whole thing uniformly — it can't produce a
transition. That's what Gradient is for, and why it's the default. If you pick
Face Angle and nothing on the object points that way, you get told rather than
left with an invisible overlay.

Either mode scales the finished mask, so the overlay still prefers crevices
within the area it's allowed. Left at **Any**, nothing changes.

Once a blend is on the active object, an **ET: Blend Mask** panel appears in the
sidebar with every control live on the material itself — Amount, Softness,
Noise, axis buttons, Gradient/Face Angle, Strength, Falloff, Paint Influence.
Adjust there rather than re-running the dialog: the dialog builds a *new*
material each time, which would leave your painted mask behind. The panel edits
the one you already have, and disappears when the active material has no blend
in it.

**Paint Mask** is the manual half. Click it and you're in paint mode on a mask
layer: **white adds** the overlay, **black scrubs it back** to the base, and
`X` swaps the two. Mid grey is neutral, which is what lets one layer both add
and remove instead of needing separate add/erase masks.

- Paints onto an **image** by default — full resolution on any mesh, needs UVs —
  or a **vertex colour** layer when the object has none.
- On a tiling surface it adds its own `ET_BlendPaintUV` layer, a copy of your
  layout scaled into 0-1. Without it a single stroke would appear in every tile
  at once, because that's what tiled UVs are for. Your texture UVs are left
  exactly as they are.
- **One material means one mask.** A blend spread over twenty floor planes is a
  single material, so all of them share one mask image and a stroke on one
  lands on all of them. Every object on the material gets the paint UV layer,
  so none of them silently render *no* paint — but to paint one object on its
  own, make its material single-user first (the number beside the material
  name). Paint Mask says so when it finds the material on more than one object.
- Switching between the two only moves the link. The layer you already painted
  stays in the node tree, so switching back brings that work with it.
- The mask image is written to `textures/` next to your blend. Blender silently
  refuses to pack a generated image and rebuilds it as flat grey on load, so
  without that your paint would quietly vanish.
- Leaving paint mode **saves the mask**. Strokes live in memory until something
  writes them, so an unsaved mask renders blank however good it looks in the
  viewport. The panel flags it and offers **Save Painted Mask** whenever there
  is unsaved paint.
- Painting targets that one image and nothing else, so a stray stroke can't
  land on the Megascans albedo and overwrite your library file.

Channels only one material has — a height map the dirt doesn't ship — pass
straight through rather than being mixed against a flat colour that would wash
them out.

The blend is a **new material** — the two sources are read, never modified, and
stay in your file. It replaces the material the object actually renders: the
slots your faces are on, not just the first slot, so it shows up regardless of
how the object was put together. Slots nothing renders keep what they had.

### Face Stretch Analyzer
Colour-codes UV distortion as vertex colours across the whole selection, in the
3D viewport — blue compressed, green even, red stretched. Run it again to
restore your materials exactly.

### UV Health Check
Measures the selection for what actually stops a texture or a paint mask
behaving: **no UV map**, **zero-area UV faces**, layouts **tiling past 0-1**,
and **texel density** in pixels per metre.

Density is the one worth having. Two walls at different densities show visibly
different texture detail side by side, and nothing in Blender tells you before
you render. Pick the texture size you intend to use, and it reports the range,
the median, and any object more than 2× off it. Object scale counts, so a
scaled-up asset reports the density it really renders at.

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
