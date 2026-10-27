"""Import guard — structural checks that predict Xactimate import success.

Guard-pass is necessary but not import-proof; a real Xactimate import is
the only final proof. Checks come from the public format forensics.
"""

import xml.etree.ElementTree as ET
import zipfile

from . import codes as C
from . import container as K

ROOT_ORDER = [
    "PROJECT_INFO", "GROUP", "PARAMS", "COMPANY_HEADER", "SALESTAXES",
    "ADM", "CLAIM_INFO", "CONTACTS", "EMBEDDED_PL", "MINIMUMS", "UNITS",
    "ITEM_TAGS", "COMPONENTS", "SUPP_EVENTS", "AUDIT_DOC", "SESSION_STATS",
    "EXT_FILES",
]


def _check_container(path, errs, warns):
    members = K.read_members(path)
    if not members["ok"]:
        errs.extend(members["errors"])
        return members, None
    cont = members["container"]
    if not cont["outer_stored"]:
        errs.append("outer ZIP must be ZIP_STORED (deflated outer = rejected)")
    if not cont["dos_metadata"]:
        errs.append("ZIP metadata must be DOS/FAT (create_system=0; Unix metadata = rejected)")
    if members["encrypted"]:
        errs.append(
            "native encrypted container (XACTDOC.ZIPXML, AES-256-ECB): estimate XML "
            "cannot be read or validated here; only photos are extractable")
    for name, _size, ctype in members["members"]:
        if name.upper().endswith(".JPG") and ctype != zipfile.ZIP_STORED:
            errs.append(f"photo {name!r} must be ZIP_STORED (raw), not deflated/encrypted")
    return members, members["doc_xml"]


def validate(path):
    """Validate an .esx file. Returns dict {file, ok, errors, warnings, stats}."""
    errs, warns = [], []
    stats = {}
    members, doc_xml = _check_container(path, errs, warns)

    if doc_xml is None:
        return {"file": str(path), "ok": not errs, "errors": errs,
                "warnings": warns, "stats": {}}

    try:
        root = ET.fromstring(doc_xml)
    except ET.ParseError as e:
        errs.append(f"XACTDOC.XML is not parseable XML: {e}")
        return {"file": str(path), "ok": False, "errors": errs,
                "warnings": warns, "stats": {}}

    if root.tag != "XACTDOC":
        errs.append(f"root element is {root.tag!r}, expected XACTDOC")

    # forbidden / rejected patterns
    if root.find("TOTALS") is not None:
        errs.append("<TOTALS> section present — real files never carry it (0/102); rejected")
    for s in root.iter("SUMITEM"):
        if any(k.startswith("{") for k in s.attrib):
            errs.append("SUMITEM carries a namespace (xmlns) attribute — rejected")
            break
    for tag in ("UC", "unitCost", "price"):
        for s in root.iter("SUMITEM"):
            if tag in s.attrib:
                errs.append(f"SUMITEM carries {tag}= price attribute — rejected "
                            "(prices belong in SUM_ACTIVITY / MODIFICATION)")

    # root child order
    seen = [c.tag for c in root]
    idx = [ROOT_ORDER.index(t) if t in ROOT_ORDER else 99 for t in seen]
    if idx != sorted(idx):
        warns.append(f"root child order deviates from documented order: {seen}")

    # line items
    sums = {s.get("id"): s for s in root.iter("SUMITEM")}
    n_items = 0
    n_calc_mismatch = 0
    n_bad_sel = 0
    n_no_price = 0
    n_missing_sk = 0
    for s in sums.values():
        if s.get("sel") and C.check_sel(s.get("sel")):
            n_bad_sel += 1
        acts = s.find("SUM_ACTIVITIES")
        if acts is None or len(list(acts)) == 0:
            n_no_price += 1
        if s.get("skClass") is None:
            n_missing_sk += 1
    for ref in root.iter("SUMMARY_REF"):
        n_items += 1
        try:
            calc, qty = float(ref.get("calc", "0")), float(ref.get("qty", "0"))
        except ValueError:
            n_calc_mismatch += 1
            continue
        if abs(calc - qty) > 1e-9:
            n_calc_mismatch += 1
    for ref in root.iter("SUMMARY_REF"):
        if ref.get("sumRef") not in sums:
            errs.append(f"SUMMARY_REF {ref.get('sumRef')!r} has no matching SUMITEM id")

    if n_calc_mismatch:
        errs.append(f"{n_calc_mismatch} SUMMARY_REF(s) where calc != qty "
                    "(calc must equal qty; Xactimate computes dollars itself)")
    if n_bad_sel:
        warns.append(f"{n_bad_sel} SUMITEM SEL code(s) outside the generated charset "
                     "(sanitize before emission)")
    if n_missing_sk:
        warns.append(f"{n_missing_sk} SUMITEM(s) missing skClass/skGroup "
                     "(price-list re-link may fail for those lines)")
    if n_no_price:
        warns.append(f"{n_no_price} SUMITEM(s) with empty SUM_ACTIVITIES "
                     "(will show 'Not in Price List' until re-linked)")

    stats = {"sumitems": len(sums), "line_items": n_items, "photos": len(members["photos"])}
    return {"file": str(path), "ok": not errs, "errors": errs,
            "warnings": warns, "stats": stats}
