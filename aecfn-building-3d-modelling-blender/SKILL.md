---
name: aecfn-building-3d-modelling-blender
description: Reconstructs a real building (structure, envelope, interior, base and site) in Blender 5.2 at architectural precision from a preprocessed, measured brief package (read-only spec JSON, per-domain markups with machine-readable callouts, calibrated underlay drawings, keynoted photos). Builds parametric CSG solids (Manifold3D), meshes and instances from the spec, exports IFC4 and an ANSI31 CAD blueprint, gates every pass on numbers (measured checks, drawing-overlay deviations, a decisions log), then runs a multi-subagent loop (zone specialists + orchestrator + fresh adversarial critic) to a Cycles showcase, section cutaway, overlay matrix and before/after matrix. Use when the user has a measured brief package for an existing building or names aecfn-building-3d-modelling-blender.
---

# Measured brief → precise Blender reconstruction

Reproduce a **real building from a measured brief package** and judge it by **numbers first**: every dimension comes
from the spec, every drawing is overlaid, every unknown is a logged decision. Typology-agnostic: everything
building-specific lives in the package, the thin `spec/site.json` and the zone modules. Input contract:
`reference/brief_package.md`. All commands: `python3 -m pipeline.run <cmd>` from the project root, venv active.

| Stage | What | Command | Gate |
|---|---|---|---|
| 00 | Read package; write `spec/site.json`; seed zones + shared kits; `decisions_log.md` | orchestrator | user confirms era, frame, zones, gates |
| 00b | Markups → per-zone checklists + issue/assumption registers | `checklist` → `decisions` | no undecided id |
| 01 | Generators → IFC4 + N-panel blueprint over the underlays | `blueprint` | orchestrator reads `blueprint.png` |
| 01m | Measured gate: levels, radii, points, polygons, counts vs spec | `measure --tag PNN` | 0 failures |
| 01o | Overlay gate: model linework over every underlay | `overlay --pass N` | as-built drawings within tol |
| 01c | Compile 1:1 into Blender (datums, metric UVs, instances, `REF_Drawings`, rig, cutaways) | `compile` | `qa` |
| 02–04 | Gauntlet loop: specialists by dependency tier, freezes, fresh critic | `cycle --pass N` | scorecard + stop criteria |
| Final | Showcase, section beside as-built section, overlay + progression matrices, REPORT | `render --final`, `overlay --side-by-side`, `matrix` | orchestrator review |

## 0. Ground rules

