"""Opt-in 100 MiB acceptance test: RUN_100MB_TEST=1 python -m unittest tests.test_100mb_streaming."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from xml_fingerprint.scanner import load_registry, scan

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("RUN_100MB_TEST") == "1", "set RUN_100MB_TEST=1 to run the 100 MiB streaming acceptance test")
class Streaming100MiBTest(unittest.TestCase):
    def test_100_mib_text_payload_has_bounded_python_memory(self):
        target_size = 100 * 1024 * 1024
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "large.xml"
            with path.open("wb") as fh:
                fh.write(b"<root>")
                # Mix large payload with more unique QNames than the in-memory
                # inventory limit so this proves bounded structural state too.
                for index in range(10_500):
                    fh.write(f"<unique{index}/><group><leaf{index}/></group>".encode("ascii"))
                chunk = b"<item>" + (b"x" * 4096) + b"</item>"
                while fh.tell() + len(chunk) + 7 < target_size:
                    fh.write(chunk)
                fh.write(b"</root>")
            report = scan(path, load_registry(ROOT / "standards" / "registry.json"))
            print(
                f"100MiB metrics: file_size={report['run_metadata']['file_size_bytes']} "
                f"peak_python_memory={report['run_metadata']['peak_memory_bytes']} "
                f"elements={report['run_metadata']['element_count']}"
            )
            self.assertGreaterEqual(report["run_metadata"]["file_size_bytes"], 99 * 1024 * 1024)
            self.assertLess(report["run_metadata"]["peak_memory_bytes"], 32 * 1024 * 1024)
            self.assertTrue(report["inventory_completeness"]["element_qnames"]["truncated"])
            self.assertTrue(report["coverage"]["unique_qname_coverage"]["truncated"])


if __name__ == "__main__":
    unittest.main()
