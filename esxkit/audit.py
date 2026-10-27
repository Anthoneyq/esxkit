"""Supplement-audit engine: detect line-item manipulation patterns.

Rules are grounded in the documented manipulation literature:

  [25] "Xactimate Line Item Manipulation Tactics," InsuranceClaimsInfo.com
       (Leland Coontz III, CA PA #2B53445), published 2026-07-06
  [26] "Xactimate Labor Minimums: The Line Items Carriers Quietly Remove,"
       InsuranceClaimsInfo.com (Leland Coontz III), published 2026-07-06
  [32] IA Solutions (licensed independent adjusters) on O&P stripping

The audit is STRUCTURAL. It reads what the file says and flags patterns
that match documented manipulation tactics. It never invents prices:
dollar figures appear only where the file itself carries prices, plus one
computed potential (O&P = 20% of priced subtotal when 3+ trades are
visible — the industry "three-trade rule", which is a heuristic, not a
rule of law). Everything unpriced is reported as "needs local price ref".

Advisory only — a flag is a question, not a finding.
"""

import io
import re
import xml.etree.ElementTree as ET

from . import container as K
from . import docxml as D

CITE_25 = "[25] Coontz, 'Xactimate Line Item Manipulation Tactics' (CA PA, 2026-07-06)"
CITE_26 = "[26] Coontz, 'Xactimate Labor Minimums' (CA PA, 2026-07-06)"
CITE_OP = "['three-trade rule': 10% O&P when 3+ trades — industry convention, see note 04 sources [25][32]]"

# Trade-recognizing heuristics. cat values here are Xactimate-standard
# prefixes; desc keywords catch specialty work hidden under other cats.
TRADE_HINTS = {
    "ELE": ("electrical", "receptacle", "outlet", "switch", "panel",
            "wire", "light fixture"),
    "PLM": ("plumb", "drain", "water heater", "supply line", "p-trap",
            "shutoff", "faucet supply", "angle stop"),
    "HVC": ("hvac", "furnace", "duct", "air handler", "condenser", "coil"),
}
GEN_CATS = {"GEN", "GENERAL", "GC", "MISC", "SUN", "OTH"}

TRADES_KNOWN = set(  # heuristic trade universe for O&P counting
    {"RFG", "PNT", "SFG", "DRY", "DMO", "INS", "WDW", "WDA", "HVC",
     "ELE", "SDG", "PLM", "FLR", "CLN", "CAB", "CNT", "FRN", "STR"})

CLEAN_RE = re.compile(r"\bclean", re.I)
SMOKE_EVIDENCE_RE = re.compile(r"smoke|soot|fire|char\b|thermal", re.I)
LIGHT_RE = re.compile(r"\blight\b", re.I)
HEAVY_RE = re.compile(r"\bheavy\b", re.I)
PROTECT_RE = re.compile(
    r"\bmask|protect|cover|plastic|drape|drop ?cloth|sanding|taping\b", re.I)
ELECTRICAL_DESC_RE = re.compile(
    r"\belectric|receptacle|outlet|switch|panel\b|\bwire", re.I)
PLUMBING_DESC_RE = re.compile(
    r"\bplumb|drain|water heater|supply line|p-trap|angle stop|shutoff", re.I)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _line_label(it, group):
    code = f"{it['cat']}|{it['sel']}" + (f"|{it['act']}" if it.get("act") else "")
    price = it.get("unit_price")
    return (f"#{it.get('lineNum') or '?'} {code} "
            f"[{group}] qty={it.get('qty')}@{price if price is not None else 'unpriced'} "
            f"— {it.get('desc') or 'no desc'}")


def _line_total(it):
    q, p = _num(it.get("qty")), _num(it.get("unit_price"))
    if q is not None and p is not None:
        return round(q * p, 2)
    return None


