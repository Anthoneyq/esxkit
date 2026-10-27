"""XACTDOC.XML — parse and emit the estimate document.

Element order (documented real-file root order):
  PROJECT_INFO, GROUP, PARAMS, COMPANY_HEADER, SALESTAXES, ADM, CLAIM_INFO,
  CONTACTS, EMBEDDED_PL, MINIMUMS, UNITS, ITEM_TAGS, COMPONENTS,
  SUPP_EVENTS, AUDIT_DOC, SESSION_STATS, EXT_FILES

Known import-killers enforced by this module:
  - no <TOTALS> section
  - no xmlns on SUMITEM
  - no UC=/unitCost price attributes
  - line items only via EMBEDDED_PL/SUMITEMS/SUMITEM (+ ITEM/SUMMARY_REF
    linkage), never GROUP/SUMMARY/SUMMARY_ITEM
  - SUMMARY_REF calc == qty (calc is NOT qty x price)
  - zero SUM_ASSEMBLY refs (import-safe baseline)

Sections whose exact native attribute sets are not publicly documented
(PARAMS, COMPANY_HEADER, ADM, ...) are emitted as empty elements in the
correct order — pending calibration against a real export.
"""

import datetime
import xml.etree.ElementTree as ET

# Root children in documented real-file order
ROOT_ORDER = [
    "PROJECT_INFO", "GROUP", "PARAMS", "COMPANY_HEADER", "SALESTAXES",
    "ADM", "CLAIM_INFO", "CONTACTS", "EMBEDDED_PL", "MINIMUMS", "UNITS",
    "ITEM_TAGS", "COMPONENTS", "SUPP_EVENTS", "AUDIT_DOC", "SESSION_STATS",
    "EXT_FILES",
]

NEUTRAL_XID = "900001"  # documented neutral identity
ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"

PRICE_LIST_STAMP = None  # never stamp a price list (receiver reprices)


def _stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime(ISO_FMT)


def _fresh_model():
    return {
        "profile": "8D",          # 8D = contractor, 5L = carrier
        "claim": {},
        "groups": [],             # [{name, items:[{...}]}]
    }


# ---------------------------------------------------------------- parsing

