#!/usr/bin/env python3
"""Explicit, read-only component freshness evidence; never an upgrade authorization."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

if __package__:
    from .manage import normalized, read_pins, reconcile, render_owner, reviewed_onyx_source
else:
    from manage import normalized, read_pins, reconcile, render_owner, reviewed_onyx_source

ROOT = Path(__file__).resolve().parents[2]
ACCEPT_MANIFEST = ", ".join(("application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"\b(?:CVE-\d{4}-\d{4,}|TS-\d{4}-\d{3,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})\b", re.I)
MAX_BYTES = 8 * 1024 * 1024


class Unavailable(ValueError):
    """An explicit coverage gap, with a non-secret machine-readable reason."""


class Reply(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes


class Source(NamedTuple):
    name: str
    component: str
    kind: str
    url: str
    required: bool = True
    expected_title: str = ""


# Source locations contain no transient findings, affected ranges, or selected versions.
SOURCES = (
    Source("onyx-release", "onyx", "release", "https://api.github.com/repos/onyx-dot-app/onyx/releases/latest"),
    Source("controller-release", "controller", "release", "https://api.github.com/repos/onyx-dot-app/python-sandbox/releases/latest"),
    Source("obscura-release", "obscura", "release", "https://api.github.com/repos/h4ckf0r0day/obscura/releases/latest"),
    Source("tailscale-release", "tailscale", "release", "https://api.github.com/repos/tailscale/tailscale/releases/latest"),
    Source("teep-source-head", "teep", "github-commit", "https://api.github.com/repos/13rac1/teep/commits?per_page=1"),
    Source("searxng-source-head", "searxng", "github-commit", "https://api.github.com/repos/searxng/searxng/commits?per_page=1"),
    Source("myst-fork-source-head", "myst", "github-commit", "https://api.github.com/repos/mikeperry-tor/node/commits?per_page=1"),
    Source("tor-release", "tor", "gitlab-tag", "https://gitlab.torproject.org/api/v4/projects/tpo%2Fcore%2Ftor/repository/tags?per_page=1", False),
    Source("cryptography-release", "executor", "pypi-release", "https://pypi.org/pypi/cryptography/json"),
    Source("onyx-advisories", "onyx", "github-advisories", "https://api.github.com/repos/onyx-dot-app/onyx/security-advisories?per_page=100"),
    Source("redis-advisories", "cache", "github-advisories", "https://api.github.com/repos/redis/redis/security-advisories?per_page=100"),
    Source("minio-advisories", "minio", "github-advisories", "https://api.github.com/repos/minio/minio/security-advisories?per_page=100"),
    Source("opensearch-advisories", "opensearch", "github-advisories", "https://api.github.com/repos/opensearch-project/OpenSearch/security-advisories?per_page=100"),
    Source("searxng-advisories", "searxng", "github-advisories", "https://api.github.com/repos/searxng/searxng/security-advisories?per_page=100"),
    Source("obscura-advisories", "obscura", "github-advisories", "https://api.github.com/repos/h4ckf0r0day/obscura/security-advisories?per_page=100"),
    Source("cryptography-advisories", "executor", "github-advisories", "https://api.github.com/repos/pyca/cryptography/security-advisories?per_page=100"),
    Source("nginx-advisories", "nginx", "vendor", "https://nginx.org/en/security_advisories.html", True, "nginx"),
    Source("postgresql-advisories", "relational_db", "vendor", "https://www.postgresql.org/support/security/", True, "postgresql"),
    Source("tailscale-advisories", "tailscale", "vendor", "https://tailscale.com/security-bulletins", True, "tailscale"),
    Source("openssl-advisories", "libraries", "vendor", "https://openssl-library.org/news/vulnerabilities-4.0/", True, "openssl"),
    Source("cryptography-changelog", "libraries", "vendor", "https://cryptography.io/en/latest/changelog/", True, "cryptography"),
    Source("alpine-support", "build_bases", "vendor", "https://alpinelinux.org/releases/", True, "alpine"),
    Source("debian-openssl", "libraries", "vendor", "https://security-tracker.debian.org/tracker/source-package/openssl", True, "openssl"),
    Source("python-support", "build_bases", "vendor", "https://devguide.python.org/versions/", False, "python"),
)


class OnlineTransport:
    def __call__(self, url: str, headers: dict[str, str]) -> Reply:
        request = Request(url, headers={"User-Agent": "private-onyx-component-audit/1", **headers})
        with urlopen(request, timeout=30) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise Unavailable("response_size_limit")
            return Reply(response.status, dict(response.headers.items()), body)


class FixtureTransport:
    def __init__(self, responses: dict):
        self.responses = responses

    def __call__(self, url: str, headers: dict[str, str]) -> Reply:
        if url not in self.responses:
            raise Unavailable("fixture_missing_response")
        value = self.responses[url]
        body = json.dumps(value["json"]).encode() if "json" in value else value.get("body", "").encode()
        return Reply(value.get("status", 200), value.get("headers", {}), body)


def response(transport, url: str, headers: dict[str, str] | None = None) -> Reply:
    reply = transport(url, headers or {})
    if not 200 <= reply.status < 300:
        raise Unavailable(f"http_{reply.status}")
    if len(reply.body) > MAX_BYTES:
        raise Unavailable("response_size_limit")
    return reply


def json_response(transport, url: str, headers: dict[str, str] | None = None):
    reply = response(transport, url, headers)
    try:
        return json.loads(reply.body), reply
    except (UnicodeError, ValueError) as exc:
        raise Unavailable("invalid_json_response") from exc


def image_parts(reference: str) -> tuple[str, str, str | None]:
    reference = normalized(reference)
    name, separator, digest = reference.partition("@")
    if not name.startswith("docker.io/"):
        raise Unavailable("registry_provider_not_supported")
    repo, colon, tag = name.removeprefix("docker.io/").rpartition(":")
    if not colon or not re.fullmatch(r"[a-z0-9._/-]+", repo) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise Unavailable("invalid_registry_reference")
    if separator and not DIGEST.fullmatch(digest):
        raise Unavailable("invalid_reviewed_digest")
    return repo, tag, digest or None


def registry_catalog(pins: dict[str, str], inventory: dict) -> list[dict]:
    entries: dict[str, dict] = {}
    ledger = inventory.get("registry_digests", {})
    source_reference = None
    if "searxng" in inventory["components"]:
        tag = pins.get("SEARXNG_IMAGE_TAG")
        if not tag:
            raise Unavailable("missing_selected_source_tag")
        source_reference = "docker.io/searxng/searxng:" + tag
        if source_reference not in ledger:
            raise Unavailable("missing_selected_source_digest")
    application_repos = {value["repository"] for value in inventory["onyx_services"].values() if value.get("application")}
    for repo in application_repos:
        selected = repo + ":" + pins["ONYX_IMAGE_TAG"]
        if selected not in ledger:
            raise Unavailable("missing_selected_application_digest")
        if any(reference.split(":", 1)[0] == repo and reference != selected for reference in ledger):
            raise Unavailable("stale_application_digest_inventory")
    owners = {key: (name, component["owner"]) for name, component in inventory["components"].items()
              for key in component["selection"]}
    for key, reference in pins.items():
        if not reference.startswith(("docker.io/", "index.docker.io/")):
            continue
        repo, tag, digest = image_parts(reference)
        name = f"docker.io/{repo}:{tag}"
        digest = digest or inventory.get("registry_digests", {}).get(name)
        if digest is None:
            raise Unavailable("missing_reviewed_registry_digest")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise Unavailable("invalid_registry_digest_inventory")
        if key not in owners:
            raise Unavailable(f"missing_component_owner:{key}")
        component, owner = owners[key]
        entry = entries.setdefault(name, {"reference": name, "reviewed_digest": digest,
                                         "selectors": [], "components": [], "owners": []})
        if entry["reviewed_digest"] != digest:
            raise Unavailable("conflicting_reviewed_digest")
        entry["selectors"].append(key)
        entry["components"].append(component)
        entry["owners"].append(owner)
    for reference, digest in inventory.get("registry_digests", {}).items():
        repo, tag, embedded = image_parts(reference)
        if embedded or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise Unavailable("invalid_registry_digest_inventory")
        name = f"docker.io/{repo}:{tag}"
        entry = entries.setdefault(name, {"reference": name, "reviewed_digest": None,
                                         "selectors": [], "components": [], "owners": []})
        if entry["reviewed_digest"] not in (None, digest):
            raise Unavailable("conflicting_reviewed_digest")
        entry["reviewed_digest"] = digest
        if not entry["owners"]:
            services = [key for key, value in inventory["onyx_services"].items()
                        if value["repository"] == "docker.io/" + repo
                        and (value.get("application") or value.get("inherited"))]
            if services:
                component, owner = "onyx", inventory["components"]["onyx"]["owner"]
            elif name == source_reference:
                component, owner = "searxng", inventory["components"]["searxng"]["owner"]
            else:
                raise Unavailable("registry_selection_owner_ambiguous")
            entry["components"].append(component)
            entry["owners"].append(owner)
            entry["services"] = services
    for entry in entries.values():
        for key in ("selectors", "components", "owners"):
            entry[key] = sorted(set(entry[key]))
        if not entry["reviewed_digest"]:
            raise Unavailable("missing_reviewed_registry_digest")
    return sorted(entries.values(), key=lambda value: value["reference"])


def registry_evidence(transport, entry: dict) -> dict:
    repo, tag, _ = image_parts(entry["reference"])
    token_url = "https://auth.docker.io/token?" + urlencode({"service": "registry.docker.io", "scope": f"repository:{repo}:pull"})
    credentials, _ = json_response(transport, token_url)
    token = (credentials.get("token") or credentials.get("access_token")) if isinstance(credentials, dict) else None
    if not isinstance(token, str) or not token or any(value.isspace() for value in token):
        raise Unavailable("invalid_anonymous_registry_token")
    url = f"https://registry-1.docker.io/v2/{repo}/manifests/{quote(tag, safe='')}"
    data, reply = json_response(transport, url, {"Accept": ACCEPT_MANIFEST, "Authorization": "Bearer " + token})
    actual = "sha256:" + hashlib.sha256(reply.body).hexdigest()
    headers = {key.lower(): value for key, value in reply.headers.items()}
    declared = headers.get("docker-content-digest")
    if declared is not None and declared != actual:
        raise Unavailable("registry_content_digest_mismatch")
    if not isinstance(data, dict) or data.get("schemaVersion") != 2 or data.get("mediaType") not in ACCEPT_MANIFEST.split(", "):
        raise Unavailable("unsupported_registry_manifest")
    manifests = data.get("manifests", [])
    if not isinstance(manifests, list) or any(not isinstance(value, dict) for value in manifests):
        raise Unavailable("invalid_registry_platform_records")
    is_index = data["mediaType"] in ACCEPT_MANIFEST.split(", ")[:2]
    if is_index and not manifests:
        raise Unavailable("empty_registry_index")
    for descriptor in manifests:
        platform = descriptor.get("platform")
        if (not isinstance(descriptor.get("digest"), str) or not DIGEST.fullmatch(descriptor["digest"])
            or not isinstance(descriptor.get("size"), int) or isinstance(descriptor["size"], bool) or descriptor["size"] < 0
            or descriptor.get("mediaType") not in ACCEPT_MANIFEST.split(", ")
            or not isinstance(platform, dict)
            or any(not isinstance(platform.get(key), str) or not platform[key].strip() for key in ("os", "architecture"))
            or any(key in platform and not isinstance(platform[key], str) for key in ("variant", "os.version"))):
            raise Unavailable("invalid_registry_platform_records")
        if (platform["os"] == "unknown") != (platform["architecture"] == "unknown"):
            raise Unavailable("invalid_registry_platform_records")
    platforms = [value["platform"] for value in manifests if value["platform"]["os"] != "unknown"]
    attestations = [value for value in manifests if value["platform"]["os"] == "unknown"]
    matches = actual == entry["reviewed_digest"]
    return {"source": url, "resolved_digest": actual, "digest_header_verified": declared is not None,
            "artifact_kind": "index" if is_index else "manifest",
            "comparison": "matches_reviewed" if matches else "review_required",
            "reviewed_artifact_availability": "verified" if matches else "unverified",
            "reviewed_artifact_platforms": platforms if matches else None,
            "platforms": platforms, "attestation_descriptors": attestations,
            "authorization": "Digest drift is evidence for review, not permission to change owner versions."}


class VendorPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        self.text_parts.append(data)
        if self.in_title:
            self.title_parts.append(data)


def source_evidence(transport, source: Source, selections: dict) -> dict:
    if source.kind == "vendor":
        reply = response(transport, source.url)
        parser = VendorPage()
        try:
            parser.feed(reply.body.decode("utf-8"))
        except UnicodeError as exc:
            raise Unavailable("invalid_vendor_encoding") from exc
        title = " ".join(" ".join(parser.title_parts).split())
        if not title or source.expected_title.casefold() not in title.casefold() or any(
            value in title.casefold() for value in ("just a moment", "access denied", "attention required")):
            raise Unavailable("unrecognized_vendor_page")
        identifiers = sorted(set(value.upper() for value in IDENTIFIER.findall(" ".join(parser.text_parts))))
        return {"title": title, "identifiers": identifiers,
                "applicability": "unresolved", "applicability_review": "compare the source with actual selected libraries and configured features",
                "notice": "An empty identifier list does not establish absence of applicable advisories."}
    data, reply = json_response(transport, source.url, {"Accept": "application/vnd.github+json"})
    if source.kind == "github-commit":
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise Unavailable("invalid_owner_commit_response")
        head = data[0]
        sha, commit = head.get("sha"), head.get("commit")
        if (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
            or not isinstance(commit, dict) or not isinstance(commit.get("message"), str)
            or not isinstance(head.get("html_url"), str) or not head["html_url"]):
            raise Unavailable("invalid_owner_commit_record")
        candidates = []
        for key, value in selections.get(source.component, {}).get("selected", {}).items():
            if key.endswith("_REF") and re.fullmatch(r"[0-9a-f]{40}", value):
                candidates.append((value, "selected_full_source_sha"))
            elif key.endswith("_IMAGE_TAG"):
                # Rolling SearXNG tags carry a single explicit source suffix.
                match = re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}-([0-9a-f]{7,40})", value)
                if match:
                    candidates.append((match[1], "selected_source_tag_suffix"))
        selected, basis = candidates[0] if len(candidates) == 1 else (None, "ambiguous_or_missing_selected_source")
        matches = selected is not None and sha.startswith(selected)
        comparison = ("unresolved_selection" if selected is None else "review_candidate" if not matches
                      else "matches_selected_source_suffix" if basis == "selected_source_tag_suffix" else "matches_selected_source")
        return {"owner_default_branch_head": sha, "commit_source": head["html_url"],
                "commit_title": commit["message"].splitlines()[0] if commit["message"] else "",
                "comparison": comparison,
                "comparison_basis": basis, "compared_selected_source": selected,
                "source_identity_notice": "A tag suffix comparison does not independently resolve the selected full commit or prove installed contents.",
                "candidate_ordering": "Default-branch head is source evidence, not a published release or proof of newer ordering.",
                "authorization": "Source differences require owner and compatibility review; this report does not authorize an upgrade."}
    if source.kind in ("release", "gitlab-tag", "pypi-release"):
        if source.kind == "gitlab-tag":
            if not isinstance(data, list) or not data or not isinstance(data[0], dict):
                raise Unavailable("invalid_owner_tag_response")
            data = {"tag_name": data[0].get("name"), "web_url": source.url}
        elif source.kind == "pypi-release":
            if not isinstance(data, dict) or not isinstance(data.get("info"), dict):
                raise Unavailable("invalid_python_release_response")
            data = {"tag_name": data["info"].get("version"), "html_url": data["info"].get("project_url", source.url)}
        if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str) or not data["tag_name"]:
            raise Unavailable("invalid_owner_release_response")
        tag = data["tag_name"]
        values = selections.get(source.component, {}).get("selected", {}).values()
        versions = [image_parts(value)[1] if value.startswith("docker.io/") else value for value in values]
        if source.kind == "pypi-release":
            versions = [selections.get("executor", {}).get("declared_requirements", {}).get("cryptography", "")]
        def version(value):
            for prefix in ("code-interpreter-", "python-executor-", "tor-", "v"):
                if value.startswith(prefix):
                    return value.removeprefix(prefix)
            return value
        matches = any(version(tag) == version(value) for value in versions)
        return {"published_owner_release": tag, "published_at": data.get("published_at"),
                "release_source": data.get("html_url", source.url),
                "comparison": "matches_selection" if matches else "review_candidate",
                "candidate_ordering": "not inferred from tag differences or source SHAs",
                "authorization": "Review the owning component and dependent contracts before changing selections; upstream-pending is not an approved independent upgrade."}
    if source.kind != "github-advisories" or not isinstance(data, list):
        raise Unavailable("invalid_advisory_response")
    headers = {key.lower(): value for key, value in reply.headers.items()}
    if 'rel="next"' in headers.get("link", ""):
        raise Unavailable("advisory_pagination_incomplete")
    advisories = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("summary"), str) or not isinstance(item.get("ghsa_id"), str) or not re.fullmatch(
            r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", item["ghsa_id"], re.I):
            raise Unavailable("invalid_advisory_record")
        advisories.append({"id": item["ghsa_id"], "cve": item.get("cve_id"), "title": item["summary"],
                           "source": item.get("html_url", source.url), "published_at": item.get("published_at"),
                           "applicability": "unresolved"})
    return {"advisories": advisories, "applicability": "unresolved",
            "notice": "No published advisory records is not a clean dependency or artifact assessment."}


def collect(operation, *, source: str, required: bool, **metadata) -> dict:
    entry = {"source": source, "required": required, **metadata}
    try:
        return entry | {"status": "available", **operation()}
    except HTTPError as exc:
        reason = f"http_{exc.code}"
    except (URLError, TimeoutError, OSError, HTTPException):
        reason = "network_unavailable"
    except subprocess.SubprocessError:
        reason = "native_owner_parser_unavailable"
    except Unavailable as exc:
        reason = str(exc)
    return entry | {"status": "unavailable", "reason": reason}


def declared_requirements(path: Path) -> dict[str, str]:
    selected = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)\s*(?:#.*)?", line)
        if not match:
            raise Unavailable("unsupported_public_requirement")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in selected:
            raise Unavailable("duplicate_public_requirement")
        selected[name] = match[2]
    return selected


def owner_source_url(pins: dict, inventory: dict) -> str:
    release = pins["ONYX_IMAGE_TAG"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.-]+)?", release):
        raise Unavailable("invalid_owner_release")
    try:
        source = reviewed_onyx_source(release, inventory)
    except ValueError as exc:
        raise Unavailable("invalid_reviewed_owner_source") from exc
    return f"https://raw.githubusercontent.com/onyx-dot-app/onyx/{source}/deployment/docker_compose/docker-compose.yml"


def online_owner_model(transport, pins: dict, inventory: dict, container_bin: str) -> dict:
    url = owner_source_url(pins, inventory)
    body = response(transport, url).body
    with tempfile.TemporaryDirectory(prefix="private-onyx-audit-owner-") as directory:
        compose = Path(directory) / "docker-compose.yml"
        compose.write_bytes(body)
        try:
            return render_owner(compose, container_bin, pins["ONYX_IMAGE_TAG"])
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise Unavailable("native_owner_parser_unavailable") from exc


def owner_alignment(resolver, pins: dict, inventory: dict) -> dict:
    if resolver is None:
        raise Unavailable("owner_model_not_supplied")
    try:
        model = resolver(pins, inventory)
        changes = reconcile(model, inventory, pins)
        if changes:
            raise Unavailable("owner_selected_artifacts_differ")
        contracts = inventory["onyx_services"]
        for name, contract in contracts.items():
            if not contract.get("inherited"):
                continue
            selected, separator, selected_digest = normalized(model["services"][name]["image"]).partition("@")
            reviewed = inventory.get("registry_digests", {}).get(selected)
            if reviewed is None:
                raise Unavailable("missing_selected_inherited_digest")
            if separator and selected_digest != reviewed:
                raise Unavailable("owner_inherited_digest_mismatch")
            if any(reference.split(":", 1)[0] == contract["repository"] and reference != selected
                   for reference in inventory.get("registry_digests", {})):
                raise Unavailable("stale_inherited_digest_inventory")
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, Unavailable):
            raise
        raise Unavailable("owner_reconciliation_failed") from exc
    return {"alignment": "verified", "release": pins["ONYX_IMAGE_TAG"],
            "selected_services": {name: normalized(service["image"]) for name, service in model["services"].items()},
            "notice": "Owner model reconciled with the selected release contract; installed artifacts remain unverified."}


def run_audit(root: Path, transport, sources: tuple[Source, ...] | None = None,
              requirements: Path | None = None, owner_model_resolver=None) -> dict:
    sources = SOURCES if sources is None else sources
    pins = read_pins(root / "stack.versions.env")
    inventory = json.loads((root / "tools/components/ownership.json").read_text())
    selections = {name: {"owner": value["owner"], "selected": {key: pins[key] for key in value["selection"]},
                         "applicability": "unresolved"} for name, value in inventory["components"].items()}
    wheels = declared_requirements(requirements or root / "executor/requirements.in")
    if "executor" in selections:
        selections["executor"]["declared_requirements"] = wheels
    catalog = registry_catalog(pins, inventory)
    owner = collect(lambda: owner_alignment(owner_model_resolver, pins, inventory),
                    source=owner_source_url(pins, inventory), required=True, alignment="unverified")
    registry = [collect(lambda entry=entry: registry_evidence(transport, entry),
                        source=entry["reference"], required=True, **entry)
                for entry in catalog]
    evidence = [collect(lambda source=source: source_evidence(transport, source, selections),
                        source=source.url, required=source.required, name=source.name,
                        component=source.component, kind=source.kind) for source in sources]
    advisory_coverage = {}
    components = set(selections) | {entry["component"] for entry in evidence} | set(inventory["onyx_services"])
    for component in sorted(components):
        queried = [entry for entry in evidence if entry["component"] == component and
                   (entry["kind"] == "github-advisories" or entry["name"].endswith("-advisories"))]
        advisory_coverage[component] = {
            "status": "unqueried" if not queried else "unavailable" if any(entry["status"] == "unavailable" for entry in queried) else "queried",
            "sources": [entry["source"] for entry in queried], "applicability": "unresolved"}
    gaps = [entry for entry in (owner, *registry, *evidence) if entry["status"] == "unavailable"]
    required = sum(entry["required"] for entry in gaps)
    return {"schema": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "incomplete_required_evidence" if required else "partial_evidence" if gaps else "evidence_collected",
            "scope": "Committed pins, ownership, and public dependency input only; operator overrides, private configuration, and installed packages are not read.",
            "security_assessment": {"status": "unresolved", "notice": "Successful retrieval is not a clean audit or upgrade authorization."},
            "refresh_revision": pins.get("OS_SECURITY_UPDATE_REVISION"), "components": selections,
            "owner_alignment": owner, "registry": registry, "sources": evidence,
            "advisory_coverage": advisory_coverage,
            "coverage": {"unavailable_required": required, "unavailable_optional": len(gaps) - required},
            "installed_evidence": [
                {"kind": "system_packages", "status": "unverified", "required_checks": ["make test-security-images", "selected package inventories"]},
                {"kind": "python_wheel_and_bundled_openssl", "declared_wheel_versions": wheels, "installed_wheel_status": "unverified", "bundled_openssl_status": "unverified", "selection_source": "public executor requirements input", "required_checks": ["make test-patch-images", "make test-security-images"]},
                {"kind": "compiled_go_dependencies", "status": "unverified", "required_checks": ["actual selected binary version/build metadata", "owner source dependency review"]},
            ]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--requirements", type=Path, help="public executor dependency input; defaults to the committed executor/requirements.in")
    parser.add_argument("--container-bin", default="docker", help="native Compose parser for exact owner source (online mode only)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--online", action="store_true")
    mode.add_argument("--fixture", type=Path, help="replay explicit public response fixtures without networking")
    args = parser.parse_args(argv)
    try:
        if args.online:
            transport = OnlineTransport()
            resolver = lambda pins, inventory: online_owner_model(transport, pins, inventory, args.container_bin)
        else:
            fixtures = json.loads(args.fixture.read_text())
            transport = FixtureTransport(fixtures)
            resolver = (lambda pins, inventory: fixtures["_owner_model"]) if "_owner_model" in fixtures else None
        report = run_audit(args.root, transport, requirements=args.requirements, owner_model_resolver=resolver)
    except (Unavailable, OSError, ValueError, KeyError) as exc:
        # Never render HTTP errors, transport exceptions, auth headers, or proxy URLs.
        report = {"schema": 1, "status": "invalid_local_audit_inputs", "reason": str(exc) if isinstance(exc, Unavailable) else type(exc).__name__,
                  "security_assessment": {"status": "unresolved"}}
        print(json.dumps(report, sort_keys=True, indent=2))
        return 1
    print(json.dumps(report, sort_keys=True, indent=2))
    return int(bool(report["coverage"]["unavailable_required"]))


if __name__ == "__main__":
    raise SystemExit(main())
