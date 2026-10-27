"""Xactimate code triples and SEL charset rules.

A line item's identity is `cat|sel` with an optional activity:
    RFG|300S      laminated shingle (base)
    RFG|300S|+    install
    RFG|300S|-    removal (labor/equipment only)
    RFG|FLCH|&    remove & replace
"""

import re

# Documented SEL charset for generated files, after entity unescaping.
# (Native files may carry < and > inside SEL for size-tier families;
# generated files must not.)
SEL_RE = re.compile(r"^[A-Za-z0-9#+./-]+$")

# Activity codes. '&' is a legitimate activity (remove & replace).
ACT_RE = re.compile(r"^[+\-&]?$")

CATEGORIES_KNOWN = {
    "RFG": "Roofing", "PNT": "Painting", "SFG": "Soffit/Fascia/Gutter",
    "DRY": "Drywall", "DMO": "Demo", "INS": "Insulation", "WDW": "Windows",
    "WDA": "Doors/Accessories", "HVC": "HVAC", "ELE": "Electrical",
    "SDG": "Siding",
}


def parse_code(code):
    """Parse 'CAT|SEL' or 'CAT|SEL|ACT' into (cat, sel, act).

    Accepts codes typed with real pipes. Returns (cat, sel, act) where act
    may be '' (base code). Raises ValueError on malformed input.
    """
    if not code or not isinstance(code, str):
        raise ValueError(f"empty code: {code!r}")
    parts = code.strip().split("|")
    if len(parts) == 2:
        cat, sel, act = parts[0].strip(), parts[1].strip(), ""
    elif len(parts) == 3:
        cat, sel, act = (p.strip() for p in parts)
    else:
        raise ValueError(f"code must be CAT|SEL or CAT|SEL|ACT, got {code!r}")
    if not cat or not sel:
        raise ValueError(f"code missing cat or sel: {code!r}")
    if not ACT_RE.match(act):
        raise ValueError(f"bad activity {act!r} in code {code!r} (allowed: + - & or none)")
    return cat, sel, act


def format_code(cat, sel, act=""):
    return f"{cat}|{sel}|{act}" if act else f"{cat}|{sel}"


def check_sel(sel):
    """Return a list of problems with a SEL code (empty list = clean)."""
    problems = []
    if not SEL_RE.match(sel):
        problems.append(
            f"SEL {sel!r} outside generated charset ^[A-Za-z0-9#+./-]+$ "
            "(native size-tier codes with < > must be sanitized for generated files)"
        )
    # Canonicalize -A suffix artifact (e.g. ELE|MN-A is not a real catalog code)
    if re.search(r"MN-A$", sel):
        problems.append(f"SEL {sel!r} carries a '-A' minimum-suffix artifact "
                        "(should be canonicalized, e.g. ELE|MN-A -> ELE|MN)")
    return problems
