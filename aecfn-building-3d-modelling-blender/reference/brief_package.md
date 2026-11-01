# Brief package — input contract

The skill never invents a building. It reproduces a preprocessed, measured package; a missing part is asked for or
logged as an assumption, never filled from memory or a generated image.

```
<brief>/
  PROMPT.md                  the task: building, era, zones, gates, deliverables
  00_README.md               reading order, precedence, coordinate system, era, key spec fields, unknowns
  spec/<building>_spec.json  SINGLE SOURCE OF TRUTH for every dimension (read-only)
  markups/<SET>/             one set per build domain (STR, FAC, INT, BAS …)
     <name>.png / _preview.png   calibrated drawing with the spec drawn on top (full res / ~1500 px)
     <name>.json             callouts {id, type, text, kind, cls, spec_key, spec_key_query, value, issue_id, xy_px}
                             kind = measured (spec key cited) | assumed | observed (photo) | issue (open issue id)
     README.md               sheets, legend, line types
  underlays/<name>.png+json  key drawings resampled into the model frame
                             {file, kind: plan|section, metres_per_px, size_px, world_origin_px, era}
                             plan: image +x = +X, up = +Y · section: image +x = r, up = Z
  images/<ID>_*.jpg          photos + IMAGE_NOTES.md: materials, assembly, colour — never dimensions
```

**Precedence:** spec > markups > underlays > photos. Log every conflict in `decisions_log.md`.

**The spec must contain:** a `coordinate_system` (units, Z = 0, XY origin, +Y, angle convention); `levels[]` (finished
and structural levels); each system's governing geometry as numbers or rules (r(z) profiles, polygons, node rules,
counts, modules, mm profiles); `open_issues[]`; provenance on values. Unknowns are written `"unknown"`, never guessed.

**Preparing a package (outside this skill):** pick the era and register every sheet to one frame → measure governing
values into the spec with provenance → one markup set per domain with callouts citing spec keys → resample key
plans/sections to a fixed m/px with the origin pixel → keynote each photo → README + PROMPT.
