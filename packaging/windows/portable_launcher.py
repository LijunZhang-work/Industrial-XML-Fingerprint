from __future__ import annotations

import sys
from pathlib import Path

from xml_fingerprint.cli import main as cli_main


def _strip_outer_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("portable launcher: expected package root and optional XML path", file=sys.stderr)
        return 2

    package_root = Path(args[0])
    input_text = args[1]
    if not input_text:
        print("Industrial XML Fingerprint v0.1.0")
        print("Paste the full path of an XML file, then press Enter.")
        print("You can also close this window and drag an XML file onto scan-xml.cmd.")
        try:
            input_text = input("> ")
        except EOFError:
            input_text = ""

    input_text = _strip_outer_quotes(input_text)
    if not input_text:
        print("No XML file was provided.", file=sys.stderr)
        return 2

    input_path = Path(input_text)
    if not input_path.is_file():
        print(f"File not found: {input_path}", file=sys.stderr)
        return 2

    report_dir = package_root / "reports" / input_path.stem
    print(f"Scanning: {input_path}")
    result = cli_main(["scan", str(input_path), "--profiles", "all", "--out", str(report_dir)])
    if result == 0:
        print(f"Scan complete. Reports: {report_dir}")
    else:
        print(f"Scan failed with exit code {result}.", file=sys.stderr)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