1. **The measured spec (`spec["source_spec"]`) is read-only.** Every number comes from it (`src(spec, "levels[level=17].ffl_m")`, `srcnum`, `datum(spec, "FFL_17")`) or is a declared assumption. Never from memory or photos. Precedence: spec > markups > underlays > photos.
2. **Unknowns are decisions.** A value the spec marks "unknown" goes in the zone's `ASSUMPTIONS`, is used via `A("id")`, hangs DOWN from the governing datum (slab depth below SSL) and gets a row in `decisions_log.md`. FFL/SSL never move.
3. **Source of truth:** `spec/site.json` + `zones/*.py` + `pipeline/*.py`. `build/` is disposable; never hand-edit the `.blend`.
4. **Metric 1:1** in the package's frame (metres, +Z up, origin/+Y per `00_README.md`). Named datums only (`FFL_17`, `SSL_17`, `NODE_18`… from `datum_rules`).
5. **Right tool per element.** Manifold CSG (`solids()`) only for slabs, cores, voids, bases, section cuts. Members, mullions, panels, rails, brackets: `meshes()` and `instances()` via `zones/lib_geom.py` (sweeps of the spec's mm profiles, tubes, panels, revolves, ring arrays). Repeated elements are geometry, never texture.
6. **Numbers before eyes.** `decisions` → `measure` → `overlay` → `qa` green before the critic sees a render. Verify any critic P0/P1 against the numbers before routing it.
7. **Dependency order + freezes.** `zones.<z>.depends_on` orders the build; `run freeze <zone>` after its gates pass; unfreeze only for a logged spec-level reason.
8. **One writer per file.** Names `D3_<ZONE>_<Part>[_Variant]` (instances `_i0001`), collections `NN_<Zone>`, `00_Ground`, `90_Camera_Rig`, `REF_Drawings`, `Section_<ID>` (QA-enforced).
9. **Every pass is a git commit**; one Blender render at a time (`render_lock`).

## 1. Environment & profiles

Blender 5.2 (`AEP_BLENDER` if not auto-detected); Python ≥ 3.11 venv: `pip install manifold3d ifcopenshell ezdxf matplotlib pillow numpy` (scipy optional). `.env`: `AEP_PROFILE`. Keep the machine awake on long runs (`caffeinate -dimsu -t <s>` on macOS).

| Profile | For | Review / Final | Textures · overlay px · unique tris · specialists |
|---|---|---|---|
| `high` | GPU or ≥ 32 GB unified | 1280×720 64 spp / 2560×1440 512 spp | 2K · 3000 · 40 M · 4 |
| `low` | ≤ 16 GB | 960×540 32 spp / 1920×1080 256 spp | 1K · 2000 · 12 M · 2 |
| `smoke` | pipeline tests | 480×270 / 960×540 | 512 · 800 · 2 M · 1 |

Instances are free in the tri budget. Keep repeated prototypes ≤ 400 tris, glass as single quads, full mm-profile sweeps only within ~15 m of a camera.

## 2. Stage 00 — thin spec and zones

Read the user's prompt, `00_README.md`, the spec's `coordinate_system`, `levels[]`, each system block, `open_issues[]`, `markups/README.md`, the markup previews, `images/IMAGE_NOTES.md`. Query the spec, never scroll it: `run q "levels[level=17].ffl_m"` (same syntax as the markups' `spec_key_query`).

**`spec/site.json`** (full example: `templates/site.example.json`) maps the package to the pipeline:
`source_spec`, `brief` paths, hand `datums`, `datum_rules`, `imprint` / `sheet_datums` globs, `storeys` (IFC), `zones` (`module`, `owner`, `markups`, `depends_on`), `libraries` (shared kits → owner), `underlays` (z per drawing), `overlays` (`tol_m`, `expect_tol_m` for other eras, `depth_m`, `cut_offset_m`), `sheets` (plan / section / elevation, any number), `checks`, `materials`, `cameras`, `sun`, `exposure`, `ifc_export`, `pass_budget`, `lean`. Unknown rule values are skipped and listed in `spec["_datum_skipped"]`; a check with an unknown expectation reports *skipped*, never passed.

**Zones:** one per markup set (e.g. STR, FAC, INT, BAS). Cross-cutting systems (envelope r(z), node rule, panel module, mm profiles) go into shared `zones/lib_*.py` kits now, so no specialist waits on another. Zone module API (`zones/_template.py`): `ASSUMPTIONS`, `solids`, `meshes`, `instances`, `materials`, `dress`; entries may set `material`, `uv: box|axis`, `imprint: false`, `smooth`.
**Derived expectations** (e.g. node xyz from a rule) go in `spec/derived/<name>.json` with `_provenance`, referenced by a check's `expect_file`; zone code must compute them independently.

## 3. Gates

- **`checklist` / `decisions`:** markups → `build/passes/domain_<zone>.md` + `registers.json`; `decisions` fails until every assumption, open issue and conflict has a row.
- **`blueprint`:** validates solids (manifold, positive volume) and meshes; writes `build/{solids,meshes,instances}/`, discipline IFC4 files (storeys, real IFC classes from the part word, styled materials, instances as `IfcMappedItem`), DXF (CUT/BEYOND/DATUM/ANNO layers, ANSI31 hatch) and `build/blueprint/blueprint.png` with each underlay at true scale in orange. Read it. Re-run `run ifc` after the final compile so IFC colours match the render.
- **`measure`** (`checks[]`, no Blender; `each` fans out over a spec list with `{field}` templates):

| type | checks | keys |
|---|---|---|
| `level` | zmax/zmin of matched objects | `match`, `edge`, `expect`, `tol` |
| `extent` | bbox size on an axis | `match`, `axis`, `expect` |
| `radius_at_z` | radial extent sliced at z | `match`, `z`, `expect`, `stat`, `centre?` |
| `points` | expected points hit within tol | `match`, `expect`/`expect_file`, `what`, `exact?` |
| `polygon` | polygon vertices lie on the slice | `match`, `z`, `expect`, `vertices` |
| `count` | objects/instances | `match`, `expect` |

- **`overlay`:** draws the model as a drawing (plan cut at z + `cut_offset_m` plus `depth_m` below; section cut plus `depth_m` behind) at the underlay's scale. Pass criterion: **drawing→model p90 ≤ `tol_m`**. Model→drawing share is reported (low = extra geometry or detail the drawing omits; read the composite). `expect_tol_m` entries are report-only, never "fixed". Outputs `build/overlays/PNN/*_overlay.png`, `report.json`, `build/overlay_matrix_PNN.png`.
- **`compile` + `qa`:** n-gon solids, linked-duplicate instances, bisected datum loops, `UVMap_Metric_1m`, 5-channel PBR (fallback colours never block), hidden true-scale `REF_Drawings` planes + `Cam_9x_Overlay_*` cameras, ground, rig, section cutaways, studio polish (`apply_visual_strengths`: sun ≈ 9 W/m², `sky_strength` ≈ 0.04, exposure ≈ −0.5, gobo opt-in). `--zone Z` + `AEP_SCENE` for private specialist scenes.

**Textures (optional):** put maps you supply in `textures/<material key>/` (`albedo`, `roughness`, `normal`, `height`, `ao`; any subset, 1 m tile on `UVMap_Metric_1m`). Without them each material renders its `fallback_rgb`, which never blocks a gate. Photos win on look; spec, markups and underlays win on every dimension.

## 4. Gauntlet loop

| Role | Who | Writes |
|---|---|---|
| Orchestrator | main session | `site.json`, `spec/derived/`, `pipeline/`, cameras, `decisions_log.md`, work orders, freezes, git |
| Specialists (one per zone) | subagents, `model: inherit`, per dependency tier | only their zone module, owned kits, their `ASSUMPTIONS` |
| Critic | fresh read-only subagent each pass | scorecard JSON |

Tier 1 (no dependencies, e.g. STR + BAS) runs in parallel → freeze when `measure`, `overlay`, `decisions` pass → tier 2 (FAC + INT) on the frozen structure.

**Specialist brief** (fill brackets):
```
You are the <ZONE> specialist (markups/<SET>) in an aecfn-building-3d-modelling-blender build (Blender 5.2, metric 1:1).
Root: <ROOT>. Read: 00_README.md, build/passes/domain_<zone>.md, markups/<SET>/README.md + *_preview.png, images/IMAGE_NOTES.md,
decisions_log.md, zones/_template.py, zones/lib_geom.py, your files <...>.
Every number comes from `python3 -m pipeline.run q "<spec_key_query>"` or datum(spec, ...). Edit only your zone/kits; put other
requests in your report. Missing value = ASSUMPTION (ASSUMPTIONS dict, hung DOWN from the datum, row in decisions_log.md).
CSG only for slabs/cores/voids/bases; everything else meshes/instances from lib_geom. Names D3_<ZONE>_<Part>.
Per defect in build/passes/PNN/workorder_<zone>.md: fix → `run blueprint --zone <zone> && run measure --zone <zone>` →
`run overlay --pass NN` → AEP_SCENE=scene_<zone>.blend `run compile --zone <zone>` + `run render --pass NN --zone <zone> --cams <cams>`
→ compare with the photo; iterate. Never leave your zone failing. Rewrite build/passes/notes_<zone>.md (≤ 15 lines).
Report ≤ 250 words: fixed / not fixed + reason, measure/overlay figures, assumptions added, requests, render paths.
```

**Critic brief** (fresh agent, has not seen the code):
```
You are the Adversarial Critic for pass PNN. Inputs: build/renders/PNN/*.png, build/overlay_matrix_PNN.png + report.json,
measure summary <PASTE>, images/ + IMAGE_NOTES.md (look and assembly, NOT proportions), blueprint.png, datum table <PASTE>,
known constraints <PASTE>, last scorecard <PASTE|none>. Do not dispute what the gates verify; judge assembly, detail density,
materials, light, composition, artifacts. Per camera score 0-5: accuracy, detail, materials, light_atmos, composition,
artifacts (5 = indistinguishable from the photo). Any score < 5 lists ≥ 3 defects: id "PNN-<cam#>-<k>", severity
(P0 contradicts drawings · P1 broken geometry/texture · P2 visible deficit vs photo · P3 polish), zone, region, observed,
expected (cite image/markup/panel), fix_hint. Return ONLY JSON:
{"pass":NN,"cameras":{"<Cam>":{"ref":..,"scores":{..},"defects":[..]}},"regressions":[..],"top3":[..],"mean":<float>}
```
Lean mode (`"lean": true`): critic sees contact sheet, overlay matrix and photo-matched cameras only; full review every 5th pass and at the end.

**Each pass:** `run state pass=N phase=cycle` → `run cycle --pass N` → gate failures to owners first → `phase=critic` → work orders (≤ 8 defects/zone, ≤ 5 lean) → `phase=specialists` (parallel within a tier) → freeze passing tier-1 zones → row in `build/passes/log.md`.
**Regression** (camera mean −0.75 vs best): `git checkout PBEST -- zones/<zone>.py`, resend. **Stop:** all gates green and mean ≥ 4.2 with no category < 3 for 2 passes; or < 0.1 gain over 4 passes; or `pass_budget`.
**Resume:** all state is on disk (`state.json`, reports, scorecards, work orders, notes). `run status` → continue from the recorded phase; never redo a finished phase; respawn specialists fresh every 2–3 passes.

## 5. Cameras & delivery

Rig of 10–17 cameras (36 mm sensor, `clip_end` ≥ 1 km): hero, street with `shift_y`, entrance, one close detail per documented system, interiors, ortho elevation (`overlay: "datums"`), site plan (`overlay: "plan"`), `Cam_NN_Section_<ID>_Cutaway` (ortho, `underlay` = as-built section). One camera per photo in IMAGE_NOTES; flag 4–6 `review`.
Final: `compile --final && render --pass N --final` (6-camera showcase: hero, base, interior, detail, elevation/plan, section) → `overlay --pass N --side-by-side` and `overlay --pass N` → `matrix P00,…,PNN_FINAL` → `run ifc` → `build/REPORT.md` (measure + overlay tables, scorecard, pass log, decisions, render timings). Read every final image first.

## 6. Failure modes

| Symptom | Fix |
|---|---|
| `KeyError: specq …` | Wrong path; `run q` the markup's `spec_key_query` first. |
| Datum/check *skipped* | Spec value unknown: log an assumption or drop it; never substitute silently. |
| `invalid solid` | Offset coincident faces ≥ 1 mm; holes via `lib_geom.prism(outer, holes=…)`. |
| Radius off by a constant | Era or line-role mismatch (slab edge vs cladding): log a conflict row. |
| Overlay fails on one era only | Set `expect_tol_m` on that underlay; never move geometry toward it. |
| Plan overlay misses walls | Element doesn't cross the cut: check `cut_offset_m`. |
| Frozen zone won't compile | Intended; `run unfreeze <zone>` with a logged reason. |
| Tri budget exceeded | Use `instances()`; simplify prototypes far from cameras. |
| Instances mis-oriented | Prototypes along +Z (members) or +X radial; `lib_geom.frame_matrix` / `ring_transforms`. |
| Z-fighting between zones | Orchestrator arbitrates ownership; offset 2–5 mm. |
| Camera black / clipped | Inside a solid or `clip_end` too short. |

## 7. Bootstrap

```bash
SKILL="<this skill's base directory>"; cd <brief package>   # contains 00_README.md, spec/, markups/, underlays/, images/
git init -q 2>/dev/null; mkdir -p zones pipeline textures build/passes spec/derived
cp "$SKILL"/scripts/pipeline/*.py pipeline/ && cp "$SKILL"/scripts/zones/*.py zones/
cp "$SKILL"/templates/decisions_log.md . && cp "$SKILL"/templates/site.example.json spec/site.json   # then edit site.json
printf 'build/renders/\nbuild/solids/\nbuild/meshes/\nbuild/instances/\nbuild/overlays/*/*_model.png\nbuild/*.blend*\nbuild/.render.lock\n__pycache__/\n.venv/\n.env*\n.DS_Store\n' > .gitignore
[ -f .env ] || printf 'AEP_PROFILE=high\n' > .env
git add -A && git commit -qm "bootstrap pipeline"
```
Session order: `run status` → preflight (package complete, Blender, venv, `.env`; ask for what's missing) → bootstrap → Stage 00 → gates green → P00 + calibrate pass time, tell the user the plan → loop → delivery.
