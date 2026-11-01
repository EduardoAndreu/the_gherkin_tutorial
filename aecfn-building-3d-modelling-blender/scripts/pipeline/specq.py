"""specq — tiny query language over the measured source spec (and any JSON). Pure Python, importable inside Blender.

Path syntax (the same one the markup sidecars use in `spec_key` / `spec_key_query`):
    levels[18].ffl_m                 index
    levels[level=17].ffl_m           first item whose field equals the value (numbers and strings compare loosely: 17 == "17")
    levels[].ffl_m                   map: every item -> list of values (missing/non-numeric kept as None unless numeric=True)
    diagrid.node_centre_z_m[level=18].z_m
    envelope.top.dome_lens_profile_asbuilt[]
    base.plaza.spot_levels[3].z_m
Values that are strings like "unknown" are NOT numbers: `num()` returns None for them, so every consumer must decide
(and log) what to do with an unknown, instead of silently treating it as 0.

CLI:  python3 -m pipeline.specq "levels[level=17].ffl_m"   (uses spec/site.json -> source_spec)
"""
import json, re, sys

_TOK = re.compile(r"([A-Za-z0-9_:\-\. ]+?)(?:\[([^\]]*)\])?(?:\.|$)")


def _parse(path):
    """'a.b[2].c[k=v].d[]' -> [('a',None), ('b','2'), ('c','k=v'), ('d','')]"""
    parts = []
    i = 0
    path = path.strip()
    while i < len(path):
        m = re.match(r"([^.\[\]]+)", path[i:])
        if not m:
            raise ValueError(f"bad path near {path[i:]!r}")
        key = m.group(1); i += m.end()
        sel = None
        while i < len(path) and path[i] == "[":
            j = path.index("]", i)
            parts.append((key, path[i + 1:j])); key = None; i = j + 1
        if key is not None:
            parts.append((key, None))
        if i < len(path) and path[i] == ".":
            i += 1
    return parts


def _loose_eq(a, b):
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _step(obj, key, sel):
    if key is not None:
        if isinstance(obj, dict):
            if key not in obj:
                raise KeyError(key)
            obj = obj[key]
        elif isinstance(obj, list) and key.isdigit():
            obj = obj[int(key)]
        else:
            raise KeyError(key)
    if sel is None:
        return obj, False
    if not isinstance(obj, list):
        raise KeyError(f"[{sel}] on a non-list")
    if sel == "":
        return obj, True                      # map over all
    if "=" in sel:
        k, v = sel.split("=", 1)
        for it in obj:
            if isinstance(it, dict) and k in it and _loose_eq(it[k], v):
                return it, False
        raise KeyError(f"no item with {k}={v}")
    return obj[int(sel)], False


def q(obj, path, default=KeyError):
    """Resolve `path` in `obj`. A `[]` selector maps the rest of the path over the list (one level of fan-out)."""
    try:
        cur = obj
        parts = _parse(path)
        for n, (key, sel) in enumerate(parts):
            cur, fan = _step(cur, key, sel)
            if fan:
                rest = parts[n + 1:]
                out = []
                for it in cur:
                    try:
                        out.append(_walk(it, rest))
                    except KeyError:
                        out.append(None)
                return out
        return cur
    except (KeyError, IndexError, ValueError, TypeError) as e:
        if default is KeyError:
            raise KeyError(f"specq: {path!r}: {e}") from None
        return default


def _walk(cur, parts):
    for key, sel in parts:
        cur, fan = _step(cur, key, sel)
        if fan:
            raise KeyError("nested [] not supported")
    return cur


def num(v):
    """float(v) or None (strings such as 'unknown', None, dicts -> None). bool is not a number."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def fmt(template, item, ctx=None):
    """'FFL_{level}' with item {'level': 17} -> 'FFL_17'. Floats print compact. Missing keys raise."""
    def rep(m):
        k = m.group(1)
        src = item if (isinstance(item, dict) and k in item) else (ctx or {})
        if k not in src:
            raise KeyError(f"fmt: {k!r} not in item for {template!r}")
        v = src[k]
        if isinstance(v, float):
            return f"{v:g}"
        return str(v).replace(" ", "_").replace("/", "-").replace(".", "p") if k != "value" else str(v)
    return re.sub(r"\{([A-Za-z0-9_]+)\}", rep, template)


def expand(expr, item, root):
    """Resolve an expectation written in a rule/check: a number -> itself; '{field}' -> item[field]; any other string
    -> a specq path on the root (relative to the item if it starts with './'). Returns float or None (unknown)."""
    if isinstance(expr, (int, float)) and not isinstance(expr, bool):
        return float(expr)
    if not isinstance(expr, str):
        return None
    s = expr.strip()
    if s.startswith("{") and s.endswith("}") and "." not in s[1:-1] and "[" not in s:
        return num(item.get(s[1:-1])) if isinstance(item, dict) else None
    if s.startswith("./"):
        return num(q(item, s[2:], None))
    try:
        return num(float(s))
    except ValueError:
        return num(q(root, s, None))


if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from pipeline.aep_spec import load_spec
    spec = load_spec()
    for path in sys.argv[1:]:
        try:
            v = q(spec["_src"], path)
        except KeyError as e:
            v = f"<{e}>"
        print(path, "=", json.dumps(v, indent=1)[:4000] if isinstance(v, (dict, list)) else v)
