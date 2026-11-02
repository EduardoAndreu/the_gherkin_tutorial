---
name: aecfn-building-modelling-brief
description: Turn architectural blueprint PDFs (plans, sections, elevations, details) and photos into a verified modelling brief folder for a Blender/IFC reconstruction agent. The brief holds a calibrated world frame, a spec JSON with provenance, true-scale underlays, colour-coded markups traced to spec keys and image notes. Use whenever someone wants to model, reconstruct or digitise a building from drawings, extract levels, grids or dimensions from architectural PDFs, or prepare drawings for a 3D modelling agent, even if they don't say "brief" or "spec".
---

# Blueprint → modelling brief

The brief makes the modeller accurate. Ship only `spec.json` (every value with provenance), underlays, markups, images + `IMAGE_NOTES.md` and `00_README.md`. Extra material confuses the modeller.

Scripts are in `scripts/` (Python 3: `pymupdf numpy opencv-python pillow openpyxl matplotlib`); run any with `--help`. Templates are in `assets/`. Work in `<project>/brief_work/` (`sheets/ inventory/ calibration.json spec/ underlays/ markup_defs/ markups/ images/ brief/`). Tell the user each key decision as you make it: target era, sheet list, world frame, open issues.

## Phases
**0. Intake.** Confirm, asking only what you can't infer: source and photo folders, scope and **one target era**.

**1. Sheets (before anything else).** Run `sheet_prep.py detect <dir> brief_work/sheets`. For every drawing (PDF page or TIF/PNG/JPG scan), it finds the **drawing frame**: the ruled border around the drawing and its title-block column, not the paper edge. Scans are often tilted and sheared, so it straightens them before searching, and it handles frames that are open at the bottom. It snaps each side onto the drawn border line, then renders `previews/`, `corners/` (each frame corner zoomed in) and `titleblocks/`.
- Look at every preview and corner sheet. Each corner must sit on the border line; everything outside the frame (print-size ruler, scale table, clips, empty paper) goes. Where the detector found no frame or picked the wrong rectangle, set `frame.corners_pt` [TL, TR, BR, BL] by eye from the preview ruler, then run `detect --only ID --keep-boxes --snap`.
- Fill `read` from the title block exactly as written: drawing number, title, revision, date, project, author, **scale** (`1:100 @ A1`, `NTS`) and **paper** (`A1`). Write `not shown` when absent; never guess. Set `reviewed: true`.
- Run `sheet_prep.py apply brief_work/sheets/sheets.json` until it reports 0 errors. It straightens and crops each sheet to its frame (+3 mm), writing `png/` to look at and `cropped/` PDFs at the scan's own scale. It also checks the frame size against the stated paper: when the scan is not at paper size, it gives no stated metres-per-point.
- **From here on, use only the cropped PDFs.** PDF-point coordinates refer to the cropped page. The stated scale is at best a first estimate; calibrate from references.

**2. Inventory.** Run `inventory.py brief_work/sheets/cropped brief_work/inventory --sheets brief_work/sheets/sheets.json`, look at every contact sheet, and correct `drawing_index.xlsx`. Drop duplicates and superseded revisions. Select one plan per floor type, the governing sections, one elevation per facade type, the details of repeating modules, and the site plan. Check `content`: scanned (image-only) sheets need the raster route.

**3. Frame and calibration.**
- First fix `spec.coordinate_system`: origin at a grid intersection (or the centre of a round building), axes along the main grid, Z=0 at ground FFL with its AOD.
- **Vector sheets:** use `calibrate.py plan|section` with 2+ far-apart references and at least one `--check` dimension per sheet. Find grid bubbles and level tags with `pdf_vectors.py text --regex`, and line crossings with `calibrate.py intersect`.
- **Scanned sheets:** use `raster_tools.py bubbles` → `calibrate.py autogrid` with one anchor per axis (`--min-r-pt` drops sub-grid bubbles). For sections, `raster_tools.py lines` → `pair_levels.py`. Fit z and s separately, and ignore the scanned page size.
- Fitted scale must match the stated scale within 0.3%. Investigate anything over 0.5%: a wrong reference, a different print size (1:100 @ A1 on A3 is 1:200), or a stretched export.
- For sections and elevations, record the cut or view line (`--line`). Fit the azimuth for curved facades, and negate s if it runs right-to-left.

