"""Estimate reconciliation: two estimates in, variance out.

Compares a reference estimate (A — full scope, independent, or prior
version) against a received estimate (B — carrier scope, revised version)
and accounts for every priced dollar difference, line by line:

  - matched lines with quantity or price changes (delta $ computed)
  - lines present in A and missing from B (the supplement case)
  - lines present only in B (additions / substitutions)
  - code substitutions matched by description similarity (catches
    grade/selector swaps AND trade-merge recategorization)

Matching rules:
  1. Exact code key (cat, sel, act), quantities aggregated per key.
  2. Unmatched leftovers: best description-token similarity >= 0.5,
     any category (category changes are exactly what TRADE-MERGE does).

Dollar rule: only prices the files themselves carry. No invented prices.
"""

import os
import re

from . import container as K
from . import docxml as D
from . import extract as X

FUZZY_MIN = 0.5
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _load_model(path):
    members = K.read_members(path)
    if members["doc_xml"] is None:
        raise ValueError(
            f"{path}: estimate XML is not plaintext"
            + (" (native encrypted container)" if members["encrypted"] else ""))
    return D.parse_doc(members["doc_xml"])


def _tokens(text):
    return set(_TOKEN_RE.findall((text or "").lower()))


def _sim(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _code_key(it):
    return (it.get("cat", "").upper(), it.get("sel", "").upper(),
            it.get("act", "").upper())


def _code_str(it):
    return f"{it['cat']}|{it['sel']}" + (f"|{it['act']}" if it.get("act") else "")


def _index_by_code(model):
    """code_key -> {qty_sum, value_sum, prices:set, labels:[(gname,label)], descs:set}"""
    idx = {}
    for g in model.get("groups", []):
        gname = g.get("name", "")
        for it in g.get("items", []):
            k = _code_key(it)
            q = _num(it.get("qty")) or 0.0
            p = _num(it.get("unit_price"))
            value = round(q * p, 2) if p is not None else None
            rec = idx.setdefault(k, {"qty": 0.0, "value": 0.0, "has_value": False,
                                     "prices": set(), "labels": [], "descs": set()})
            rec["qty"] += q
            if value is not None:
                rec["value"] = round(rec["value"] + value, 2)
                rec["has_value"] = True
                rec["prices"].add(p)
            rec["labels"].append((gname, _label(it, gname)))
            if it.get("desc"):
                rec["descs"].add(it["desc"])
    return idx


def _label(it, gname):
    return (f"#{it.get('lineNum') or '?'} {_code_str(it)} [{gname}] "
            f"qty={it.get('qty')}@{it.get('unit_price') if it.get('unit_price') is not None else 'unpriced'} "
            f"— {it.get('desc') or 'no desc'}")


def _flat_desc(model):
    """All (gname, it) pairs for fuzzy matching on raw items."""
    out = []
    for g in model.get("groups", []):
        for it in g.get("items", []):
            out.append((g.get("name", ""), it))
    return out


def compare(path_a, path_b):
    """Reconcile A (reference) vs B (received). Returns report dict."""
    model_a, warns_a = _load_model(path_a)
    model_b, warns_b = _load_model(path_b)

    ia, ib = _index_by_code(model_a), _index_by_code(model_b)
    matched, missing, added = [], [], []

    for k in sorted(set(ia) & set(ib)):
        ra, rb = ia[k], ib[k]
        dq = round(rb["qty"] - ra["qty"], 4)
        dv = round(rb["value"] - ra["value"], 2) if (ra["has_value"] and rb["has_value"]) else None
        price_delta = None
        if ra["prices"] and rb["prices"]:
            pa, pb = min(ra["prices"]), min(rb["prices"])
            if abs(pa - pb) > 1e-9:
                price_delta = (pa, pb)
        dv_zero = dv is None or abs(dv) < 0.005
        if abs(dq) < 1e-9 and dv_zero and price_delta is None:
            kind = "same"
        elif abs(dq) > 1e-9 and price_delta is None:
            kind = "qty"
        elif abs(dq) < 1e-9:
            kind = "price"
        else:
            kind = "qty+price"
        if kind == "same":
            matched.append({"kind": kind, "code": "|".join(k).rstrip("|"),
                            "qty_a": ra["qty"], "qty_b": rb["qty"],
                            "delta_value": 0.0, "labels_a": ra["labels"],
                            "labels_b": rb["labels"]})
        else:
            matched.append({"kind": kind, "code": "|".join(k).rstrip("|"),
                            "qty_a": ra["qty"], "qty_b": rb["qty"],
                            "value_a": ra["value"] if ra["has_value"] else None,
                            "value_b": rb["value"] if rb["has_value"] else None,
                            "price_a": price_delta[0] if price_delta else None,
                            "price_b": price_delta[1] if price_delta else None,
                            "delta_value": dv, "labels_a": ra["labels"],
                            "labels_b": rb["labels"]})

    keys_a = set(ia) - set(ib)
    keys_b = set(ib) - set(ia)

    # fuzzy pass: match leftover codes by description similarity
    fuzzy_pairs = []
    used_a, used_b = set(), set()
    for ka in sorted(keys_a):
        ra = ia[ka]
        best, best_score = None, 0.0
        for kb in sorted(keys_b - used_b):
            rb = ib[kb]
            score = max((_sim(da, db) for da in ra["descs"] for db in rb["descs"]),
                        default=0.0)
            if score > best_score:
                best, best_score = kb, score
        if best is not None and best_score >= FUZZY_MIN:
            rb = ib[best]
            dv = round(rb["value"] - ra["value"], 2) if (ra["has_value"] and rb["has_value"]) else None
            fuzzy_pairs.append({
                "code_a": "|".join(ka).rstrip("|"), "code_b": "|".join(best).rstrip("|"),
                "similarity": round(best_score, 2),
                "qty_a": ra["qty"], "qty_b": rb["qty"],
                "value_a": ra["value"] if ra["has_value"] else None,
                "value_b": rb["value"] if rb["has_value"] else None,
                "delta_value": dv, "labels_a": ra["labels"], "labels_b": rb["labels"]})
            used_a.add(ka)
            used_b.add(best)
    keys_a -= used_a
    keys_b -= used_b

    for ka in sorted(keys_a):
        ra = ia[ka]
        missing.append({"code": "|".join(ka).rstrip("|"),
                        "value": ra["value"] if ra["has_value"] else None,
                        "qty": ra["qty"], "labels": ra["labels"]})
    for kb in sorted(keys_b):
        rb = ib[kb]
        added.append({"code": "|".join(kb).rstrip("|"),
                      "value": rb["value"] if rb["has_value"] else None,
                      "qty": rb["qty"], "labels": rb["labels"]})

    total_a = round(sum(r["value"] for r in ia.values() if r["has_value"]), 2)
    total_b = round(sum(r["value"] for r in ib.values() if r["has_value"]), 2)
    changed_money = round(sum(f["delta_value"] or 0.0 for f in matched
                              if f["kind"] != "same"), 2)
    fuzzy_money = round(sum(f["delta_value"] or 0.0 for f in fuzzy_pairs), 2)
    missing_money = round(sum(m["value"] or 0.0 for m in missing), 2)
    added_money = round(sum(a["value"] or 0.0 for a in added), 2)
    tied_out = (round(changed_money + fuzzy_money - missing_money + added_money, 2)
                == round(total_b - total_a, 2))

    return {
        "reference": str(path_a), "received": str(path_b),
        "claim": model_a.get("claim", {}),
        "total_reference": total_a, "total_received": total_b,
        "delta_total": round(total_b - total_a, 2),
        "stats": {"matched": sum(1 for f in matched if f["kind"] == "same"),
                  "changed": sum(1 for f in matched if f["kind"] != "same"),
                  "substituted": len(fuzzy_pairs),
                  "missing": len(missing), "added": len(added)},
        "changed_lines": [f for f in matched if f["kind"] != "same"],
        "substituted_lines": fuzzy_pairs,
        "missing_lines": missing,
        "added_lines": added,
        "accounting": {"changed_money": changed_money,
                       "substituted_money": fuzzy_money,
                       "missing_money": missing_money,
                       "added_money": added_money,
                       "tied_out": tied_out},
        "parser_warnings": warns_a + warns_b,
        "disclaimer": ("Reconciliation compares what each file says. Prices "
                       "are as written in the files; Xactimate reprices on "
                       "import. This is an accounting aid, not a scope or "
                       "price opinion."),
    }


# ---------------------------------------------------------------- rendering

def to_markdown(rep):
    lines = []
    lines.append("# Estimate Reconciliation — Variance Report")
    lines.append("")
    lines.append(f"**Reference (A):** `{rep['reference']}`  ")
    lines.append(f"**Received (B):** `{rep['received']}`  ")
    if rep.get("claim"):
        claim_bits = " · ".join(f"{k}: {v}" for k, v in sorted(rep["claim"].items()))
        lines.append(f"**Claim:** {claim_bits}  ")
    lines.append("")
    lines.append(f"| | Priced subtotal |")
    lines.append(f"|---|---|")
    lines.append(f"| A — reference | {money(rep['total_reference'])} |")
    lines.append(f"| B — received | {money(rep['total_received'])} |")
    lines.append(f"| **Delta (B − A)** | **{money(rep['delta_total'])}** |")
    lines.append("")
    s = rep["stats"]
    lines.append(f"Matched as-written: **{s['matched']}** · changed: **{s['changed']}** · "
                 f"code-substituted: **{s['substituted']}** · "
                 f"missing from B: **{s['missing']}** · added in B: **{s['added']}**")
    lines.append("")

    if rep["missing_lines"]:
        lines.append("## Lines missing from the received estimate (B)")
        lines.append("")
        lines.append("| Code | Qty | Value | Evidence (A) |")
        lines.append("|------|-----|-------|--------------|")
        for m in rep["missing_lines"]:
            first = m["labels"][0][1] if m["labels"] else "—"
            more = f" (+{len(m['labels']) - 1} more)" if len(m["labels"]) > 1 else ""
            lines.append(f"| `{m['code']}` | {m['qty']} | {money(m['value'])} | {esc(first)}{more} |")
        lines.append("")
        lines.append(f"Missing value: **{money(rep['accounting']['missing_money'])}**")
        lines.append("")

    if rep["changed_lines"]:
        lines.append("## Lines changed between A and B")
        lines.append("")
        lines.append("| Code | Qty A→B | Value A→B | Δ$ |")
        lines.append("|------|---------|-----------|----|")
        for f in rep["changed_lines"]:
            lines.append(f"| `{f['code']}` | {f['qty_a']}→{f['qty_b']} | "
                         f"{money(f.get('value_a'))}→{money(f.get('value_b'))} | "
                         f"{money(f['delta_value']) if f['delta_value'] is not None else '—'} |")
        lines.append("")
        lines.append(f"Changed-line money: **{money(rep['accounting']['changed_money'])}**")
        lines.append("")

    if rep["substituted_lines"]:
        lines.append("## Code substitutions (matched by description)")
        lines.append("")
        lines.append("| A code | B code | Sim | Qty A→B | Δ$ |")
        lines.append("|--------|--------|-----|---------|----|")
        for f in rep["substituted_lines"]:
            lines.append(f"| `{f['code_a']}` | `{f['code_b']}` | {f['similarity']} | "
                         f"{f['qty_a']}→{f['qty_b']} | "
                         f"{money(f['delta_value']) if f['delta_value'] is not None else '—'} |")
        lines.append("")
        lines.append("Category changes in this table are the trade-merge O&P "
                     "dodge; grade changes are the builder-vs-premium dodge. "
                     f"Substitution money: **{money(rep['accounting']['substituted_money'])}**")
        lines.append("")

    if rep["added_lines"]:
        lines.append("## Lines only in the received estimate (B)")
        lines.append("")
        lines.append("| Code | Qty | Value | Evidence (B) |")
        lines.append("|------|-----|-------|--------------|")
        for a in rep["added_lines"]:
            first = a["labels"][0][1] if a["labels"] else "—"
            lines.append(f"| `{a['code']}` | {a['qty']} | {money(a['value'])} | {esc(first)} |")
        lines.append("")
        lines.append(f"Added money: **{money(rep['accounting']['added_money'])}**")
        lines.append("")

    acc = rep["accounting"]
    lines.append("## Accounting")
    lines.append("")
    lines.append(f"changed {money(acc['changed_money'])} + substituted "
                 f"{money(acc['substituted_money'])} − missing "
                 f"{money(acc['missing_money'])} + added "
                 f"{money(acc['added_money'])} = "
                 f"{money(round(acc['changed_money'] + acc['substituted_money'] - acc['missing_money'] + acc['added_money'], 2))} "
                 f"vs delta {money(rep['delta_total'])} — "
                 + ("**tied out ✓**" if acc["tied_out"] else "**MISMATCH — review**"))
    lines.append("")
    lines.append("---")
    lines.append(f"*{rep['disclaimer']}*")
    lines.append("*Free and open (esxkit). Every competitor charges for "
                 "this class of analysis; run it locally on claim "
                 "documents and share only with parties who need them.*")
    return "\n".join(lines)


def money(v):
    if v is None:
        return "unpriced"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def esc(text):
    return str(text).replace("|", "\\|")
