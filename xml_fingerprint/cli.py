from __future__ import annotations

import argparse
import json
import sys
import xml.sax
from importlib.resources import files
from pathlib import Path

from . import __version__
from .samples import (
    SampleError,
    default_cache_dir,
    fetch_samples,
    load_sample_manifest,
    manifest_resource,
    validate_samples,
    write_validation_report,
)
from .scanner import UnsafeXML, load_registry, scan, write_reports


def registry_path():
    """Return the packaged registry resource (works from source and installed wheels)."""
    return files("standards").joinpath("registry.json")


def _profile(scores, profile_id: str):
    wanted = profile_id.lower()
    aliases = {"iec61131-10": "iec61131-10", "plcopen": "plcopen-tc6", "xmi": "xmi-emf", "automationml": "automationml-caex"}
    wanted = aliases.get(wanted, wanted)
    return next((x for x in scores if x["profile"].lower() == wanted), None)


def explain(profile: dict) -> str:
    lines = [
        f"{profile['profile']}: {profile['overall_similarity']} ({profile['confidence']})",
        f"identity={profile['identity_score']} vocabulary={profile['vocabulary_score']} structure={profile['structure_score']} "
        f"reference={profile['reference_model_score']} semantic={profile['semantic_role_score']} conflict={profile['conflict_score']}",
    ]
    for item in profile["evidence"]:
        detail = item["detail"] if isinstance(item["detail"], str) else ", ".join(item["detail"])
        lines.append(f"{item['effect']} {item['kind']}: {detail}")
    lines.append("This is a similarity explanation, not an XSD conformance claim.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xml-fingerprint", description="Industrial XML Standard Fingerprint Detector")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    scan_cmd = sub.add_parser("scan", help="stream-scan an XML file and write sanitized JSON reports")
    scan_cmd.add_argument("xml", type=Path)
    scan_cmd.add_argument("--out", required=True, type=Path)
    scan_cmd.add_argument("--profiles", choices=("core", "all"), default="core")
    scan_cmd.add_argument("--sanitized", action="store_true", default=True, help="default and only reporting mode in v0.1")
    scan_cmd.add_argument("--explain-profile")
    explain_cmd = sub.add_parser("explain", help="explain a profile score from a generated report")
    explain_cmd.add_argument("report", type=Path)
    explain_cmd.add_argument("--profile", required=True)
    samples_cmd = sub.add_parser("samples", help="explicitly fetch or offline-validate pinned official samples")
    samples_sub = samples_cmd.add_subparsers(dest="samples_command", required=True)
    fetch_cmd = samples_sub.add_parser("fetch", help="download pinned official evidence into a separate cache")
    fetch_cmd.add_argument("--cache", type=Path, default=default_cache_dir())
    fetch_cmd.add_argument("--sample", action="append", dest="samples")
    fetch_cmd.add_argument("--timeout", type=float, default=30.0)
    validate_cmd = samples_sub.add_parser("validate", help="offline validation using only the pinned cache and bundled excerpt")
    validate_cmd.add_argument("--cache", type=Path, default=default_cache_dir())
    validate_cmd.add_argument("--sample", action="append", dest="samples")
    validate_cmd.add_argument("--out", type=Path, default=Path("official_validation_report.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            registry = load_registry(registry_path())
            if args.profiles == "core":
                registry = {**registry, "profiles": [p for p in registry["profiles"] if p["priority"] == "P0"]}
            report = scan(args.xml, registry, sanitized=True)
            paths = write_reports(report, args.out)
            print(f"Scanned {report['run_metadata']['element_count']} elements; wrote {len(paths)} sanitized JSON reports to {args.out}")
            if args.explain_profile:
                profile = _profile(report["standard_profile_scores"], args.explain_profile)
                if not profile:
                    print(f"Unknown profile: {args.explain_profile}", file=sys.stderr)
                    return 2
                print(explain(profile))
            return 0
        if args.command == "samples":
            manifest = load_sample_manifest(manifest_resource())
            if args.samples_command == "fetch":
                if args.timeout <= 0:
                    raise SampleError("--timeout must be greater than zero")
                results = fetch_samples(manifest, args.cache, args.samples, timeout=args.timeout)
                for result in results:
                    print(f"{result['sample_id']}: {result['status']}")
                return 0
            registry = load_registry(registry_path())
            report = validate_samples(manifest, args.cache, registry, args.samples)
            write_validation_report(report, args.out)
            print(f"Official sample validation: {report['overall_status']}; wrote sanitized report to {args.out}")
            return 0 if report["overall_status"] == "PASS" else 1
        with args.report.open("r", encoding="utf-8") as fh:
            report = json.load(fh)
        profile = _profile(report["standard_profile_scores"], args.profile)
        if not profile:
            print(f"Unknown profile: {args.profile}", file=sys.stderr)
            return 2
        print(explain(profile))
        return 0
    except (OSError, ValueError, SampleError, UnsafeXML, json.JSONDecodeError, xml.sax.SAXException) as exc:
        print(f"xml-fingerprint: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