**4. Spec** (start from `assets/spec_template.json`). Extract in this order: levels → grids → structure → envelope → floor plates and interior → site and context.
- Every number is `{value, unit, src:{file, method, uncertainty_m, confidence, era}}`.
- Confidence is **high** when written or cross-checked, **medium** when measured but unchecked, **low** when inferred.
- Preference order: written > measured > inferred.
- Read vector geometry with `pdf_vectors.py layers|dump --calib|circle`, and any sheet with `view_underlay.py --window|--profile`.
- For repeated floors, crop the same world window from every level into one strip.
- Unknowns go in `assumptions[]` with a reason. Conflicts go in `open_issues[]` {id, topic, description, options, **default**, status}. Keep real drawing disagreements separate from your extraction gaps and your method's own uncertainty.
- Derived geometry (tapers, slopes) is stored as parameters, with per-level values as evidence.
- Line roles need a stated basis and stay low-confidence until another sheet confirms them.
- **Never use remembered figures.** Put them only in `sanity_checks`, marked unverified.
- On a change: bump `schema_version`, add a changelog entry, archive the old file, and mark replaced keys `superseded_by` (never delete them).

**5. Underlays.** Run `rectify.py --db calibration.json --id … --out underlays --ppm 50` (15–25 for tall sections). Check each one by drawing 2–3 spec items taken from a *different* sheet: they must land on the linework.

**6. Markups.** Make sets STR, FAC, INT, SITE with about 6–12 sheets each. Write definitions from `assets/markup_def_example.json` and render them with `markup.py`.
- **Measured items** use `*_key` fields and `{placeholder}` labels, never literal numbers (unless `literal_ok`).
- **Assumed items** cite `A-n`. **Observed items** cite their source. **Issue items** carry their `issue_id`.
- Use the classes in `assets/style.json`.
- Look at every preview and full-resolution crops of the busy areas; the overlay must sit within about 2 px. A constant offset means calibration; a local one means the spec value or line role.
- **Never move the drawing to fit.**
- Run the full `validate.py --spec --markups --underlays` until it reports 0 errors, then fix or explain every warning. Re-render after any spec change.

**7. Images.** Run `curate_images.py scan`, look at every preview, and drop near-duplicates (keep the highest resolution) and off-topic images. Write a mapping CSV (`01_structure/STR-01_<what>.jpg`), then run `curate_images.py apply`. Write `IMAGE_NOTES.md` from its template: what each image shows (in spec terms), what to use it for, what to ignore, lens caveats, and the build-up knowns and unknowns.

**8. READMEs.** Fill `00_README.md` and `markups/README.md` from the `assets/` templates. In `00_README.md`, name the real spec keys (check each with `bmb_common.get_path`), state the achieved accuracy, and list the expected era differences.

**9. Package.** Run `package_brief.py --out <brief folder> --readme --spec --underlays --markups --markups-readme --images`. It assembles and re-validates the brief in a staging folder and writes it to `--out` only if there are no errors.

**Report:** the brief folder path and contents, accuracy per sheet type, the open issues that affect geometry (with their defaults), the assumptions, and anything unresolved. Keep `brief_work/` for rebuilds.

**Multi-agent** (only if asked): the orchestrator owns the frame, the sheet list, the style, the merge and the review. It calibrates one plan and one section first, then runs STR, FAC, INT and SITE specialists in parallel, each in its own folder. Before accepting their work, it looks at their previews itself and cross-checks the domains (structure vs envelope levels, slab edge vs facade line, site datum vs ground FFL).

## Pitfalls
- Labels can be wrong (bubbles shifted by 8 positions). Trust geometry confirmed on another sheet.
- "Missing" can mean "not drawn". Decide on evidence and log an issue with a default.
- Read the labels next to a shape before classifying it (a glass deck is not a void).
- Check element heights against FFL on a section (nodes sat 0.70 m below FFL).
- Eras really differ, by up to 0.3 m. Calibrate each set on its own references, and tell the modeller not to "fix" the differences.
- Before calling something a discrepancy, overlay the sheets and check what each dimension ends on. A misreading is likelier than a drawing error.
- Look for section markers on plans before assuming where a cut is.
- Plans of inclined facades show the floors below beyond the cut. Settle which layer is which on a section.
- Avoid circular checks: confirm a registration on something unrelated.
- Register elevations on AOD markers (z) and on members that sit on grid lines (s), not on silhouettes.
- Detected lines give both edges of a member; take the midline.
- On hatched sheets, bubbles fail the halo test. Use `--halo-max 0.7` or a per-sheet anchor.
- Scanned sheets are often not at paper size (archive scanners output every sheet at about the same size), and scale bars can disagree with the drawing by as much as 20 %. Trust calibration on drawn references over both.
- Drive repeated tool calls from Python with argument lists; shell loops pass quoted arguments as one string.
