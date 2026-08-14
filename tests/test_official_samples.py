from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from xml_fingerprint.cli import main
from xml_fingerprint.samples import (
    HTTPSOnlyRedirectHandler,
    SampleError,
    fetch_samples,
    load_sample_manifest,
    manifest_resource,
    validate_samples,
)
from xml_fingerprint.scanner import load_registry, scan as scanner_scan

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "standards" / "registry.json")
TC6 = b'<project xmlns="http://www.plcopen.org/xml/tc6_0201"><types><pous><pou><body><FBD/></body></pou></pous></types></project>'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def direct_manifest(payload: bytes = TC6, minimum: int = 40) -> dict:
    return {
        "manifest_version": "1.0.0",
        "retrieval_date": "2026-08-14",
        "samples": [{
            "sample_id": "test_direct",
            "evidence_level": "official_normative_file",
            "retrieval_date": "2026-08-14",
            "source_page_url": "https://example.test/source",
            "artifact_url": "https://example.test/sample.xml",
            "artifact": {
                "type": "direct_file", "filename": "sample.xml", "size_bytes": len(payload),
                "max_download_bytes": 4096, "sha256": sha(payload),
            },
            "validation_input": {"type": "artifact", "size_bytes": len(payload), "sha256": sha(payload)},
            "expected_profiles": [{"profile": "PLCopen-TC6", "minimum_similarity": minimum}],
            "negative_guards": [{"profile": "IEC61131-10", "maximum_similarity_exclusive": 25}],
            "claim_limitation": "Test evidence only; not schema conformance.",
        }],
    }


def write_manifest(directory: Path, data: dict) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def put_direct_cache(cache: Path, manifest: dict, payload: bytes) -> Path:
    sample = manifest["samples"][0]
    target = cache / sample["sample_id"] / "artifact" / sample["artifact"]["filename"]
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    return target


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, url: str):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}
        self._url = url

    def geturl(self):
        return self._url


