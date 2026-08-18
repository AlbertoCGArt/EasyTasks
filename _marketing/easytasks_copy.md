# Easy Tasks — product copy

Voice notes: written to match the README — direct, concrete, no hype, specific
examples over adjectives. Free/GPL is stated as a fact, not the headline.

**Spelling.** The README is British (organisation, colour, analyse) and this copy keeps
that, with two deliberate exceptions:

- **"Collection Color Sync"** stays American — that's the tool's actual name in the UI
  and the README heading. "Colour" in the prose around it is fine.
- **"organization"** goes in the Gumroad tags field alongside "organisation". It's the
  higher-volume search spelling, and Gumroad's search is literal. Don't mix the two in
  the prose.

**Two numbers to know.** The add-on registers **35 operators**, across 3 sidebar panels
and 13 menus — that's the honest tool count, so this copy says 35 rather than a rounded
"40+". And the README's "distinctive part" is **eight** tools, not nine; the batch work
is deliberately framed as *"things Blender can do, but not in one step."* Keeping those
two halves separate is what makes the copy credible.

---

## 1. Titles

### Gumroad product title — ranked

| # | Title | Chars | Why |
| --- | --- | --- | --- |
| **1** | **Easy Tasks — Blender Collection, Cleanup & UV Checking Add-on** | 61 | Recommended. Fits Gumroad's card without truncating, and "collection" is the sharper keyword than "scene organisation" — it's what an outliner-frustrated user actually types. |
| 2 | Easy Tasks — Blender Scene Organisation, Cleanup & UV Analysis Add-on | 69 | Best keyword coverage that's still close to safe. Will clip by a word or two on narrow cards. |
| 3 | Easy Tasks — Blender Environment Art Add-on: Collections, Cleanup, UV Checks | 76 | Niche-first. Use if you'd rather reach environment and prop artists specifically than all Blender users. |
| 4 | Easy Tasks — Blender Add-on for Scene Organisation, Mesh Cleanup & Modelling Analysis | 85 | Fullest coverage, definitely truncates in the grid. Only worth it if you care more about the product page than the card. |

Gumroad clips the card label somewhere around 60–65 characters, and it's everything
after the em dash that gets cut — so the keywords are exactly what you lose. That's the
whole reason #1 is ranked first over #2.

### Gumroad subtitle / summary line

> Scene organisation, mesh cleanup and modelling analysis — one keystroke away.

Alternates:

- Keep a Blender scene tidy while you build it, not after.
- 35 tools for collections, cleanup and UV checks, on three keys.
- Built for environment and prop work. Free, open source, nothing held back.

### Website H1

> Keep your Blender scene tidy while you build it

Sub: **Easy Tasks** — scene organisation, mesh cleanup and modelling analysis, one
keystroke away. Blender 3.0 – 5.x.

Alternate H1s:

- Collections that build themselves
- Your outliner, finally worth using
- Scene organisation, mesh cleanup and modelling analysis for Blender

### GitHub repo description (112 chars, limit is 350 but ~120 shows without clipping)

> Blender add-on for scene organisation, mesh cleanup and modelling analysis. Built for environment and prop work.

---

## 2. Gumroad product description

Paste as-is; the `##` headers become headings in Gumroad's editor and the bold
lead-ins survive the paste.

---

Blender's outliner will hold any structure you want. It just won't build it for you,
and it won't keep it once you're three hours into blocking out a level.

Easy Tasks does both. It's a Blender add-on for environment and prop work: keep a
scene organised while you build it, check your forms and UVs without leaving the
viewport, and batch assets out when you're done.

## The distinctive part

Two halves, and it's worth being straight about which is which. The eight tools below
have no built-in equivalent — they're the reason to install it. Further down are the
batch operations and shortcuts, which are Blender work you already do, in one step
instead of six. That half is what makes it pleasant day to day; this half is what
makes it worth the install.

**Assign to Category.** Select the floor meshes, pick **Floor**, click. They go
straight into the right collection.

Point it at a **project root** and it works with the hierarchy you already have,
rather than inventing one beside it — clicking Floor drops the selection into
`PRODUCTION ▸ MODULES ▸ FLOOR`. Quick Assign rebuilds its buttons from the collections
actually found under that root, and a picker chooses which get one. With no root set
it falls back to 11 presets — Floor, Walls, Ceiling, Props, Decals, Trim, Modular, FX,
Lights, Cameras, Blocking — plus free-text categories, naming new collections from a
`{category}` / `{project}` pattern you control.

