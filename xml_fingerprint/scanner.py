from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
import time
import tracemalloc
import xml.sax
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.handler import (
    ContentHandler,
    EntityResolver,
    feature_external_ges,
    feature_external_pes,
    feature_namespaces,
    property_lexical_handler,
)

XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XML_NS = "http://www.w3.org/XML/1998/namespace"
REFERENCE_NAMES = {
    "href", "ref", "reference", "idref", "reflocalid", "source", "target",
    "from", "to", "resource", "refpartnersidea", "refpartnersideb",
}
DEFINITION_NAMES = {"id", "localid", "nodeid"}
EXTENSION_NAMES = {"adddata", "additionalinformation", "extension", "extensions"}
NETWORK_NAMES = {"network", "fbd", "ld", "sfc", "instancehierarchy"}
MAX_QNAMES = 10_000
MAX_STRUCTURES = 20_000
MAX_PATHS = 20_000
MAX_FAMILIES = 2_000
MAX_DEPTH = 2_048
MAX_SCHEMA_TOKENS = 2_000
MAX_PI_TARGETS = 1_000
MAX_CHILDREN_PER_ELEMENT = 1_000
MAX_ATTRIBUTES_PER_ELEMENT = 2_000
MAX_REFERENCES_PER_ELEMENT = 1_000
MAX_REFERENCE_MECHANISMS = 1_000


class UnsafeXML(ValueError):
    pass


class MemoryTrace:
    def __enter__(self):
        tracemalloc.start()
        self.peak = 0
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None and tracemalloc.is_tracing():
            tracemalloc.stop()
        return False

    def finish(self) -> int:
        _, self.peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return self.peak


class BoundedCounter:
    def __init__(self, limit: int):
        self.limit = limit
        self.data: Counter[Any] = Counter()
        self.overflow = 0

    def add(self, key: Any, amount: int = 1) -> None:
        if key in self.data or len(self.data) < self.limit:
            self.data[key] += amount
        else:
            self.overflow += amount

    def items(self):
        return self.data.items()

    def keys(self):
        return self.data.keys()

    def status(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "tracked_keys": len(self.data),
            "overflow_occurrences": self.overflow,
            "complete": self.overflow == 0,
            "truncated": self.overflow > 0,
        }


class BoundedClassificationMap:
    def __init__(self, limit: int):
        self.limit = limit
        self.data: dict[Any, str] = {}
        self.overflow = 0

    def add(self, key: Any, classification: str) -> None:
        if key in self.data:
            return
        if len(self.data) < self.limit:
            self.data[key] = classification
        else:
            self.overflow += 1

    def coverage(self) -> dict[str, Any]:
        classes = (
            "STANDARD_EXACT", "STANDARD_ALLOWED_EXTENSION", "STANDARD_ROLE_EQUIVALENT",
            "KNOWN_FOREIGN_STANDARD", "VENDOR_PRIVATE", "UNKNOWN",
        )
        counts = Counter(self.data.values())
        total = len(self.data) or 1
        return {
            "classes": {name: {"count": counts[name], "ratio": round(counts[name] / total, 6)} for name in classes},
            "tracked": len(self.data), "overflow_unique_candidates": self.overflow,
            "complete": self.overflow == 0, "truncated": self.overflow > 0,
        }


def split_name(name: tuple[str | None, str]) -> tuple[str, str]:
    return (name[0] or "", name[1])


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def normalize_ref(value: str) -> str:
    value = value.strip()
    if value.startswith("#"):
        value = value[1:]
    return value


@dataclass
class Frame:
    ns: str
    local: str
    classification: str
    path: tuple[tuple[str, str], ...]
    attr_names: tuple[tuple[str, str], ...]
    ref_names: tuple[str, ...]
    instance_id: int
    network_scope_id: int
    exact_profile_ids: frozenset[str]
    extension_profile_ids: frozenset[str]
    children: set[tuple[str, str]] = field(default_factory=set)
    children_overflow: int = 0


class RejectingResolver(EntityResolver):
    def resolveEntity(self, publicId, systemId):  # noqa: N802 (SAX API)
        raise UnsafeXML("External entities are disabled")