class OfficialSampleTests(unittest.TestCase):
    def test_packaged_manifest_schema_and_evidence_levels(self):
        manifest = load_sample_manifest(manifest_resource())
        self.assertEqual(len(manifest["samples"]), 5)
        self.assertEqual(
            {sample["evidence_level"] for sample in manifest["samples"]},
            {"official_machine_readable_artifact", "official_normative_file", "official_published_excerpt"},
        )
        excerpt = next(sample for sample in manifest["samples"] if sample["evidence_level"] == "official_published_excerpt")
        self.assertEqual(excerpt["validation_input"]["printed_page"], "63/80")

    def test_manifest_rejects_unsafe_zip_member(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../escape.xml", TC6)
        data = direct_manifest()
        sample = data["samples"][0]
        sample["artifact"] = {
            "type": "zip", "filename": "sample.zip", "size_bytes": len(payload.getvalue()),
            "max_download_bytes": 4096, "sha256": sha(payload.getvalue()),
        }
        sample["validation_input"] = {
            "type": "zip_member", "member": "../escape.xml", "size_bytes": len(TC6),
            "max_uncompressed_bytes": 4096, "max_compression_ratio": 100, "sha256": sha(TC6),
        }
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SampleError):
                load_sample_manifest(write_manifest(Path(td), data))

    def test_fetch_extracts_only_manifest_member_and_is_idempotent(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("sample.xml", TC6)
            archive.writestr("../not-extracted.txt", b"danger")
        archive_bytes = payload.getvalue()
        data = direct_manifest()
        sample = data["samples"][0]
        sample["artifact"] = {
            "type": "zip", "filename": "sample.zip", "size_bytes": len(archive_bytes),
            "max_download_bytes": 4096, "sha256": sha(archive_bytes),
        }
        sample["validation_input"] = {
            "type": "zip_member", "member": "sample.xml", "size_bytes": len(TC6),
            "max_uncompressed_bytes": 4096, "max_compression_ratio": 100, "sha256": sha(TC6),
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = load_sample_manifest(write_manifest(root, data))
            cache = root / "cache"
            with patch("xml_fingerprint.samples._open_https", return_value=FakeResponse(archive_bytes, sample["artifact_url"])) as mocked:
                first = fetch_samples(manifest, cache)
                second = fetch_samples(manifest, cache)
            self.assertEqual(first[0]["status"], "downloaded")
            self.assertEqual(second[0]["status"], "cached")
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual((cache / "test_direct" / "input" / "validation-input.xml").read_bytes(), TC6)
            self.assertFalse((root / "not-extracted.txt").exists())

    def test_zip_compression_ratio_limit_blocks_bomb_like_member(self):
        large = b"x" * 20000
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("sample.xml", large)
        archive_bytes = payload.getvalue()
        data = direct_manifest(large)
        sample = data["samples"][0]
        sample["artifact"] = {
            "type": "zip", "filename": "sample.zip", "size_bytes": len(archive_bytes),
            "max_download_bytes": 4096, "sha256": sha(archive_bytes),
        }
        sample["validation_input"] = {
            "type": "zip_member", "member": "sample.xml", "size_bytes": len(large),
            "max_uncompressed_bytes": len(large), "max_compression_ratio": 2, "sha256": sha(large),
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = load_sample_manifest(write_manifest(root, data))
            with patch("xml_fingerprint.samples._open_https", return_value=FakeResponse(archive_bytes, sample["artifact_url"])):
                with self.assertRaisesRegex(SampleError, "compression-ratio"):
                    fetch_samples(manifest, root / "cache")

    def test_validate_reports_missing_cache_and_hash_mismatch_without_paths(self):
        manifest = direct_manifest()
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache"
            missing = validate_samples(manifest, cache, REGISTRY)
            self.assertEqual(missing["overall_status"], "FAIL")
            self.assertEqual(missing["results"][0]["error"], "cached artifact is missing")
            put_direct_cache(cache, manifest, b"x" * len(TC6))
            mismatch = validate_samples(manifest, cache, REGISTRY)
            self.assertEqual(mismatch["results"][0]["error"], "cached artifact SHA-256 mismatch")
            self.assertNotIn(str(cache), json.dumps(mismatch))

    def test_validation_parse_failure_is_sanitized(self):
        malformed = b"<project>"
        manifest = direct_manifest(malformed)
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "secret-cache-name"
            put_direct_cache(cache, manifest, malformed)
            report = validate_samples(manifest, cache, REGISTRY)
            self.assertEqual(report["results"][0]["error"], "validation input XML parsing failed")
            self.assertNotIn(str(cache), json.dumps(report))

    def test_validate_scans_staged_bytes_after_cache_path_is_replaced(self):
        manifest = direct_manifest()
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache"
            cached_path = put_direct_cache(cache, manifest, TC6)

            def replace_then_scan(staged_path, registry, sanitized=True):
                self.assertNotEqual(Path(staged_path), cached_path)
                cached_path.write_bytes(b"x" * len(TC6))
                return scanner_scan(staged_path, registry, sanitized=sanitized)

            with patch("xml_fingerprint.samples.scan", side_effect=replace_then_scan):
                report = validate_samples(manifest, cache, REGISTRY)
            self.assertEqual(report["overall_status"], "PASS")
            self.assertTrue(report["results"][0]["artifact_verified"])
            self.assertTrue(report["results"][0]["validation_input_verified"])
            self.assertNotEqual(cached_path.read_bytes(), TC6)

    def test_validate_rejects_symlink_or_reparse_cache_file_when_supported(self):
        manifest = direct_manifest()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "cache"
            target = root / "real.xml"
            target.write_bytes(TC6)
            cached_path = cache / "test_direct" / "artifact" / "sample.xml"
            cached_path.parent.mkdir(parents=True)
            try:
                os.symlink(target, cached_path)
            except OSError as exc:
                self.skipTest(f"symlink creation is not available: {exc}")
            report = validate_samples(manifest, cache, REGISTRY)
            self.assertEqual(report["overall_status"], "FAIL")
            self.assertIn("non-reparse", report["results"][0]["error"])

    def test_validate_reparse_guard_is_enforced(self):
        manifest = direct_manifest()
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache"
            put_direct_cache(cache, manifest, TC6)
            with patch("xml_fingerprint.samples._is_reparse_point", return_value=True):
                report = validate_samples(manifest, cache, REGISTRY)
            self.assertEqual(report["overall_status"], "FAIL")
            self.assertIn("non-reparse", report["results"][0]["error"])

    def test_validate_pass_fail_and_stable_success_report(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache"
            passing_manifest = direct_manifest(minimum=40)
            put_direct_cache(cache, passing_manifest, TC6)
            first = validate_samples(passing_manifest, cache, REGISTRY)
            second = validate_samples(passing_manifest, cache, REGISTRY)
            self.assertEqual(first, second)
            self.assertEqual(first["overall_status"], "PASS")
            self.assertFalse(first["schema_conformance_performed"])
            failing = validate_samples(direct_manifest(minimum=100), cache, REGISTRY)
            self.assertEqual(failing["overall_status"], "FAIL")
            self.assertFalse(failing["results"][0]["assertions"][0]["passed"])

    def test_scan_command_never_uses_network(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.xml"
            output = Path(td) / "output"
            source.write_bytes(TC6)
            with patch("xml_fingerprint.samples.urllib.request.urlopen", side_effect=AssertionError("network used")) as mocked:
                self.assertEqual(main(["scan", str(source), "--out", str(output)]), 0)
            mocked.assert_not_called()

    def test_https_redirect_is_rejected_before_next_request_is_created(self):
        handler = HTTPSOnlyRedirectHandler()
        with patch.object(urllib.request.HTTPRedirectHandler, "redirect_request") as downstream:
            with self.assertRaisesRegex(SampleError, "HTTPS"):
                handler.redirect_request(None, None, 302, "Found", {}, "http://downgrade.test/file?secret=value")
        downstream.assert_not_called()

    def test_production_cli_rejects_custom_manifest_option(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["samples", "fetch", "--manifest", "untrusted.json"])
        self.assertEqual(raised.exception.code, 2)

    def test_network_error_is_sanitized_and_cli_returns_two(self):
        with tempfile.TemporaryDirectory() as td:
            error_output = io.StringIO()
            with patch(
                "xml_fingerprint.samples._open_https",
                side_effect=urllib.error.URLError("https://private.test/file?token=SECRET"),
            ), redirect_stderr(error_output):
                result = main([
                    "samples", "fetch", "--cache", str(Path(td) / "cache"),
                    "--sample", "omg_xmi_official_model",
                ])
            self.assertEqual(result, 2)
            self.assertIn("official artifact download failed", error_output.getvalue())
            self.assertNotIn("SECRET", error_output.getvalue())
            self.assertNotIn("Traceback", error_output.getvalue())

    def test_malformed_zip_and_unsupported_compression_are_sample_errors(self):
        malformed = b"not a zip"
        malformed_manifest = direct_manifest()
        malformed_sample = malformed_manifest["samples"][0]
        malformed_sample["artifact"] = {
            "type": "zip", "filename": "bad.zip", "size_bytes": len(malformed),
            "max_download_bytes": 4096, "sha256": sha(malformed),
        }
        malformed_sample["validation_input"] = {
            "type": "zip_member", "member": "sample.xml", "size_bytes": len(TC6),
            "max_uncompressed_bytes": 4096, "max_compression_ratio": 100, "sha256": sha(TC6),
        }
        with tempfile.TemporaryDirectory() as td:
            with patch("xml_fingerprint.samples._open_https", return_value=FakeResponse(malformed, malformed_sample["artifact_url"])):
                with self.assertRaisesRegex(SampleError, "not a readable ZIP"):
                    fetch_samples(malformed_manifest, Path(td) / "cache")

        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("sample.xml", TC6)
        unsupported = bytearray(payload.getvalue())
        local = unsupported.find(b"PK\x03\x04")
        central = unsupported.find(b"PK\x01\x02")
        self.assertGreaterEqual(local, 0)
        self.assertGreaterEqual(central, 0)
        unsupported[local + 8:local + 10] = (99).to_bytes(2, "little")
        unsupported[central + 10:central + 12] = (99).to_bytes(2, "little")
        unsupported_bytes = bytes(unsupported)
        unsupported_manifest = direct_manifest()
        unsupported_sample = unsupported_manifest["samples"][0]
        unsupported_sample["artifact"] = {
            "type": "zip", "filename": "unsupported.zip", "size_bytes": len(unsupported_bytes),
            "max_download_bytes": 4096, "sha256": sha(unsupported_bytes),
        }
        unsupported_sample["validation_input"] = {
            "type": "zip_member", "member": "sample.xml", "size_bytes": len(TC6),
            "max_uncompressed_bytes": 4096, "max_compression_ratio": 100, "sha256": sha(TC6),
        }
        with tempfile.TemporaryDirectory() as td:
            with patch("xml_fingerprint.samples._open_https", return_value=FakeResponse(unsupported_bytes, unsupported_sample["artifact_url"])):
                with self.assertRaisesRegex(SampleError, "unsupported feature"):
                    fetch_samples(unsupported_manifest, Path(td) / "cache")


if __name__ == "__main__":
    unittest.main()
