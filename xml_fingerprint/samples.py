from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.request
import xml.sax
import zipfile
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import urlparse

from .scanner import scan

EVIDENCE_LEVELS = {
    "official_machine_readable_artifact",
    "official_normative_file",
    "official_published_excerpt",
}
ARTIFACT_TYPES = {"zip", "direct_file", "pdf_source"}
INPUT_TYPES = {"zip_member", "artifact", "bundled_fixture"}
SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
SAMPLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
ABSOLUTE_DOWNLOAD_LIMIT = 64 * 1024 * 1024
COPY_CHUNK = 1024 * 1024


class SampleError(ValueError):
    pass


class HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_https(newurl, "redirect target")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "industrial-xml-fingerprint" / "official-samples"
    return Path.home() / ".cache" / "industrial-xml-fingerprint" / "official-samples"


def manifest_resource():
    return files("standards").joinpath("official_samples_manifest.json")


def _open_text(resource: Any):
    return resource.open("r", encoding="utf-8")


def _require_sha256(value: Any, field: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SampleError(f"{field} must be an uppercase SHA-256 digest")


def _require_https(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise SampleError(f"{field} must be an HTTPS URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SampleError(f"{field} must be an HTTPS URL without embedded credentials")


def _safe_filename(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise SampleError(f"{field} must be a plain filename")
    if Path(value).name != value or "/" in value or "\\" in value or "\x00" in value:
        raise SampleError(f"{field} must be a plain filename")
    return value


def _safe_zip_member(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise SampleError("validation_input.member is unsafe")
    member = PurePosixPath(value)
    if member.is_absolute() or ".." in member.parts or member.parts[0].endswith(":"):
        raise SampleError("validation_input.member is unsafe")
    return value


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SampleError(f"{field} must be a positive integer")
    return value


def load_sample_manifest(resource: Any | None = None) -> dict[str, Any]:
    resource = resource or manifest_resource()
    with _open_text(resource) as handle:
        data = json.load(handle)
    if data.get("manifest_version") != "1.0.0" or not isinstance(data.get("samples"), list):
        raise SampleError("Unsupported or malformed official sample manifest")
    seen: set[str] = set()
    for sample in data["samples"]:
        if not isinstance(sample, dict):
            raise SampleError("Each manifest sample must be an object")
        sample_id = sample.get("sample_id")
        if not isinstance(sample_id, str) or not SAMPLE_ID_RE.fullmatch(sample_id) or sample_id in seen:
            raise SampleError("sample_id must be unique and filesystem-safe")
        seen.add(sample_id)
        if sample.get("evidence_level") not in EVIDENCE_LEVELS:
            raise SampleError(f"{sample_id}: unsupported evidence_level")
        if not isinstance(sample.get("retrieval_date"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", sample["retrieval_date"]):
            raise SampleError(f"{sample_id}: retrieval_date must be YYYY-MM-DD")
        _require_https(sample.get("source_page_url"), f"{sample_id}.source_page_url")
        _require_https(sample.get("artifact_url"), f"{sample_id}.artifact_url")
        artifact = sample.get("artifact")
        validation_input = sample.get("validation_input")
        if not isinstance(artifact, dict) or artifact.get("type") not in ARTIFACT_TYPES:
            raise SampleError(f"{sample_id}: unsupported artifact type")
        _safe_filename(artifact.get("filename"), f"{sample_id}.artifact.filename")
        size = _positive_int(artifact.get("size_bytes"), f"{sample_id}.artifact.size_bytes")
        maximum = _positive_int(artifact.get("max_download_bytes"), f"{sample_id}.artifact.max_download_bytes")
        if maximum < size or maximum > ABSOLUTE_DOWNLOAD_LIMIT:
            raise SampleError(f"{sample_id}: invalid download size limit")
        _require_sha256(artifact.get("sha256"), f"{sample_id}.artifact.sha256")
        if not isinstance(validation_input, dict) or validation_input.get("type") not in INPUT_TYPES:
            raise SampleError(f"{sample_id}: unsupported validation input type")
        input_type = validation_input["type"]
        _positive_int(validation_input.get("size_bytes"), f"{sample_id}.validation_input.size_bytes")
        _require_sha256(validation_input.get("sha256"), f"{sample_id}.validation_input.sha256")
        if input_type == "zip_member":
            if artifact["type"] != "zip":
                raise SampleError(f"{sample_id}: zip_member requires a ZIP artifact")
            _safe_zip_member(validation_input.get("member"))
            uncompressed = _positive_int(validation_input.get("max_uncompressed_bytes"), f"{sample_id}.validation_input.max_uncompressed_bytes")
            ratio = _positive_int(validation_input.get("max_compression_ratio"), f"{sample_id}.validation_input.max_compression_ratio")
            if uncompressed < validation_input["size_bytes"] or uncompressed > ABSOLUTE_DOWNLOAD_LIMIT or ratio > 1000:
                raise SampleError(f"{sample_id}: invalid ZIP expansion limits")
        elif input_type == "artifact" and artifact["type"] != "direct_file":
            raise SampleError(f"{sample_id}: artifact validation input requires a direct file")
        elif input_type == "bundled_fixture":
            if sample["evidence_level"] != "official_published_excerpt" or artifact["type"] != "pdf_source":
                raise SampleError(f"{sample_id}: bundled fixtures are only permitted for published PDF excerpts")
            resource_name = validation_input.get("resource")
            if not isinstance(resource_name, str) or not resource_name.startswith("fixtures/") or ".." in PurePosixPath(resource_name).parts:
                raise SampleError(f"{sample_id}: unsafe bundled fixture resource")
        for field, threshold_name in (("expected_profiles", "minimum_similarity"), ("negative_guards", "maximum_similarity_exclusive")):
            assertions = sample.get(field)
            if not isinstance(assertions, list) or (field == "expected_profiles" and not assertions):
                raise SampleError(f"{sample_id}: {field} must be a non-empty list" if field == "expected_profiles" else f"{sample_id}: {field} must be a list")
            for assertion in assertions:
                threshold = assertion.get(threshold_name) if isinstance(assertion, dict) else None
                if not isinstance(assertion, dict) or not isinstance(assertion.get("profile"), str) or not isinstance(threshold, int) or not 0 <= threshold <= 100:
                    raise SampleError(f"{sample_id}: malformed {field} assertion")
        if not isinstance(sample.get("claim_limitation"), str) or not sample["claim_limitation"]:
            raise SampleError(f"{sample_id}: claim_limitation is required")
    return data


def select_samples(manifest: dict[str, Any], selected: list[str] | None) -> list[dict[str, Any]]:
    by_id = {sample["sample_id"]: sample for sample in manifest["samples"]}
    if not selected:
        return [by_id[key] for key in sorted(by_id)]
    unknown = sorted(set(selected) - set(by_id))
    if unknown:
        raise SampleError(f"Unknown sample_id: {', '.join(unknown)}")
    return [by_id[key] for key in sorted(set(selected))]


def _is_reparse_point(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & flag)


@contextmanager
def _open_regular_source(path: Path, label: str):
    try:
        before = os.lstat(path)
    except FileNotFoundError as exc:
        raise SampleError(f"{label} is missing") from exc
    except OSError as exc:
        raise SampleError(f"{label} could not be read") from exc
    if not stat.S_ISREG(before.st_mode) or _is_reparse_point(before):
        raise SampleError(f"{label} is not a regular non-reparse file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SampleError(f"{label} could not be opened safely") from exc
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or _is_reparse_point(after):
            raise SampleError(f"{label} is not a regular non-reparse file")
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise SampleError(f"{label} changed while being opened")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            yield handle, after
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_regular_source(path, "file") as (handle, _):
        while chunk := handle.read(COPY_CHUNK):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _verify_file(path: Path, expected_size: int, expected_sha256: str, label: str) -> None:
    digest = hashlib.sha256()
    total = 0
    with _open_regular_source(path, label) as (handle, info):
        if info.st_size != expected_size:
            raise SampleError(f"{label} size mismatch")
        while chunk := handle.read(COPY_CHUNK):
            total += len(chunk)
            digest.update(chunk)
    if total != expected_size:
        raise SampleError(f"{label} size mismatch")
    if digest.hexdigest().upper() != expected_sha256:
        raise SampleError(f"{label} SHA-256 mismatch")


def _stage_verified_file(source: Path, target: Path, expected_size: int, expected_sha256: str, label: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    complete = False
    try:
        digest = hashlib.sha256()
        total = 0
        with _open_regular_source(source, label) as (input_handle, info):
            if info.st_size != expected_size:
                raise SampleError(f"{label} size mismatch")
            with target.open("xb") as output:
                while chunk := input_handle.read(COPY_CHUNK):
                    total += len(chunk)
                    if total > expected_size:
                        raise SampleError(f"{label} size mismatch")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
        if total != expected_size:
            raise SampleError(f"{label} size mismatch")
        if digest.hexdigest().upper() != expected_sha256:
            raise SampleError(f"{label} SHA-256 mismatch")
        complete = True
        return target
    finally:
        if not complete:
            target.unlink(missing_ok=True)


def _cache_paths(cache_dir: Path, sample: dict[str, Any]) -> tuple[Path, Path]:
    sample_dir = cache_dir / sample["sample_id"]
    artifact_path = sample_dir / "artifact" / sample["artifact"]["filename"]
    input_path = sample_dir / "input" / "validation-input.xml"
    return artifact_path, input_path


def _atomic_stream_copy(source: BinaryIO, target: Path, maximum: int, expected_size: int, expected_sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".partial-", dir=target.parent, delete=False) as output:
            temp_path = Path(output.name)
            digest = hashlib.sha256()
            total = 0
            while chunk := source.read(COPY_CHUNK):
                total += len(chunk)
                if total > maximum:
                    raise SampleError("download or extraction exceeded its size limit")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total != expected_size:
            raise SampleError("downloaded or extracted size mismatch")
        if digest.hexdigest().upper() != expected_sha256:
            raise SampleError("downloaded or extracted SHA-256 mismatch")
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _open_https(request: urllib.request.Request, timeout: float):
    return urllib.request.build_opener(HTTPSOnlyRedirectHandler()).open(request, timeout=timeout)


def _download(sample: dict[str, Any], target: Path, timeout: float) -> None:
    artifact = sample["artifact"]
    request = urllib.request.Request(sample["artifact_url"], headers={"User-Agent": "industrial-xml-fingerprint/0.1 official-sample-fetch"})
    try:
        with _open_https(request, timeout) as response:
            final_url = response.geturl()
            _require_https(final_url, "redirect target")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    announced = int(content_length)
                except ValueError as exc:
                    raise SampleError("invalid Content-Length") from exc
                if announced > artifact["max_download_bytes"]:
                    raise SampleError("announced download exceeds its size limit")
            _atomic_stream_copy(response, target, artifact["max_download_bytes"], artifact["size_bytes"], artifact["sha256"])
    except SampleError:
        raise
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, RuntimeError) as exc:
        raise SampleError("official artifact download failed") from exc


def _extract_member(sample: dict[str, Any], artifact_path: Path, input_path: Path) -> None:
    validation_input = sample["validation_input"]
    member_name = _safe_zip_member(validation_input["member"])
    try:
        with zipfile.ZipFile(artifact_path, "r") as archive:
            try:
                info = archive.getinfo(member_name)
            except KeyError as exc:
                raise SampleError("manifest ZIP member is missing") from exc
            if info.is_dir() or info.flag_bits & 0x1:
                raise SampleError("manifest ZIP member is a directory or encrypted")
            if info.file_size != validation_input["size_bytes"] or info.file_size > validation_input["max_uncompressed_bytes"]:
                raise SampleError("manifest ZIP member size mismatch")
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > validation_input["max_compression_ratio"]:
                raise SampleError("manifest ZIP member exceeds compression-ratio limit")
            with archive.open(info, "r") as source:
                _atomic_stream_copy(
                    source,
                    input_path,
                    validation_input["max_uncompressed_bytes"],
                    validation_input["size_bytes"],
                    validation_input["sha256"],
                )
    except SampleError:
        raise
    except zipfile.BadZipFile as exc:
        raise SampleError("official artifact is not a readable ZIP") from exc
    except (RuntimeError, NotImplementedError) as exc:
        raise SampleError("official artifact ZIP uses an unsupported feature") from exc
    except OSError as exc:
        raise SampleError("official artifact ZIP could not be processed") from exc


def fetch_samples(manifest: dict[str, Any], cache_dir: Path, selected: list[str] | None = None, timeout: float = 30.0) -> list[dict[str, str]]:
    results = []
    for sample in select_samples(manifest, selected):
        artifact_path, input_path = _cache_paths(cache_dir, sample)
        artifact = sample["artifact"]
        cached = True
        try:
            _verify_file(artifact_path, artifact["size_bytes"], artifact["sha256"], "cached artifact")
        except SampleError:
            cached = False
            _download(sample, artifact_path, timeout)
        if sample["validation_input"]["type"] == "zip_member":
            try:
                _verify_file(input_path, sample["validation_input"]["size_bytes"], sample["validation_input"]["sha256"], "cached validation input")
            except SampleError:
                _extract_member(sample, artifact_path, input_path)
        results.append({"sample_id": sample["sample_id"], "status": "cached" if cached else "downloaded"})
    return results


def _profile_score(report: dict[str, Any], profile_id: str) -> dict[str, Any]:
    try:
        return next(item for item in report["standard_profile_scores"] if item["profile"] == profile_id)
    except StopIteration as exc:
        raise SampleError(f"Profile not present in registry: {profile_id}") from exc


def _failure_result(
    sample: dict[str, Any],
    error: str,
    artifact_verified: bool = False,
    validation_input_verified: bool = False,
) -> dict[str, Any]:
    return {
        "sample_id": sample["sample_id"],
        "status": "FAIL",
        "evidence_level": sample["evidence_level"],
        "retrieval_date": sample["retrieval_date"],
        "source_page_url": sample["source_page_url"],
        "artifact_sha256": sample["artifact"]["sha256"],
        "artifact_verified": artifact_verified,
        "validation_input_verified": validation_input_verified,
        "claim_limitation": sample["claim_limitation"],
        "error": error,
        "assertions": [],
    }


def _safe_validation_error(exc: Exception) -> str:
    if isinstance(exc, SampleError):
        return str(exc)
    if isinstance(exc, zipfile.BadZipFile):
        return "cached artifact is not a readable ZIP"
    if isinstance(exc, xml.sax.SAXException):
        return "validation input XML parsing failed"
    if isinstance(exc, OSError):
        return "cached evidence could not be read"
    return "validation input was rejected"


def validate_samples(manifest: dict[str, Any], cache_dir: Path, registry: dict[str, Any], selected: list[str] | None = None) -> dict[str, Any]:
    results = []
    for sample in select_samples(manifest, selected):
        artifact_path, input_path = _cache_paths(cache_dir, sample)
        artifact = sample["artifact"]
        validation_input = sample["validation_input"]
        artifact_verified = False
        validation_input_verified = False
        try:
            with tempfile.TemporaryDirectory(prefix="xml-fingerprint-validate-") as staging_dir_text:
                staging_dir = Path(staging_dir_text)
                staged_artifact = _stage_verified_file(
                    artifact_path,
                    staging_dir / "artifact.bin",
                    artifact["size_bytes"],
                    artifact["sha256"],
                    "cached artifact",
                )
                artifact_verified = True
                if validation_input["type"] == "zip_member":
                    scan_path = _stage_verified_file(
                        input_path,
                        staging_dir / "validation-input.xml",
                        validation_input["size_bytes"],
                        validation_input["sha256"],
                        "cached validation input",
                    )
                elif validation_input["type"] == "artifact":
                    if validation_input["size_bytes"] != artifact["size_bytes"] or validation_input["sha256"] != artifact["sha256"]:
                        raise SampleError("validation input metadata does not match the verified artifact")
                    scan_path = staged_artifact
                else:
                    resource = files("standards").joinpath(*PurePosixPath(validation_input["resource"]).parts)
                    with as_file(resource) as resource_path:
                        scan_path = _stage_verified_file(
                            resource_path,
                            staging_dir / "validation-input.xml",
                            validation_input["size_bytes"],
                            validation_input["sha256"],
                            "bundled published excerpt",
                        )
                validation_input_verified = True
                scan_report = scan(scan_path, registry, sanitized=True)
            assertions = []
            passed = True
            for expected in sample["expected_profiles"]:
                observed = _profile_score(scan_report, expected["profile"])["overall_similarity"]
                ok = observed >= expected["minimum_similarity"]
                passed = passed and ok
                assertions.append({
                    "kind": "expected_profile",
                    "profile": expected["profile"],
                    "minimum_similarity": expected["minimum_similarity"],
                    "observed_similarity": observed,
                    "passed": ok,
                })
            for guard in sample["negative_guards"]:
                observed = _profile_score(scan_report, guard["profile"])["overall_similarity"]
                ok = observed < guard["maximum_similarity_exclusive"]
                passed = passed and ok
                assertions.append({
                    "kind": "negative_guard",
                    "profile": guard["profile"],
                    "maximum_similarity_exclusive": guard["maximum_similarity_exclusive"],
                    "observed_similarity": observed,
                    "passed": ok,
                })
            provenance = {
                "type": validation_input["type"],
                "sha256": validation_input["sha256"],
            }
            for key in ("member", "printed_page", "pdf_page_index_zero_based"):
                if key in validation_input:
                    provenance[key] = validation_input[key]
            results.append({
                "sample_id": sample["sample_id"],
                "status": "PASS" if passed else "FAIL",
                "evidence_level": sample["evidence_level"],
                "retrieval_date": sample["retrieval_date"],
                "source_page_url": sample["source_page_url"],
                "artifact_sha256": artifact["sha256"],
                "artifact_verified": True,
                "validation_input_verified": True,
                "validation_input_provenance": provenance,
                "claim_limitation": sample["claim_limitation"],
                "document_core": {
                    "element_count": scan_report["run_metadata"]["element_count"],
                    "root_qname": scan_report["document_identity"]["root_qname"],
                },
                "assertions": assertions,
            })
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, xml.sax.SAXException) as exc:
            results.append(_failure_result(
                sample,
                _safe_validation_error(exc),
                artifact_verified=artifact_verified,
                validation_input_verified=validation_input_verified,
            ))
    return {
        "report_version": "1.0.0",
        "manifest_version": manifest["manifest_version"],
        "sanitized": True,
        "overall_status": "PASS" if results and all(item["status"] == "PASS" for item in results) else "FAIL",
        "schema_conformance_performed": False,
        "interpretation_warning": "Results validate deterministic fingerprint thresholds against pinned official evidence; they do not prove XSD conformance.",
        "results": results,
    }


def write_validation_report(report: dict[str, Any], target: Path) -> Path:
    temp_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", prefix=".partial-", dir=target.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        temp_path = None
        return target
    except OSError as exc:
        raise SampleError("validation report could not be written") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
