"""Command-line interface: medanon synth | anonymize | qa | demo."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .anonymizer import anonymize_directory
from .qa import scan_directory, write_markdown
from .synth import generate


def cmd_synth(args: argparse.Namespace) -> int:
    paths = generate(Path(args.out), n=args.n, seed=args.seed, annotate=args.annotate)
    print(f"[medanon] generated {len(paths)} synthetic DICOM files in {args.out} "
          f"(seed={args.seed}, annotated={args.annotate})")
    return 0


def cmd_anonymize(args: argparse.Namespace) -> int:
    results, salt = anonymize_directory(Path(args.src), Path(args.out),
                                       salt=args.salt, map_path=args.map)
    n_patients = len({r["pseudonym"] for r in results})
    print(f"[medanon] anonymized {len(results)} files "
          f"({n_patients} patients) -> {args.out}")
    print(f"[medanon] pseudonym map: {Path(args.out) / 'pseudonym_map.json'}")
    return 0


def cmd_qa(args: argparse.Namespace) -> int:
    report = scan_directory(Path(args.src))
    counts = report.counts()
    print(f"[medanon] QA verdict: {report.verdict}")
    print(f"  files scanned : {report.files_scanned}")
    print(f"  tags checked  : {report.tags_checked}")
    print(f"  HIGH: {counts['HIGH']}  MEDIUM: {counts['MEDIUM']}  LOW: {counts['LOW']}")
    for f in report.findings:
        print(f"  [{f.severity}] {f.file} {f.tag}: {f.message}")
    if args.report:
        write_markdown(report, Path(args.report))
        print(f"[medanon] report written to {args.report}")
    return 0 if report.verdict == "PASS" else 1


def cmd_demo(args: argparse.Namespace) -> int:
    base = Path(args.out)
    raw, anon = base / "data" / "raw", base / "data" / "anon"

    print("== medanon demo: synth -> anonymize -> qa ==")
    print("\n[1/3] generating synthetic DICOMs (3 with burned-in annotations)...")
    generate(raw, n=20, seed=20261007, annotate=3)

    print("\n[2/3] anonymizing (PS3.15 Basic Profile)...")
    results, salt = anonymize_directory(raw, anon)
    n_patients = len({r["pseudonym"] for r in results})
    print(f"      {len(results)} files, {n_patients} patients, "
          f"map: {anon / 'pseudonym_map.json'}")

    print("\n[3/3] QA pass...")
    report = scan_directory(anon)
    counts = report.counts()
    write_markdown(report, base / "qa.md")
    print(f"      verdict: {report.verdict} "
          f"(HIGH={counts['HIGH']} MEDIUM={counts['MEDIUM']} LOW={counts['LOW']})")
    for f in report.findings:
        print(f"      [{f.severity}] {f.file} {f.tag}: {f.message}")
    print(f"\nDemo artifacts in {base}/  (report: {base / 'qa.md'})")
    print("NOTE: QA is a heuristic safety net, not a compliance guarantee — "
          "validate against your institution's policy/IRB.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="medanon",
                                description="DICOM anonymization + PHI-leak QA")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("synth", help="generate synthetic test DICOMs")
    s.add_argument("--out", default="data/raw")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--annotate", type=int, default=0,
                   help="burn text-like annotations into N images (tests the pixel QA)")
    s.set_defaults(func=cmd_synth)

    a = sub.add_parser("anonymize", help="anonymize a folder of DICOM files")
    a.add_argument("src")
    a.add_argument("--out", required=True)
    a.add_argument("--salt", default=None,
                   help="hex salt for deterministic pseudonyms (generated if omitted)")
    a.add_argument("--map", default=None,
                   help="existing pseudonym_map.json to extend (longitudinal studies)")
    a.set_defaults(func=cmd_anonymize)

    q = sub.add_parser("qa", help="PHI-leak QA scan over anonymized DICOMs")
    q.add_argument("src")
    q.add_argument("--report", default=None, help="write markdown report to this path")
    q.set_defaults(func=cmd_qa)

    d = sub.add_parser("demo", help="run the full pipeline on synthetic data")
    d.add_argument("--out", default="medanon-demo")
    d.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
