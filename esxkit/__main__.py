"""esxkit command line: python3 -m esxkit <command> ..."""

import argparse
import json
import sys

from . import __version__
from . import batch as B
from . import extract as X
from . import generate as G
from . import guard as V
from . import container as K


def _print(data):
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _pairs(kv):
    out = {}
    for item in kv or []:
        k, _, v = item.partition("=")
        out[k] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="esxkit",
        description="Toolkit for Xactimate .esx estimate files "
                    "(read / create / convert / validate / batch).")
    ap.add_argument("--version", action="version", version=f"esxkit {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inspect", help="container + document report for one .esx")
    p.add_argument("file")
    p.add_argument("--photos", metavar="DIR", help="also extract photos to DIR")

    p = sub.add_parser("validate", help="import-guard checks for one .esx")
    p.add_argument("file")

    p = sub.add_parser("create", help="build a .esx from CSV/XLSX/JSON line items")
    p.add_argument("input", help=".csv / .xlsx / .json line-item file")
    p.add_argument("-o", "--out", required=True, help="output .esx path")
    p.add_argument("--claim", action="append", metavar="KEY=VALUE",
                   help="claim info attr, e.g. --claim claimNumber=2026-0042")
    p.add_argument("--profile", default="8D", choices=["8D", "5L"],
                   help="8D=contractor, 5L=carrier (default 8D)")
    p.add_argument("--photos", nargs="*", default=[], help="JPEG files to embed")

    p = sub.add_parser("extract", help="ESX -> CSV/XLSX/JSON (+ photos)")
    p.add_argument("file")
    p.add_argument("-o", "--out", required=True, help="output directory")
    p.add_argument("--format", default="csv,json",
                   help="comma list: csv,xlsx,json (default csv,json)")
    p.add_argument("--photos", action="store_true", help="extract photo members")

    p = sub.add_parser("batch", help="operate over a folder")
    b = p.add_subparsers(dest="batch_cmd", required=True)
    p = b.add_parser("validate", help="validate every .esx in a folder")
    p.add_argument("folder")
    p = b.add_parser("tocsv", help="extract every readable .esx to CSV/JSON")
    p.add_argument("folder")
    p.add_argument("-o", "--out", required=True)
    p = b.add_parser("merge", help="merge ESX files or line-item JSONs into one estimate")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-o", "--out", required=True)
    p = b.add_parser("split", help="split one ESX into one ESX per group")
    p.add_argument("file")
    p.add_argument("-o", "--out", required=True)

    p = sub.add_parser("audit",
                       help="supplement-audit: flag documented manipulation patterns")
    p.add_argument("file")
    p.add_argument("--format", default="json", choices=["json", "md"],
                   help="json report (default) or markdown variance report")
    p.add_argument("--out", metavar="FILE", help="write the report to FILE")

    p = sub.add_parser("compare",
                       help="reconcile two estimates: A (reference) vs B (received)")
    p.add_argument("reference")
    p.add_argument("received")
    p.add_argument("--format", default="json", choices=["json", "md"],
                   help="json report (default) or markdown variance report")
    p.add_argument("--out", metavar="FILE", help="write the report to FILE")

    p = sub.add_parser("edit", help="drop lines / change prices in a plaintext ESX")
    p.add_argument("file")
    p.add_argument("-o", "--out", help="updated .esx output path (required unless --list)")
    p.add_argument("--list", action="store_true", help="list lines with selectors and exit")
    p.add_argument("--drop", action="append", default=[],
                   metavar="SEL", help="line to remove: '#7' or 'RFG|300S|+' (repeatable)")
    p.add_argument("--price", action="append", default=[],
                   metavar="SEL=VALUE", help="set unit price: '#2=250.00' (repeatable)")
    p.add_argument("--total-preview", action="store_true",
                   help="show rough qty x price preview total")

    args = ap.parse_args(argv)

    if args.cmd == "inspect":
        report = X.inspect(args.file)
        if args.photos:
            from . import container as KC
            report["photos_extracted"] = KC.extract_photos(args.file, args.photos)
        _print(report)
    elif args.cmd == "validate":
        _print(V.validate(args.file))
    elif args.cmd == "create":
        photos = []
        import os as _os
        for pth in args.photos:
            with open(pth, "rb") as fh:
                photos.append((_os.path.basename(pth), fh.read()))
        out, problems = G.create(args.input, args.out, claim=_pairs(args.claim),
                                 profile=args.profile, photos=photos)
        _print({"out": out, "problems": problems})
    elif args.cmd == "audit":
        from . import audit as A
        report = A.audit(args.file)
        if args.format == "md":
            text = A.to_markdown(report)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
                print(f"wrote {args.out}")
            else:
                print(text)
        else:
            _print(report)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    json.dump(report, fh, indent=2, ensure_ascii=False)
                print(f"wrote {args.out}", file=sys.stderr)
    elif args.cmd == "compare":
        from . import compare as R
        rep = R.compare(args.reference, args.received)
        if args.format == "md":
            text = R.to_markdown(rep)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
                print(f"wrote {args.out}")
            else:
                print(text)
        else:
            _print(rep)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    json.dump(rep, fh, indent=2, ensure_ascii=False)
                print(f"wrote {args.out}", file=sys.stderr)
    elif args.cmd == "edit":
        from . import modify as M
        if args.list:
            _print(M.list_lines(args.file))
            return
        if not args.out:
            sys.exit("error: -o/--out is required to write the updated estimate")
        out, changes, dropped = M.edit(args.file, args.out, drop=args.drop,
                                       price=args.price)
        result = {"out": out, "changes": changes,
                  "line_value_removed": dropped}
        if args.total_preview:
            result["before"] = M.preview_totals(args.file)
        _print(result)
        if args.total_preview:
            result_after = M.preview_totals(out)
            print("after preview:", result_after)
    elif args.cmd == "extract":
        formats = tuple(x.strip() for x in args.format.split(",") if x.strip())
        written, warns = X.extract(args.file, args.out, formats=formats,
                                   photos=args.photos)
        _print({"written": written, "warnings": warns})
    elif args.cmd == "batch":
        if args.batch_cmd == "validate":
            _print(B.validate_folder(args.folder))
        elif args.batch_cmd == "tocsv":
            _print(B.folder_to_csv(args.folder, args.out))
        elif args.batch_cmd == "merge":
            esx = [f for f in args.inputs if f.lower().endswith(".esx")]
            data = [f for f in args.inputs if not f.lower().endswith(".esx")]
            if esx and data:
                sys.exit("error: merge either ESX files or line-item data files, not both")
            if esx:
                _print(B.merge(esx, args.out))
            else:
                _print(B.merge_json_inputs(data, args.out))
        elif args.batch_cmd == "split":
            _print({"written": B.split(args.file, args.out)})


if __name__ == "__main__":
    main()
