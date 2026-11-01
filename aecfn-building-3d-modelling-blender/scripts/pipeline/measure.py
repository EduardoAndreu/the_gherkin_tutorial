"""Measured gate — numeric checks of the compiled geometry against the measured spec. System Python, no Blender.
  python3 -m pipeline.run measure [--zone Z] [--tag P07]        -> build/measure/<tag>.json ; exit 1 on any failure

spec["checks"]: list of checks. Each check may fan out with "each" (a specq list path on the source spec) and
"where" (field that must be numeric); "{field}" templates then expand per item. Types:
  level         zmax|zmin of the matched objects == expect                      {"match","edge","expect","tol"}
  extent        bbox size along axis x|y|z == expect                            {"match","axis","expect","tol"}
  radius_at_z   radial distance (from centre) of the geometry sliced at z       {"match","z","expect","stat":"max|min|mean|p95","centre":[0,0],"tol"}
  count         number of matched objects (instances counted individually)      {"match","expect","tol":0}
  points        every expected point has geometry within tol                    {"match","expect"|"expect_file","what":"origins|centres|vertices","tol","exact":bool}
                expected points: [x,y,z] | {"x","y","z"} | {"theta_deg","r_m","z_m"} (polar, theta anticlockwise from +X)
  polygon       every expected polygon vertex lies on the slice of the matched geometry at z (within tol)
                {"match","z","expect"|"expect_file","vertices":"all"|[idx],"tol"}
  heights_at_xy every expected (x, y, z) point: the TOP surface of the matched geometry above (x, y) is at z within tol
                (plaza spot levels)  {"match","expect","tol","skip_ids"?: [ids]}
  centres_at_z  slice at z, chain the cut segments into closed loops; every expected [x,y] point has a loop centroid
                within tol (member sections at a plan cut, e.g. A-frame legs at the GF cut)  {"match","z","expect","tol"}
"z_offset" (radius_at_z, polygon, centres_at_z): added to z, e.g. -0.05 to slice just below a slab top (a slice exactly
at a face plane is degenerate). "@<file>#<specq path>" in expect/z reads an orchestrator-derived JSON (spec/derived/),
{field} placeholders expanded per fan-out item, e.g. "@spec/derived/nodes.json#rings[level={level}].r_m".
match: glob on object names (fnmatch), optionally a list of globs. All distances in metres.
"""
import fnmatch, json, os, sys, time
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pipeline.aep_spec import load_spec, out  # noqa: E402
from pipeline.specq import q, num, fmt, expand  # noqa: E402
from pipeline import geom_io as GIO, linework as LW  # noqa: E402


def _match(items, pat):
    pats = pat if isinstance(pat, list) else [pat]
    return [it for it in items if any(fnmatch.fnmatch(it["name"], p) for p in pats)]


def _world(it):
    return GIO.expand_instances(it) if it["kind"] == "instances" else (it["verts"], it["tris"])


def _tmpl(v, item):
    """Text fields (match, id): expand every {field} placeholder."""
    if isinstance(v, str) and "{" in v:
        return fmt(v, item)
    return v


def _val(v, item):
    """Numeric fields (expect, z): a lone {field} is left for specq.expand to read raw (no lossy formatting)."""
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("{") and s.endswith("}") and s.count("{") == 1:
            return s
    return _tmpl(v, item)


def _num(expr, item, spec):
    """expand() plus "@file#path" expectations read from orchestrator-derived JSON files."""
    e = _val(expr, item)
    if isinstance(e, str) and e.startswith("@"):
        fp, _, path = e[1:].partition("#")
        with open(os.path.join(spec["_root"], fp)) as f:
            data = json.load(f)
        return num(q(data, fmt(path, item) if "{" in path else path, None))
    return expand(e, item, spec["_src"])


def _z(c, item, spec):
    z = _num(c["z"], item, spec)
    return None if z is None else z + float(c.get("z_offset", 0.0))


def _loops(S, eps=1e-4):
    """Group cut segments (s,2,3) into connected loops -> list of (k,2) point arrays."""
    P = S.reshape(-1, 3)[:, :2]
    key = np.round(P / eps).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1, 2)
    parent = list(range(inv.max() + 1))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for a, b in inv:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comp = np.array([find(a) for a in inv[:, 0]])
    return [P.reshape(-1, 2, 2)[comp == c].reshape(-1, 2) for c in np.unique(comp)]