def parse_doc(xml_bytes):
    """Parse plaintext XACTDOC.XML into a model dict.

    Returns (model, warnings).
    """
    root = ET.fromstring(xml_bytes)
    if root.tag != "XACTDOC":
        raise ValueError(f"unexpected root element {root.tag!r} (expected XACTDOC)")
    warnings = []
    model = _fresh_model()
    model["profile"] = (root.find("PROJECT_INFO") is not None
                        and (root.find("PROJECT_INFO").get("profile") or "8D")) or "8D"

    ci = root.find("CLAIM_INFO")
    if ci is not None:
        for k, v in ci.attrib.items():
            model["claim"][k] = v

    # SUMITEM index
    sums = {}
    pl = root.find("EMBEDDED_PL")
    if pl is not None:
        for s in pl.iter("SUMITEM"):
            sums[s.get("id", "")] = s

    def sumitem_info(s):
        act_info = {}
        acts = s.find("SUM_ACTIVITIES")
        if acts is not None:
            a = acts.find("SUM_ACTIVITY")
            if a is not None:
                act_info = dict(a.attrib)
        total = 0.0
        for k in ("labBase", "labMarkup", "labBurden", "matCS", "mkt", "equ"):
            try:
                total += float(act_info.get(k, "0") or 0)
            except ValueError:
                pass
        return {
            "cat": s.get("cat", ""), "sel": s.get("sel", ""), "act": s.get("act", ""),
            "desc": s.get("desc", ""), "unit": s.get("unit", ""),
            "catId": s.get("catId"), "itemId": s.get("itemId"),
            "skClass": s.get("skClass"), "skGroup": s.get("skGroup"),
            "activity": act_info, "unit_price": round(total, 2) if total else None,
        }

    # Groups: GROUP/ITEMS/ITEM (documented writer shape); fall back to
    # any GROUP containing ITEM descendants.
    for g in root.findall("GROUP"):
        gname = g.get("name") or g.get("id") or "Group"
        items = []
        for item in g.iter("ITEM"):
            if item.get("type") == "N":      # F9 note item
                continue
            ref = item.find("SUMMARY_REF")
            if ref is None:
                continue
            s = sums.get(ref.get("sumRef", ""))
            info = sumitem_info(s) if s is not None else {}
            unit_price = None
            hist = item.find("HISTORY")
            if hist is not None:
                mod = hist.find("MODIFICATION")
                if mod is not None and mod.get("unitPrice"):
                    try:
                        unit_price = float(mod.get("unitPrice"))
                    except ValueError:
                        pass
            if unit_price is None:
                unit_price = info.get("unit_price")
            items.append({
                "cat": info.get("cat", ""), "sel": info.get("sel", ""),
                "act": info.get("act", ""), "desc": info.get("desc", ""),
                "unit": info.get("unit", ""), "qty": ref.get("qty", ""),
                "unit_price": unit_price,
                "recoverable": ref.get("recoverable", ""),
                "lineNum": ref.get("lineNum", ""),
            })
        if items:
            model["groups"].append({"name": gname, "items": items})

    # Items outside any GROUP (rare) — collect under a catch-all group
    if not model["groups"]:
        fallback = []
        for item in root.iter("ITEM"):
            if item.get("type") == "N":
                continue
            ref = item.find("SUMMARY_REF")
            if ref is None:
                continue
            s = sums.get(ref.get("sumRef", ""))
            info = sumitem_info(s) if s is not None else {}
            fallback.append({
                "cat": info.get("cat", ""), "sel": info.get("sel", ""),
                "act": info.get("act", ""), "desc": info.get("desc", ""),
                "unit": info.get("unit", ""), "qty": ref.get("qty", ""),
                "unit_price": None,
            })
        if fallback:
            model["groups"].append({"name": "Estimate", "items": fallback})
            warnings.append("no GROUP wrappers found; items collected under 'Estimate'")

    # sections we did not model
    modeled = {"XACTDOC", "PROJECT_INFO", "GROUP", "CLAIM_INFO", "EMBEDDED_PL"}
    extra = sorted({c.tag for c in root} - modeled)
    if extra:
        warnings.append("sections parsed but not carried in model: " + ", ".join(extra))
    return model, warnings


# ---------------------------------------------------------------- emission

def _sumitem(idx, it):
    """Build one SUMITEM exactly per the documented real-file shape."""
    s = ET.Element("SUMITEM")
    s.set("id", f"SUM{idx}")
    s.set("cat", it["cat"])
    if it.get("catId"):
        s.set("catId", str(it["catId"]))
    if it.get("itemId"):
        s.set("itemId", str(it["itemId"]))
    s.set("sel", it["sel"])
    if it["act"]:
        s.set("act", it["act"])
    s.set("desc", it.get("desc", ""))
    s.set("unit", it.get("unit", ""))
    s.set("depMax", "100")
    s.set("lifeExpectancy", "20")
    s.set("source", "2")
    s.set("sourceDescription", "General Quote Items")
    s.set("itemAddedSource", "2")
    s.set("coverage", "1")
    if it.get("skClass"):
        s.set("skClass", str(it["skClass"]))
    if it.get("skGroup"):
        s.set("skGroup", str(it["skGroup"]))
    s.set("owner", NEUTRAL_XID)

    if it.get("unit_price") is not None:
        acts = ET.SubElement(s, "SUM_ACTIVITIES")
        a = ET.SubElement(acts, "SUM_ACTIVITY")
        a.set("act", it["act"] or "+")
        price = float(it["unit_price"])
        # Documented rule: install lines carry material; removal lines
        # carry labor/equipment only. Displayed price = sum of components.
        if it["act"] == "-":
            a.set("labBase", f"{price:.2f}")
            a.set("phs", "66")
        else:
            a.set("matCS", f"{price:.2f}")
            a.set("phs", "86")
        mil = ET.SubElement(s, "MIL_ITEM_INFOS")
        ET.SubElement(mil, "MIL_ITEM_INFO")
        ET.SubElement(mil, "MIL_ITEM_INFO")
    else:
        ET.SubElement(s, "SUM_ACTIVITIES")  # empty = Not in Price List until re-linked
    ET.SubElement(s, "HISTORY")
    return s