Move or link. Colour tags applied only to collections that don't already have one.
The destination shown in the panel before you click, marked `(existing)` when it
resolves to something you already have.

**Arrange Scene, and auto-routing.** Arrange Scene sorts every object into matching
collections by type and name, falling back to grouping by name stem. Auto-route does
the same for new objects the moment you create them — a light or a camera lands in
LIGHTS or CAMERAS without you doing anything. Toggle it off in preferences.

**Right-click ▸ Add to Collection.** In the 3D viewport and the outliner. It
*suggests* a destination for the selection using the same matcher Arrange Scene uses,
lists existing collections with object counts, creates new ones with a parent and
colour tag, and offers a search field past twenty collections instead of truncating
the menu.

**Face Stretch Analyzer.** Colour-codes UV distortion as vertex colours across the
whole selection, in the 3D viewport — blue compressed, green even, red stretched. Run
it again to restore your materials exactly.

**Stacked UV Detector.** Finds objects sharing an identical UV layout and selects
them, which is how stacked or accidentally-duplicated UVs surface before they reach a
bake.

**Silhouette Check and Cavity Preview.** One click each, per-viewport, restoring
precisely the shading you had. They're mutually exclusive, so neither can strand the
other's settings in your viewport.

**Visibility Bookmarks.** Snapshot which collections are visible under a name, then
restore that state later — useful when a scene has several working configurations.

**Collection Color Sync.** Pushes each collection's colour tag onto its objects'
viewport colour, walking the tree so nested collections inherit and a child's own tag
wins.

## Batch work

Things Blender can do, but not in one step.

- **Batch Export** — each selected object to its own FBX / OBJ / GLB, with optional
  prefix/suffix renaming and transform apply.
- **Project Structure** — build a PRODUCTION ▸ STUDIO / MODULES / BLOCKING hierarchy,
  picking which collections you want.
- **Generate LODs** — Decimate-reduced copies into an LODs collection.
- **Consolidate Materials** — merge `Mat.001` / `Mat.002` back into `Mat`.
- **Origin to Base** — origin to the bottom centre of the bounding box.
- **Smart Duplicate** — copies stay in their original collection.
- **Clean Up Mesh** — loose vertices and edges, optionally weld doubles.
- **WNormals + Bevel** — bevel, weighted normals and shade smooth in one.
- **Scene Snapshot** — timestamped copy of the .blend into `snapshots/`.
- **Scene and Collection Statistics.**

## Three keys

**Gumroad's editor does not support markdown tables** — it strips the pipes and runs
every cell together into one unreadable line. Use these lists instead. (Everything else
in this description pastes fine: headings, bold, bullets, blockquote.)

- **Ctrl Shift X** — Easy Tasks pie: overlays, collections, transforms, setup
- **Q** — FavTools menu: the full tool list
- **Shift Alt X** — Interaction mode pie: object/edit/sculpt, vert/edge/face

All three are rebindable in **Preferences ▸ Add-ons ▸ Easy Tasks**. They're bound in
the 3D View keymap, so they don't shadow keys in other editors.

Beyond those, there's one-key access to things Blender already does — modifiers, UV
seams and sharp edges, asset marking, link/transfer data, origin modes, interaction
modes, apply transform, shading, select by type, and isolate / swap / select / rename
by collection.

## Built for large scenes

Version 2.3 rewrote the slow paths. Measured on a 160k-polygon mesh and a 1,200-object
scene:

- Face stretch, write vertex colours — **1037 ms → 0.9 ms**
- Stacked UV, build signature — **980 ms → 25 ms**
- Face stretch, compute colours — **808 ms → 127 ms**
- Arrange Scene, match all objects — **18 ms → 1.8 ms**

The auto-route handler used to rebuild a full set of scene object names on every
depsgraph update. It now gates on an integer comparison, so its idle cost doesn't
scale with scene size.

## Install

1. Download `easy_tasks_2.4.0.zip` below.
2. In Blender: **Edit ▸ Preferences ▸ Add-ons ▸ Install…**, pick the zip.
3. Tick **Easy Tasks** to enable it.

Tools appear in the **EasyTasks** tab of the 3D viewport sidebar (`N`), and on the
shortcuts above.

