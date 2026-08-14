from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from importlib.resources import files

from xml_fingerprint.cli import main
from xml_fingerprint.scanner import UnsafeXML, load_registry, scan, write_reports

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "standards" / "registry.json")

# These are deliberately small synthetic fingerprints, not official conformance
# samples. Their namespace/schema facts are derived from the stated official
# artifacts. Official-sample validation remains a later phase.
FIXTURE_PROVENANCE = {
    "iec61131_10": {"kind": "synthetic", "source_artifact": "iec_61131-10_ed1_fdis.zip", "source_artifact_sha256": "154EFD2F9159A96C1C36688541256060361F60E5D6DC3932BA749F09E502F992"},
    "tc6": {"kind": "synthetic", "source_artifact": "plcopen_tc6_201.zip", "source_artifact_sha256": "2E412F478A21FDEDAF758CB7DA19E333BCBF95E30B56EE24293F09DC32244B99"},
}


SAMPLES = {
    "iec61131_10": """<?xml version="1.0"?>
<Project xmlns="www.iec.ch/public/TC65SC65BWG7TF10"><FileHeader/><ContentHeader/>
<Types><Pous><Pou><Body><FBD><Block localId="1"><ConnectionPointIn><Connection refLocalId="2"/></ConnectionPointIn></Block></FBD></Body></Pou></Pous></Types></Project>""",
    "tc6": """<?xml version="1.0"?>
<project xmlns="http://www.plcopen.org/xml/tc6_0201"><fileHeader/><contentHeader/><types><pous><pou><body><FBD>
<block localId="1"><connectionPointIn><connection refLocalId="2"/></connectionPointIn></block></FBD></body></pou></pous></types></project>""",
    "semantic": """<VendorProject><Configuration><Resource><Program><FunctionBlock><FBD><Step/><Transition/></FBD></FunctionBlock></Program></Resource></Configuration></VendorProject>""",
    "automationml": """<CAEXFile xmlns="http://www.dke.de/CAEX"><InstanceHierarchy><InternalElement ID="a"><ExternalInterface ID="b"/></InternalElement><InternalLink RefPartnerSideA="a" RefPartnerSideB="b"/></InstanceHierarchy></CAEXFile>""",
    "xmi": """<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" xmi:version="2.0"><model:Node xmlns:model="urn:private:model" xmi:id="n1"/><model:Relation xmlns:model="urn:private:model" href="#n1"/></xmi:XMI>""",
    "uanodeset": """<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd"><NamespaceUris/><Models/><UAObject NodeId="ns=1;i=1"><References><Reference ReferenceType="HasComponent">ns=1;i=2</Reference></References></UAObject></UANodeSet>""",
    "scl": """<SCL xmlns="http://www.iec.ch/61850/2003/SCL"><IED><AccessPoint><Server><LDevice><LN/></LDevice></Server></AccessPoint></IED><DataTypeTemplates/></SCL>""",
    "cim": """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:cim="http://iec.ch/TC57/CIM100#"><cim:PowerTransformer rdf:ID="pt1"/><rdf:Description rdf:about="#pt1"/></rdf:RDF>""",
}


def run_sample(xml: str):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "sample.xml"
        path.write_text(xml, encoding="utf-8")
        return scan(path, REGISTRY)


def score(report, profile):
    return next(x for x in report["standard_profile_scores"] if x["profile"] == profile)


