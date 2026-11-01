# Markups

Each markup is a true-scale underlay, faded, with spec v<VERSION> drawn on top. Numbered tags on the drawing match the CALLOUTS list in the side panel. Each comes with:
- `<name>.png` at full resolution
- `<name>_preview.png`, for a first look
- `<name>.json`, which lists every item with its spec key(s), resolved values, kind and world position

## How to read them
| Line | Meaning |
|---|---|
| Solid | MEASURED. The value comes from the spec, and the callout cites its spec key. |
| Dashed | ASSUMED. Not in the spec; the source or assumption id is cited. |
| Dotted | OBSERVED from a photo or the drawing graphics. Not measured. |
| Red cloud | An open issue. Decide how to handle it, and log the decision. |

- Each element class has one colour across all sets; the legend is on each sheet.
- If a markup and the spec disagree, the spec wins.

## By build step
### 1. Structure (`STR/`)
| File | Shows |
|---|---|
| <file> | <what> |

### 2. Envelope (`FAC/`)
| File | Shows |
|---|---|

### 3. Interior (`INT/`)
| File | Shows |
|---|---|

### Site (`SITE/`), built alongside steps 1–3
| File | Shows |
|---|---|
