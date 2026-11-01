# <BUILDING> reference images: keynotes for the modelling agent

## Ground rules
- Dimensions come from `../spec/spec.json`, never from these images.
- Use the images for:
  - what things are made of
  - how parts meet
  - colour, finish and reflectance
  - things the drawings don't show
- If an image seems to contradict the spec, the spec wins. Log the conflict.
- Note lens distortion (wide-angle, fisheye) and renders vs photos per image. Don't measure proportions from them.
- Watermarked stock images: don't sample textures from the watermarked areas.
- Construction-stage photos may show temporary works (scaffold, crash decks, hoists). Say so per image.

## Folders follow the construction sequence
`01_structure` → `02_facade` → `03_site` → `04_interior`

---

## 01_structure
**STR-01_<what>.jpg** (<W×H>; <photo/render>; <lens / watermark notes>)
- **Shows:** <what is visible, named the way the spec names it>.
- **Use it for:** <materials / how parts meet / sequence>.
- **Ignore:** <temporary works, or things not in the target era>.

## 02_facade
...

## 03_site
...

## 04_interior
...

### What we know vs what we don't (fill in for build-ups)
| Layer / element | Status | Source |
|---|---|---|
| <e.g. raised floor zone> | measured: FFL − SSL = <x> m | spec `levels[*]` |
| <e.g. slab thickness> | unknown: assume and label | — |