def _minimum_evidence(root, items):
    """Minimums present if the MINIMUMS section has content, or minimum
    lines exist as regular items (PNT|MN, ELE|MN, 'labor minimum')."""
    mins = root.find("MINIMUMS") if root is not None else None
    if mins is not None and (len(mins) or mins.attrib):
        return True
    for it in items:
        sel = (it.get("sel") or "").upper()
        if sel == "MN" or sel.endswith("|MN"):
            return True
        if "minimum" in (it.get("desc") or "").lower():
            return True
    return False


def _iter_items(model):
    for g in model.get("groups", []):
        for it in g.get("items", []):
            yield g.get("name", ""), it


def audit(path):
    """Audit one ESX. Returns a report dict (flags, subtotals, potential $)."""
    members = K.read_members(path)
    if not members["ok"]:
        raise ValueError(f"{path}: not a readable ESX container")
    if members["encrypted"] and members["doc_xml"] is None:
        raise ValueError(
            f"{path}: estimate XML is encrypted (native XACTDOC.ZIPXML); "
            "audit needs a plaintext XML export from Xactimate")
    model, warns = D.parse_doc(members["doc_xml"])
    root = ET.fromstring(members["doc_xml"])

    flags = []

    def add(rule, severity, lines, message, citation, impact=None):
        flags.append({
            "rule": rule, "severity": severity, "lines": lines,
            "message": message, "citation": citation, "impact": impact,
        })

    all_items = list(_iter_items(model))
    priced_subtotal = 0.0
    zeroed = []
    unpriced = []
    recoverable_off = []
    trade_cats = set()

    install_codes = {}   # (cat,sel) -> True if any +/& line
    remove_codes = {}    # (cat,sel) -> True if any - line
    clean_lines = []
    paint_lines = []
    protect_lines = []
    smoke_evidence = False
    merged_specialty = []

    for gname, it in all_items:
        cat = (it.get("cat") or "").upper()
        sel = (it.get("sel") or "").upper()
        desc = it.get("desc") or ""
        total = _line_total(it)
        if total is not None:
            priced_subtotal += total
        if _num(it.get("qty")) == 0 and it.get("unit_price") is not None:
            zeroed.append(_line_label(it, gname))
        if _num(it.get("qty")) and it.get("unit_price") is None:
            unpriced.append(_line_label(it, gname))
        if (it.get("recoverable") or "") in ("0", "false") and _num(it.get("qty")):
            recoverable_off.append(_line_label(it, gname))
        if cat in TRADES_KNOWN:
            trade_cats.add(cat)
        if it.get("act") == "-":
            remove_codes[(cat, sel)] = True
        elif it.get("act") in ("+", "&"):
            install_codes[(cat, sel)] = True
        if cat == "PNT":
            paint_lines.append(_line_label(it, gname))
        if cat == "CLN" or CLEAN_RE.search(desc):
            clean_lines.append((desc, _line_label(it, gname)))
        if PROTECT_RE.search(desc):
            protect_lines.append(_line_label(it, gname))
        if SMOKE_EVIDENCE_RE.search(desc) or SMOKE_EVIDENCE_RE.search(gname):
            smoke_evidence = True
        # trade merging: specialty desc under a general/non-matching cat
        hint = None
        if ELECTRICAL_DESC_RE.search(desc) and cat not in ("ELE",):
            hint = "electrical"
        elif PLUMBING_DESC_RE.search(desc) and cat not in ("PLM",):
            hint = "plumbing"
        if hint and (cat in GEN_CATS or cat not in TRADES_KNOWN):
            merged_specialty.append((hint, cat, _line_label(it, gname)))

    # R1 — removal without replacement (remove-only substitution)
    orphan_removes = [
        _line_label(it, gname) for gname, it in all_items
        if it.get("act") == "-"
        and not install_codes.get(((it.get("cat") or "").upper(),
                                   (it.get("sel") or "").upper()))
    ]
    if orphan_removes:
        add("RR-ORPHAN-REMOVE", "high", orphan_removes,
            "Removal line with no matching install/replace line for the same "
            "code. Documented tactic: swap remove-&-replace for remove-only — "
            "the replace work (and its price) disappears. Legitimate if the "
            "scope really is demo-only; verify against scope photos.",
            CITE_25)

    # R2 — labor minimums stripped
    if all_items and not _minimum_evidence(root,
                                           [it for _, it in all_items]):
        n_trades = len(trade_cats)
        add("MIN-STRIPPED", "high", [],
            f"No labor-minimum evidence: MINIMUMS section is empty and no "
            f"minimum lines (e.g. PNT|MN, ELE|MN) exist despite "
            f"{n_trades} trade categories. Xactimate auto-generates per-trade "
            f"minimums; review layers routinely delete them. On small "
            f"multi-trade claims minimums can approach 30–40% of the total.",
            CITE_26)

    # R3 — three-trade O&P potential (computed where prices exist)
    if len(trade_cats) >= 3 and priced_subtotal > 0:
        add("OP-TRADES", "verify", [],
            f"{len(trade_cats)} distinct trade categories visible "
            f"({', '.join(sorted(trade_cats))}). Industry three-trade rule: "
            f"O&P (10% + 10%) typically applies at 3+ trades. Verify O&P is "
            f"applied on the receiving document — carriers routinely strip it.",
            CITE_OP,
            impact={"kind": "potential_oandp", "rate": 0.20,
                    "base": round(priced_subtotal, 2),
                    "amount": round(priced_subtotal * 0.20, 2)})

    # R4 — specialty work merged into general categories
    if merged_specialty:
        lines = [f"({hint}, cat={cat}) {label}"
                 for hint, cat, label in merged_specialty]
        add("TRADE-MERGE", "high", lines,
            "Specialty-trade descriptions carried under a general or "
            "non-matching category. Documented O&P dodge: 'placing specialty "
            "items in general categories' / combining electrical and "
            "plumbing under 'general' reduces the visible trade count. "
            "Heuristic (desc keywords) — confirm against scope.",
            CITE_25)

    # R5 — cleaning omitted or downgraded after fire/smoke
    if smoke_evidence:
        if not clean_lines:
            add("CLEAN-OMITTED", "high", [],
                "Fire/smoke indicators present but no cleaning line anywhere "
                "in the estimate. Documented: 'omitting cleaning entirely can "
                "eliminate thousands of dollars from a fire claim estimate.'",
                CITE_25)
        else:
            light_only = [lab for desc, lab in clean_lines
                          if LIGHT_RE.search(desc) and not HEAVY_RE.search(desc)]
            if light_only:
                add("CLEAN-SUBST", "high", light_only,
                    "Light clean where fire/smoke evidence suggests heavy "
                    "clean. Documented worked example: light-vs-heavy clean "
                    "substitution removed ~$1,200 from a kitchen-fire claim.",
                    CITE_25)

    # R6 — protection/masking omitted while painting present
    if paint_lines and not protect_lines:
        add("PROTECT-OMIT", "medium", paint_lines,
            "Paint lines present and no masking/protection/covering line. "
            "Documented: masking routinely omitted by wrongly claiming it is "
            "'included' — verify against the price list in force.",
            CITE_25)

    # R7 — lines kept but zeroed; unpriced lines
    if zeroed:
        add("QTY-ZERO", "medium", zeroed,
            "Priced line with quantity zeroed. Documented tactic: keep the "
            "line, zero the money. Verify the quantity against measurements.",
            CITE_25)
    if unpriced:
        add("UNPRICED-LINE", "info", unpriced,
            "Quantity present but no unit price (imports 'Not in Price "
            "List'). Expected for esxkit-generated files; on native files "
            "verify repricing on import.", "")

    # R8 — recoverable depreciation flags switched off (native files)
    if recoverable_off:
        add("RECOVERABLE-OFF", "medium", recoverable_off,
            "Recoverable-depreciation flag is off on these lines. Verify "
            "against policy terms — stripping recoverable status reduces "
            "the claimant's settlement.", "['policy-dependent — advisory']")

    potential = sum(f["impact"]["amount"] for f in flags
                    if f.get("impact"))
    report = {
        "file": str(path),
        "claim": model.get("claim", {}),
        "profile": model.get("profile"),
        "groups": [{"name": g.get("name"), "items": len(g.get("items", []))}
                   for g in model.get("groups", [])],
        "line_items": sum(len(g.get("items", [])) for g in model.get("groups", [])),
        "priced_subtotal": round(priced_subtotal, 2),
        "trade_categories": sorted(trade_cats),
        "flags": flags,
        "flag_count": len(flags),
        "potential_impact": round(potential, 2),
        "parser_warnings": warns,
        "disclaimer": ("Structural audit only. Flags are questions for the "
                       "reviewer, not findings. No prices are invented; "
                       "Xactimate reprices on import."),
    }
    return report