def _item(idx, sum_idx, it):
    """Build the estimate line ITEM + SUMMARY_REF linkage."""
    now = _stamp()
    i = ET.Element("ITEM")
    i.set("id", f"ITM{idx}")
    i.set("type", "S")
    i.set("dateTimeStamp", now)
    i.set("addedByUser", "esxkit")
    i.set("addedByXID", NEUTRAL_XID)
    i.set("user", "esxkit")
    i.set("xid", NEUTRAL_XID)
    i.set("featureType", "1")
    i.set("owner", NEUTRAL_XID)
    i.set("lastModifiedPriceUser", "esxkit")
    i.set("lastModifiedPriceXID", NEUTRAL_XID)
    i.set("recievedStamp", now)          # sic — documented misspelling
    i.set("lastModifiedSessionID", "1")
    hist = ET.SubElement(i, "HISTORY")
    if it.get("unit_price") is not None:
        mod = ET.SubElement(hist, "MODIFICATION")
        mod.set("type", "1")
        mod.set("user", "esxkit")
        mod.set("xid", NEUTRAL_XID)
        mod.set("unitPrice", f"{float(it['unit_price']):.2f}")
        mod.set("instance", "N")
        mod.set("lastModified", now)
    ref = ET.SubElement(i, "SUMMARY_REF")
    ref.set("sumRef", f"SUM{sum_idx}")
    ref.set("lineNum", str(idx))
    try:
        qty = f"{float(it['qty']):g}"
    except (TypeError, ValueError):
        qty = "0"
    ref.set("calc", qty)                  # calc MUST equal qty — never qty x price
    ref.set("qty", qty)
    ref.set("depType", "D")
    ref.set("recoverable", "1" if it.get("recoverable", "1") not in ("0", "", "false") else "0")
    ET.SubElement(ref, "XPERT_VARS")
    ET.SubElement(ref, "RESTORED_MATERIALS")
    ET.SubElement(i, "CustomStatuses")
    return i


def emit_doc(model):
    """Emit XACTDOC.XML bytes from a model dict.

    Root children are emitted in the documented real-file order:
    PROJECT_INFO, GROUP..., PARAMS, ..., CLAIM_INFO, ..., EMBEDDED_PL, ...
    (SUMITEM data is collected while building GROUPs, then assembled into
    EMBEDDED_PL afterwards — creation order does not affect the references.)
    """
    # First pass: build group elements + SUMITEMs
    group_els = []
    sumitem_els = []
    sum_idx = 0
    itm_idx = 0
    for gi, g in enumerate(model.get("groups", [])):
        ge = ET.Element("GROUP")
        ge.set("id", f"GRP{gi + 1}")
        ge.set("name", g.get("name", "Group"))
        items_el = ET.SubElement(ge, "ITEMS")
        for it in g.get("items", []):
            if not it.get("cat") or not it.get("sel"):
                continue
            sum_idx += 1
            itm_idx += 1
            sumitem_els.append(_sumitem(sum_idx, it))
            items_el.append(_item(itm_idx, sum_idx, it))
        group_els.append(ge)

    # Assemble root in documented order
    root = ET.Element("XACTDOC")

    pi = ET.SubElement(root, "PROJECT_INFO")
    pi.set("profile", model.get("profile", "8D"))

    for ge in group_els:
        root.append(ge)

    for tag in ("PARAMS", "COMPANY_HEADER", "SALESTAXES", "ADM"):
        ET.SubElement(root, tag)

    ci = ET.SubElement(root, "CLAIM_INFO")
    for k, v in (model.get("claim") or {}).items():
        ci.set(k, str(v))

    ET.SubElement(root, "CONTACTS")

    pl = ET.SubElement(root, "EMBEDDED_PL")
    sums = ET.SubElement(pl, "SUMITEMS")
    for s in sumitem_els:
        sums.append(s)

    for tag in ("MINIMUMS", "UNITS", "ITEM_TAGS", "COMPONENTS", "SUPP_EVENTS",
                "AUDIT_DOC", "SESSION_STATS", "EXT_FILES"):
        ET.SubElement(root, tag)

    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
