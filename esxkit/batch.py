"""Batch operations over a folder of .esx files and line-item data."""

import json
import os

from . import container as K
from . import docxml as D
from . import extract as X
from . import generate as G
from . import guard as V


def _esx_files(folder):
    return sorted(
        os.path.join(folder, f) for f in os.listdir(folder)
        if f.lower().endswith(".esx") and os.path.isfile(os.path.join(folder, f))
    )


def validate_folder(folder):
    results = [V.validate(p) for p in _esx_files(folder)]
    ok = sum(1 for r in results if r["ok"])
    return {"folder": folder, "files": len(results), "ok": ok, "results": results}


def folder_to_csv(folder, out_dir):
    """Extract every readable ESX in a folder to CSV+JSON in out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    summary = []
    for path in _esx_files(folder):
        entry = {"file": os.path.basename(path)}
        try:
            written, warns = X.extract(path, out_dir, formats=("csv", "json"))
            entry["written"] = [os.path.basename(w) for w in written]
            if warns:
                entry["warnings"] = warns
        except ValueError as e:
            entry["error"] = str(e)
        summary.append(entry)
    return summary


def merge(files, out_path, prefix_groups=True):
    """Merge multiple plaintext-XML ESX files into one estimate.

    Group names are prefixed with the source claim number (or file stem) so
    line items from different estimates don't collide visually.
    """
    merged = D._fresh_model()
    sources = []
    for path in files:
        members = K.read_members(path)
        if members["doc_xml"] is None:
            raise ValueError(f"{path}: not readable/plaintext (skipping merges of "
                             "encrypted native files)")
        model, _ = D.parse_doc(members["doc_xml"])
        src = (model.get("claim", {}).get("claimNumber")
               or model.get("claim", {}).get("ClaimNumber")
               or os.path.splitext(os.path.basename(path))[0])
        sources.append(src)
        for g in model.get("groups", []):
            name = f"{src} — {g['name']}" if prefix_groups else g["name"]
            merged["groups"].append({"name": name, "items": g["items"]})
    xml_bytes = D.emit_doc(merged)
    K.write_esx(out_path, xml_bytes)
    return {"out": out_path, "sources": sources,
            "groups": [g["name"] for g in merged["groups"]]}


def split(path, out_dir):
    """Split one plaintext ESX into one ESX per group."""
    members = K.read_members(path)
    if members["doc_xml"] is None:
        raise ValueError(f"{path}: estimate XML not plaintext; cannot split")
    model, _ = D.parse_doc(members["doc_xml"])
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    written = []
    for i, g in enumerate(model.get("groups", []), start=1):
        part = D._fresh_model()
        part["profile"] = model.get("profile", "8D")
        part["claim"] = dict(model.get("claim", {}))
        part["groups"] = [g]
        dest = os.path.join(out_dir, f"{stem}.part{i:02d}.esx")
        K.write_esx(dest, D.emit_doc(part))
        written.append(dest)
    return written


def merge_json_inputs(json_paths, out_path):
    """Merge multiple line-item JSON/CSV inputs into one ESX (batch create)."""
    merged = D._fresh_model()
    problems = []
    for path in json_paths:
        rows = G.load_rows(path)
        model, probs = G.rows_to_model(rows)
        problems.extend(f"{os.path.basename(path)}: {p}" for p in probs)
        merged["groups"].extend(model["groups"])
    K.write_esx(out_path, D.emit_doc(merged))
    return {"out": out_path, "problems": problems}
