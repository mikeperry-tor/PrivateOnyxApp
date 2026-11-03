from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlencode

from tools.components import audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="component-audit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                         "manifests": [{"digest": "sha256:" + "a" * 64,
                                        "size": 42, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                                        "platform": {"os": "linux", "architecture": "arm64"}}]}
        self.digest = "sha256:" + hashlib.sha256(json.dumps(self.manifest).encode()).hexdigest()
        (self.root / "tools/components").mkdir(parents=True)
        (self.root / "executor").mkdir()
        (self.root / "executor/requirements.in").write_text("cryptography==50.0.2\nsympy==1.14.0\n")
        (self.root / "stack.versions.env").write_text(
            "ONYX_IMAGE_TAG=v1.0.0\nOS_SECURITY_UPDATE_REVISION=20261102.2\n"
            f"BASE_IMAGE=docker.io/library/alpine:3.22@{self.digest}\n")
        self.inventory = {
            "components": {"onyx": {"owner": "Onyx release", "selection": ["ONYX_IMAGE_TAG"]},
                           "namespace": {"owner": "wrapper infrastructure", "selection": ["BASE_IMAGE"]},
                           "executor": {"owner": "wrapper Python lock", "selection": []}},
            "onyx_services": {}, "registry_digests": {},
        }
        self.save_inventory()
        self.token_url = "https://auth.docker.io/token?" + urlencode({
            "service": "registry.docker.io", "scope": "repository:library/alpine:pull"})
        self.manifest_url = "https://registry-1.docker.io/v2/library/alpine/manifests/3.22"
        self.release = audit.Source("owner-release", "onyx", "release", "https://api.github.com/repos/example/onyx/releases/latest")
        self.advisory = audit.Source("advisories", "onyx", "github-advisories", "https://api.github.com/repos/example/onyx/security-advisories?per_page=100")
        self.vendor = audit.Source("vendor", "namespace", "vendor", "https://vendor.example.invalid/security", True, "vendor")
        self.wheel = audit.Source("wheel-release", "executor", "pypi-release", "https://pypi.org/pypi/cryptography/json")
        self.responses = {
            "_owner_model": {"services": {}},
            self.token_url: {"json": {"token": "fixture-anonymous-token-never-report"}},
            self.manifest_url: {"json": self.manifest, "headers": {"Docker-Content-Digest": self.digest}},
            self.release.url: {"json": {"tag_name": "v1.1.0", "published_at": "2026-11-01T00:00:00Z",
                                       "html_url": "https://github.com/example/onyx/releases/tag/v1.1.0"}},
            self.advisory.url: {"json": [{"ghsa_id": "GHSA-abcd-1234-eeee", "cve_id": "CVE-2026-12345",
                                          "summary": "Fixture advisory", "html_url": "https://github.com/example/advisories/GHSA-abcd-1234-eeee"}]},
            self.vendor.url: {"body": "<html><title>Vendor Security</title><h2>TS-2026-013</h2><p>CVE-2026-54321</p></html>"},
            self.wheel.url: {"json": {"info": {"name": "cryptography", "version": "50.0.2"}}},
        }

    def save_inventory(self):
        (self.root / "tools/components/ownership.json").write_text(json.dumps(self.inventory))

    def report(self, *sources):
        return audit.run_audit(self.root, audit.FixtureTransport(self.responses), sources,
                               owner_model_resolver=lambda *_: self.responses["_owner_model"])

    def test_report_separates_declared_versions_registry_and_installed_evidence(self):
        report = self.report(self.release, self.advisory, self.vendor, self.wheel)
        self.assertEqual(report["status"], "evidence_collected")
        self.assertEqual(report["security_assessment"]["status"], "unresolved")
        self.assertEqual(report["registry"][0]["comparison"], "matches_reviewed")
        self.assertEqual(report["registry"][0]["resolved_digest"], self.digest)
        self.assertTrue(report["registry"][0]["digest_header_verified"])
        self.assertEqual(report["registry"][0]["platforms"], [{"os": "linux", "architecture": "arm64"}])
        self.assertEqual(report["components"]["executor"]["declared_requirements"]["cryptography"], "50.0.2")
        self.assertEqual(report["sources"][0]["comparison"], "review_candidate")
        self.assertEqual(report["sources"][1]["advisories"][0]["id"], "GHSA-abcd-1234-eeee")
        self.assertEqual(report["sources"][1]["advisories"][0]["applicability"], "unresolved")
        self.assertEqual(report["sources"][2]["identifiers"], ["CVE-2026-54321", "TS-2026-013"])
        self.assertEqual(report["sources"][3]["comparison"], "matches_selection")
        kinds = {value["kind"]: value for value in report["installed_evidence"]}
        self.assertEqual(kinds["system_packages"]["status"], "unverified")
        self.assertEqual(kinds["compiled_go_dependencies"]["status"], "unverified")
        self.assertEqual(kinds["python_wheel_and_bundled_openssl"]["bundled_openssl_status"], "unverified")
        self.assertNotIn("fixture-anonymous-token-never-report", json.dumps(report))

    def test_changed_tag_bytes_require_review_without_mutating_committed_selection(self):
        before = (self.root / "stack.versions.env").read_bytes()
        changed = dict(self.manifest, annotations={"published": "changed behind unchanged tag"})
        digest = "sha256:" + hashlib.sha256(json.dumps(changed).encode()).hexdigest()
        self.responses[self.manifest_url] = {"json": changed, "headers": {"Docker-Content-Digest": digest}}
        report = self.report()
        self.assertEqual(report["registry"][0]["comparison"], "review_required")
        self.assertEqual(report["registry"][0]["reviewed_digest"], self.digest)
        self.assertEqual(report["registry"][0]["reviewed_artifact_availability"], "unverified")
        self.assertIsNone(report["registry"][0]["reviewed_artifact_platforms"])
        self.assertEqual((self.root / "stack.versions.env").read_bytes(), before)

    def test_registry_content_digest_mismatch_is_unavailable(self):
        self.responses[self.manifest_url]["headers"]["Docker-Content-Digest"] = "sha256:" + "b" * 64
        report = self.report()
        self.assertEqual(report["registry"][0]["status"], "unavailable")
        self.assertEqual(report["registry"][0]["reason"], "registry_content_digest_mismatch")
        self.assertEqual(report["coverage"]["unavailable_required"], 1)

    def test_manifest_body_digest_remains_verified_without_optional_header(self):
        self.responses[self.manifest_url]["headers"] = {}
        record = self.report()["registry"][0]
        self.assertEqual(record["resolved_digest"], self.digest)
        self.assertFalse(record["digest_header_verified"])

    def test_bad_registry_token_or_index_is_unavailable(self):
        for token in (None, "", "bad\nheader"):
            with self.subTest(token=token):
                self.responses[self.token_url] = {"json": {"token": token}}
                self.assertEqual(self.report()["registry"][0]["reason"], "invalid_anonymous_registry_token")
        self.responses[self.token_url] = {"json": {"token": "fixture-token"}}
        for manifests, reason in (([], "empty_registry_index"),
                                  ([{"platform": None}], "invalid_registry_platform_records")):
            with self.subTest(reason=reason):
                self.responses[self.manifest_url] = {"json": dict(self.manifest, manifests=manifests)}
                self.assertEqual(self.report()["registry"][0]["reason"], reason)

    def test_empty_advisory_feed_does_not_classify_artifacts_clean(self):
        self.responses[self.advisory.url] = {"json": []}
        report = self.report(self.advisory)
        self.assertEqual(report["sources"][0]["advisories"], [])
        self.assertEqual(report["sources"][0]["applicability"], "unresolved")
        self.assertEqual(report["security_assessment"]["status"], "unresolved")

    def test_http_json_pagination_and_vendor_challenge_failures_are_explicit(self):
        failures = (
            (self.advisory, {"status": 403}, "http_403"),
            (self.advisory, {"body": "invalid JSON"}, "invalid_json_response"),
            (self.advisory, {"json": [{"ghsa_id": None, "summary": "bad"}]}, "invalid_advisory_record"),
            (self.advisory, {"json": [], "headers": {"Link": '<https://api.github.com/next>; rel="next"'}}, "advisory_pagination_incomplete"),
            (self.vendor, {"body": "<title>Just a moment</title>"}, "unrecognized_vendor_page"),
            (self.release, {"json": {"message": "not a release"}}, "invalid_owner_release_response"),
        )
        for source, response, reason in failures:
            with self.subTest(reason=reason):
                self.responses[source.url] = response
                report = self.report(source)
                self.assertEqual(report["sources"][0]["status"], "unavailable")
                self.assertEqual(report["sources"][0]["reason"], reason)
                self.assertEqual(report["coverage"]["unavailable_required"], 1)

    def test_optional_unavailable_release_feed_is_partial_evidence(self):
        optional = self.release._replace(required=False)
        self.responses[optional.url] = {"status": 404}
        report = self.report(optional)
        self.assertEqual(report["status"], "partial_evidence")
        self.assertEqual(report["coverage"], {"unavailable_required": 0, "unavailable_optional": 1})

    def test_network_exception_does_not_expose_proxy_credentials(self):
        delegate = audit.FixtureTransport(self.responses)
        def transport(url, headers):
            if url == self.advisory.url:
                raise URLError("private-user:private-password@proxy.invalid")
            return delegate(url, headers)
        report = audit.run_audit(self.root, transport, (self.advisory,), owner_model_resolver=lambda *_: {"services": {}})
        rendered = json.dumps(report)
        self.assertNotIn("private-password", rendered)
        self.assertEqual(report["sources"][0]["reason"], "network_unavailable")

    def test_private_operator_files_are_never_read(self):
        private = self.root / ".env.wrapper"
        private.write_text("PRIVATE_FIXTURE=never-read\n")
        read = Path.read_text
        def guarded(path, *args, **kwargs):
            if path == private:
                raise AssertionError("private operator configuration read")
            return read(path, *args, **kwargs)
        with patch.object(Path, "read_text", guarded):
            report = self.report()
        self.assertNotIn("never-read", json.dumps(report))

    def test_registry_inventory_errors_fail_before_any_network(self):
        self.inventory["components"]["namespace"]["selection"] = []
        self.save_inventory()
        with self.assertRaisesRegex(audit.Unavailable, "missing_component_owner"):
            audit.run_audit(self.root, lambda *_: self.fail("network used"), ())

    def test_conflicting_pin_and_ledger_digest_cannot_be_silently_combined(self):
        self.inventory["registry_digests"] = {"docker.io/library/alpine:3.22": "sha256:" + "b" * 64}
        self.save_inventory()
        with self.assertRaisesRegex(audit.Unavailable, "conflicting_reviewed_digest"):
            self.report()
        self.inventory["registry_digests"] = {}
        self.inventory["components"]["namespace"]["selection"].append("OTHER_BASE")
        self.save_inventory()
        with (self.root / "stack.versions.env").open("a") as stream:
            stream.write("OTHER_BASE=docker.io/library/alpine:3.22@sha256:" + "b" * 64 + "\n")
        with self.assertRaisesRegex(audit.Unavailable, "conflicting_reviewed_digest"):
            self.report()

    def test_bare_selected_tag_requires_its_own_reviewed_mapping(self):
        pins = self.root / "stack.versions.env"
        pins.write_text(pins.read_text().replace("@" + self.digest, ""))
        with self.assertRaisesRegex(audit.Unavailable, "missing_reviewed_registry_digest"):
            self.report()
        self.inventory["registry_digests"] = {"docker.io/library/alpine:3.22": self.digest}
        self.save_inventory()
        self.assertEqual(self.report()["registry"][0]["comparison"], "matches_reviewed")

    def test_current_onyx_tag_requires_current_app_digest_and_rejects_stale(self):
        repo = "docker.io/example/onyx-backend"
        self.inventory["onyx_services"] = {"api": {"repository": repo, "application": True}}
        self.inventory["registry_digests"] = {repo + ":v1.0.0": self.digest}
        pins = self.root / "stack.versions.env"
        pins.write_text(pins.read_text().replace("ONYX_IMAGE_TAG=v1.0.0", "ONYX_IMAGE_TAG=v1.1.0"))
        self.save_inventory()
        with self.assertRaisesRegex(audit.Unavailable, "missing_selected_application_digest"):
            self.report()
        self.inventory["registry_digests"][repo + ":v1.1.0"] = self.digest
        self.save_inventory()
        with self.assertRaisesRegex(audit.Unavailable, "stale_application_digest_inventory"):
            self.report()

    def select_searxng(self):
        pins = audit.read_pins(self.root / "stack.versions.env")
        pins["SEARXNG_IMAGE_TAG"] = "2026.7.15-7b2199ecd"
        self.inventory["components"]["searxng"] = {
            "owner": "SearXNG source release", "selection": ["SEARXNG_IMAGE_TAG"]}
        reference = "docker.io/searxng/searxng:" + pins["SEARXNG_IMAGE_TAG"]
        self.inventory["registry_digests"][reference] = self.digest
        return pins, reference

    def test_selected_source_repository_binding_and_missing_direct_cli_entry(self):
        pins, reference = self.select_searxng()
        entry = next(item for item in audit.registry_catalog(pins, self.inventory)
                     if item["reference"] == reference)
        self.assertEqual(entry["components"], ["searxng"])
        del self.inventory["registry_digests"][reference]
        self.save_inventory()
        with (self.root / "stack.versions.env").open("a") as stream:
            stream.write("SEARXNG_IMAGE_TAG=" + pins["SEARXNG_IMAGE_TAG"] + "\n")
        fixture = self.root / "public-responses.json"
        fixture.write_text(json.dumps(self.responses))
        output = StringIO()
        with redirect_stdout(output), patch.object(audit, "registry_evidence", side_effect=AssertionError("network used")):
            result = audit.main(["--root", str(self.root), "--fixture", str(fixture)])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "invalid_local_audit_inputs")
        with self.assertRaisesRegex(audit.Unavailable, "missing_selected_source_digest"):
            audit.registry_catalog(pins, self.inventory)

    def test_same_tag_unrelated_repository_and_stale_source_are_unowned(self):
        pins, reference = self.select_searxng()
        for extra in ("docker.io/unrelated/not-searx:" + pins["SEARXNG_IMAGE_TAG"],
                      "docker.io/searxng/searxng:2026.1.1-stale"):
            with self.subTest(reference=extra):
                self.inventory["registry_digests"][extra] = self.digest
                with self.assertRaisesRegex(audit.Unavailable, "registry_selection_owner_ambiguous"):
                    audit.registry_catalog(pins, self.inventory)
                del self.inventory["registry_digests"][extra]

    def test_unselected_known_service_repository_does_not_acquire_ownership(self):
        pins = audit.read_pins(self.root / "stack.versions.env")
        repo = "docker.io/onyxdotapp/code-interpreter"
        pins["CODE_INTERPRETER_UPSTREAM_IMAGE"] = repo + ":0.4.9@" + self.digest
        self.inventory["components"]["controller"] = {
            "owner": "selected controller release", "selection": ["CODE_INTERPRETER_UPSTREAM_IMAGE"]}
        self.inventory["onyx_services"] = {"code-interpreter": {"repository": repo}}
        selected = next(item for item in audit.registry_catalog(pins, self.inventory)
                        if item["reference"] == repo + ":0.4.9")
        self.assertEqual(selected["components"], ["controller"])
        self.inventory["registry_digests"][repo + ":0.4.10"] = self.digest
        with self.assertRaisesRegex(audit.Unavailable, "registry_selection_owner_ambiguous"):
            audit.registry_catalog(pins, self.inventory)

    def test_owner_parser_failure_is_required_unavailable(self):
        def failed(*_):
            raise subprocess.CalledProcessError(1, "engine", stderr="private proxy secret")
        report = audit.run_audit(self.root, audit.FixtureTransport(self.responses), (), owner_model_resolver=failed)
        self.assertEqual(report["owner_alignment"]["status"], "unavailable")
        self.assertEqual(report["owner_alignment"]["reason"], "native_owner_parser_unavailable")
        self.assertEqual(report["coverage"]["unavailable_required"], 1)
        self.assertNotIn("private proxy secret", json.dumps(report))
        missing = audit.run_audit(self.root, audit.FixtureTransport(self.responses), ())
        self.assertEqual(missing["owner_alignment"]["reason"], "owner_model_not_supplied")

    def test_owner_model_must_reconcile_and_inherited_ledger_must_match(self):
        repo = "docker.io/library/redis"
        self.inventory["onyx_services"] = {"cache": {"repository": repo, "inherited": True}}
        self.inventory["registry_digests"] = {repo + ":7": self.digest, repo + ":6": self.digest}
        self.save_inventory()
        self.responses["_owner_model"] = {"services": {"cache": {"image": repo + ":7"}}}
        report = self.report()
        self.assertEqual(report["owner_alignment"]["reason"], "stale_inherited_digest_inventory")
        self.responses["_owner_model"] = {"services": {}}
        self.assertEqual(self.report()["owner_alignment"]["reason"], "owner_reconciliation_failed")

    def test_inherited_owner_digest_requires_exact_ledger_qualification(self):
        repo = "docker.io/library/redis"
        self.inventory["onyx_services"] = {"cache": {"repository": repo, "inherited": True}}
        self.inventory["registry_digests"] = {repo + ":7": self.digest}
        self.save_inventory()
        self.responses["_owner_model"] = {"services": {"cache": {"image": repo + ":7@sha256:" + "b" * 64}}}
        self.assertEqual(self.report()["owner_alignment"]["reason"], "owner_inherited_digest_mismatch")
        self.responses["_owner_model"]["services"]["cache"]["image"] = repo + ":7@" + self.digest
        aligned = self.report()["owner_alignment"]
        self.assertEqual(aligned["alignment"], "verified")
        self.assertEqual(aligned["selected_services"], {"cache": repo + ":7@" + self.digest})
        self.inventory["registry_digests"] = {}
        self.save_inventory()
        self.assertEqual(self.report()["owner_alignment"]["reason"], "missing_selected_inherited_digest")

    def test_index_descriptors_and_attestation_platforms_are_explicit(self):
        good = self.manifest["manifests"][0]
        for descriptor in ({}, dict(good, digest="bad"), dict(good, size=True),
                           dict(good, platform={}), dict(good, mediaType="text/html"),
                           dict(good, platform={"os": "linux", "architecture": "unknown"})):
            with self.subTest(descriptor=descriptor):
                self.responses[self.manifest_url] = {"json": dict(self.manifest, manifests=[descriptor])}
                self.assertEqual(self.report()["registry"][0]["reason"], "invalid_registry_platform_records")
        attestation = dict(good, platform={"os": "unknown", "architecture": "unknown"})
        self.responses[self.manifest_url] = {"json": dict(self.manifest, manifests=[good, attestation])}
        record = self.report()["registry"][0]
        self.assertEqual(record["platforms"], [good["platform"]])
        self.assertEqual(record["attestation_descriptors"], [attestation])

    def test_advisory_coverage_distinguishes_queried_unqueried_unavailable(self):
        report = self.report(self.advisory)
        self.assertEqual(report["advisory_coverage"]["onyx"]["status"], "queried")
        self.assertEqual(report["advisory_coverage"]["namespace"]["status"], "unqueried")
        self.assertEqual(report["advisory_coverage"]["executor"]["status"], "unqueried")
        self.responses[self.advisory.url] = {"status": 403}
        self.assertEqual(self.report(self.advisory)["advisory_coverage"]["onyx"]["status"], "unavailable")

    def test_advisory_coverage_includes_owner_services_and_source_components(self):
        self.inventory["onyx_services"] = {"cache": {"repository": "docker.io/library/redis", "inherited": True},
                                           "relational_db": {"repository": "docker.io/library/postgres", "inherited": True}}
        self.save_inventory()
        source = self.advisory._replace(component="opensearch")
        coverage = self.report(source)["advisory_coverage"]
        self.assertEqual(coverage["cache"]["status"], "unqueried")
        self.assertEqual(coverage["relational_db"]["status"], "unqueried")
        self.assertEqual(coverage["opensearch"]["status"], "queried")

    def test_online_owner_resolver_fetches_exact_source_and_uses_native_parser(self):
        pins = {"ONYX_IMAGE_TAG": "v1.0.0"}
        inventory = {"compatibility": {"onyx": "v1.0.0", "onyx_source": "a" * 40}}
        source = "https://raw.githubusercontent.com/onyx-dot-app/onyx/" + "a" * 40 + "/deployment/docker_compose/docker-compose.yml"
        transport = audit.FixtureTransport({source: {"body": "services: {}"}})
        def native(path, engine, release):
            self.assertEqual(path.read_text(), "services: {}")
            self.assertEqual((engine, release), ("podman", "v1.0.0"))
            return {"services": {}}
        with patch.object(audit, "render_owner", side_effect=native):
            self.assertEqual(audit.online_owner_model(transport, pins, inventory, "podman"), {"services": {}})
        with patch.object(audit, "render_owner", side_effect=FileNotFoundError("private-path")):
            with self.assertRaisesRegex(audit.Unavailable, "native_owner_parser_unavailable"):
                audit.online_owner_model(transport, pins, inventory, "missing-engine")
        changed = {"ONYX_IMAGE_TAG": "v1.1.0"}
        self.assertIn("/v1.1.0/", audit.owner_source_url(changed, inventory))
        self.assertNotIn("/main/", audit.owner_source_url(changed, inventory))

    def test_source_head_reports_commit_evidence_without_release_ordering(self):
        source = audit.Source("source-head", "teep", "github-commit", "https://api.github.com/repos/example/teep/commits?per_page=1")
        sha = "a" * 40
        self.responses[source.url] = {"json": [{"sha": sha, "html_url": "https://github.com/example/teep/commit/" + sha,
                                               "commit": {"message": "Source update\n\nDetails"}}]}
        selected = {"teep": {"selected": {"TEEP_REF": "b" * 40}}}
        evidence = audit.source_evidence(audit.FixtureTransport(self.responses), source, selected)
        self.assertEqual(evidence["owner_default_branch_head"], sha)
        self.assertEqual(evidence["commit_title"], "Source update")
        self.assertEqual(evidence["comparison"], "review_candidate")
        self.assertEqual(evidence["comparison_basis"], "selected_full_source_sha")
        self.assertNotIn("published_owner_release", evidence)
        selected["teep"]["selected"]["TEEP_REF"] = sha
        self.assertEqual(audit.source_evidence(audit.FixtureTransport(self.responses), source, selected)["comparison"], "matches_selected_source")

    def test_rolling_source_suffix_is_explicit_and_ambiguous_selections_stay_unresolved(self):
        source = audit.Source("source-head", "searxng", "github-commit", "https://api.github.com/repos/example/searxng/commits?per_page=1")
        sha = "a" * 40
        self.responses[source.url] = {"json": [{"sha": sha, "html_url": "https://github.com/example/searxng/commit/" + sha,
                                               "commit": {"message": "Source update"}}]}
        selected = {"searxng": {"selected": {"SEARXNG_IMAGE_TAG": "2026.7.15-" + sha[:9]}}}
        evidence = audit.source_evidence(audit.FixtureTransport(self.responses), source, selected)
        self.assertEqual(evidence["comparison"], "matches_selected_source_suffix")
        self.assertEqual(evidence["comparison_basis"], "selected_source_tag_suffix")
        for ambiguous in ({"SEARXNG_IMAGE_TAG": "latest"},
                          {"SEARXNG_IMAGE_TAG": "2026.7.15-" + sha[:9], "OTHER_REF": "b" * 40}):
            selected["searxng"]["selected"] = ambiguous
            self.assertEqual(audit.source_evidence(audit.FixtureTransport(self.responses), source, selected)["comparison"], "unresolved_selection")

    def test_source_head_http_and_schema_failures_do_not_fallback(self):
        source = audit.Source("source-head", "teep", "github-commit", "https://api.github.com/repos/example/teep/commits?per_page=1")
        for response, reason in (({"status": 404}, "http_404"), ({"json": []}, "invalid_owner_commit_response"),
                                 ({"json": [{"sha": "short", "commit": {"message": "bad"}}]}, "invalid_owner_commit_record")):
            with self.subTest(reason=reason):
                self.responses[source.url] = response
                report = self.report(source)
                self.assertEqual(report["sources"][0]["reason"], reason)
                self.assertEqual(report["coverage"]["unavailable_required"], 1)

    def test_fixture_cli_outputs_json_and_returns_nonzero_for_required_gaps(self):
        fixtures = self.root / "responses.json"
        for failure in (False, True):
            self.responses[self.advisory.url] = {"status": 503} if failure else {"json": []}
            fixtures.write_text(json.dumps(self.responses))
            output = StringIO()
            with patch.object(audit, "SOURCES", (self.advisory,)), patch.object(
                audit.OnlineTransport, "__call__", side_effect=AssertionError("online transport used")), redirect_stdout(output):
                code = audit.main(["--root", str(self.root), "--fixture", str(fixtures)])
            report = json.loads(output.getvalue())
            self.assertEqual(code, int(failure))
            self.assertEqual(report["coverage"]["unavailable_required"], int(failure))

    def test_cli_requires_an_explicit_network_or_fixture_mode(self):
        with self.assertRaises(SystemExit) as result, redirect_stdout(StringIO()):
            audit.main(["--root", str(self.root)])
        self.assertEqual(result.exception.code, 2)

    def test_cli_preserves_controlled_input_failure_codes_and_sanitizes_other_errors(self):
        fixtures = self.root / "responses.json"
        fixtures.write_text(json.dumps(self.responses))
        cases = ((audit.Unavailable("missing_selected_application_digest"), "missing_selected_application_digest"),
                 (audit.Unavailable("missing_reviewed_registry_digest"), "missing_reviewed_registry_digest"),
                 (OSError("fixture-secret-path"), "OSError"),
                 (ValueError("fixture-secret-value"), "ValueError"))
        for error, reason in cases:
            with self.subTest(reason=reason), patch.object(audit, "run_audit", side_effect=error):
                output = StringIO()
                with redirect_stdout(output):
                    code = audit.main(["--root", str(self.root), "--fixture", str(fixtures)])
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(output.getvalue())["reason"], reason)
                self.assertNotIn("fixture-secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