def _points(expr, item, spec, root_dir):
    if isinstance(expr, list):
        raw = expr
    elif isinstance(expr, str) and (expr.endswith(".json") or ".json#" in expr):
        fp, _, path = expr.partition("#")
        with open(os.path.join(root_dir, fp)) as f:
            raw = json.load(f)
        if path:
            raw = q(raw, path)
    else:
        raw = q(spec["_src"], expr) if isinstance(expr, str) else expr
    pts = []
    for p in raw:
        if isinstance(p, dict):
            if "theta_deg" in p and ("r_m" in p or "r" in p) and not ("x" in p and "y" in p):
                t = np.radians(float(p["theta_deg"])); r = float(p.get("r_m", p.get("r"))); z = float(p.get("z_m", p.get("z", 0)))
                pts.append([r * np.cos(t), r * np.sin(t), z])
            else:
                pts.append([float(p["x"]), float(p["y"]), float(p.get("z", p.get("z_m", 0)))])
        else:
            pts.append([float(x) for x in p][:3] + [0.0] * (3 - len(p)))
    return np.array(pts, float).reshape(-1, 3)


def run_check(c, items, spec, item=None):
    item = item or {}
    typ = c["type"]; tol = float(c.get("tol", 0.05))
    pat = _tmpl(c["match"], item)
    sel = _match(items, pat)
    cid = _tmpl(c.get("id", typ), item)
    res = dict(id=cid, type=typ, match=pat, n_objects=len(sel), ok=False)
    if not sel:
        res.update(msg="no objects match"); return res
    if typ in ("level", "extent"):
        Vs = np.vstack([_world(it)[0] for it in sel])
        if typ == "level":
            val = float(Vs[:, 2].max() if c.get("edge", "zmax") == "zmax" else Vs[:, 2].min())
        else:
            i = "xyz".index(c.get("axis", "z")); val = float(Vs[:, i].max() - Vs[:, i].min())
        want = _num(c["expect"], item, spec)
        if want is None:
            res.update(msg=f"expected value unknown ({c['expect']}) - needs a logged decision", ok=True, skipped=True); return res
        res.update(value=round(val, 4), expect=round(want, 4), err=round(val - want, 4), ok=abs(val - want) <= tol)
    elif typ == "radius_at_z":
        z = _z(c, item, spec); want = _num(c["expect"], item, spec)
        if z is None or want is None:
            res.update(msg="z or expected radius unknown", ok=True, skipped=True); return res
        cx, cy = c.get("centre", [0, 0])
        rs = []
        for it in sel:
            V, T = _world(it); S = LW.cut_segments(V, T, 2, z)
            if len(S):
                P = S.reshape(-1, 3); rs.append(np.hypot(P[:, 0] - cx, P[:, 1] - cy))
        if not rs:
            res.update(msg=f"nothing crosses z={z:.3f}"); return res
        R = np.concatenate(rs); stat = c.get("stat", "max")
        val = float({"max": R.max(), "min": R.min(), "mean": R.mean(), "p95": np.percentile(R, 95)}[stat])
        res.update(z=round(z, 3), stat=stat, value=round(val, 4), expect=round(want, 4), err=round(val - want, 4), ok=abs(val - want) <= tol)
    elif typ == "count":
        n = sum(len(it["transforms"]) if it["kind"] == "instances" else 1 for it in sel)
        want = _num(c["expect"], item, spec)
        if want is None:
            res.update(msg="expected count unknown", ok=True, skipped=True); return res
        res.update(value=n, expect=int(want), ok=abs(n - want) <= float(c.get("tol", 0)))
    elif typ == "points":
        want = _points(c.get("expect_file") or c["expect"], item, spec, spec["_root"])
        what = c.get("what", "origins")
        cand = []
        for it in sel:
            if what == "origins" and it["kind"] == "instances":
                cand.append(it["transforms"][:, :3, 3])
            elif what == "vertices":
                cand.append(_world(it)[0])
            else:
                V = _world(it)[0]
                if it["kind"] == "instances":
                    k = len(it["transforms"]); cand.append(V.reshape(k, -1, 3).mean(1))
                else:
                    cand.append(((V.min(0) + V.max(0)) / 2)[None])
        C = np.vstack(cand)
        d = np.sqrt(((want[:, None, :] - C[None, :, :]) ** 2).sum(-1)).min(1)
        miss = int((d > tol).sum())
        res.update(n_expected=len(want), n_candidates=len(C), max_err=round(float(d.max()), 4), mean_err=round(float(d.mean()), 4),
                   misses=miss, ok=(miss == 0) and (not c.get("exact") or len(C) == len(want)))
        if miss:
            res["worst"] = [dict(point=want[i].round(3).tolist(), err=round(float(d[i]), 3)) for i in np.argsort(-d)[:5]]
    elif typ == "polygon":
        z = _z(c, item, spec)
        if z is None:
            res.update(msg="z unknown", ok=True, skipped=True); return res
        polys = _tmpl(c.get("expect_file") or c["expect"], item)
        if isinstance(polys, str) and polys.startswith("@"):
            fp, _, path = polys[1:].partition("#")
            with open(os.path.join(spec["_root"], fp)) as f:
                raw = q(json.load(f), path)
        elif isinstance(polys, str) and polys.startswith("./"):
            raw = q(item, polys[2:])
        else:
            raw = q(spec["_src"], polys) if isinstance(polys, str) and not polys.endswith(".json") else polys
        if isinstance(raw, str):
            with open(os.path.join(spec["_root"], raw)) as f:
                raw = json.load(f)
        verts_sel = c.get("vertices", "all")
        P = []
        for poly in raw:
            arr = np.asarray(poly, float)
            idx = range(len(arr)) if verts_sel == "all" else verts_sel
            for i in idx:
                P.append([arr[i][0], arr[i][1]])
        P = np.array(P, float)
        S = []
        for it in sel:
            V, T = _world(it); s = LW.cut_segments(V, T, 2, z)
            if len(s):
                S.append(s.reshape(-1, 3)[:, :2])
        if not S:
            res.update(msg=f"nothing crosses z={z:.3f}"); return res
        C = np.vstack(S)
        d = np.sqrt(((P[:, None, :] - C[None, :, :]) ** 2).sum(-1)).min(1)
        miss = int((d > tol).sum())
        res.update(z=round(z, 3), n_expected=len(P), max_err=round(float(d.max()), 4), misses=miss, ok=miss == 0)
        if miss:
            res["worst"] = [dict(point=P[i].round(3).tolist(), err=round(float(d[i]), 3)) for i in np.argsort(-d)[:5]]
    elif typ == "heights_at_xy":
        raw = c.get("expect_file") or c["expect"]
        raw_items = q(spec["_src"], raw) if isinstance(raw, str) and not raw.endswith(".json") else None
        want = _points(raw, item, spec, spec["_root"])
        ids = [r.get("id") if isinstance(r, dict) else None for r in raw_items] if raw_items else [None] * len(want)
        keep = [i for i, pid in enumerate(ids) if pid not in set(c.get("skip_ids", []))]
        want = want[keep]; ids = [ids[i] for i in keep]
        tops = np.full(len(want), np.nan)
        for it in sel:
            V, T = _world(it); P = V[T]                       # (m, 3, 3)
            lo, hi = P[:, :, :2].min(1), P[:, :, :2].max(1)
            for i, (x, y, _) in enumerate(want):
                m = (lo[:, 0] <= x) & (hi[:, 0] >= x) & (lo[:, 1] <= y) & (hi[:, 1] >= y)
                if not m.any():
                    continue
                A, Bv, Cv = P[m, 0], P[m, 1], P[m, 2]
                v0, v1 = Bv[:, :2] - A[:, :2], Cv[:, :2] - A[:, :2]; v2 = np.array([x, y]) - A[:, :2]
                den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
                ok_ = np.abs(den) > 1e-12
                u = np.where(ok_, (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / np.where(ok_, den, 1), -1)
                w = np.where(ok_, (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / np.where(ok_, den, 1), -1)
                inside = ok_ & (u >= -1e-9) & (w >= -1e-9) & (u + w <= 1 + 1e-9)
                if inside.any():
                    zt = (A[:, 2] + u * (Bv[:, 2] - A[:, 2]) + w * (Cv[:, 2] - A[:, 2]))[inside].max()
                    tops[i] = zt if np.isnan(tops[i]) else max(tops[i], zt)
        err = tops - want[:, 2]
        bad = np.isnan(err) | (np.abs(np.nan_to_num(err, nan=9e9)) > tol)
        res.update(n_expected=len(want), n_uncovered=int(np.isnan(err).sum()), misses=int(bad.sum()),
                   max_err=round(float(np.nanmax(np.abs(err))) if np.isfinite(err).any() else -1, 4), ok=not bad.any())
        if bad.any():
            res["worst"] = [dict(id=ids[i], point=want[i].round(3).tolist(), top=None if np.isnan(tops[i]) else round(float(tops[i]), 3))
                            for i in np.argsort(-np.nan_to_num(np.abs(err), nan=9e9))[:8]]
    elif typ == "centres_at_z":
        z = _z(c, item, spec)
        want = _points(c.get("expect_file") or c["expect"], item, spec, spec["_root"])[:, :2]
        S = [LW.cut_segments(*_world(it), 2, z) for it in sel]
        S = [s for s in S if len(s)]
        if not S:
            res.update(msg=f"nothing crosses z={z:.3f}"); return res
        loops = _loops(np.concatenate(S))
        C = np.array([lp.mean(0) for lp in loops])
        dia = np.array([2 * np.sqrt(((lp - lp.mean(0)) ** 2).sum(1)).mean() for lp in loops])
        d = np.sqrt(((want[:, None, :] - C[None, :, :]) ** 2).sum(-1))
        j = d.argmin(1); dmin = d.min(1); miss = int((dmin > tol).sum())
        res.update(z=round(z, 3), n_expected=len(want), n_loops=len(C), max_err=round(float(dmin.max()), 4),
                   mean_err=round(float(dmin.mean()), 4), mean_loop_dia=round(float(dia[j].mean()), 4), misses=miss, ok=miss == 0)
        if c.get("expect_dia") is not None:
            wd = _num(c["expect_dia"], item, spec)
            res["dia_err"] = round(float(dia[j].mean() - wd), 4)
            res["ok"] = res["ok"] and abs(res["dia_err"]) <= float(c.get("dia_tol", 0.05))
        if miss:
            res["worst"] = [dict(point=want[i].round(3).tolist(), err=round(float(dmin[i]), 3)) for i in np.argsort(-dmin)[:5]]
    else:
        res.update(msg=f"unknown check type {typ}")
    res["tol"] = tol
    return res


def run(spec, zones=None, tag="measure"):
    t = time.time()
    items = GIO.load_all(spec)  # always all zones on disk: --zone only selects which checks run (cross-zone matches stay valid)
    results = []
    owner_zone = {z["owner"]: zn for zn, z in spec["zones"].items()}
    for c in spec.get("checks", []):
        if zones and c.get("zone") and c["zone"] not in zones:
            continue
        if zones and not c.get("zone"):  # zone of a check = owner code in its match glob(s): D3_<OWNER>_...
            pats = c["match"] if isinstance(c["match"], list) else [c["match"]]
            cz = {owner_zone.get(p.split("_")[1]) for p in pats if p.count("_") >= 2}
            if cz and not (cz & set(zones)):
                continue
        if c.get("each"):
            lst = q(spec["_src"], c["each"])
            for it in lst:
                if not isinstance(it, dict):
                    continue
                if c.get("where") and num(q(it, c["where"], None)) is None:
                    continue
                if c.get("skip_levels") and str(it.get("level")) in {str(v) for v in c["skip_levels"]}:
                    continue
                results.append(run_check(c, items, spec, it))
        else:
            results.append(run_check(c, items, spec))
    fails = [r for r in results if not r["ok"]]
    skipped = [r for r in results if r.get("skipped")]
    rep = dict(tag=tag, n=len(results), failed=len(fails), skipped=len(skipped), seconds=round(time.time() - t, 1), results=results)
    p = out(spec, "measure", f"{tag}.json")
    with open(p, "w") as f:
        json.dump(rep, f, indent=1)
    print(f"[measure] {tag}: {len(results)} checks, {len(fails)} failed, {len(skipped)} skipped (unknown values) in {rep['seconds']}s -> {os.path.relpath(p, spec['_root'])}")
    for r in fails[:30]:
        detail = r.get("msg") or f"value {r.get('value')} expect {r.get('expect')} err {r.get('err', r.get('max_err'))} tol {r['tol']}"
        print(f"   FAIL {r['id']:<28} {detail}")
    return rep


if __name__ == "__main__":
    spec = load_spec()
    a = sys.argv[1:]
    zones = [a[a.index("--zone") + 1]] if "--zone" in a else None
    tag = a[a.index("--tag") + 1] if "--tag" in a else ("measure_" + zones[0] if zones else "measure")
    rep = run(spec, zones, tag)
    sys.exit(1 if rep["failed"] else 0)
