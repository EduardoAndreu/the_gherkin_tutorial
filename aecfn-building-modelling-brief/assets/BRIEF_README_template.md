# <BUILDING> brief (spec v<VERSION>): read this first

The reference package for reconstructing <BUILDING> in Blender. Read the files in this order:

| # | Path | What it is | Authority |
|---|---|---|---|
| 1 | `spec/spec.json` | Measured design data, with provenance for every value. | **Single source of truth for every dimension.** |
| 2 | `markups/` | <N> colour-coded drawings, each with the spec drawn on it, grouped by build step (see `markups/README.md`). | Explains what the numbers mean and where they sit. The numbers themselves come from the spec. |
| 3 | `underlays/` | <N> key drawings resampled into the model frame (true scale). | Visual check only. |
| 4 | `images/` | <N> photos, plus `IMAGE_NOTES.md`. | Materials, how parts meet, colour. **Never use them for dimensions.** |

## Precedence when sources disagree
spec > markups > underlays > photos. Log every conflict in your decisions log. Never edit the spec.

## Coordinate system
<copy from spec.coordinate_system in plain words: units, origin, axes, Z datum (+ AOD), north>.
Model the <era>. <Any era caveats.>

## Using the underlays in Blender
Each `underlays/<name>.png` has a `<name>.json` alongside it.
- `metres_per_px`: one pixel in metres.
- `size_px [W, H]`: the image size. The plane is W·mpp by H·mpp metres.
- `world_origin_px [u, v]`: the pixel position of the world origin, with v measured downwards from the top.
- **Plans (`PLN_*`):** image +x is world +X and image up is world +Y. Shift the plane so pixel (u, v) sits on the origin, then lift it to that level's FFL.
- **Sections and elevations (`SEC_*`, `ELV_*`):** image +x is s along `section_line` and image up is z. Stand the plane vertically along that line.
- **Accuracy:** about <calibration rms> m.

## Key spec fields
<5–15 bullets naming the keys the model needs most, grouped as levels / structure / envelope / interior / site / open_issues>

## Unknowns: never invent these silently
<list what is NOT in the spec, e.g. slab thicknesses, member sizes>. If the model needs them, add them as labelled ASSUMPTIONS in your decisions log. Never move an FFL or SSL.
