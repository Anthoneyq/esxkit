"""esxkit test suite: container, document round-trip, guard, audit, compare.

Fixtures are built in-memory from CSV rows — no dependence on samples/.
"""

import io
import json
import zipfile

import pytest

from esxkit import container as K
from esxkit import docxml as D
from esxkit import generate as G
from esxkit import audit as A
from esxkit import compare as R

FULL_CSV = """group,code,description,quantity,unit,unit_price
Kitchen,DRY|DW|-,Remove smoke-damaged drywall,28,SQ,41.20
Kitchen,DRY|DW|+,Replace drywall - 1/2 in,28,SQ,58.90
Kitchen,DRY|TX|+,Knockdown texture,28,SQ,35.00
Kitchen,PNT|PRM|+,Prime and paint - walls and ceiling,28,SQ,52.00
Kitchen,PNT|MASK|+,Masking and protection - floors and adjacent surfaces,1,EA,385.00
Kitchen,PNT|MN,Paint labor minimum,1,EA,275.00
Kitchen,FLR|VIN|-,Remove sheet vinyl,15,SQ,28.00
Kitchen,FLR|VIN|+,Replace sheet vinyl,15,SQ,44.00
Kitchen,CAB|BASE|-,Remove base cabinets,12,LF,18.00
Kitchen,CAB|BASE|+,Replace base cabinets,12,LF,310.00
Kitchen,CNT|LAM|&,Remove and replace laminate countertop,12,LF,95.00
Kitchen,ELE|RECP|+,Replace smoke-damaged receptacles and switches,6,EA,85.00
Kitchen,ELE|MN,Electrical labor minimum,1,EA,180.00
Kitchen,PLM|STOP|+,Replace angle stops and supply lines,2,EA,120.00
Kitchen,CLN|HEAVY|+,Heavy smoke cleaning - structure,40,HR,68.00
Kitchen,CLN|CNTM|+,Content manipulation and handling,6,HR,55.00
Kitchen,DMO|DUMP|+,Dumpster haul and disposal,1,EA,595.00
Kitchen,DMO|SFP|+,Air scrubber - soot filtration,3,DA,145.00
"""

GAMED_CSV = """group,code,description,quantity,unit,unit_price
Kitchen,DRY|DW|-,Remove smoke-damaged drywall,28,SQ,41.20
Kitchen,DRY|TX|+,Knockdown texture,0,SQ,35.00
Kitchen,PNT|PRM|+,Prime and paint - walls and ceiling,28,SQ,52.00
Kitchen,FLR|VIN|-,Remove sheet vinyl,15,SQ,28.00
Kitchen,FLR|VIN|+,Replace sheet vinyl,0,SQ,44.00
Kitchen,CAB|BASE|+,Replace base cabinets,12,LF,310.00
Kitchen,CNT|LAM|&,Remove and replace laminate countertop,12,LF,95.00
Kitchen,CLN|SMK|+,Light smoke cleaning - structure,20,HR,52.00
Kitchen,DMO|DUMP|+,Dumpster haul and disposal,1,EA,595.00
General,GEN|RECP|+,Replace receptacles and switches,6,EA,85.00
General,GEN|STOP|+,Replace angle stops and supply lines,2,EA,120.00
"""

CLAIM = {"claimNumber": "TEST-0001", "policyNumber": "POL-1"}