# ---------------------------------------------------------------- rendering

SEVERITY_ORDER = {"high": 0, "verify": 1, "medium": 2, "info": 3}


def _cell(text):
    """Escape pipes for GFM table cells."""
    return str(text).replace("|", "\\|")


def to_markdown(report):
    """Render the variance report as a shareable Markdown document."""
    c = report.get("claim", {})
    lines = []
    lines.append("# Estimate Audit — Variance Report")
    lines.append("")
    lines.append(f"**File:** `{report['file']}`  ")
    if c:
        claim_bits = " · ".join(f"{k}: {v}" for k, v in sorted(c.items()))
        lines.append(f"**Claim:** {claim_bits}  ")
    lines.append(f"**Profile:** {report.get('profile')} · "
                 f"**Line items:** {report['line_items']} · "
                 f"**Groups:** {len(report['groups'])}")
    lines.append(f"**Priced subtotal (as written in file):** "
                 f"${report['priced_subtotal']:,.2f}")
    lines.append("")
    n = report["flag_count"]
    if n == 0:
        lines.append("## Result: no structural flags")
        lines.append("")
        lines.append("No documented manipulation patterns matched. This is "
                     "not a scope or price opinion.")
    else:
        pot = report["potential_impact"]
        head = (f"**{n} flag(s)** — potential computed impact "
                f"**${pot:,.2f}**" if pot else f"**{n} flag(s)**")
        lines.append(f"## Result: {head}")
        lines.append("")
        lines.append("| # | Sev | Rule | Lines | What it means | Citation | $ impact |")
        lines.append("|---|-----|------|-------|----------------|----------|----------|")
        for i, f in enumerate(sorted(report["flags"],
                                     key=lambda f: SEVERITY_ORDER.get(f["severity"], 9)),
                              start=1):
            lines_f = f["lines"]
            if len(lines_f) > 1:
                line_cell = f"{len(lines_f)} lines (see detail)"
            elif lines_f:
                line_cell = lines_f[0]
            else:
                line_cell = "—"
            imp = f.get("impact")
            imp_cell = f"${imp['amount']:,.2f}" if imp else "—"
            cite = f["citation"] or "—"
            lines.append(f"| {i} | {f['severity']} | `{f['rule']}` | "
                         f"{_cell(line_cell)} | {_cell(f['message'])} | "
                         f"{_cell(cite)} | {imp_cell} |")
        lines.append("")
        detail = [(f, ln) for f in sorted(report["flags"],
                  key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
                  for ln in f["lines"]]
        if detail:
            lines.append("### Line detail")
            lines.append("")
            for f, ln in detail:
                lines.append(f"- `{f['rule']}` — {ln}")
    lines.append("")
    lines.append("---")
    lines.append(f"*{report['disclaimer']}* ")
    lines.append("*Rules grounded in public manipulation research: L. Coontz III, "
                 "insuranceclaimsinfo.com (Jul 2026). Sources: README.*")
    return "\n".join(lines)
