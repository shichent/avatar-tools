# Avatar Asset Prompt Guide

Lightweight, API-only pipeline for a 2D layered paper-doll avatar (one boy + one
girl canonical body, plus swappable hair / top / bottom / shoes). No local models.

Everything lives in one app — `index.html` — served by `server.py`. The five tabs
(**Generate · Chroma · Diff · Brush · Align**) share state and hand artifacts to each
other, so you rarely touch the filesystem between steps.

## Run

```bash
cd avatar-tools
python server.py            # stdlib only, reads OPENAI_API_KEY from ../.env
# open http://localhost:8000/
```

The server proxies the image API (your key never reaches the browser) and writes
saves to `assets/<category>/`. **Saving requires the server** — opening the file
directly won't work.

## Header controls (apply to all tabs)

- **Girl / Boy toggle** — the active body. Diff and Align read it automatically.
- **Base chips** — show, per body, whether the `green` source and `cutout`
  (transparent) versions are loaded. Bases auto-load on startup from
  `assets/base_<body>.png` (cutout) and `assets/_generated/base_<body>_green.png`.

## Hard conventions (do not vary)

- **Canvas:** every asset is **1024 × 1536 (2:3 portrait)**, RGBA.
- **Background:** solid flat **green `#00FF00`**, evenly lit, **no ground shadow**.
- **Keep pure green OFF the body** — no green eyes, clothing, or underwear on the
  base, or the chroma key will eat them.
- **Pose is fixed** across every generation (front-facing A-pose, below).
- For outfit *edits*: **change nothing except adding the item** — the diff only
  works if the rest of the image stays identical to the base.

## Two base versions (important)

Each body is **two** images:

| Version | What | Used by | File |
|---|---|---|---|
| **green** | the on-green base (opaque) | Generate input · Diff *base* slot | `assets/_generated/base_<body>_green.png` |
| **cutout** | the keyed transparent base | Align ghost / body layer | `assets/base_<body>.png` |

The green source is what makes the diff cancel; the cutout is what you actually
layer the avatar on.

---

## Reusable prompt tokens

The Generate presets are built from these. Keep them byte-identical across a set so
the look stays coherent; **vary only the `{item}`**.

```
[STYLE] = flat 2D cartoon illustration for a children's storybook, clean bold
uniform outlines, simple cel shading with flat color fills, minimal gradients,
soft rounded friendly proportions, bright cheerful colors.

[POSE] = full body head-to-toe, centered, facing the viewer straight on, relaxed
A-pose with arms held slightly away from the torso and palms facing inward, legs
straight, feet flat and about shoulder-width apart, weight even, symmetrical,
neutral closed-mouth smile.

[BG] = solid flat green #00FF00 background, evenly lit, NO ground shadow, no floor,
no props, no text. Tall 2:3 portrait image, small even margin above the head and
below the feet.
```

## Generate presets (the dropdown)

Each preset fills the Instruction / Item / Pose / Style / Background fields; edit any
of them before clicking **Generate**. Mode is automatic: **attach an input image →
edits** endpoint; **no image → generations** endpoint.

- **Girl base** *(generations, no image)* — bald, barefoot, neutral grey
  undergarments. → archives + **Set as girl green**, then **Send to Chroma**.
- **Boy edit** *(edits — attach the girl green)* — redraw as a boy, identical pose/
  scale/framing, only minor proportion changes. → **Send to Chroma**.