def _csv_file(tmp_path, text, name="items.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _make_esx(tmp_path, csv_text, name="out.esx"):
    csvp = _csv_file(tmp_path, csv_text)
    outp = str(tmp_path / name)
    out, problems = G.create(csvp, outp, claim=CLAIM)
    assert problems == []
    return outp


# ------------------------------------------------------------ container/doc

def test_create_inspect_validate_roundtrip(tmp_path):
    esx = _make_esx(tmp_path, FULL_CSV)
    rep = __import__("esxkit.extract", fromlist=["inspect"]).inspect(esx)
    assert rep["container_ok"] and rep["readable"]
    assert rep["line_items"] == 18
    assert rep["claim"]["claimNumber"] == "TEST-0001"

    from esxkit import guard as V
    v = V.validate(esx)
    assert v["ok"] is True

    from esxkit import extract as X
    written, warns = X.extract(esx, str(tmp_path / "out"))
    csv_out = [w for w in written if w.endswith(".items.csv")][0]
    rows = list(csv.DictReader(open(csv_out, encoding="utf-8")))
    assert len(rows) == 18
    assert rows[0]["code"] == "DRY|DW|-"


import csv  # noqa: E402  (used in roundtrip test)


def test_calc_equals_qty_and_no_price_attrs(tmp_path):
    esx = _make_esx(tmp_path, FULL_CSV)
    members = K.read_members(esx)
    xml = members["doc_xml"].decode()
    assert "<TOTALS" not in xml
    assert "UC=" not in xml
    # every SUMMARY_REF calc == qty
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    for ref in root.iter("SUMMARY_REF"):
        assert ref.get("calc") == ref.get("qty")


def test_sel_canonicalization(tmp_path):
    rows = G.rows_to_model(G.load_rows(
        _csv_file(tmp_path, "group,code,quantity,unit\nG,ELE|MN-A,1,EA\n")))
    item = rows[0]["groups"][0]["items"][0]
    assert item["sel"] == "MN"


def test_encrypted_native_style_rejected_by_audit(tmp_path):
    """A container with XACTDOC.ZIPXML and no plaintext XML cannot be audited."""
    p = str(tmp_path / "native.esx")
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(K._dos_zinfo("XACTDOC.ZIPXML", 64), b"\x04\x04" + b"\x00" * 62)
    with pytest.raises(ValueError):
        A.audit(p)


# ------------------------------------------------------------ audit

def _audit_flags(esx):
    rep = A.audit(esx)
    return rep, {f["rule"] for f in rep["flags"]}


def test_audit_clean_file_only_advisory(tmp_path):
    rep, rules = _audit_flags(_make_esx(tmp_path, FULL_CSV, "clean.esx"))
    high = [f for f in rep["flags"] if f["severity"] == "high"]
    assert high == []                       # no high-severity flags on full scope
    assert "MIN-STRIPPED" not in rules      # minimum lines present
    assert "CLEAN-SUBST" not in rules       # heavy clean present
    assert "PROTECT-OMIT" not in rules      # masking present
    assert rep["priced_subtotal"] == pytest.approx(17064.80, abs=0.01)


def test_audit_gamed_file_catches_all_tactics(tmp_path):
    rep, rules = _audit_flags(_make_esx(tmp_path, GAMED_CSV, "gamed.esx"))
    for rule in ("RR-ORPHAN-REMOVE", "MIN-STRIPPED", "TRADE-MERGE",
                 "CLEAN-SUBST", "PROTECT-OMIT", "QTY-ZERO", "OP-TRADES"):
        assert rule in rules, f"missing {rule}"
    # clean-subst points at the light-clean line specifically
    subst = [f for f in rep["flags"] if f["rule"] == "CLEAN-SUBST"][0]
    assert "CLN|SMK" in subst["lines"][0]
    # O&P potential = 20% of priced subtotal
    op = [f for f in rep["flags"] if f["rule"] == "OP-TRADES"][0]
    assert op["impact"]["amount"] == pytest.approx(rep["priced_subtotal"] * 0.20)


def test_audit_report_flags_line_evidence_and_no_flags_case(tmp_path):
    rep, _ = _audit_flags(_make_esx(tmp_path, FULL_CSV, "clean.esx"))
    md = A.to_markdown(rep)
    assert "Variance Report" in md and "OP-TRADES" in md
    # zero-flag case renders a clean bill
    rep0 = dict(rep, flags=[], flag_count=0, potential_impact=0.0)
    assert "no structural flags" in A.to_markdown(rep0)


# ------------------------------------------------------------ compare

def test_compare_full_vs_gamed_ties_out(tmp_path):
    a = _make_esx(tmp_path, FULL_CSV, "a.esx")
    b = _make_esx(tmp_path, GAMED_CSV, "b.esx")
    rep = R.compare(a, b)
    assert rep["total_reference"] == pytest.approx(17064.80, abs=0.01)
    assert rep["total_received"] == pytest.approx(10274.60, abs=0.01)
    assert rep["delta_total"] == pytest.approx(-6790.20, abs=0.01)
    assert rep["accounting"]["tied_out"] is True
    assert rep["stats"]["missing"] == 7
    assert rep["stats"]["substituted"] == 3
    # zeroed lines are the changed pair
    changed_codes = {f["code"] for f in rep["changed_lines"]}
    assert changed_codes == {"DRY|TX|+", "FLR|VIN|+"}


def test_compare_catches_trade_merge_substitution(tmp_path):
    a = _make_esx(tmp_path, FULL_CSV, "a.esx")
    b = _make_esx(tmp_path, GAMED_CSV, "b.esx")
    rep = R.compare(a, b)
    subs = {f["code_a"]: f["code_b"] for f in rep["substituted_lines"]}
    assert subs["ELE|RECP|+"] == "GEN|RECP|+"     # recategorized, same dollars
    assert subs["CLN|HEAVY|+"] == "CLN|SMK|+"     # grade substitution, dollars move
    # recategorization alone moves no money — the dodge is structural
    elec = [f for f in rep["substituted_lines"] if f["code_a"] == "ELE|RECP|+"][0]
    assert elec["delta_value"] == 0.0


def test_compare_identical_files_tie_out_zero(tmp_path):
    a = _make_esx(tmp_path, FULL_CSV, "a.esx")
    rep = R.compare(a, a)
    assert rep["delta_total"] == 0.0
    assert rep["stats"] == {"matched": 18, "changed": 0, "substituted": 0,
                            "missing": 0, "added": 0}
    assert rep["accounting"]["tied_out"] is True


def test_compare_markdown_renders(tmp_path):
    a = _make_esx(tmp_path, FULL_CSV, "a.esx")
    b = _make_esx(tmp_path, GAMED_CSV, "b.esx")
    md = R.to_markdown(R.compare(a, b))
    assert "Reconciliation" in md and "tied out" in md
