"""Edit an existing (plaintext) ESX: drop lines, change unit prices.

This is the manual-modification workflow: take an estimate, remove lines
that shouldn't be there, adjust prices, emit the updated ESX. Selectors:

  #7            the 7th line in flattened order (same order as extract CSV)
  RFG|300S|+    every line with exactly that code
  RFG|300S      every line with that CAT|SEL regardless of activity

Prices: --price '#7=250.00' or --price 'RFG|300S+=250.00'
"""

from . import container as K
from . import docxml as D
from . import extract as X


def load_model(path):
    members = K.read_members(path)
    if members["doc_xml"] is None:
        raise ValueError(
            f"{path}: estimate XML is not plaintext"
            + (" (native encrypted container — edit via XML export instead)"
               if members["encrypted"] else ""))
    return D.parse_doc(members["doc_xml"])


def _flatten(model):
    """Flatten groups into [(group_name, index, item), ...] (1-based)."""
    flat = []
    n = 0
    for g in model["groups"]:
        for it in g["items"]:
            n += 1
            flat.append((g["name"], n, it))
    return flat


def _code_of(it):
    return X.C_fmt(it.get("cat", ""), it.get("sel", ""), it.get("act", ""))


def _matches(sel, it, gname, idx):
    if sel.startswith("#"):
        try:
            return idx == int(sel[1:])
        except ValueError:
            return False
    return _code_of(it) == sel or _code_of(it).split("|")[0:2] == sel.split("|")[0:2]


def list_lines(path):
    model, _ = load_model(path)
    return [
        {"#": idx, "group": gname, "code": _code_of(it), "desc": it.get("desc", ""),
         "qty": it.get("qty", ""), "unit": it.get("unit", ""),
         "unit_price": it.get("unit_price", "")}
        for (gname, idx, it) in _flatten(model)
    ]


def edit(path, out_path, drop=(), price=()):
    """Apply edits and write the updated ESX. Returns (out, changes, dropped_value)."""
    model, _ = load_model(path)
    flat = _flatten(model)
    changes = []
    dropped_value = 0.0

    if drop:
        keep = []
        for (gname, idx, it) in flat:
            if any(_matches(s, it, gname, idx) for s in drop):
                if it.get("unit_price") is not None:
                    try:
                        dropped_value += float(it["qty"]) * float(it["unit_price"])
                    except (TypeError, ValueError):
                        pass
                changes.append({"action": "drop", "line": idx,
                                "code": _code_of(it), "group": gname})
            else:
                keep.append((gname, idx, it))
        regroup = {}
        for (gname, _idx, it) in keep:
            regroup.setdefault(gname, []).append(it)
        model["groups"] = [{"name": g, "items": items} for g, items in regroup.items()]

    if price:
        for assignment in price:
            sel, _, value = assignment.rpartition("=")
            if not sel:
                raise ValueError(f"--price needs SELECTOR=VALUE, got {assignment!r}")
            try:
                value = round(float(value), 2)
            except ValueError:
                raise ValueError(f"--price value must be a number, got {value!r}")
            hits = 0
            # selectors always resolve against the ORIGINAL flattened listing
            for (gname, idx, it) in flat:
                if _matches(sel, it, gname, idx):
                    it["unit_price"] = value
                    hits += 1
                    changes.append({"action": "price", "code": _code_of(it),
                                    "group": gname, "new_price": value})
            if hits == 0:
                changes.append({"action": "price", "selector": sel, "result": "no match"})

    K.write_esx(out_path, D.emit_doc(model))
    return out_path, changes, round(dropped_value, 2)


def preview_totals(path):
    """A rough display-only sum of qty x unit_price. Xactimate computes its own
    totals from its price list — this is a sanity preview, not the official total."""
    model, _ = load_model(path)
    total = 0.0
    unpriced = 0
    for g in model["groups"]:
        for it in g["items"]:
            if it.get("unit_price") is None:
                unpriced += 1
                continue
            try:
                total += float(it["qty"]) * float(it["unit_price"])
            except (TypeError, ValueError):
                pass
    return {"preview_total": round(total, 2), "unpriced_lines": unpriced}