class FingerprintHandler(ContentHandler):
    def __init__(self, profiles: list[dict[str, Any]], db: sqlite3.Connection):
        super().__init__()
        self.profiles = profiles
        self.db = db
        self.stack: list[Frame] = []
        self.namespaces = BoundedCounter(MAX_QNAMES)
        self.elements = BoundedCounter(MAX_QNAMES)
        self.attributes = BoundedCounter(MAX_QNAMES)
        self.structures = BoundedCounter(MAX_STRUCTURES)
        self.paths = BoundedCounter(MAX_PATHS)
        self.regions = BoundedCounter(MAX_FAMILIES)
        self.families = BoundedCounter(MAX_FAMILIES)
        self.qname_classes = BoundedClassificationMap(MAX_QNAMES)
        self.structure_classes = BoundedClassificationMap(MAX_STRUCTURES)
        self.class_counts: Counter[str] = Counter()
        self.pi_names = BoundedCounter(MAX_PI_TARGETS)
        self.schema_locations = BoundedCounter(MAX_SCHEMA_TOKENS)
        self.no_namespace_schema_locations = BoundedCounter(MAX_SCHEMA_TOKENS)
        self.pending_prefixes: list[tuple[str, str]] = []
        self.pending_prefix_overflow = 0
        self.root: tuple[str, str] | None = None
        self.element_count = 0
        self.attribute_count = 0
        self.max_depth = 0
        self.doctype: dict[str, str | None] | None = None
        self.element_child_overflow = 0
        self.element_attribute_overflow = 0
        self.element_reference_overflow = 0
        self._next_instance_id = 0
        self._known_namespaces = set()
        self._known_names = set()
        for p in profiles:
            self._known_namespaces.update(p.get("namespaces", []))
            self._known_namespaces.update(p.get("extension_namespaces", []))
            self._known_names.update(p.get("vocabulary", []))
        self._db_writes = 0

    # LexicalHandler methods. Raising in startDTD prevents internal-entity expansion.
    def startDTD(self, name, public_id, system_id):  # noqa: N802
        self.doctype = {"name": name, "public_id": public_id, "system_id": system_id}
        raise UnsafeXML("DOCTYPE declarations are disabled")

    def endDTD(self):  # noqa: N802
        pass

    def startCDATA(self):  # noqa: N802
        pass

    def endCDATA(self):  # noqa: N802
        pass

    def comment(self, text):
        pass

    def processingInstruction(self, target, data):  # noqa: N802
        self.pi_names.add(target)

    def startPrefixMapping(self, prefix, uri):  # noqa: N802
        if len(self.pending_prefixes) < MAX_ATTRIBUTES_PER_ELEMENT:
            self.pending_prefixes.append((prefix or "", uri or ""))
        else:
            self.pending_prefix_overflow += 1

    def _is_known_namespace(self, ns: str) -> bool:
        if not ns:
            return False
        if ns in self._known_namespaces or ns in {XSI_NS, XML_NS}:
            return True
        return any(token.lower() in ns.lower() for p in self.profiles for token in p.get("namespace_contains", []))

    def _namespace_profiles(self, ns: str) -> list[dict[str, Any]]:
        if not ns:
            return []
        return [
            p for p in self.profiles
            if ns in p.get("namespaces", [])
            or any(token.lower() in ns.lower() for token in p.get("namespace_contains", []))
        ]

    def _classify(self, ns: str, local: str, attr_locals: set[str]) -> tuple[str, frozenset[str], frozenset[str]]:
        namespace_profiles = self._namespace_profiles(ns)
        exact_profiles = frozenset(
            p["id"] for p in namespace_profiles
            if local in p.get("vocabulary", []) or local in p.get("root_names", [])
        )
        extension_profiles = frozenset(
            p["id"] for p in namespace_profiles
            if local.lower() in EXTENSION_NAMES and p["id"] in exact_profiles
        )
        inherited_extension = frozenset().union(*(f.extension_profile_ids for f in self.stack)) if self.stack else frozenset()
        if inherited_extension:
            return "STANDARD_ALLOWED_EXTENSION", exact_profiles, inherited_extension | extension_profiles
        if exact_profiles:
            is_foreign = all(next(p for p in self.profiles if p["id"] == pid)["priority"] != "P0" for pid in exact_profiles)
            return ("KNOWN_FOREIGN_STANDARD" if is_foreign else "STANDARD_EXACT"), exact_profiles, extension_profiles

        # A known local name is only a role-equivalent candidate when accompanied by
        # grammar, reference-model, or recognized semantic-scope evidence.
        role_profiles = []
        parent_local = self.stack[-1].local if self.stack else ""
        ancestor_locals = {f.local for f in self.stack}
        for profile in self.profiles:
            if local not in profile.get("vocabulary", []) and local not in profile.get("semantic_roles", []):
                continue
            grammar = f"{parent_local}>{local}" in profile.get("structures", [])
            reference = bool(attr_locals.intersection(profile.get("reference_attributes", [])))
            scoped = bool(ancestor_locals.intersection(profile.get("semantic_roles", [])))
            if grammar or reference or scoped:
                role_profiles.append(profile["id"])
        if role_profiles:
            return "STANDARD_ROLE_EQUIVALENT", frozenset(role_profiles), frozenset()
        if namespace_profiles:
            return "UNKNOWN", frozenset(), frozenset()
        return ("VENDOR_PRIVATE" if ns else "UNKNOWN"), frozenset(), frozenset()

    @staticmethod
    def _mechanism(ns: str, local: str) -> tuple[str | None, str | None]:
        low = local.lower()
        q = f"{{{ns}}}{local}" if ns else local
        if low in DEFINITION_NAMES or (ns == XML_NS and low == "id"):
            return ("definition", q)
        if low in REFERENCE_NAMES or low.endswith("ref") or low.endswith("idref"):
            return ("reference", q)
        if ns.endswith("XMI") and low == "id":
            return ("definition", q)
        return (None, None)

    def startElementNS(self, name, qname, attrs):  # noqa: N802
        ns, local = split_name(name)
        depth = len(self.stack) + 1
        if depth > MAX_DEPTH:
            raise UnsafeXML(f"XML nesting exceeds safety limit ({MAX_DEPTH})")
        path = tuple((f.ns, f.local) for f in self.stack) + ((ns, local),)
        attr_names: list[tuple[str, str]] = []
        ref_names: list[str] = []
        attr_locals = {split_name(attr_name)[1] for attr_name in attrs.getNames()}
        classification, exact_profile_ids, extension_profile_ids = self._classify(ns, local, attr_locals)
        self._next_instance_id += 1
        instance_id = self._next_instance_id
        parent_scope_id = self.stack[-1].instance_id if self.stack else 0
        inherited_network_id = self.stack[-1].network_scope_id if self.stack else 0
        network_scope_id = instance_id if local.lower() in NETWORK_NAMES else inherited_network_id
        for attr_name in attrs.getNames():
            ans, alocal = split_name(attr_name)
            value = attrs.getValue(attr_name)
            if len(attr_names) < MAX_ATTRIBUTES_PER_ELEMENT:
                attr_names.append((ans, alocal))
            else:
                self.element_attribute_overflow += 1
            self.attributes.add((ans, alocal))
            self.attribute_count += 1
            if ans == XSI_NS and alocal == "schemaLocation":
                for token in re.finditer(r"\S+", value):
                    self.schema_locations.add(token.group(0))
            elif ans == XSI_NS and alocal == "noNamespaceSchemaLocation":
                self.no_namespace_schema_locations.add(value)
            kind, mechanism = self._mechanism(ans, alocal)
            if kind and mechanism:
                normalized = normalize_ref(value)
                value_hash = digest(normalized)
                if kind == "definition":
                    self.db.execute(
                        "INSERT INTO definitions(value_hash, parent_scope_id, network_scope_id) VALUES (?, ?, ?)",
                        (value_hash, parent_scope_id, network_scope_id),
                    )
                else:
                    if len(ref_names) < MAX_REFERENCES_PER_ELEMENT:
                        ref_names.append(mechanism)
                    else:
                        self.element_reference_overflow += 1
                    self.db.execute(
                        "INSERT INTO refs(mechanism, value_hash, parent_scope_id, network_scope_id) VALUES (?, ?, ?, ?)",
                        (mechanism, value_hash, parent_scope_id, network_scope_id),
                    )
                self._db_writes += 1
                if self._db_writes % 10_000 == 0:
                    self.db.commit()
        if self.stack:
            if len(self.stack[-1].children) < MAX_CHILDREN_PER_ELEMENT or (ns, local) in self.stack[-1].children:
                self.stack[-1].children.add((ns, local))
            else:
                self.stack[-1].children_overflow += 1
                self.element_child_overflow += 1
            self.structures.add((self.stack[-1].local, local))
            self.structure_classes.add((self.stack[-1].ns, self.stack[-1].local, ns, local), classification)
        else:
            self.root = (ns, local)
        for _, uri in self.pending_prefixes:
            self.namespaces.add(uri)
        self.pending_prefixes.clear()
        self.namespaces.add(ns)
        self.elements.add((ns, local))
        self.qname_classes.add((ns, local), classification)
        self.paths.add(path)
        region = path[: min(4, len(path))]
        self.regions.add((region, classification))
        self.class_counts[classification] += 1
        self.element_count += 1
        self.max_depth = max(self.max_depth, depth)
        self.stack.append(Frame(
            ns, local, classification, path, tuple(sorted(attr_names)), tuple(sorted(ref_names)),
            instance_id, network_scope_id, exact_profile_ids, extension_profile_ids,
        ))

    def endElementNS(self, name, qname):  # noqa: N802
        frame = self.stack.pop()
        if frame.classification in {"VENDOR_PRIVATE", "UNKNOWN"}:
            parent = self.stack[-1] if self.stack else None
            family = (
                frame.ns,
                (parent.ns, parent.local) if parent else ("", ""),
                tuple(sorted(frame.children)[:20]),
                frame.attr_names,
                frame.ref_names,
                min(len(frame.path) // 4, 8),
                frame.classification,
                frame.children_overflow > 0,
            )
            self.families.add(family)


def load_registry(registry_path: Path) -> dict[str, Any]:
    with registry_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("registry_version") != "0.1.0" or not isinstance(data.get("profiles"), list):
        raise ValueError("Unsupported or malformed profile registry")
    return data


def _safe_label(value: str, known: set[str], prefix: str) -> str:
    if not value:
        return "(none)"
    if value in known or value in {XSI_NS, XML_NS}:
        return value
    return f"{prefix}_{digest(value)[:10]}"


def _read_declaration(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        head = fh.read(512)
    match = re.match(br"\s*<\?xml\s+([^?]+)\?>", head)
    if not match:
        return {"present": False, "version": None, "encoding": None}
    text = match.group(1).decode("ascii", "replace")
    version = re.search(r"version\s*=\s*['\"]([^'\"]+)", text, re.I)
    encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)", text, re.I)
    return {"present": True, "version": version.group(1) if version else None, "encoding": encoding.group(1) if encoding else None}


def _fraction_hits(observed: set[str], expected: list[str], cap: int = 5) -> tuple[int, list[str]]:
    hits = sorted(observed.intersection(expected))
    denominator = min(max(len(expected), 1), cap)
    return round(min(len(hits) / denominator, 1.0) * 100), hits


def score_profiles(handler: FingerprintHandler, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root_ns, root_local = handler.root or ("", "")
    locals_seen = {local for _, local in handler.elements.keys()}
    namespaces_seen = set(handler.namespaces.keys())
    structures_seen = {f"{a}>{b}" for a, b in handler.structures.keys()}
    attr_locals = {local for _, local in handler.attributes.keys()}
    schema_tokens = set(handler.schema_locations.keys()) | set(handler.no_namespace_schema_locations.keys())
    results = []
    for profile in profiles:
        exact_ns = [ns for ns in profile.get("namespaces", []) if ns in namespaces_seen]
        contains_ns = [token for token in profile.get("namespace_contains", []) if any(token.lower() in ns.lower() for ns in namespaces_seen)]
        root_hit = root_local in profile.get("root_names", [])
        identity_parts = (2 if exact_ns else 0) + (1 if contains_ns else 0) + (1 if root_hit else 0)
        identity = round(min(identity_parts / 3, 1) * 100) if profile.get("weights", {}).get("identity", 0) else 0
        vocabulary, vocab_hits = _fraction_hits(locals_seen, profile.get("vocabulary", []), 6)
        structure, structure_hits = _fraction_hits(structures_seen, profile.get("structures", []), 4)
        reference_model, reference_hits = _fraction_hits(attr_locals, profile.get("reference_attributes", []), 3)
        semantic_role, semantic_hits = _fraction_hits(locals_seen, profile.get("semantic_roles", []), 5)
        schema_hits = sorted(t for t in schema_tokens if any(ns in t for ns in profile.get("namespaces", [])) or any(f.lower() in t.lower() for f in profile.get("schema_files", [])))
        schema_evidence = 100 if schema_hits else 0
        patterns = profile.get("incompatible_patterns", {})
        incompatible_root = root_local in profile.get("incompatible_roots", []) or root_local in patterns.get("roots", [])
        incompatible_namespaces = sorted(
            token for token in patterns.get("namespace_contains", [])
            if any(token.lower() in ns.lower() for ns in namespaces_seen)
        )
        incompatible_structures = sorted(set(patterns.get("structures", [])).intersection(structures_seen))
        incompatible_references = sorted(set(patterns.get("reference_attributes", [])).intersection(attr_locals))
        # Foreign evidence conflicts globally only when this profile has no identity.
        # With positive identity, nested foreign regions stay local coverage evidence.
        conflict_parts = (4 if incompatible_root else 0)
        if not exact_ns and not root_hit:
            conflict_parts += min(len(incompatible_namespaces), 2)
            conflict_parts += min(len(incompatible_structures), 2)
            conflict_parts += min(len(incompatible_references), 2)
        conflict = min(conflict_parts * 20, 100)
        components = {
            "identity": identity,
            "vocabulary": vocabulary,
            "structure": structure,
            "reference_model": reference_model,
            "semantic_role": semantic_role,
            "schema_evidence": schema_evidence,
        }
        weights = profile["weights"]
        weighted = sum(components[k] * weights.get(k, 0) for k in components) / max(sum(weights.values()), 1)
        overall = max(0, round(weighted - conflict * 0.5))
        strong_identity = bool(exact_ns or contains_ns or root_hit)
        if overall >= 70 and strong_identity:
            confidence = "HIGH"
        elif overall >= 40 and (strong_identity or len(structure_hits) >= 2):
            confidence = "MEDIUM"
        elif overall >= 15:
            confidence = "LOW"
        else:
            confidence = "NOT_DETECTED"
        version_hint = None
        if profile["id"] == "PLCopen-TC6":
            version_hint = "2.01" if "http://www.plcopen.org/xml/tc6_0201" in exact_ns else ("2.0" if exact_ns else None)
        evidence = []
        if exact_ns:
            evidence.append({"effect": "+", "kind": "namespace", "detail": exact_ns})
        if contains_ns:
            evidence.append({"effect": "+", "kind": "namespace_pattern", "detail": contains_ns})
        if root_hit:
            evidence.append({"effect": "+", "kind": "root", "detail": root_local})
        for kind, hits in (("vocabulary", vocab_hits), ("structure", structure_hits), ("reference_model", reference_hits), ("semantic_role", semantic_hits)):
            if hits:
                evidence.append({"effect": "+", "kind": kind, "detail": hits[:20]})
        if schema_hits:
            evidence.append({"effect": "+", "kind": "schema_location", "detail": "profile schema token present"})
        if incompatible_root:
            evidence.append({"effect": "-", "kind": "incompatible_root", "detail": root_local})
        if incompatible_namespaces and not strong_identity:
            evidence.append({"effect": "-", "kind": "incompatible_namespace", "detail": incompatible_namespaces})
        if incompatible_structures and not strong_identity:
            evidence.append({"effect": "-", "kind": "incompatible_structure", "detail": incompatible_structures})
        if incompatible_references and not strong_identity:
            evidence.append({"effect": "-", "kind": "incompatible_reference_model", "detail": incompatible_references})
        if not strong_identity and weights.get("identity", 0):
            evidence.append({"effect": "-", "kind": "identity", "detail": "no profile namespace/root identity"})
        results.append({
            "profile": profile["id"], "standard_name": profile["standard_name"], "layer": profile["layer"],
            "priority": profile["priority"], "identity_score": identity, "vocabulary_score": vocabulary,
            "structure_score": structure, "reference_model_score": reference_model,
            "semantic_role_score": semantic_role, "schema_evidence_score": schema_evidence,
            "conflict_score": conflict, "overall_similarity": overall, "confidence": confidence,
            "version_hint": version_hint, "evidence": evidence,
            "conformance_claim": False,
        })
    return results


def _reference_inventory(db: sqlite3.Connection) -> dict[str, Any]:
    mechanism_count = db.execute("SELECT COUNT(DISTINCT mechanism) FROM refs").fetchone()[0]
    rows = db.execute(
        """
        SELECT r.mechanism, COUNT(*) total,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM definitions d WHERE d.value_hash=r.value_hash) THEN 1 ELSE 0 END) hits,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM definitions d WHERE d.value_hash=r.value_hash)
                         AND NOT EXISTS (SELECT 1 FROM definitions d WHERE d.value_hash=r.value_hash AND d.parent_scope_id=r.parent_scope_id)
                        THEN 1 ELSE 0 END) cross_parent,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM definitions d WHERE d.value_hash=r.value_hash)
                         AND NOT EXISTS (SELECT 1 FROM definitions d WHERE d.value_hash=r.value_hash AND d.network_scope_id=r.network_scope_id)
                        THEN 1 ELSE 0 END) cross_network
        FROM refs r
        GROUP BY r.mechanism ORDER BY r.mechanism LIMIT ?
        """, (MAX_REFERENCE_MECHANISMS,)
    ).fetchall()
    mechanisms = []
    for mechanism, total, hits, cross_parent, cross_network in rows:
        target_counts = db.execute("SELECT COUNT(*) FROM refs WHERE mechanism=? GROUP BY value_hash", (mechanism,)).fetchall()
        one_to_many = sum(1 for (count,) in target_counts if count > 1)
        mechanisms.append({
            "mechanism": mechanism,
            "reference_count": total,
            "resolved_count": hits or 0,
            "resolution_rate": round((hits or 0) / total, 4) if total else 0,
            "targets_referenced_once": len(target_counts) - one_to_many,
            "targets_referenced_multiple_times": one_to_many,
            "cross_parent_rate": round((cross_parent or 0) / (hits or 1), 4),
            "cross_network_candidate_rate": round((cross_network or 0) / (hits or 1), 4),
        })
    defs = db.execute("SELECT COUNT(*), COUNT(DISTINCT value_hash) FROM definitions").fetchone()
    refs = db.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
    return {
        "interpretation_warning": "Structural reference inventory only; no mechanism is asserted to be a visible connection.",
        "definition_count": defs[0], "unique_definition_count": defs[1], "reference_count": refs,
        "inventory_status": {"limit": MAX_REFERENCE_MECHANISMS, "tracked_keys": len(mechanisms),
                             "overflow_unique_mechanisms": max(mechanism_count - len(mechanisms), 0),
                             "complete": mechanism_count <= MAX_REFERENCE_MECHANISMS,
                             "truncated": mechanism_count > MAX_REFERENCE_MECHANISMS},
        "mechanisms": mechanisms,
    }


def scan(path: Path, registry: dict[str, Any], sanitized: bool = True) -> dict[str, Any]:
    if not sanitized:
        raise ValueError("v0.1 only supports sanitized reports")
    if not path.is_file():
        raise FileNotFoundError(path)
    start = time.perf_counter()
    with MemoryTrace() as memory, tempfile.TemporaryDirectory(prefix="xml-fingerprint-") as temp_dir, closing(sqlite3.connect(Path(temp_dir) / "refs.sqlite")) as db:
        db.executescript(
            "CREATE TABLE definitions(value_hash TEXT, parent_scope_id INTEGER, network_scope_id INTEGER);"
            "CREATE INDEX definitions_hash ON definitions(value_hash);"
            "CREATE TABLE refs(mechanism TEXT, value_hash TEXT, parent_scope_id INTEGER, network_scope_id INTEGER);"
            "CREATE INDEX refs_hash ON refs(value_hash);"
        )
        handler = FingerprintHandler(registry["profiles"], db)
        parser = xml.sax.make_parser()
        parser.setFeature(feature_namespaces, True)
        for feature in (feature_external_ges, feature_external_pes):
            try:
                parser.setFeature(feature, False)
            except (xml.sax.SAXNotRecognizedException, xml.sax.SAXNotSupportedException):
                pass
        parser.setEntityResolver(RejectingResolver())
        parser.setContentHandler(handler)
        try:
            parser.setProperty(property_lexical_handler, handler)
        except (xml.sax.SAXNotRecognizedException, xml.sax.SAXNotSupportedException) as exc:
            raise RuntimeError("The XML parser cannot enforce DOCTYPE rejection") from exc
        with path.open("rb") as stream:
            parser.parse(stream)
        db.commit()
        reference_inventory = _reference_inventory(db)
        scores = score_profiles(handler, registry["profiles"])
    elapsed = time.perf_counter() - start
    known_namespaces = {ns for p in registry["profiles"] for ns in p.get("namespaces", []) + p.get("extension_namespaces", [])}
    known_names = {
        n for p in registry["profiles"]
        for field_name in ("vocabulary", "reference_attributes", "root_names")
        for n in p.get(field_name, [])
    }
    def safe_ns(ns: str) -> str:
        return _safe_label(ns, known_namespaces, "private_namespace")
    def safe_name(name: tuple[str, str]) -> str:
        ns, local = name
        label = local if local in known_names or ns in known_namespaces else f"private_name_{digest(ns + '|' + local)[:10]}"
        return f"{{{safe_ns(ns)}}}{label}" if ns else label
    def safe_path(path_value: tuple[tuple[str, str], ...]) -> str:
        return "/" + "/".join(safe_name(x) for x in path_value)
    def safe_mechanism(mechanism: str) -> str:
        if mechanism.startswith("{") and "}" in mechanism:
            ns, local = mechanism[1:].split("}", 1)
            return safe_name((ns, local))
        if mechanism.lower() in REFERENCE_NAMES | DEFINITION_NAMES:
            return mechanism
        return safe_name(("", mechanism))

    for mechanism in reference_inventory["mechanisms"]:
        mechanism["mechanism"] = safe_mechanism(mechanism["mechanism"])

    namespace_inventory = [
        {"namespace": safe_ns(ns), "occurrences": count, "recognized": ns in known_namespaces or handler._is_known_namespace(ns)}
        for ns, count in sorted(handler.namespaces.items(), key=lambda x: (-x[1], x[0]))
    ]
    if handler.namespaces.overflow:
        namespace_inventory.append({"namespace": "(inventory_overflow)", "occurrences": handler.namespaces.overflow, "recognized": False})
    qname_inventory = [
        {"qname": safe_name(q), "occurrences": count}
        for q, count in sorted(handler.elements.items(), key=lambda x: (-x[1], x[0]))[:1000]
    ]
    attribute_inventory = [
        {"qname": safe_name(q), "occurrences": count}
        for q, count in sorted(handler.attributes.items(), key=lambda x: (-x[1], x[0]))[:1000]
    ]
    region_map: dict[tuple[tuple[str, str], ...], Counter[str]] = {}
    for (region, classification), count in handler.regions.items():
        region_map.setdefault(region, Counter())[classification] += count
    regions = []
    for index, (region, counts) in enumerate(sorted(region_map.items(), key=lambda x: -sum(x[1].values()))[:100], 1):
        regions.append({
            "region_id": f"R{index:03d}", "path_family": safe_path(region), "element_count": sum(counts.values()),
            "classification_counts": dict(sorted(counts.items())), "dominant_classification": counts.most_common(1)[0][0],
        })
    unknown_families = []
    for index, (family, count) in enumerate(sorted(handler.families.items(), key=lambda x: (-x[1], str(x[0])))[:100], 1):
        ns, parent, children, attrs, ref_names, depth_bucket, classification, children_truncated = family
        potential = "relation-like" if len(ref_names) >= 2 and not children else None
        unknown_families.append({
            "family_id": f"UF{index:03d}", "classification": classification, "occurrences": count,
            "namespace": safe_ns(ns), "parent": safe_name(parent) if parent[1] else "(document)",
            "child_qnames": [safe_name(x) for x in children], "attribute_qnames": [safe_name(x) for x in attrs],
            "reference_mechanisms": [safe_mechanism(x) for x in ref_names], "depth_bucket": depth_bucket,
            "children_truncated": children_truncated,
            "potential_role": potential, "is_final_conclusion": False,
        })
    total = handler.element_count or 1
    coverage_counts = {k: handler.class_counts.get(k, 0) for k in (
        "STANDARD_EXACT", "STANDARD_ALLOWED_EXTENSION", "STANDARD_ROLE_EQUIVALENT",
        "KNOWN_FOREIGN_STANDARD", "VENDOR_PRIVATE", "UNKNOWN")}
    coverage = {
        "interpretation_warning": "Structural coverage statistics; not a claim that the document was assembled from standards in these proportions.",
        "element_occurrence_coverage": {k: {"count": v, "ratio": round(v / total, 6)} for k, v in coverage_counts.items()},
        "unique_qname_coverage": handler.qname_classes.coverage(),
        "structure_signature_coverage": handler.structure_classes.coverage(),
        "approx_byte_coverage": None,
    }
    extensions = [{"type": k, "element_count": coverage_counts[k]} for k in (
        "STANDARD_ALLOWED_EXTENSION", "KNOWN_FOREIGN_STANDARD", "VENDOR_PRIVATE", "UNKNOWN")]
    detected = [x for x in scores if x["confidence"] not in {"NOT_DETECTED"}]
    exchange = [x for x in detected if x["layer"] == "PLC_EXCHANGE"]
    primary = max(exchange, key=lambda x: x["overall_similarity"], default=None)
    secondary = sorted((x for x in detected if not primary or x["profile"] != primary["profile"]), key=lambda x: -x["overall_similarity"])
    root_ns, root_local = handler.root or ("", "")
    serialization_scores = [x for x in scores if x["layer"] == "SERIALIZATION_SUBSTRATE"]
    run_metadata = {
        "tool_version": "0.1.0", "registry_version": registry["registry_version"], "sanitized": True,
        "scan_time_seconds": round(elapsed, 6), "peak_memory_bytes": 0, "peak_memory_scope": "Python allocations measured by tracemalloc, including report construction",
        "element_count": handler.element_count, "attribute_count": handler.attribute_count, "max_depth": handler.max_depth,
        "file_size_bytes": path.stat().st_size, "passes": 1,
    }
    report = {
        "document_identity": {
            "source": "sanitized-input.xml", "xml_declaration": _read_declaration(path),
            "root_qname": safe_name((root_ns, root_local)) if root_local else None,
            "doctype": None, "processing_instructions": {
                (target if target == "xml-stylesheet" else f"private_pi_{digest(target)[:10]}"): count
                for target, count in sorted(handler.pi_names.items())
            },
            "schema_locations": [
                {"token": _safe_label(v, known_namespaces, "schema_location"), "occurrences": count}
                for v, count in sorted(handler.schema_locations.items())
            ],
            "no_namespace_schema_locations": [
                {"token": f"schema_location_{digest(v)[:10]}", "occurrences": count}
                for v, count in sorted(handler.no_namespace_schema_locations.items())
            ],
        },
        "serialization_substrate": {"profiles": serialization_scores},
        "standard_profile_scores": scores,
        "coverage": coverage,
        "regions": regions,
        "extensions": extensions,
        "unknown_families": unknown_families,
        "recommended_primary_standard": primary["profile"] if primary and primary["overall_similarity"] >= 25 else None,
        "recommended_secondary_profiles": [x["profile"] for x in secondary if x["overall_similarity"] >= 25],
        "confidence": {
            "method": "deterministic weighted rules with negative evidence",
            "schema_validation_performed": False,
            "validation_status": "synthetic_fixture_regression_only",
            "official_sample_validation_completed": False,
        },
        "next_action": {"private_islands": [x["family_id"] for x in unknown_families[:10]], "recommendation": "Review the highest-frequency private/unknown structural families before connection forensics."},
        "inventories": {"namespaces": namespace_inventory, "element_qnames": qname_inventory, "attribute_qnames": attribute_inventory},
        "inventory_completeness": {
            "namespaces": handler.namespaces.status(),
            "element_qnames": {**handler.elements.status(), "output_limit": 1000, "output_truncated": len(handler.elements.data) > 1000},
            "attribute_qnames": {**handler.attributes.status(), "output_limit": 1000, "output_truncated": len(handler.attributes.data) > 1000},
            "structures": handler.structures.status(),
            "paths": {**handler.paths.status(), "output_limit": 1000, "output_truncated": len(handler.paths.data) > 1000},
            "regions": {**handler.regions.status(), "output_limit": 100, "output_truncated": len(region_map) > 100},
            "unknown_families": {**handler.families.status(), "output_limit": 100, "output_truncated": len(handler.families.data) > 100},
            "schema_locations": handler.schema_locations.status(),
            "no_namespace_schema_locations": handler.no_namespace_schema_locations.status(),
            "processing_instruction_targets": handler.pi_names.status(),
            "per_element": {
                "children_limit": MAX_CHILDREN_PER_ELEMENT, "children_overflow": handler.element_child_overflow,
                "attributes_limit": MAX_ATTRIBUTES_PER_ELEMENT, "attributes_overflow": handler.element_attribute_overflow,
                "reference_names_limit": MAX_REFERENCES_PER_ELEMENT, "reference_names_overflow": handler.element_reference_overflow,
                "namespace_declarations_limit": MAX_ATTRIBUTES_PER_ELEMENT, "namespace_declarations_overflow": handler.pending_prefix_overflow,
                "complete": not any((handler.element_child_overflow, handler.element_attribute_overflow, handler.element_reference_overflow, handler.pending_prefix_overflow)),
            },
        },
        "reference_mechanisms": reference_inventory,
        "run_metadata": run_metadata,
    }
    report["inventories"]["structural_paths"] = [
        {"path": safe_path(path_key), "occurrences": count}
        for path_key, count in sorted(handler.paths.items(), key=lambda x: (-x[1], x[0]))[:1000]
    ]
    run_metadata["scan_time_seconds"] = round(time.perf_counter() - start, 6)
    run_metadata["peak_memory_bytes"] = memory.finish()
    return report


def write_reports(report: dict[str, Any], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "fingerprint_report.json": report,
        "standard_scores.json": report["standard_profile_scores"],
        "namespace_inventory.json": {"inventories": report["inventories"], "inventory_status": report["inventory_completeness"]},
        "structural_regions.json": {"items": report["regions"], "inventory_status": report["inventory_completeness"]["regions"]},
        "extension_inventory.json": report["extensions"],
        "unknown_families.json": {"items": report["unknown_families"], "inventory_status": report["inventory_completeness"]["unknown_families"]},
        "reference_mechanisms.json": report["reference_mechanisms"],
        "run_metadata.json": report["run_metadata"],
    }
    paths = []
    for name, payload in files.items():
        target = out_dir / name
        with target.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        paths.append(target)
    return paths
