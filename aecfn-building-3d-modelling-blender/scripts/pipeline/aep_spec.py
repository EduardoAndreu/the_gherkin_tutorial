"""Single source of truth loader. Pure Python: imported by Stage 01 (system Python) and by Blender.

Two specs:
  spec/site.json          the THIN pipeline spec (project, zones, sheets, checks, cameras, materials...) - orchestrator-owned
  spec["source_spec"]     the MEASURED building spec delivered with the brief package (e.g. spec/gherkin_spec.json)
                          -> loaded read-only into spec["_src"]; NEVER written by the pipeline or any agent.
Datums: spec["datums"] (hand-written) + spec["datum_rules"] (derived from the measured spec, e.g. FFL_17 from levels[]).
"""
import fnmatch, hashlib, importlib, json, os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from pipeline.specq import q, num, fmt, expand  # noqa: E402


def load_env(start=ROOT):
    """Load the nearest .env (project root, then parent folders) into os.environ. Real env vars win; never printed."""
    d = start
    while True:
        p = os.path.join(d, ".env")
        if os.path.isfile(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.removeprefix("export ").split("=", 1)
                        k, v = k.strip(), v.strip()
                        if v[:1] in ("'", '"') and v[0] in v[1:]:
                            v = v[1:v.index(v[0], 1)]
                        else:
                            v = v.split(" #", 1)[0].split("\t#", 1)[0].strip()
                        if v:
                            os.environ.setdefault(k, v)
            return p
        if os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


load_env()

PROFILES = {
    # Pick per machine with AEP_PROFILE (in that machine's .env); the spec's "profile" is only the default.
    "high": dict(review_res=(1280, 720), review_samples=64, final_res=(2560, 1440), final_samples=512, tex_px=2048,
                 review_cams="all", volume_in_review=True, max_parallel_specialists=4, tri_budget=40_000_000, overlay_px=3000),
    "low": dict(review_res=(960, 540), review_samples=32, final_res=(1920, 1080), final_samples=256, tex_px=1024,
                review_cams="flagged", volume_in_review=False, max_parallel_specialists=2, tri_budget=12_000_000, overlay_px=2000),
    "smoke": dict(review_res=(480, 270), review_samples=8, final_res=(960, 540), final_samples=32, tex_px=512,
                  review_cams="all", volume_in_review=False, max_parallel_specialists=1, tri_budget=2_000_000, overlay_px=800),
}


def _derive_datums(spec):
    """datum_rules: [{"each": "levels[]", "name": "FFL_{level}", "value": "ffl_m", "where?": "ffl_m"}] -> datums.
    Items whose value is not a number (e.g. "unknown") are skipped and listed in spec["_datum_skipped"]."""
    src = spec.get("_src") or {}
    skipped = []
    for rule in spec.get("datum_rules", []):
        items = q(src, rule["each"]) if rule["each"].endswith("[]") else [q(src, rule["each"])]
        for it in items:
            if not isinstance(it, dict):
                continue
            if rule.get("where") and num(q(it, rule["where"], None)) is None:
                skipped.append((rule["name"], it.get("level", "?"))); continue
            v = num(q(it, rule["value"], None))
            if v is None:
                skipped.append((rule["name"], it.get("level", "?"))); continue
            name = fmt(rule["name"], it)
            if name in spec["datums"] and abs(spec["datums"][name] - v) > 1e-9:
                raise SystemExit(f"datum {name} defined twice with different values ({spec['datums'][name]} vs {v})")
            spec["datums"][name] = v
    spec["_datum_skipped"] = skipped


def load_spec(path=None):
    path = path or os.path.join(ROOT, "spec", "site.json")
    with open(path) as f:
        spec = json.load(f)
    spec["_root"] = ROOT
    spec["_path"] = path
    spec.setdefault("datums", {})
    try:
        spec["datums"] = {k: float(v) for k, v in spec["datums"].items()}
    except (TypeError, ValueError) as e:
        raise SystemExit(f"spec datums must all be numbers (metres): {e}")
    srcp = spec.get("source_spec")
    if srcp:
        full = os.path.join(ROOT, srcp)
        if not os.path.exists(full):
            raise SystemExit(f"source_spec {srcp!r} not found (the measured spec of the brief package)")
        with open(full, "rb") as f:
            raw = f.read()
        spec["_src"] = json.loads(raw)
        spec["_src_sha"] = hashlib.sha256(raw).hexdigest()[:16]
    else:
        spec["_src"] = {}
        spec["_src_sha"] = None
    _derive_datums(spec)
    if not spec["datums"]:
        raise SystemExit("spec has no datums: give at least Z_GROUND (or datum_rules from the source spec)")
    name = os.environ.get("AEP_PROFILE") or spec.get("profile", "high")
    if name not in PROFILES:
        raise SystemExit(f"Unknown profile {name!r} (from AEP_PROFILE or spec 'profile'). Use one of: {', '.join(PROFILES)}.")
    spec["_profile"] = dict(PROFILES[name], name=name)
    spec.setdefault("zones", {})
    for zn, z in spec["zones"].items():
        z.setdefault("depends_on", []); z.setdefault("origin", [0, 0, 0])
    spec.setdefault("sheets", [])
    return spec


def datum(spec, name):
    try:
        return float(spec["datums"][name])
    except KeyError:
        raise KeyError(f"unknown datum {name!r}; known: {', '.join(sorted(spec['datums']))[:400]}") from None


def datums_matching(spec, patterns):
    """Datums whose name matches any glob in patterns (None/'*' = all). Returns sorted list of (name, z)."""
    pats = patterns if patterns not in (None, "*") else ["*"]
    if isinstance(pats, str):
        pats = [pats]
    return sorted(((n, z) for n, z in spec["datums"].items() if any(fnmatch.fnmatch(n, p) for p in pats)), key=lambda t: t[1])


def src(spec, path, default=KeyError):
    """Read the measured spec: src(spec, "levels[level=17].ffl_m")."""
    return q(spec["_src"], path, default)


def srcnum(spec, path):
    """Numeric read of the measured spec; None if unknown/missing. Callers must handle None (log an ASSUMPTION)."""
    return num(q(spec["_src"], path, None))


def zone_module(spec, zone):
    return importlib.import_module(spec["zones"][zone]["module"])


def zone_order(spec):
    """Zones in dependency order (depends_on first). Raises on cycles."""
    done, order = set(), []

    def visit(z, stack=()):
        if z in done:
            return
        if z in stack:
            raise SystemExit(f"zone dependency cycle: {' -> '.join(stack + (z,))}")
        for d in spec["zones"][z].get("depends_on", []):
            if d not in spec["zones"]:
                raise SystemExit(f"zone {z} depends_on unknown zone {d}")
            visit(d, stack + (z,))
        done.add(z); order.append(z)
    for z in spec["zones"]:
        visit(z)
    return order


def scene_path(spec):
    """build/scene.blend for the orchestrator; specialists set AEP_SCENE=scene_<zone>.blend for private self-checks."""
    return out(spec, os.environ.get("AEP_SCENE", "scene.blend"))


def out(spec, *parts):
    p = os.path.join(spec["_root"], "build", *parts)
    os.makedirs(os.path.dirname(p) if os.path.splitext(p)[1] else p, exist_ok=True)
    return p


def brief_path(spec, key, *parts):
    """Path inside the brief package: brief_path(spec, "underlays", "PLN_L17.json"). Defaults: markups/, underlays/, images/."""
    b = spec.get("brief", {})
    base = b.get(key, key)
    return os.path.join(spec["_root"], base, *parts)


def file_sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]