> Install the zip, not the source folder. Blender takes the module name from the
> folder inside the archive, and the add-on's preferences are keyed to it.

**Blender 3.0 – 5.x.** A single Python module — `EasyTasks/__init__.py` — with no
external packages to install. Tested headlessly in real Blender, no mocking, across
every Blender version installed, with suites covering registration and operator
behaviour, every icon name and property the UI references, a full install of the built
zip, and a static audit that fails if a button is drawn twice or an operator is
reachable from no UI.

## Licence

GPL-3.0-or-later. Use it, modify it, ship commercial work with it — no licence to buy
and nothing held back. Source is on GitHub.

If it saves you time and you'd like to support the work, you can name your price
above, or find the rest of what I make at [3dartstuff.com](https://3dartstuff.com).

— Alberto Cordero

---

## 3. Gumroad tags

Use every slot. Both spellings, since Gumroad's search is literal:

```
blender, blender addon, blender add-on, 3d, scene organization, scene organisation,
collections, outliner, uv, uv checker, environment art, prop art, game art,
batch export, fbx, cleanup, workflow, free, open source, modelling
```

## 4. Gumroad "call to action" button

`I want this!` (default) — or set it to **Download**, since it's free. Default is fine.

---

## 5. Website product page copy

Structured to sit around the three images in `_marketing/`.

### Hero — above `web_hero_2400x1000.png`

**Easy Tasks**

# Keep your Blender scene tidy while you build it

Scene organisation, mesh cleanup and modelling analysis — one keystroke away. Built
for environment and prop work, for Blender 3.0 – 5.x.

`[ Download — free ]`  `[ View on GitHub ]`

Free and open source, GPL-3.0-or-later. No licence to buy, nothing held back.

### Intro — one paragraph under the hero

Blender's outliner will hold any structure you want. It just won't build it for you,
and it won't keep it once you're three hours into blocking out a level. Easy Tasks
does both — and while it's there, it checks your UVs, reads your silhouettes, and
batches your assets out.

### Features section — above `web_features_2400x1380.png`

## Thirty-five tools. Eight that don't exist anywhere else.

It's worth being straight about which is which. The first six below have no built-in
equivalent — they're the reason to install it. The last three are Blender work you
already do, in one step instead of six.

*(feature grid image)*

Short blurbs, if you'd rather set the grid in HTML than use the image. The first six
are the distinctive half; the last three are batch work — the image colour-codes them
the same way, orange/blue for the first six and green for the last three.

**Assign to Category** — Select the floor meshes, click Floor. Point it at a project
root and it uses the hierarchy you already have, instead of inventing one beside it.

**Arrange Scene & Auto-route** — Sorts every object into matching collections by type
and name. Auto-route does it the moment you create something new.

**Right-click ▸ Add to Collection** — In the viewport and the outliner. Suggests a
destination, lists existing collections with object counts, searches past twenty.

**Face Stretch Analyzer** — Colour-codes UV distortion as vertex colours right in the
viewport: blue compressed, green even, red stretched. Run it again to restore.

**Stacked UV Detector** — Finds and selects objects sharing an identical UV layout, so
duplicated or stacked UVs surface before they reach a bake.

**Silhouette & Cavity** — One click each, per-viewport, mutually exclusive — and they
restore precisely the shading you had before.

**Batch Export** — Every selected object to its own FBX, OBJ or GLB, with optional
prefix/suffix renaming and transform apply.

**Project Structure & LODs** — Build a PRODUCTION ▸ STUDIO / MODULES / BLOCKING tree,
and generate Decimate-reduced LOD copies in one step.

**Cleanup & Color Sync** — Consolidate materials, clean up loose geometry, origin to
base, and push collection colour tags down onto object viewport colours.

> Not in the grid, but in the add-on: **Visibility Bookmarks** — snapshot which
> collections are visible under a name, then restore that state later. It's the eighth
> of the distinctive tools; it just didn't fit the nine-card layout.

### Shortcuts + install section — above `web_specs_2400x1240.png`

## Three keys, everything reachable

`Ctrl Shift X` opens the pie. `Q` opens the full tool list. `Shift Alt X` switches
interaction mode. All three rebindable, all three scoped to the 3D View keymap so
nothing gets shadowed in your other editors.

*(specs image)*

### Closing / CTA

## Free, and staying that way

GPL-3.0-or-later. Use it, modify it, ship commercial work with it — there's no licence
to buy and nothing held back for a paid tier. The source is on GitHub.

If it saves you time and you'd like to support the work, everything else I make is at
3dartstuff.com.

`[ Download easy_tasks_2.4.0.zip ]`  `[ Source on GitHub ]`  `[ Changelog ]`

---

## 6. Meta / SEO

**`<title>`** (60 chars)

> Easy Tasks — Blender Scene Organisation & UV Analysis Add-on

**Meta description** (150 chars)

> Free Blender add-on for scene organisation, cleanup and UV analysis. Collections that build themselves, stretch checks, batch export. Blender 3.0–5.x.

**Open Graph image** — `web_hero_2400x1000.png`

---

## 7. Short forms

**One-liner (88 chars)**

> Free Blender add-on: collections that build themselves, UV stretch checks, batch export.

**Two-sentence pitch**

> Easy Tasks keeps a Blender scene organised while you build it — assign selections to
> collections in one click, auto-route new objects, and check UV distortion and
> silhouettes without leaving the viewport. Free and open source, Blender 3.0 – 5.x.

**Forum / Discord announcement**

> **Easy Tasks 2.4.0** — a free, GPL-3.0-or-later Blender add-on for environment and
> prop work.
>
> Assign selections to collections in a click (and point it at your existing project
> root so it uses the hierarchy you already built, rather than making a new one beside
> it), auto-route new objects into LIGHTS/CAMERAS/etc as you create them, colour-code
> UV distortion as vertex colours in the viewport, find stacked UVs before they hit a
> bake, and batch out each selected object to its own FBX/OBJ/GLB.
>
> Blender 3.0–5.x, a single Python module, nothing to install alongside it. 2.3 rewrote
> the slow paths — face stretch vertex-colour writes went 1037 ms → 0.9 ms, measured on
> a 160k-polygon mesh and a 1,200-object scene.
>
> Source + releases: github.com/AlbertoCGArt/EasyTasks

---

## 8. Gumroad → Receipt tab

Two fields, both with hard limits. Counts below are exact, including spaces and the
`▸` / `…` / `—` characters (each counts as one).

### Button text (26 char limit)

| Option | Chars | Note |
| --- | --- | --- |
| **Download Easy Tasks** | 19 | **Recommended.** Names the product on the receipt, where "Download" alone is ambiguous if someone has several receipts open. |
| Get Easy Tasks 2.4.0 | 20 | Only if you're happy editing it every release. It will go stale. |
| Download the add-on | 19 | Fine, slightly flatter. |
| Download the .zip | 17 | Sets the expectation that they get an archive, not an installer. |

Leave it blank and Gumroad uses its own default — that works too, but this button is
also what shows on the product page, so it's worth claiming.

### Custom message (500 char limit)

**Recommended — 483 chars.** Leads with the install path and spends its length on the
zip-vs-folder trap, which is the single thing most likely to generate a "doesn't work"
email:

```
Thanks for downloading Easy Tasks.

Install: in Blender, Edit ▸ Preferences ▸ Add-ons ▸ Install…, pick the zip, then tick Easy Tasks. Tools appear in the EasyTasks tab of the 3D viewport sidebar (N), and on Ctrl Shift X, Q and Shift Alt X.

One thing: install the zip itself, not an unzipped folder. Blender takes the module name from the folder inside the archive, and the preferences are keyed to it.

Source and issues: github.com/AlbertoCGArt/EasyTasks

— Alberto, 3dartstuff.com
```

**Alternate — 468 chars.** Same install info, trades some of the zip warning for the
licence and an explicit "here's how to reach me":

```
Thanks for downloading Easy Tasks.

Install the zip (not an unzipped folder) via Edit ▸ Preferences ▸ Add-ons ▸ Install…, then tick Easy Tasks. Tools land in the EasyTasks tab of the sidebar (N), plus Ctrl Shift X for the pie and Q for the full list.

It's GPL-3.0-or-later — modify it, ship commercial work with it, nothing held back. If something breaks, an issue on GitHub is the fastest way to reach me: github.com/AlbertoCGArt/EasyTasks

— Alberto, 3dartstuff.com
```

**Short — 315 chars.** If you'd rather the receipt stayed out of the way:

```
Thanks for downloading Easy Tasks.

Install the zip via Edit ▸ Preferences ▸ Add-ons ▸ Install… — the zip itself, not an unzipped folder. Then tick Easy Tasks. Everything is in the EasyTasks sidebar tab (N), on Ctrl Shift X, and on Q.

Source and issues: github.com/AlbertoCGArt/EasyTasks

— Alberto, 3dartstuff.com
```

**If the ▸ character doesn't render** in Gumroad's receipt email on some clients,
replace it with `>` — `Edit > Preferences > Add-ons > Install…`. Costs you nothing and
is safe everywhere. Worth sending yourself a test receipt to check before you leave it.

---

## 9. Gumroad → Content tab (the file entry)

The card already shows `ZIP · 30.0 KB`, so neither field needs to repeat the format.
This is the last thing a buyer reads before they install, which makes it the best place
in the whole listing for the don't-unzip warning — it sits directly above the button
they're about to click.

### Name

| Option | Chars | Note |
| --- | --- | --- |
| **Easy Tasks 2.4.0 — Blender add-on** | 33 | **Recommended.** The bare filename reads as a build artifact; this reads as a product. "Blender add-on" also orients anyone who downloaded it weeks ago and forgot what it was. |
| Easy Tasks 2.4.0 | 16 | Clean. Fine if you'd rather the description carry everything. |
| Easy Tasks 2.4.0 (install this zip as-is) | 41 | Puts the warning where nobody can miss it. Slightly cluttered, but it works. |

Keep the version number in the name. You re-upload the file each release anyway, so
it's no extra maintenance, and it lets someone check at a glance which build they have
without opening the zip.

### Description

**Recommended — 251 chars.** Says what to do, then why, so it doesn't read as a nag:

```
Blender 3.0 – 5.x. Install this zip as-is: Edit ▸ Preferences ▸ Add-ons ▸ Install…, pick this file, tick Easy Tasks. Don't unzip it first — Blender takes the module name from the folder inside the archive, and the add-on's preferences are keyed to it.
```

**Shorter — 155 chars**, if it's already in the receipt and you don't want to say it
three times:

```
Blender 3.0 – 5.x. Install the zip as-is via Edit ▸ Preferences ▸ Add-ons ▸ Install… — don't unzip it first. Tools appear in the EasyTasks sidebar tab (N).
```

**Minimal — 58 chars:**

```
The add-on. Install as-is, don't unzip. Blender 3.0 – 5.x.
```

Same `▸` caveat as the receipt — if it doesn't render cleanly, use `>`.

---

## Where each piece goes — quick map

| Gumroad field | Section here |
| --- | --- |
| Product → Name | §1, title option 1 |
| Product → Description | §2 |
| Product → Tags | §3 |
| Product → Cover / Thumbnail | `gumroad_cover_2560x1440.png` / `gumroad_thumbnail_1200x1200.png` |
| Content → file Name + Description | §9 |
| Receipt → Button text + Custom message | §8 |
| Website hero / features / specs | §5, with the three `web_*.png` images |
| `<title>` + meta description | §6 |

---

## 10. Gumroad editor gotchas (found on the live preview)

**Markdown tables don't survive the paste.** Gumroad's rich-text editor has no table
support, so it drops the pipes and concatenates every cell into one line. On the first
paste this produced:

> `KeysOpensCtrl Shift XEasy Tasks pie — overlays, collections, transforms, setupQFavTools menu — the full tool listShift Alt XInteraction mode pie — object/edit/sculpt, vert/edge/face`

and

> `PathBeforeAfterFace stretch — write vertex colours1037 ms0.9 msStacked UV — build signature980 ms25 ms…`

Both sections in §2 are now plain bulleted lists, which paste correctly. **These were
the only two tables in the description** — headings, bold, italics, bullets, numbered
lists, links and the blockquote all came through fine.

If you want the shortcut keys to look like keys rather than bold text, Gumroad won't
render `<kbd>`. Bold is the best available option.

**Before publishing, check:**

- The listing still shows **"This product is not currently for sale."** — publish it.
- Price is set to **$0+** (name a fair price), which is right for this.
- The summary box on the right reads *"Scene organisation, mesh cleanup and modelling
  analysis — one keystroke away."* — correct.
- Send yourself a test receipt and confirm the `▸` characters render in your email
  client. They display correctly on the product page; the receipt email is a different
  renderer. Swap to `>` if anything looks broken.
