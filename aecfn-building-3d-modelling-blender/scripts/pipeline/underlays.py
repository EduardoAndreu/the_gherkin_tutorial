"""Underlay drawings: calibrated rasters with a sidecar JSON, placed at true scale (never by eye). Pure Python + numpy.

Sidecar (<name>.json next to <name>.png):
  kind: "plan" | "section"           metres_per_px: float          size_px: [W, H]
  world_origin_px: [u0, v0]          (pixel of the world origin; v measured DOWN from the top)
  plan:    image +x = world +X, image up = world +Y
  section: image +x = r (horizontal coordinate in the section plane), image up = Z
Thin-spec overrides (spec["underlays"][name]): {"z": <m or specq path>, "y": <m>, "azimuth_deg": <deg>, "level": ...}
"""
import glob, json, os
from pipeline.aep_spec import brief_path
from pipeline.specq import num, q


def list_underlays(spec):
    d = brief_path(spec, "underlays")
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(d, "*.json")))


def load(spec, name):
    d = brief_path(spec, "underlays")
    jp = os.path.join(d, name + ".json")
    if not os.path.exists(jp):
        raise SystemExit(f"underlay {name!r}: no sidecar {jp}")
    with open(jp) as f:
        u = json.load(f)
    png = os.path.join(d, u.get("file", name + ".png"))
    if not os.path.exists(png):
        raise SystemExit(f"underlay {name!r}: image {png} missing")
    W, H = u["size_px"]; mpp = float(u["metres_per_px"]); u0, v0 = u["world_origin_px"]
    cfg = (spec.get("underlays") or {}).get(name, {})
    z = cfg.get("z", 0.0)
    if isinstance(z, str):
        zv = num(q(spec["_src"], z, None))
        if zv is None:
            raise SystemExit(f"underlay {name!r}: z {z!r} is not a number in the source spec")
        z = zv
    info = dict(name=name, png=png, kind=u["kind"], mpp=mpp, W=W, H=H, u0=float(u0), v0=float(v0),
                z=float(z), y=float(cfg.get("y", 0.0)), azimuth_deg=cfg.get("azimuth_deg"), era=u.get("era", ""),
                extent=(-u0 * mpp, (W - u0) * mpp, (v0 - H) * mpp, v0 * mpp))  # (a_min, a_max, b_min, b_max) in metres
    return info


def px_to_world(info, u, v):
    """plan: (X, Y); section: (r, Z)"""
    return (u - info["u0"]) * info["mpp"], (info["v0"] - v) * info["mpp"]


def world_to_px(info, a, b):
    return info["u0"] + a / info["mpp"], info["v0"] - b / info["mpp"]
