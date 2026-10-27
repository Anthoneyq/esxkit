"""Generate .esx files from CSV, XLSX, or JSON line-item data.

Accepted input columns (case-insensitive; extra columns ignored):
  group        section/trade name (optional; default 'Estimate')
  code         'CAT|SEL' or 'CAT|SEL|ACT'   (required)
  description  overrides the SUMITEM desc (optional)
  quantity     numeric                     (required)
  unit         SQ, LF, EA, ...             (required)
  unit_price   printed unit price          (optional; omit = unpriced line)
  note         F9-style note text          (optional; carried in JSON, not emitted)

Rules enforced from the format forensics:
  - calc == qty, never qty x price
  - no TOTALS, no UC= price attrs, no xmlns, no SUM_ASSEMBLY
  - SEL charset sanitized; '-A' minimum-suffix artifacts canonicalized
  - no price-list stamp (receiver's Xactimate reprices on import)
"""

import csv
import json
import os
import re

from . import codes as C
from . import container as K
from . import docxml as D

COLUMN_ALIASES = {
    "group": {"group", "section", "room", "trade", "location"},
    "code": {"code", "line code", "sel", "xactimate code", "cat"},
    "description": {"description", "desc", "item"},
    "quantity": {"quantity", "qty"},
    "unit": {"unit", "uom", "u/m"},
    "unit_price": {"unit_price", "unit price", "price", "cost", "unit cost"},
    "note": {"note", "notes", "f9", "comment"},
}


def _norm_header(h):
    return re.sub(r"\s+", " ", (h or "").strip().lower())


def _canon_sel(sel):
    """Canonicalize SEL: strip '-A' minimum artifacts, keep documented charset."""
    sel = re.sub(r"^(.*MN)-A$", r"\1", sel)
    return sel


def load_rows(path):
    """Load rows from CSV, XLSX, or JSON into normalized dicts."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    elif ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = [_norm_header(h) for h in next(rows_iter)]
        rows = [dict(zip(headers, r)) for r in rows_iter]
    elif ext == ".json":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "items" in data:
            # ESXPress fixture style: {items:[{desc,qty,unit,sel,cat}]}
            rows = []
            for it in data["items"]:
                sel = it.get("sel")
                cat = it.get("cat")
                rows.append({
                    "code": f"{cat}|{sel}" if cat and sel else "",
                    "description": it.get("desc", ""),
                    "quantity": it.get("qty", ""),
                    "unit": it.get("unit", ""),
                    "group": it.get("group", ""),
                    "unit_price": it.get("unit_price", ""),
                })
        elif isinstance(data, dict) and "groups" in data:
            rows = []
            for g in data["groups"]:
                for it in g.get("items", []):
                    rows.append({
                        "group": g.get("name", "Estimate"),
                        "code": it.get("code", ""),
                        "description": it.get("desc") or it.get("description", ""),
                        "quantity": it.get("qty", ""),
                        "unit": it.get("unit", ""),
                        "unit_price": it.get("unit_price", ""),
                        "note": it.get("note", ""),
                    })
        else:
            rows = data
    else:
        raise ValueError(f"unsupported input type {ext!r} (use .csv, .xlsx, .json)")

    # normalize headers to canonical names
    out = []
    for row in rows:
        norm = {}
        for k, v in row.items():
            nk = _norm_header(k)
            for canon, aliases in COLUMN_ALIASES.items():
                if nk in aliases:
                    norm[canon] = v
                    break
            else:
                norm[nk] = v
        out.append(norm)
    return out


def rows_to_model(rows, claim=None, profile="8D", default_group="Estimate"):
    """Normalized rows -> docxml model, applying code + charset rules."""
    groups = {}
    order = []
    problems = []
    for n, row in enumerate(rows, start=2):  # assume header on line 1
        code = (row.get("code") or "").strip()
        if not code:
            problems.append(f"row {n}: missing code, skipped")
            continue
        try:
            cat, sel, act = C.parse_code(code)
        except ValueError as e:
            problems.append(f"row {n}: {e}, skipped")
            continue
        sel = _canon_sel(sel)
        sel_issues = C.check_sel(sel)
        for issue in sel_issues:
            problems.append(f"row {n}: {issue}")
        try:
            qty = float(row.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            problems.append(f"row {n}: bad quantity {row.get('quantity')!r}, skipped")
            continue
        unit_price = row.get("unit_price")
        if unit_price in ("", None):
            unit_price = None
        else:
            try:
                unit_price = round(float(unit_price), 2)
            except (TypeError, ValueError):
                problems.append(f"row {n}: bad unit_price {unit_price!r}, treated as unpriced")
                unit_price = None
        gname = (row.get("group") or default_group).strip() or default_group
        if gname not in groups:
            groups[gname] = []
            order.append(gname)
        groups[gname].append({
            "cat": cat, "sel": sel, "act": act,
            "desc": (row.get("description") or "").strip(),
            "unit": (row.get("unit") or "").strip(),
            "qty": qty,
            "unit_price": unit_price,
            "note": (row.get("note") or "").strip(),
        })
    model = D._fresh_model()
    model["profile"] = profile
    model["claim"] = claim or {}
    model["groups"] = [{"name": g, "items": groups[g]} for g in order]
    return model, problems


def create(path_in, path_out, claim=None, profile="8D", photos=None):
    """Build a .esx from CSV/XLSX/JSON. Returns (output_path, problems)."""
    rows = load_rows(path_in)
    model, problems = rows_to_model(rows, claim=claim, profile=profile)
    xml_bytes = D.emit_doc(model)
    K.write_esx(path_out, xml_bytes, photos=photos)
    return path_out, problems