class DetectorTests(unittest.TestCase):
    def test_five_p0_profiles_positive(self):
        cases = [
            ("iec61131_10", "IEC61131-10", 65),
            ("tc6", "PLCopen-TC6", 65),
            ("semantic", "IEC61131-3-Semantic", 60),
            ("automationml", "AutomationML-CAEX", 65),
            ("xmi", "XMI-EMF", 65),
        ]
        for sample, profile, minimum in cases:
            with self.subTest(profile=profile):
                self.assertGreaterEqual(score(run_sample(SAMPLES[sample]), profile)["overall_similarity"], minimum)

    def test_negative_cross_profile_guards(self):
        for sample in ("uanodeset", "scl", "xmi", "cim"):
            with self.subTest(sample=sample):
                report = run_sample(SAMPLES[sample])
                self.assertLess(score(report, "IEC61131-10")["overall_similarity"], 25)
                self.assertLess(score(report, "PLCopen-TC6")["overall_similarity"], 25)

    def test_auxiliary_profiles_identify_nodeset_and_scl(self):
        self.assertGreaterEqual(score(run_sample(SAMPLES["uanodeset"]), "OPC-UA-NodeSet")["overall_similarity"], 50)
        self.assertGreaterEqual(score(run_sample(SAMPLES["scl"]), "IEC61850-SCL")["overall_similarity"], 50)

    def test_doctype_is_rejected_before_entity_expansion(self):
        xml = '<!DOCTYPE root [<!ENTITY x "secret">]><root>&x;</root>'
        with self.assertRaises(UnsafeXML):
            run_sample(xml)

    def test_report_is_sanitized_and_has_required_artifacts(self):
        report = run_sample('<?secretTarget hidden?><SecretProduct xmlns="urn:company:confidential"><Device serial="ABC-123"><relation from="customer-A" to="customer-B" secretLinkRef="customer-C"/></Device></SecretProduct>')
        serialized = json.dumps(report, ensure_ascii=False)
        for secret in ("SecretProduct", "company:confidential", "ABC-123", "customer-A", "customer-B", "customer-C", "secretLinkRef", "secretTarget"):
            self.assertNotIn(secret, serialized)
        for key in (
            "document_identity", "serialization_substrate", "standard_profile_scores", "coverage", "regions",
            "extensions", "unknown_families", "recommended_primary_standard", "recommended_secondary_profiles",
            "confidence", "next_action",
        ):
            self.assertIn(key, report)
        with tempfile.TemporaryDirectory() as td:
            paths = write_reports(report, Path(td))
            self.assertEqual({p.name for p in paths}, {
                "fingerprint_report.json", "standard_scores.json", "namespace_inventory.json", "structural_regions.json",
                "extension_inventory.json", "unknown_families.json", "reference_mechanisms.json", "run_metadata.json",
            })

    def test_core_is_stable_across_repeated_runs(self):
        first = run_sample(SAMPLES["tc6"])
        second = run_sample(SAMPLES["tc6"])
        for key in ("document_identity", "standard_profile_scores", "coverage", "regions", "extensions", "unknown_families", "reference_mechanisms"):
            self.assertEqual(first[key], second[key])

    def test_reference_inventory_resolves_without_emitting_values(self):
        report = run_sample('<root><node id="n1"/><edge source="n1" target="missing"/></root>')
        inventory = report["reference_mechanisms"]
        self.assertEqual(inventory["definition_count"], 1)
        self.assertEqual(inventory["reference_count"], 2)
        resolved = sum(x["resolved_count"] for x in inventory["mechanisms"])
        self.assertEqual(resolved, 1)

    def test_classification_requires_qname_and_context_evidence(self):
        ns = "www.iec.ch/public/TC65SC65BWG7TF10"
        report = run_sample(f'<Project xmlns="{ns}"><MadeUp/><AddData><v:Secret xmlns:v="urn:vendor"/></AddData></Project>')
        coverage = report["coverage"]["element_occurrence_coverage"]
        self.assertEqual(coverage["STANDARD_EXACT"]["count"], 2)  # Project + AddData only
        self.assertEqual(coverage["STANDARD_ALLOWED_EXTENSION"]["count"], 1)
        self.assertEqual(coverage["UNKNOWN"]["count"], 1)
        weak = run_sample('<root xmlns:v="urn:vendor"><v:block/></root>')
        self.assertEqual(weak["coverage"]["element_occurrence_coverage"]["STANDARD_ROLE_EQUIVALENT"]["count"], 0)

    def test_reference_scope_uses_instance_identity(self):
        report = run_sample('<root><FBD><node id="n1"/></FBD><FBD><edge ref="n1"/></FBD></root>')
        mechanism = next(x for x in report["reference_mechanisms"]["mechanisms"] if x["mechanism"] == "ref")
        self.assertEqual(mechanism["cross_parent_rate"], 1.0)
        self.assertEqual(mechanism["cross_network_candidate_rate"], 1.0)

    def test_unknown_family_ignores_attribute_order(self):
        report = run_sample('<root xmlns:v="urn:vendor"><v:item beta="2" alpha="1"/><v:item alpha="3" beta="4"/></root>')
        families = [x for x in report["unknown_families"] if x["occurrences"] == 2]
        self.assertEqual(len(families), 1)

    def test_nested_foreign_region_does_not_cancel_real_plc_identity(self):
        xml = '<Envelope><Project xmlns="www.iec.ch/public/TC65SC65BWG7TF10"><Types><Pous><Pou><Body><FBD><Block localId="1"/></FBD></Body></Pou></Pous></Types><ua:UANodeSet xmlns:ua="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd"><ua:UAObject NodeId="i=1"/></ua:UANodeSet></Project></Envelope>'
        result = score(run_sample(xml), "IEC61131-10")
        self.assertGreaterEqual(result["overall_similarity"], 55)
        self.assertEqual(result["conflict_score"], 0)

    def test_coverage_and_bounded_inventory_metadata(self):
        report = run_sample(SAMPLES["tc6"])
        expected = {"STANDARD_EXACT", "STANDARD_ALLOWED_EXTENSION", "STANDARD_ROLE_EQUIVALENT", "KNOWN_FOREIGN_STANDARD", "VENDOR_PRIVATE", "UNKNOWN"}
        self.assertEqual(set(report["coverage"]["unique_qname_coverage"]["classes"]), expected)
        self.assertEqual(set(report["coverage"]["structure_signature_coverage"]["classes"]), expected)
        for key in ("paths", "regions", "unknown_families", "schema_locations", "processing_instruction_targets"):
            self.assertIn("truncated", report["inventory_completeness"][key])

    def test_packaged_registry_resource(self):
        packaged = load_registry(files("standards").joinpath("registry.json"))
        self.assertEqual(packaged["registry_version"], "0.1.0")
        by_id = {p["id"]: p for p in packaged["profiles"]}
        self.assertTrue(all(p["source_access_date"] == "2026-08-14" and p["evidence_status"] for p in packaged["profiles"]))
        self.assertEqual(by_id["IEC61131-10"]["artifact"]["sha256"], FIXTURE_PROVENANCE["iec61131_10"]["source_artifact_sha256"])
        self.assertEqual(by_id["PLCopen-TC6"]["artifact"]["sha256"], FIXTURE_PROVENANCE["tc6"]["source_artifact_sha256"])

    def test_high_cardinality_inputs_are_bounded_and_disclosed(self):
        pis = "".join(f"<?p{i} x?>" for i in range(1001))
        schemas = " ".join(f"urn:s{i} local{i}.xsd" for i in range(1100))
        attrs = " ".join(f'a{i}="x"' for i in range(2100))
        children = "".join(f"<n{i}/>" for i in range(10050))
        xml = f'{pis}<root xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="{schemas}" {attrs}>{children}</root>'
        report = run_sample(xml)
        status = report["inventory_completeness"]
        self.assertTrue(status["element_qnames"]["truncated"])
        self.assertTrue(status["schema_locations"]["truncated"])
        self.assertTrue(status["processing_instruction_targets"]["truncated"])
        self.assertGreater(status["per_element"]["children_overflow"], 0)
        self.assertGreater(status["per_element"]["attributes_overflow"], 0)

    def test_cli_scan_and_explain_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.xml"
            output = Path(td) / "report"
            source.write_text(SAMPLES["tc6"], encoding="utf-8")
            self.assertEqual(main(["scan", str(source), "--out", str(output), "--explain-profile", "PLCopen-TC6"]), 0)
            self.assertEqual(main(["explain", str(output / "fingerprint_report.json"), "--profile", "PLCopen-TC6"]), 0)


if __name__ == "__main__":
    unittest.main()
