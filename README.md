# esxkit

Toolkit for **Xactimate `.esx` estimate files** — read, create, convert, validate,
and batch-process from Python on macOS, no Xactimate installation required.

```
python3 -m esxkit <command>      (run from this directory)
```

## What an .esx is

Xactimate's native project format: one self-contained ZIP file holding a whole
insurance estimate — folder tree, line items (`CAT|SEL` codes), quantities,
unit prices, photos, and sketch data. Xactimate reprices imported estimates
against the price list loaded in the receiving copy, so totals can shift on
import by design.

The format is proprietary, but its container and XML internals have been
publicly reverse-engineered (ESXPress AI-Knowledge corpus, CC BY 4.0,
verified against 100+ real exports and confirmed imports). esxkit implements
that knowledge.

## Commands

| Command | What it does |
|---|---|
| `inspect FILE` | Container + document report: members, photos, groups, line-item counts |
| `validate FILE` | Import-guard: checks every documented import-killer before you send the file |
| `create IN.csv -o OUT.esx` | Build an ESX from CSV / XLSX / JSON line items |
| `extract FILE -o DIR` | ESX → `items.csv` / `items.xlsx` / `doc.json` (+ `--photos`) |
| `audit FILE` | Supplement-audit: flag documented line-item manipulation patterns (`--format md` = shareable variance report) |
| `compare A.esx B.esx` | Reconcile two estimates: matched / changed / substituted / missing lines, every dollar tied out (`--format md`) |
| `batch validate DIR` | Validate every .esx in a folder |
| `batch tocsv DIR -o OUT` | Extract every readable ESX in a folder |
| `batch merge A.esx B.esx -o M.esx` | Merge estimates (groups prefixed by claim number) |
| `batch split FILE -o DIR` | One ESX per group |

## The audit (`audit FILE`)

Structural supplement-audit over parsed line items + raw XML. Grounded in the
documented manipulation literature (Leland Coontz III, licensed CA PA):
- "Xactimate Line Item Manipulation Tactics" (2026-07-06)
  https://insuranceclaimsinfo.com/resources/xactimate-line-item-manipulation
- "Xactimate Labor Minimums: The Line Items Carriers Quietly Remove" (2026-07-06)
  https://insuranceclaimsinfo.com/resources/xactimate-labor-minimums

| Rule | Catches | Severity |
|---|---|---|
| `RR-ORPHAN-REMOVE` | removal line with no matching install/replace for the same code (remove-only substituted for R&R) | high |
| `MIN-STRIPPED` | empty `MINIMUMS` section and no minimum lines despite multiple trades | high |
| `TRADE-MERGE` | specialty-trade descriptions (electrical/plumbing keywords) under general/non-matching categories — the three-trade O&P dodge | high |
| `CLEAN-OMITTED` | fire/smoke evidence with no cleaning line anywhere | high |
| `CLEAN-SUBST` | light clean where smoke evidence suggests heavy clean (documented delta ~$1,200 in the worked example) | high |
| `OP-TRADES` | 3+ visible trades → computes potential O&P = 20% of priced subtotal for verification | verify |
| `PROTECT-OMIT` | paint present, no masking/protection/covering line ("wrongly included") | medium |
| `QTY-ZERO` | priced line with quantity zeroed (kept the line, removed the money) | medium |
| `UNPRICED-LINE` | qty present, no unit price (normal for generated files; repricing check on native files) | info |
| `RECOVERABLE-OFF` | recoverable-depreciation flag switched off (native files) | medium |

Output: JSON (default) or `--format md` — a variance report with per-line
evidence and citations behind every flag. Optional `--out FILE` writes it.

**Honesty rules:** the audit never invents prices. Dollar figures appear only
where the file carries prices (plus the one computed O&P potential). Flags are
questions for the reviewer, not findings. Handles claim documents — audit
locally, share reports only with parties who need them.

## Reconciliation (`compare A B`)

Two estimates in, variance out — the class of analysis competitors charge
per-line for (ClaimsFlow "Estimate Reconciliation"), free and local:

- A = reference (full scope, independent, or prior revision); B = received
- Matches by exact code (cat|sel|act), then by description similarity ≥ 0.5
  for leftover codes — which is what catches **selector/grade substitutions**
  (builder-vs-premium) and **trade-merge recategorization** (ELE→"GEN")
