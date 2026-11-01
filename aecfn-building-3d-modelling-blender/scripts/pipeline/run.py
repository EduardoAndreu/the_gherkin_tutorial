"""Single entry point for humans and agents. Runs on system/venv Python; launches Blender only where bpy is needed.
  python3 -m pipeline.run checklist                      # markups -> build/passes/domain_<zone>.md + registers.json (issues, assumptions)
  python3 -m pipeline.run decisions                      # lint decisions_log.md against assumptions + registered open issues (exit 1 if missing)
  python3 -m pipeline.run q "levels[level=17].ffl_m"     # read the MEASURED source spec (specq syntax, same as the markups)
  python3 -m pipeline.run blueprint [--zone Z]           # Stage 01: generators (solids/meshes/instances) + IFC + N-panel blueprint
  python3 -m pipeline.run measure [--zone Z] [--tag T]   # measured gate: spec["checks"] on the Stage 01 geometry (exit 1 on failure)
  python3 -m pipeline.run compile [--final] [--zone Z]   # Stage 01c: 1:1 into Blender -> build/scene.blend (+ REF_Drawings, overlay cams)
  python3 -m pipeline.run qa --pass N                    # QA gate inside Blender (merges measure/PNN.json)   exit 1 on P0/P1
  python3 -m pipeline.run render --pass N [--final] [--cams ...] [--zone Z] [--overlays-only]
  python3 -m pipeline.run ifc [--no-styles]              # discipline IFCs (spec ifc_export) styled like the compiled scene's materials
  python3 -m pipeline.run overlay --pass N [--side-by-side]   # overlay gate on the Stage 01 linework: deviation figures + matrix
  python3 -m pipeline.run cycle --pass N                 # checklist -> decisions -> blueprint -> measure -> overlay -> compile -> qa -> render -> git commit
  python3 -m pipeline.run freeze <zone> | unfreeze <zone>     # lock a zone that passed its gates (hash-checked by blueprint/cycle)
  python3 -m pipeline.run specmat <key> '<json>'         # replace ONE materials entry of spec/site.json (never touches the source spec)
  python3 -m pipeline.run matrix [P00,P10,P20_FINAL] [--cams Cam_01,Cam_05]
  python3 -m pipeline.run state pass=7 phase=specialists done='["structure"]'
  python3 -m pipeline.run status
Env (or .env): AEP_SCENE, AEP_BLENDER, AEP_PROFILE (high|low|smoke).
"""
import glob, hashlib, json, os, shutil, subprocess, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from pipeline.aep_spec import load_spec, out, scene_path, zone_order  # noqa: E402

BLENDER_CANDIDATES = [
    "/Applications/Blender.app/Contents/MacOS/Blender",
    *sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe"), reverse=True),
    "/snap/bin/blender", "/usr/bin/blender", "/usr/local/bin/blender",
]


def find_blender():
    exe = os.environ.get("AEP_BLENDER") or shutil.which("blender")
    return exe or next((p for p in BLENDER_CANDIDATES if os.path.exists(p)), None)


def blender(script, args, blend=None):
    exe = find_blender()
    s = os.path.join(ROOT, "pipeline", script)
    if exe:
        cmd = [exe, "-b"] + ([blend] if blend else []) + ["--factory-startup", "--python-exit-code", "1", "-P", s, "--", *args]
    else:
        cmd = [sys.executable, s, *args]
    print("[run]", " ".join(cmd)); return subprocess.run(cmd, cwd=ROOT).returncode


def py(mod, args=()):
    return subprocess.run([sys.executable, "-m", mod, *args], cwd=ROOT).returncode


def sheet(pass_dir):
    from pipeline.contact_sheet import contact_sheet
    meta = json.load(open(os.path.join(pass_dir, "_paths.json")))
    p = contact_sheet(meta["paths"], os.path.join(pass_dir, f"_contact_{meta['tag']}.png"), label_prefix=f"[P{meta['pass_no']:02d}]")
    print("[run] contact sheet", p, f"({meta['seconds']}s render)")


