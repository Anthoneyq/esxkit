"""Extract ESX contents: line items to CSV/XLSX, full doc to JSON, photos out."""

import csv
import json
import os

from . import container as K
from . import docxml as D

CSV_COLUMNS = ["group", "lineNum", "code", "desc", "qty", "unit",
               "unit_price", "recoverable"]


def _fmt_code(it):
    cat, sel, act = it.get("cat", ""), it.get("sel", ""), it.get("act", "")
    if not cat and not sel:
        return ""
    return C_fmt(cat, sel, act)


def C_fmt(cat, sel, act):
    return f"{cat}|{sel}|{act}" if act else f"{cat}|{sel}"


def items_rows(model):
    rows = []
    for g in model.get("groups", []):
        for it in g.get("items", []):
            rows.append({
                "group": g.get("name", ""),
                "lineNum": it.get("lineNum", ""),
                "code": _fmt_code(it),
                "desc": it.get("desc", ""),
                "qty": it.get("qty", ""),
                "unit": it.get("unit", ""),
                "unit_price": it.get("unit_price", ""),
                "recoverable": it.get("recoverable", ""),
            })
    return rows


def extract(path, out_dir, formats=("csv", "json"), photos=False):
    """Extract a plaintext-XML ESX into out_dir. Returns (written, warnings)."""
    members = K.read_members(path)
    if not members["ok"]:
        raise ValueError(f"{path}: not a readable ESX container")
    warnings = []
    if members["encrypted"] and members["doc_xml"] is None:
        raise ValueError(
            f"{path}: estimate XML is encrypted (native XACTDOC.ZIPXML). "
            "Only photos are extractable from native encrypted files — "
            "re-export data via Xactimate's export/XML paths instead.")

    os.makedirs(out_dir, exist_ok=True)
    model, warns = D.parse_doc(members["doc_xml"])
    warnings.extend(warns)
    stem = os.path.splitext(os.path.basename(path))[0]
    written = []

    if "json" in formats:
        dest = os.path.join(out_dir, f"{stem}.doc.json")
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(model, fh, indent=2, ensure_ascii=False)
        written.append(dest)

    rows = items_rows(model)
    if "csv" in formats:
        dest = os.path.join(out_dir, f"{stem}.items.csv")
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        written.append(dest)

    if "xlsx" in formats:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Line items"
        ws.append(CSV_COLUMNS)
        for r in rows:
            ws.append([r[c] for c in CSV_COLUMNS])
        dest = os.path.join(out_dir, f"{stem}.items.xlsx")
        wb.save(dest)
        written.append(dest)

    if photos and members["photos"]:
        written.extend(K.extract_photos(path, os.path.join(out_dir, "photos")))
    return written, warnings


def inspect(path):
    """Container + document report for one file."""
    members = K.read_members(path)
    report = {
        "file": str(path),
        "container_ok": members["ok"],
        "outer_stored": members["container"].get("outer_stored"),
        "dos_metadata": members["container"].get("dos_metadata"),
        "members": [{"name": n, "size": s,
                     "stored": c == 0} for (n, s, c) in members["members"]],
        "photos": [n for (n, _) in members["photos"]],
        "encrypted_estimate": members["encrypted"],
        "errors": members["errors"],
    }
    if members["doc_xml"] is not None:
        try:
            model, warns = D.parse_doc(members["doc_xml"])
            report["readable"] = True
            report["warnings"] = warns
            report["profile"] = model.get("profile")
            report["claim"] = model.get("claim")
            n_items = sum(len(g["items"]) for g in model["groups"])
            report["groups"] = [{"name": g["name"], "items": len(g["items"])}
                                for g in model["groups"]]
            report["line_items"] = n_items
        except Exception as e:  # noqa: BLE001 — report everything
            report["readable"] = False
            report["parse_error"] = str(e)
    elif members["encrypted"]:
        report["readable"] = False
        report["note"] = ("estimate XML is AES-encrypted in native files; "
                          "photos remain extractable")
    return report