- **Outfit (dress the base)** *(edits — attach the body's green)* — *"add only the
  item, change nothing else, keep the green background identical."* → **Send to Diff**.
- **Outfit only (no body)** *(generations, no image)* — the garment shaped as if
  worn on an invisible person (folds, worn 3D shape), full extent, on green, **no
  body/person/mannequin**. → **Send to Chroma** (then place manually in Align).
- **Custom** — leave the fields as-is.

**Per-category `{item}` fills** (for the Outfit presets):

- **Hair** — `a {short wavy red} hairstyle on the head`
- **Top** — `a {red and white striped t-shirt} on the torso`
- **Bottom** — `{blue denim shorts} on the hips and legs`
- **Shoes** — `a pair of {yellow sneakers}, one on each foot`

> Front-only system: each item is a single layer above the body. Anything truly
> behind the torso isn't captured by the diff — and in a fixed front view it's never
> seen, so that's fine.

> **Hair tip:** the face's outline is identical in base and dressed, so the diff cuts
> a thin slit into hair along that line. Raise **Bridge fill** (radius ~3–5 px, ≥ ~60%)
> to reconnect it — it keeps a gap pixel when most of its neighbourhood is already
> kept, closing notches without growing the silhouette (high % closes only thin gaps;
> low % dilates more). The companion **Bridge exclude** does the inverse — it drops a
> kept pixel when ≥ N% of its neighbourhood is dropped, trimming thin spikes and stray
> bits; its % is floored at 60 so normal ~50%-boundary edges survive (raise it to keep
> convex corners sharp too). When the sliders can't get it perfectly, **Send to Brush**
> and hand-paint the fix: *Include* reconnects the slit (it samples the real garment
> colour from the dressed source), *Exclude* trims leftovers. Or sidestep the face
> entirely with the **Outfit only (no body)** preset → Chroma.

---

## Workflows (with the auto-chain buttons)

**Build the two bases**

```
Generate [Girl base] -> Set as girl green -> Send to Chroma -> Set & save girl cutout
Generate [Boy edit] (attach girl green) -> Send to Chroma -> Set & save boy cutout
```

**Dress-on-body outfit (primary)**

```
Generate [Outfit] (attach body green) -> Send to Diff
   (Diff base auto-fills with the body's green; dressed = the result)
   tune threshold / despeckle / fill-holes / edge-soften
[optional] Send to Brush -> Include/Exclude touch-ups (Ctrl+Z / Ctrl+Y)
Send to Align -> nudge over the ghost -> Save to assets/<category>/
```

**Bodyless outfit (alternative)**

```
Generate [Outfit only (no body)] -> Send to Chroma -> key out green
   -> Send to Align (load layer) -> place over the ghost -> Save
```

**Diff ↔ Chroma shortcuts** (green is only intermediate; the goal is transparent)

- Diff **→ Chroma (clean edges)** — push the cut-out garment into Chroma to despill
  residual green on the edges. *(Caveat: this also keys genuinely green parts of a
  garment — skip it for green items.)*
- Chroma **→ Diff base** / **→ Diff dressed** — key each image first, then diff the
  two transparent versions. The diff is **alpha-aware**, so it still catches garment
  pixels when colours are close.

Reusing girl outfits on the boy: switch the header to **Boy** and nudge in Align;
save a boy-specific variant if needed.

---

## Tab cheat-sheet

| Tab | In | Out |
|---|---|---|
| **Generate** | prompt parts + (optional) input image/mask | image, auto-archived to `_generated/`; handoff buttons |
| **Chroma** | a body / garment on green | transparent PNG; set as cutout or save |
| **Diff** | base + dressed (green *or* keyed) | garment on transparency |
| **Brush** | cutout + (same-size) source | hand-fixed cutout (include/exclude) |
| **Align** | layers (+ auto ghost) | nudged layer saved to `assets/<category>/` |

**Detail inspection** (Chroma · Diff · Brush · Align): mouse-wheel zooms toward the cursor,
**Fit** resets, zoom % is shown, and it's pixel-crisp above 1× so you can check edges,
alpha fringing, and green spill. Pan by dragging (in **Align**, left-drag still moves
the selected layer — pan with **right/middle-drag**).

## Naming conventions

- transparent cutout body: `assets/base_<body>.png`
- on-green source body: `assets/_generated/base_<body>_green.png`
- raw generations: auto-archived to `assets/_generated/<timestamp>.png`
- garments: `assets/<category>/<cat>_NN.png` (`top_01.png`, `hair_01.png`, …)

## Model notes

- **Model field** defaults to `gpt-image-2`. If the API rejects it (model-not-found),
  switch to `gpt-image-1`. Errors surface inline in the Generate status line.
- **gpt-image-1 (cleanest diffs):** use the edits endpoint with a **mask** over the
  region to dress — only the masked area changes, so the diff is razor-clean. It can
  also output `background:"transparent"` to skip chroma-keying the base.
- **Nano Banana / others:** re-render the whole frame, so expect some diff noise →
  lean on the threshold + despeckle + fill-holes sliders.

## Gotchas

- Repeat **"no ground shadow"** in prompts — models love adding one; it ruins keying.
- **Same 1024×1536 every time** — mismatched sizes break the diff and the stacking.
- **"Change nothing else"** is load-bearing for the dress-on-body diff; a masked edit
  guarantees it.
- Each Generate is a **paid call** — every result is auto-archived to `_generated/`
  as a safety net.