def overlay_ortho(spec, pass_dir):
    """2-D annotation on orthographic renders: camera "overlay": "datums" (dashed datum lines + labels) | "plan" (scale bar, north)."""
    from PIL import Image, ImageDraw
    from pipeline.contact_sheet import _font
    from pipeline.aep_spec import datums_matching
    for c in spec["cameras"]:
        kind, f = c.get("overlay"), os.path.join(pass_dir, f"{c['name']}.png")
        if not kind or c["class"] != "ortho" or not os.path.exists(f):
            continue
        im = Image.open(f).convert("RGB"); W, H = im.size; d = ImageDraw.Draw(im, "RGBA")
        ppm = W / c.get("ortho_scale", 60); fnt = _font(max(12, W // 110)); ink = (20, 30, 50, 255)
        if kind == "datums":
            cz, cx = c["loc"][2], c["loc"][0]
            margin_x = int(W / 2 + (c.get("margin_to_x", cx - c.get("ortho_scale", 60) / 2 + 14) - cx) * ppm)
            ds = datums_matching(spec, c.get("datums", spec.get("sheet_datums", "*")))
            step = max(1, len(ds) // 30)
            rows = []
            for k, (name, z) in enumerate(ds):
                y = H / 2 - (z - cz) * ppm
                if 0 <= y < H:
                    for x in range(margin_x, W, 18):
                        d.line([(x, y), (x + 10, y)], fill=(20, 30, 50, 150), width=1)
                    if k % step == 0:
                        rows.append([y, y, f"{name} {z:+.2f} m"])
            d.rectangle([0, 0, margin_x - 2, H], fill=(255, 255, 255, 240))  # opaque label strip: no scene behind the tags
            lh = fnt.size + 3; cols = [[], []]
            for k, r in enumerate(rows):
                cols[k % 2].append(r)
            col_w = max(d.textlength(r[2], font=fnt) for r in rows) + 14 if rows else 0
            for ci, col in enumerate(cols):
                for _ in range(200):
                    moved = False
                    for i in range(1, len(col)):
                        if col[i - 1][1] - col[i][1] < lh:
                            col[i][1] = col[i - 1][1] - lh; moved = True
                    if not moved:
                        break
                x_right = margin_x - 10 - (1 - ci) * col_w
                for y, ly, txt in col:
                    tw = d.textlength(txt, font=fnt); xl = max(4, x_right - tw)
                    d.rectangle([xl - 3, ly - lh + 1, xl + tw + 3, ly], fill=(255, 255, 255, 215))
                    d.text((xl, ly - lh + 2), txt, fill=ink, font=fnt)
                    d.line([(xl + tw + 10, ly - lh / 2), (margin_x - 6, ly - lh / 2), (margin_x - 2, y)], fill=(20, 30, 50, 110), width=1)
        elif kind == "plan":
            bar = 20 * ppm; x0, y0 = 48, H - 56
            d.rectangle([x0 - 16, y0 - fnt.size - 16, x0 + bar + 40, y0 + 22], fill=(255, 255, 255, 225))
            for i in range(4):
                d.rectangle([x0 + i * bar / 4, y0, x0 + (i + 1) * bar / 4, y0 + 10], fill=ink if i % 2 == 0 else (255, 255, 255, 255), outline=ink)
            for i, lab in enumerate(("0", "5", "10", "15", "20 m")):
                d.text((x0 + i * bar / 4 - d.textlength(lab.split()[0], font=fnt) / 2, y0 - fnt.size - 6), lab, fill=ink, font=fnt)
            nx, ny = W - 70, 64
            d.ellipse([nx - 26, ny - 38, nx + 26, ny + 40], fill=(255, 255, 255, 225))
            d.polygon([(nx, ny - 30), (nx - 14, ny + 14), (nx, ny + 4), (nx + 14, ny + 14)], fill=ink)
            d.text((nx - 6, ny + 18), "N", fill=ink, font=fnt)
        im.save(f)


# ---------------------------------------------------------------- freeze
def _zone_hash(spec, zone):
    h = hashlib.sha256()
    files = [spec["zones"][zone]["module"].replace(".", "/") + ".py"] + [p for p, o in spec.get("libraries", {}).items() if o == spec["zones"][zone]["owner"]]
    for f in files:
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            h.update(open(p, "rb").read())
    return h.hexdigest()[:16]


def _read_state():
    p = os.path.join(ROOT, "build", "passes", "state.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def _state(**kv):
    """Read-modify-write of state.json through a temp file + os.replace (parallel specialists read it concurrently)."""
    p = os.path.join(ROOT, "build", "passes", "state.json"); os.makedirs(os.path.dirname(p), exist_ok=True)
    st = _read_state()
    st.update(kv); st["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, p); return st, p


def check_frozen(spec, zones=None):
    st = _read_state()  # read-only: never rewrite state from a specialist's process
    bad = []
    for z, fr in (st.get("frozen") or {}).items():
        if zones and z not in zones:
            continue
        if _zone_hash(spec, z) != fr["hash"]:
            bad.append(z)
    if bad:
        raise SystemExit(f"[freeze] frozen zone(s) changed since freeze: {', '.join(bad)}. `pipeline.run unfreeze <zone>` first (orchestrator only).")


def main(a):
    spec = load_spec(); blend = scene_path(spec)
    cmd, rest = a[0], a[1:]
    if cmd == "q":
        return py("pipeline.specq", rest)
    if cmd == "checklist":
        from pipeline.markup_checklist import build
        build(spec); return 0
    if cmd == "decisions":
        from pipeline.decisions import lint
        return 1 if lint(spec)["missing"] else 0
    if cmd == "blueprint":
        zones = [rest[rest.index("--zone") + 1]] if "--zone" in rest else None
        check_frozen(spec, zones)
        return py("pipeline.stage01_blueprint", rest)
    if cmd == "measure":
        return py("pipeline.measure", rest)
    if cmd == "compile":
        return blender("stage03_compile.py", rest)
    if cmd == "qa":
        return blender("qa_checks.py", rest, blend)
    if cmd == "render":
        rc = blender("stage04_render.py", rest, blend)
        if rc == 0 and "--overlays-only" not in rest:
            for d in sorted(glob.glob(out(spec, "renders") + "/*/_paths.json"), key=os.path.getmtime)[-1:]:
                overlay_ortho(spec, os.path.dirname(d)); sheet(os.path.dirname(d))
        return rc
    if cmd == "overlay":
        return py("pipeline.overlay", rest)
    if cmd == "ifc":  # colours from the compiled scene, then the discipline IFCs from the Stage 01 geometry
        rc = 0 if "--no-styles" in rest else blender("ifc_styles.py", [], blend)
        return rc or py("pipeline.ifc_export")
    if cmd == "freeze":
        z = rest[0]
        p = os.path.join(ROOT, "build", "passes", "state.json")
        fr = _read_state().get("frozen") or {}
        fr[z] = dict(hash=_zone_hash(spec, z), at=time.strftime("%Y-%m-%d %H:%M:%S"),
                     commit=subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
        _state(frozen=fr); subprocess.run(["git", "add", p], cwd=ROOT); subprocess.run(["git", "commit", "-qm", f"freeze {z}"], cwd=ROOT)
        print(f"[freeze] {z} frozen at {fr[z]['hash']} ({fr[z]['commit']})"); return 0
    if cmd == "unfreeze":
        p = os.path.join(ROOT, "build", "passes", "state.json")
        fr = _read_state().get("frozen") or {}; fr.pop(rest[0], None); _state(frozen=fr)
        subprocess.run(["git", "add", p], cwd=ROOT); subprocess.run(["git", "commit", "-qm", f"unfreeze {rest[0]}"], cwd=ROOT)
        print(f"[freeze] {rest[0]} unfrozen"); return 0
    if cmd == "cycle":
        if "--pass" not in rest or not rest[rest.index("--pass") + 1].isdigit():
            raise SystemExit("usage: python3 -m pipeline.run cycle --pass N [--cams ...]")
        n = int(rest[rest.index("--pass") + 1]); tag = f"P{n:02d}"
        gate_rc = 0
        for step in (["checklist"], ["decisions"], ["blueprint"], ["measure", "--tag", tag], ["overlay", "--pass", str(n)], ["compile"], ["qa", *rest], ["render", *rest]):
            rc = main(step)
            if step[0] in ("qa", "measure", "overlay", "decisions"):
                gate_rc = gate_rc or rc  # gates block the critic, not the cycle
            elif rc:
                return rc
        subprocess.run(["git", "add", "-A"], cwd=ROOT); subprocess.run(["git", "commit", "-qm", tag], cwd=ROOT)
        if gate_rc:
            print(f"[cycle] {tag} committed, but a gate failed (decisions / measure / QA / overlay): fix before the critic sees it")
        return gate_rc
    if cmd == "matrix":
        from pipeline.contact_sheet import progression_matrix
        passes = rest[0].split(",") if rest and not rest[0].startswith("--") else None
        cams = rest[rest.index("--cams") + 1].split(",") if "--cams" in rest else None
        print(progression_matrix(out(spec, "renders"), out(spec, "progression_matrix.png"), passes, cams=cams))
        return 0
    if cmd == "specmat":
        import re
        try:
            import fcntl
        except ImportError:
            fcntl = None
        sp = spec["_path"]; key, val = rest[0], json.dumps(json.loads(rest[1]), ensure_ascii=False)
        with open(sp + ".lock", "w") as lk:
            if fcntl:
                fcntl.flock(lk, fcntl.LOCK_EX)
            lines = open(sp).read().split("\n")
            m0 = next(i for i, ln in enumerate(lines) if ln.strip().startswith('"materials"'))
            hit = next((i for i, ln in enumerate(lines[m0 + 1:], m0 + 1) if re.match(rf'\s*"{re.escape(key)}"\s*:', ln)), None)
            if hit is None:
                lines.insert(m0 + 1, f'    "{key}": {val},')
            else:
                lines[hit] = f'    "{key}": {val}' + ("," if lines[hit].rstrip().endswith(",") else "")
            txt = "\n".join(lines); json.loads(txt)
            tmp = sp + f".{os.getpid()}.tmp"; open(tmp, "w").write(txt); os.replace(tmp, sp)
        print("[specmat]", key, "->", val); return 0
    if cmd == "state":
        kv = {}
        for item in rest:
            k, v = item.split("=", 1)
            try:
                kv[k] = json.loads(v)
            except ValueError:
                kv[k] = v
        st, p = _state(**kv)
        subprocess.run(["git", "add", p], cwd=ROOT); subprocess.run(["git", "commit", "-qm", f"state {st.get('pass')} {st.get('phase')}"], cwd=ROOT)
        print(json.dumps(st, indent=1)); return 0
    if cmd == "status":
        p = os.path.join(ROOT, "build", "passes", "state.json")
        print("STATE:", open(p).read() if os.path.exists(p) else "none (fresh run: start at Stage 00)")
        print("ZONE ORDER:", " -> ".join(zone_order(spec)))
        print("COMMITS:\n" + subprocess.run(["git", "log", "--oneline", "-8"], cwd=ROOT, capture_output=True, text=True).stdout)
        for pat in ("build/qa/P*.json", "build/measure/*.json", "build/overlays/P*/report.json", "build/passes/P*/scorecard.json", "build/passes/P*/workorder_*.md", "build/renders/P*"):
            fs = sorted(glob.glob(os.path.join(ROOT, pat)))
            print(f"{pat}: {len(fs)} -> latest {os.path.relpath(fs[-1], ROOT) if fs else 'none'}")
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