- Classifies: matched / qty-changed / price-changed / substituted /
  missing-from-B / added-in-B, with Δ$ per line
- **Tie-out**: changed + substituted − missing + added must equal the total
  delta, or the report says MISMATCH — no hand-waving accounting
- `--format md` emits the shareable report; `--out FILE` writes it

## Input format for `create`

CSV / XLSX / JSON with columns (case-insensitive):

```
group, code, description, quantity, unit, unit_price, note
```

- `code` is `CAT|SEL` or `CAT|SEL|ACT` — e.g. `RFG|300S`, `RFG|300S|-` (removal),
  `RFG|FLCH|&` (remove & replace).
- `unit_price` is optional; if given it becomes the printed/displayed unit price.
  If omitted the line imports "Not in Price List" until Xactimate re-links it.
- `group` is the section/trade name (Roof, Interior, …).

```bash
python3 -m esxkit create items.csv -o out.esx \
  --claim claimNumber=2026-0042 --claim policyNumber=HOM-889231 --profile 8D
```

`--profile 8D` = contractor pricing context (O&P applies); `5L` = carrier context.
No price-list stamp is written — the receiving Xactimate applies its own list,
which is what makes files portable across regions.

## Import rules enforced (from the format forensics)

- Container: `ZIP_STORED` outer, DOS/FAT ZIP metadata, photos as raw stored JPEGs
- Root children in documented order; **no `<TOTALS>`** (adding one = rejection)
- Line items only via `EMBEDDED_PL/SUMITEMS/SUMITEM` + `ITEM/SUMMARY_REF`
- `SUMMARY_REF calc == qty` — never `qty × price` (Xactimate computes dollars itself)
- No `UC=`/price attributes, no xmlns, no `SUM_ASSEMBLY` refs
- SEL charset `^[A-Za-z0-9#+./-]+$`; `-A` minimum-suffix artifacts canonicalized
- `neutral xid 900001` identity

## Limitations (verified)

- **Xactimate rejects unencrypted ESX files.** Confirmed by a real Xactimate
  Data Transfer import test (Oct 2026): a standards-based plaintext-XML ESX
  is recognized as a project but rejected with detail **"Unencrypted Data"**.
  Native Xactimate exports encrypt the estimate XML (`XACTDOC.ZIPXML`,
  AES-256-ECB) with a non-public key. esxkit does not hold that key and does
  not extract it from Xactimate software. The working route to an
  import-ready ESX: produce the data as Xactimate XML / scope export, then
  have the final ESX written by a service that holds the container spec
  (e.g. ESXPress XML→ESX), or rebuild in Xactimate directly.
- **Reading native encrypted files:** photos are always extractable (stored
  raw). Line-item data requires the estimate to come out of Xactimate through
  its own export/XML paths instead.
- **Sketch geometry is not generated.** `.esx` sketch data (rooms/facets) and
  the `.skx` sketch format are out of scope; the estimate tree and line items
  are covered.
- **Emission is calibrated from public knowledge, not yet against your files.**
  The container profile and line-item structures follow verified real-export
  documentation, but sections like `PARAMS`/`COMPANY_HEADER`/`ADM` are emitted
  empty pending comparison with a real export. Guard-pass is necessary but not
  import-proof — a real Xactimate import is the only final proof.
- **Price-list linkage keys** (`catId`/`itemId`/`skClass`/`skGroup`) are optional
  input columns; without them Xactimate falls back to code-based matching on
  import (lines warn in `validate`).

## Provenance

Format knowledge: [esxpress-public](https://github.com/shutyobishop/esxpress-public)
(docs CC BY 4.0; fixture sample included under `samples/fixture_roof.json`, CC0).
esxkit is independent of, and not endorsed by, Verisk/Xactware. Handle .esx
files as claim documents: share only with parties who need them.

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE). Copyright (C) 2026
Anthoney Quebedeaux.

In plain terms: this tool is free, and anyone who builds on it — including
as a hosted service — must release their version free under the same terms.
That is deliberate: the toolkit can never be taken closed.

Trademarks: Xactimate, Xactware, and Verisk are trademarks of their
respective owners. esxkit is unaffiliated software that works with the
`.esx` file format; no endorsement is implied.
