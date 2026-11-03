# Component version ownership and end-user upgrade delivery

Status: G0 and C1 passed available checks and independent review. C1-C2
accumulated source review (G1 SOURCE) passed through `64470e1`; selected C2
artifact qualification remains pending. C3 source implementation and focused
checks passed accumulated G2 SOURCE review; its final full check passed and the local phase commit is being reviewed. C4 public audit and consumer-coverage drafts are independently
reviewed; repository integration and G3 remain pending.
Implementation baseline: `3f54b2a4d8647047a78ed3eb68f1fb7765aaf0fe`.
The intervening commits changed contributor instructions and this plan only.

### Consolidated implementation checkpoint

Local commits: G0 `d7a03d5e`, C1 `74df89b7`, Make-fixture fixup
`69c6c553`, and revision-coverage fixup `64689baa`. Independent reviewer
`/root/g0_design` approved each exact SHA and the accumulated C1 source against
`3f54b2a4`; no material source findings remain. The coverage fixup moved the
runtime/build input boundary into the ownership inventory and closed mounted
proxy/patch, Compose, lifecycle and standalone-sidecar omissions.

Available validation:

- Python 3.14.10 `make check` passed at `69c6c553`: 748 tests, 32 skips, plus
  compilation, help and diff checks. The skips are 21 SearXNG lxml and 11 selected
  SDK tests; the separate selected-image gate exercised those dependencies.
- Final C1 `make check` at `f749d4fd` passed on Python 3.14.10: 748 tests
  in 344.696 seconds, 32 image-only skips, compilation/help/diff checks.
  Independent exact-SHA review covers C1 and all three fixups.
- `make code-interpreter-build executor-build tailscale-build` and
  `make test-patch-images test-security-images`, with public example settings,
  passed on Docker Desktop Engine 29.9.2, Linux/arm64. Coverage/test/document
  fixups do not change those selected image build inputs.
- Online `make component-sync` and exact-release local checkout reconciliation
  reported no owner drift. Explicit-base revision enforcement passed.

| Qualified C1 artifact (generation 20261102.1) | Exact local image ID | Installed evidence |
| --- | --- | --- |
| Controller `local/private-onyx-code-interpreter:0.4.9-73ca55b412f4` | `sha256:dd3d0d7ae90d66c08aac3b666e777b4694571129f7b90ef5cdafbb942a0c61ac` | Debian OpenSSL/provider `3.5.7-1~deb13u3`, CA bundle `20250419+deb13u1`, Docker CLI 29.9.2 |
| Executor `local/private-onyx-python-executor:0.4.9-3fba8fdac06f` | `sha256:14c6122b20f5df4c6a7a645eaeb2c35750f4a8f953b1f8cda7581a758ff1a803` | Same Debian packages; cryptography 50.0.1 / bundled OpenSSL 4.0.2 remain unchanged and require separate assessment |
| Tailscale `local/private-onyx-tailscale:e8903407afc7` | `sha256:b991e37a9d74b1eb9548c10fcf966eaf16f42d2e9f6a69e142fae74cb72ec510` | Alpine 3.22.7, OpenSSL `3.5.9-r0`, CA bundles `20261020-r0`; application 1.102.3, compiled Go 1.26.6 |

Unavailable/deferred validation: Podman is not installed; native Linux engine
and other-architecture qualification are unavailable. No live lite/full,
Tailscale/onion, VPN/browser/RAG or executor integration was run against
operator configuration or private document/data stores. Effective-model
rendering is not engine qualification. Upstream-pending and unresolved advisory
rows below remain open; passing the existing floors does not close them.

C2 commits: `76d30981`, fixture fixup `bc7ebcbb`, recursive Obscura COPY
identity fixup `d29c4cbd`, and platform-test guard fixup `64470e1`. Each exact
SHA has independent approval. C2 `make check` passed at `bc7ebcbb`: 779 tests
in 586.048 seconds, 32 skips, compilation/help/diff checks. The later COPY fix
passed all 12 identity tests in 157.016 seconds; the guard fix passed 18
configuration tests and has no supported-platform behavior change.

C3 focused evidence: 31 preparer tests; 41 lifecycle, 45 Podman startup-health
and four tokenizer tests. Eight actual Make-entrypoint scenarios passed in
356.728 seconds; the final strengthened populated-cache failure/retry case
passed in 54.784 seconds. Independent accumulated G2 SOURCE review covers
ordering, configuration/store locks, acquisition, exact platforms, Podman
initialization/health, full readiness, and secret boundaries. A native Docker
API 1.56 cached-image preparation probe passed with acquisition disabled.
The isolated full check initially failed because generated public upstream
Compose/nginx artifacts were absent; it is not a passing check. Seven public
fixtures were then prepared from exact source `f400d315`; no runtime env was
copied. The gateway matrix recheck passed six tests (one sandbox Unix-socket
skip). The main-checkout `make check` passed: 818 tests in 1036.682 seconds, 32
expected image-only skips, compilation/help/diff checks.

All eight derived references and evaluated base/build-input selections match
between C2 and C3; the ongoing C2 build inputs are unchanged. Full G1/G2/G3
acceptance remains pending required artifact/lifecycle evidence. Reversible
source work proceeds under independently approved source gates; later artifact
findings reopen affected source reviews and require reviewed fixups.

Next: complete C2 artifact qualification, C3 exact-commit review, and C4 integration. The following accepted design constraints remain in force.
Its independent design review requires all eight derived identities, native
base-pull/package-cache policy, and a narrow configuration preparer replacing
installer initialization. The installer starts stock containers and deletes
unselected overlays. Preserve the complete seven-file generated set, respect
`ONYX_CONFIG_REF`, validate staged models without private env resolution, lock
preparation, preserve existing secrets, and record completion only after atomic
file installation and selected-version synchronization. Test failure/retry,
missing/modified files, unchanged-version config refresh and explicit overrides.
C1's standing behavior is documented in
[version ownership and reconciliation](../onyx_patches_upgrade.md#version-ownership-and-reconciliation).

### Accepted G0 decisions (2026-11-02)

Independent reviewer `/root/g0_design` reviewed the implementation baseline;
`/root/c1_evidence` independently examined the exact Onyx/controller/executor
compatibility call chain. Retain `OS_SECURITY_UPDATE_REVISION` as the single
date-plus-sequence refresh generation. Require an explicit review base for
bump enforcement. Record reviewed multiarchitecture registry digests while
retaining owner-selected tags; immutable image presence replaces persistent
pull-completion receipts. Serialize preparation per selected engine/store.
Generated configuration has its own input fingerprint, completed only after
installation and environment synchronization. Serialize every startup helper
that can consume images or create containers behind preparation, including
tokenizer extraction and Podman storage initialization.

The selected Onyx release explicitly permits the separately released
code-interpreter selection in
[its env.template](https://github.com/onyx-dot-app/onyx/blob/f400d3154a13ac1f3ee730896f69fbc25eb5d8f7/deployment/docker_compose/env.template).
Compatibility selection is bounded to Onyx v4.8.8 (source
`f400d3154a13ac1f3ee730896f69fbc25eb5d8f7`), its default 0.4.7, and controller
and executor 0.4.9 (source `e53850d68701e5e62440a36ed311164dafc89465`).
Independent source review compared the client, routes, schemas, Docker backend,
and executor: wire schemas are unchanged except optional health fields;
`StreamStarted` is consumed internally before SSE; 429/503 fail before opening
SSE and propagate through the client's HTTP error handling and tool error
results without retry or direct fallback. Executor dependencies are unchanged
apart from the release version. This accepts source compatibility, not artifact
qualification: the focused image/network gates remain required. A changed
Onyx release, default, controller, or executor invalidates this bounded decision.
There is no general exception allowing newer Onyx-owned dependencies.

Inventory coverage must include hard-coded build stages (SearXNG Python 3.14,
Myst/Teep Go and Alpine), explicit override acquisition rules, actual base-pull
and package-layer invalidation, and the misleading Tor output version prefix.
These findings are assigned to C1-C3 with their phase-local tests and reviews.

Starting baseline: `90f07188cc9da0ffdef9a0bd428c6073450de7ac`. The initial
plan is already present at that baseline; the changes below are proposed.
Use the current tree and selected images during implementation, and record
any intervening changes that affect the baseline evidence.

## Objective and accepted policy

Make reviewed component updates reach installations following the existing
README workflow: `make down-lite` / `make down-full`, `git pull`, then the
matching `make up-*`. End users must not need maintainer upgrade targets,
manual pulls, cache deletion, or image validation commands.

- Follow the dependency versions selected by the exact pinned upstream Onyx
  release. Do not independently advance Onyx-owned service versions to address
  advisories. Report upstream-pending vulnerabilities and their applicability.
- Review dependencies when their owning component changes, including shared
  dependencies with multiple owners.
- Bump the committed OS refresh revision for every component update, including
  source pins, dependency locks, and image build inputs. Rebuild and validate
  the selected controller, executor, and Tailscale artifacts affected by it.
- Keep maintainer discovery/synchronization separate from end-user preparation.
  Startup consumes committed selections; it does not discover new releases,
  edit committed pins, regenerate locks, or install packages in services.
- Preserve optional features, explicit image overrides, engine separation,
  routing, fail-closed behavior, and existing full-mode readiness staging.

Existing behavior and standing validation requirements belong in
[the upgrade checklist](../onyx_patches_upgrade.md),
[Podman support](../podman_suport.md),
[resource minimization](../resource_minimization.md),
[internal network security](../internal_network_security.md), and
[routing](../vpn_routing_and_proxies.md). Consult
[native Tor](../native_tor_support.md) and
[local RAG](../local_docs_rag_search.md) for affected lifecycle paths.
Update those authorities alongside implementation, then link to their current
contracts here rather than retaining duplicate specifications in this plan.

## Gaps to close

The Makefile's broad upgrade flow refreshes locks and builds artifacts without
automatically synchronizing all committed pins. Wrapper nginx and MinIO
overrides can therefore lag their selected Onyx release. PostgreSQL, Redis,
and OpenSearch currently inherit upstream deployment values.

Normal startup reuses existing local image references. That delivers changed
content-derived tags, but cannot establish freshness behind unchanged registry
tags. Myst and Teep image selection does not currently cover every local build
input. Generated Onyx deployment readiness is tied to the application tag,
which cannot represent every configuration refresh.

## Review sequence and commit boundaries

Follow [independent workflows and plan structure](../../AGENTS.md#independent-subagent-workflows).
The coordinating agent owns implementation and integration. Independent
reviewers remain read-only, verify evidence, and challenge unnecessary
responsibilities. Delegate distinct concerns when useful: version authority
and build inputs; startup ordering and engine behavior; privacy and validation.
Do not require a separate agent for each concern or duplicate identical reviews.
Serialize builds/live checks using shared engines or data.

| ID | Ordered commit-sized phase | Independent review boundary |
| --- | --- | --- |
| C1 | Ownership, upstream synchronization, and refresh policy | Review exact phase snapshot/SHA before dependent work |
| C2 | Complete local image and generated configuration identities | G1: combined authority and artifact selection |
| C3 | Ordered delivery through normal startup | G2: complete end-user upgrade/lifecycle path |
| C4 | Online freshness reporting and final integration acceptance | G3: accumulated implementation and documentation handoff |

Each phase includes its tests and owning documentation and produces a local
commit without a separate permission request. Review each exact SHA and each
introducing-SHA fixup; preserve unsquashed history through G3 and the user's
branch review before push. For preliminary working-tree reviews, identify
snapshots using diffs and file hashes including new files, then review the exact
resulting commit. Reviewer identity, scope, baseline, test results, findings,
and resolutions form the consolidated acceptance evidence. Pushing, merging,
or publishing requires explicit user instructions.

### G0: independent design review before C1

Challenge the ownership inventory, synchronization authority, cache semantics,
configuration identity, and need for persistent preparation state. Resolve
the design choices below before implementing dependent behavior, especially
moving tags versus reviewed digests before choosing completion records. Review
failure/retry behavior and Docker/Podman selection without assuming engine
equivalence. Exit when material design findings are resolved and acceptance
criteria are concrete; do not introduce a workflow framework merely to track
refreshes.

## C1: dependency ownership and maintainer synchronization

Introduce a small structured inventory used by upgrade tooling and checks.
Prefer a narrow extension of existing configuration over a second independent
version manifest. Every consumed image and build base must have an owner and
an explicit source of version authority.

| Owner | Review scope |
| --- | --- |
| Selected Onyx release | nginx, PostgreSQL, Redis, OpenSearch, MinIO, code-interpreter default, application images |
| Selected code-interpreter release | Controller/executor pairing and executor image selection |
| Obscura | Runtime digest, source ref/archive hash, Rust builder, patch series |
| SearXNG | Upstream image, Python locks, browser-client compatibility |
| Tor | Base digest, packaged Tor version, derived build inputs |
| Tailscale | Upstream digest, application/Go dependencies, OS packages |
| Myst and Teep | Source refs, build/runtime bases, local copied files and dependencies |
| Wrapper infrastructure | Shared Python bases, namespace-holder Alpine, socat, and all consumers |

Parse the exact selected Onyx release's deployment/configuration sources with
structured parsers. Synchronize wrapper-owned copies of upstream values and
verify inherited values without duplicating every inherited pin. Normalize
registry spelling without weakening tag/digest comparisons. Unknown, missing,
ambiguous, added, or removed service contracts must produce actionable results.
Do not use upstream main or latest as the authority for a selected release.

Reconcile the existing controller/executor 0.4.9 selection against Onyx v4.8.8's
Compose default of 0.4.7 through compatibility review. Do not silently downgrade
an installed controller or invent a permanent exception to the ownership policy.
Keep MinIO image/source metadata synchronized with its owner-selected artifact.
Make the WebUI nginx and both ingress gateways consume the same synchronized
nginx selection; remove their hard-coded duplicate versions.

Add a shared revision-bump operation for explicit maintainer update workflows.
Allow multiple refreshes per day, for example `20261031.1`. Run it once per
update batch before deriving build identities. Enforce bumps for component
changes made outside that workflow through a check against an explicit review
base. Ordinary startup, build, and test targets must not bump it.

### Initial image and library reconciliation

The consolidated acceptance evidence below was checked on **2026-11-02** by
`/root/c1_evidence` (source compatibility, primary advisory/support research)
and the coordinator (registry resolution and cached-artifact observations).
The exact Onyx owner source is v4.8.8,
`f400d3154a13ac1f3ee730896f69fbc25eb5d8f7`, specifically its
[deployment Compose](https://github.com/onyx-dot-app/onyx/blob/f400d3154a13ac1f3ee730896f69fbc25eb5d8f7/deployment/docker_compose/docker-compose.yml).
Registry digests supplied by the coordinator are resolved multiarchitecture
selections; cached image observations do not establish that those bytes match
the resolved selection. Unavailable artifact/package evidence remains
unresolved. This table is batch acceptance evidence, not a clean security audit.
C1 source review, final full suite and selected-image contracts passed as
recorded above. C2 selections require their own artifact qualification.

| ID | Owner and exact selected artifact/source | Primary evidence and applicability (verified 2026-11-02) | Disposition, responsible phase, required checks and current limitation |
| --- | --- | --- | --- |
| R1 | Onyx v4.8.8: `docker.io/library/nginx:1.25.5-alpine`; resolved `sha256:516475cc129da42866742567714ddc681e5eed7b9ee0b9e9c015e464b4221a00`. | [nginx advisories](https://nginx.org/en/security_advisories.html) include affected ranges containing 1.25.5. Fixed ingress gateways use HTTP/1.1 TCP backends; no configured TLS/HTTP3/AIO/slice/SSI/DAV handlers. Generated Onyx API configuration uses rewrite. Complete compiled-module/configuration applicability is unresolved. | **Upstream-pending / unresolved applicability.** C1 synchronizes WebUI and both gateways; no independent application upgrade. C1-C3 must render Docker/Podman lite/full with both ingress features and verify selected nginx behavior. Digest resolved; complete artifact/module and live ingress checks pending. |
| R2 | Onyx v4.8.8: inherited `docker.io/library/postgres:15.2-alpine`; resolved `sha256:d9c304353c031b21e9a7e33dc4781e272a9fa802a2ab9703fe4199d72ba1422c`. | [PostgreSQL 15 security](https://www.postgresql.org/support/security/15/) lists fixes after 15.2, including schema/role/RLS findings fixed in 15.3. Exact role/query prerequisites need deployment assessment. | **Upstream-pending.** C1 verifies inherited lite/full selection; retain owner-selected version and storage major. C1-C3 require effective models and database startup/restart validation if bytes change. Installed package and complete advisory applicability evidence pending. |
| R3 | Onyx v4.8.8: inherited `docker.io/library/redis:7.4-alpine`; current registry `sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f`. Cached runtime observed Redis 7.4.9 / Alpine 3.21.7; not yet matched to registry digest. | Primary [RESTORE](https://github.com/redis/redis/security/advisories/GHSA-c8h9-259x-jff4), [Lua](https://github.com/redis/redis/security/advisories/GHSA-8ghh-qpmp-7826), and [unblock](https://github.com/redis/redis/security/advisories/GHSA-93m2-935m-8rj3) advisories list relevant affected ranges and no concrete patched version in their current metadata. Cluster is not configured; that does not exclude non-cluster findings. | **Unresolved applicability / upstream-pending.** C1 records owner tag and reviewed digest; C3 must deliver selected bytes despite unchanged tag. Next action: inspect exact selected digest's Redis/APK versions and assess advisory prerequisites. Full-mode cache/startup contracts and fake unchanged-tag refresh tests required; cached version alone cannot close the row. |
| R4 | Onyx v4.8.8: inherited `docker.io/opensearchproject/opensearch:3.6.0`; resolved `sha256:b5dd1512af2a99748c942cfbbd7f32162623336b210667d0fc6333c6321f171d`. | [October zstd advisory](https://github.com/opensearch-project/OpenSearch/security/advisories/GHSA-68xw-fqpj-h922) explicitly excludes 3.x. Newer releases alone do not establish exposure. Bundled JDK/plugin/dependency applicability remains unverified. | **No applicable finding for the cited zstd advisory; remaining evidence unresolved.** C1 verifies inherited ownership; no independent upgrade. Next action C1-C3: inspect selected artifact's dependency inventory; run `make test-opensearch-image` and affected running RAG/OpenSearch contracts if bytes change. Digest resolved; image/live qualification pending. |
| R5 | Onyx v4.8.8: `docker.io/onyxdotapp/minio:RELEASE.2025-07-23T15-54-02Z-cpuv1@sha256:7330be2e7320a7a699b36f72bd06a94d8a21ec9f6f397345d9c38fc6751141f5`; manifest source `fe3a1471fda9d8adf275791a8dd3c50091750d0b`. | [Unsigned-trailer signature bypass](https://github.com/minio/minio/security/advisories/GHSA-hv4r-mvr4-25vw) affects all OSS releases; published fix is AIStor. [Session-policy bypass](https://github.com/minio/minio/security/advisories/GHSA-jjjj-jwhf-8rgr) was fixed after the selected July2025 release. Restricted private reachability reduces exposure but does not patch either issue. | **Upstream-pending exposure; mirror/source identity unresolved.** Retain owner artifact without product/license/storage migration. C1 synchronizes image/source metadata. Next action: verify mirror runtime/source identity and session-policy use; C1-C3 require full effective model and affected object-store/RAG checks. No fixed claim or independently selected AIStor replacement. |
| R6 | Wrapper: `docker.io/library/alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f`. | [Alpine support](https://alpinelinux.org/releases/), verified 2026-11-02: 3.20 regular support ended 2026-04-01; 3.22 main is supported through 2027-05-01. The holder requires `sleep infinity`. | **C2 supported-base remediation selected; qualification pending.** Validate retained namespace/readiness contracts on Docker and available engines; Podman is unavailable. Source support and registry digest were independently verified; model rendering alone is not qualification. |
| R7 | Wrapper: `docker.io/library/python:3.12-slim-bookworm`, cached digest `sha256:76d4b7b6305788c6b4c6a19d6a22a3921bf802e9af4d5e1e5bd771208dba74bf`; `docker.io/library/python:3.12-alpine`, cached digest `sha256:236173eb74001afe2f60862de935b74fcbd00adfca247b2c27051a70a6a39a2d`. | [Bookworm LTS](https://www.debian.org/releases/bookworm/) continues through June2028 on supported architectures; [Python 3.12 policy](https://www.python.org/downloads/release/python-31212/) is security-only through October2028. Slim serves policy proxies and host-isolation validation; Alpine serves doc-drop, embedding shim and Obscura fixture validation. | **Unresolved actual package/runtime versions.** C1-C3 resolve reviewed current digests, enumerate effective inherited consumers, inspect Python/distribution/package evidence and qualify refresh delivery. Preserve Python major/minor and distribution. Require deterministic proxy/shim tests, affected image gates and full RAG/browser checks; cached digest metadata is not reviewed remote-package evidence. |
| R8 | Wrapper: `docker.io/alpine/socat:1.8.0.3`; cached digest `sha256:76d1e4fc91bdd9d08f2a72a3fde1776798fd00cc00d3bded940dc154cd7ab6fd`. | [Current publisher Dockerfile](https://raw.githubusercontent.com/alpine-docker/multi-arch-libs/master/socat/Dockerfile) uses moving Alpine and APK installation. Current source cannot prove historical image provenance/package versions. Fixed bridges use TCP and local health commands. | **Unresolved provenance/advisory applicability; C1-C3.** Next action: resolve selected registry digest, inspect socat/APK/base versions and primary application advisories before selecting refresh. Qualify every affected fixed bridge/health command, including Podman/macOS and optional executor/Teep/Exa paths. No newer tag accepted merely because it exists. |
| R9 | Tailscale: `docker.io/tailscale/tailscale:v1.104.4@sha256:ed6480b33c6eb67c9cbd203ed42b9fb57e361986725b6856def71b7a3d9e276c`; source `27047635f237d1f3e0b08c0f487674757f30d2f7`. | Verified 2026-11-02: [TS-2026-013](https://tailscale.com/security-bulletins#ts-2026-013) requires compiled Go certificate distrust; CA-package date alone cannot close it. [Selected release](https://github.com/tailscale/tailscale/releases/tag/v1.104.4) requires Go 1.27.3. Independent exact-source review found unchanged publication builder/Alpine 3.22 base/Serve substitution, existing env contract and unchanged actual platform set. New boot-timeout option retains 60 seconds. Single HTTP mount excludes multi-capability/Unix-backend triggers; operator-enabled SSH needs separate assessment. | **C2 application remediation selected; actual binary/Go/package and live qualification pending.** Run selected security gate, inspect Go and trust separately, retain static linkage/containerboot contracts. Enabled ingress cannot be validated without credentials. No whole-application clean bill of health. |
| R10 | Wrapper refresh generation `20261102.2` applies to all eight local build identities. C1 generation `.1` artifacts and packages are recorded above; C2 selections require renewed qualification. | [OpenSSL published advisory timeline](https://www.openssl-library.org/news/timeline/) establishes releases after historical package floors; revision labels provide no package evidence. | **C1 generation `.1` qualified on Docker Linux/arm64; C2 generation `.2` qualification remains pending.** Exact artifacts/packages and passing patch/security gates are recorded above. C2-C3 must complete remaining delivery and applicability work; Podman and live qualification remain unavailable. Existing package floors are regression checks, not proof every advisory is fixed. |
| R11 | Executor `cryptography==50.0.2`, hashed lock regenerated for Python 3.11/Linux through `make upgrade-python-deps PYTHON_DEPS_COMPONENTS=executor`; selected wheel contract is bundled OpenSSL 4.0.3. | Verified 2026-11-02: [cryptography changelog](https://cryptography.io/en/latest/changelog/) and [platform support](https://cryptography.io/en/50.0.2/installation/) support the same Python/runtime line. [OpenSSL advisory](https://mirror.openssl-library.org/news/secadv/20260929.txt) fixes affected 4.0.2 ranges in 4.0.3. Concrete PDF X.509/CRL exploit prerequisites remain unestablished; AES/PDF checks establish compatibility. Debian [CRL fix](https://security-tracker.debian.org/tracker/CVE-2026-35189) needs `3.5.7-1~deb13u3`; Alpine floor is 3.5.9-r0. | **C2 wrapper remediation implemented; selected artifact qualification pending.** Security assertions now target the reviewed packages/wheel; validate actual system and bundled copies, dependency consistency, AES, encrypted PDFs and executor contracts. Changed assertions and regenerated lock alone do not establish artifact remediation. |
| R12 | Onyx v4.8.8 default 0.4.7; bounded controller/executor selection 0.4.9, source `e53850d68701e5e62440a36ed311164dafc89465`; controller digest `sha256:7626d1f139e56d640cd0e333a90f632e26dc986db8641d6dbce45dc494e03580`; executor digest as R11. | Exact [Onyx env.template](https://github.com/onyx-dot-app/onyx/blob/f400d3154a13ac1f3ee730896f69fbc25eb5d8f7/deployment/docker_compose/env.template) permits a separate release selection. Independent source call-chain review and failure behavior are recorded in the accepted G0 decision above. | **Source compatibility and C1 generation `.1` selected-image contracts passed for the exact tuple; C2 `.2` and live integration pending.** C1 records bounded authority; changes to either owner/default/selected release reopen compatibility review. Selected patch/security image gates passed; retain live executor command/network qualification. No silent downgrade and no permanent allow-newer exception. |
| R13 | SearXNG `2026.7.15-7b2199ecd`, Python3.14 build stage; Obscura 0.2.5 source `bd9529418b7fa975b50eabeac415cfaaa865a588`, runtime digest `sha256:354c3d2a0fa2dd2939debe6903aa26c35d121329245dfa42aa7d1c292a209814`, Rust1.94.0-bookworm digest `sha256:365468470075493dc4583f47387001854321c5a8583ea9604b297e67f01c5a4f`; Tor0.4.9.14 digest `sha256:38f57fbef35b372f5eac374fa3d44a4edf6c5e2ff2a6883ace851617c09f16bb`; Myst source `74d144d4261adbb9feb84825946078b8161ebe46`; Teep source `d9f59d839eb887b50f5afd234e192eac5ec45a2d`; Myst/Teep Go1.26-alpine and Alpine3.22 build/runtime bases. | [SearXNG](https://github.com/searxng/searxng/security/advisories) and [Obscura](https://github.com/h4ckf0r0day/obscura/security/advisories) currently publish no own advisories; absence does not establish bundled dependency cleanliness. Independent [Tor 0.4.9.15 review](https://lists.torproject.org/mailman3/hyperkitty/list/tor-announce%40lists.torproject.org/thread/WXHTSV43NO5UZQ7Q634S5NS5MIRL5JSJ/) supports retaining 0.4.9.14 for this client-only configuration: relay/directory defenses and unused control commands do not apply; remaining dependency evidence is separate. C2 identities include all source/base/copied/context inputs; native source/runtime review is incomplete. | **Unresolved support/advisory/dependency evidence; identity remediation assigned C2-C3.** Next action: exact-source dependency/SBOM audit, registry/runtime evidence and complete build-input coverage including hard-coded stages. Require affected `make test-patch-images`, `make test-obscura-image`, `make test-tor-image`, browser/search, Myst namespace and Teep inference contracts. No automatic unrelated upgrades authorized; focused gates and live evidence pending. |

C1 establishes a disposition for every row: validated/no applicable finding,
upstream-pending with residual risk, wrapper remediation assigned to C1-C3,
or unresolved with a named next action. The inventory is incomplete while a
row is missing or unassessed. An unresolved material authority/design issue
blocks dependent work; an upstream-pending advisory does not by itself
authorize divergence from the agreed version policy.

C2-C3 complete assigned remediation and delivery evidence; C4 verifies the
final dispositions. A row requiring a new owner-approved version must be
reassessed at that owner's next update. Do not mark remediation complete on
the strength of new update machinery, changed test assertions, or planned
validation. Carry upstream-pending findings and unavailable evidence into the
final handoff without describing them as fixed.

Add the standing reconciliation requirements to
[the upgrade checklist](../onyx_patches_upgrade.md) in C1: owner/version
alignment, advisory applicability, distribution support, system versus bundled
libraries, and residual-risk reporting. Update its OS/package/library floors
and component-specific review instructions alongside the relevant C1-C3
implementation. Keep transient findings and batch results in the active plan
and final review evidence, rather than turning the owning guide into a security
incident history. Tests and focused image gates accompany actual changes.

Acceptance: structured fixtures cover changed, unchanged, missing, ambiguous,
and added/removed owner contracts; verify inherited values, all nginx consumers,
MinIO metadata, revision bumps, and unchanged committed inputs on startup.
Resolve the controller authority discrepancy with recorded source evidence.
Establish the per-row reconciliation dispositions and evidence above; recurring
upgrade tooling alone does not satisfy this initial acceptance requirement.
Run `make check`, inspect affected effective Compose models, and run affected
image contracts if selected artifacts change. Update
[the upgrade procedure](../onyx_patches_upgrade.md) and applicable
[AGENTS.md workflow guidance](../../AGENTS.md) in this phase; update README only
for lasting user-facing consequences. Independently review C1's exact snapshot
and resulting commit before building on its authority decisions.

## C2: complete identities for local builds and generated files

Include source selection, relevant Dockerfiles, copied files, base selections,
and applicable refresh revision in every derived image identity. Extend the
existing pattern to Myst and Teep; audit the other builders for omissions.
Preserve explicit caller image overrides and report their effective selection.

Deliberately refresh moving build bases and invalidate package installation
layers when publishing package refreshes. A changed output tag does not by
itself invalidate Dockerfile layers, and `--no-cache` does not replace base
image refresh. Use supported Docker and Podman mechanisms separately.

Fingerprint generated Onyx deployment artifacts using the selected configuration
ref and wrapper generation inputs/revision. Detect missing or stale artifacts
without relying only on the application tag. Download and validate the complete
required set before installation; record completion only after installation and
required synchronization succeed. Preserve existing local secrets.

Acceptance: fixtures change each relevant build input independently and prove
that selection changes; unchanged inputs remain reusable. Exercise base-pull
and package-cache policy, explicit overrides, same-Onyx-version configuration
refresh, missing files, and failed/partial preparation. Run `make check` and
affected focused image gates against the selected artifacts. Update
[the upgrade checklist](../onyx_patches_upgrade.md),
[Podman image preparation](../podman_suport.md), and
[resource policy](../resource_minimization.md) with implemented behavior.

### G1: authority and artifact selection after C2

Independently review C1-C2 and every fix/fixup together, from owner selection
through generated configuration and complete image build inputs. Check all
consumers, explicit overrides, package versus bundled-library ownership, and
both engines. Review complexity of parsing, Make re-evaluation, and fingerprint
state against the baseline; prefer existing native image identities over
additional tracking. Exit when selected identities represent the intended
refresh and failure paths cannot mark stale/partial work complete. Resolve
material findings before C3. Check the initial reconciliation dispositions,
assigned remediation, and accompanying upgrade-checklist changes together.

## C3: deliver reviewed updates through normal startup

Add one ordered image-preparation path to both `make up-*` flows, after engine
and configuration validation and before any container creation/start that can
consume the images. Audit the dependency graph rather than relying on sibling
Make prerequisites, especially under parallel Make.

1. Prepare the selected generated deployment configuration.
2. Resolve the effective mode/engine/feature-specific model and image selection.
3. Refresh required registry images when the committed refresh generation or
   effective selection changes; prepare missing images on ordinary starts.
4. Build missing selected derived images with the required cache/base policy.
5. Continue existing engine-specific creation, health configuration, and staged
   full-mode embedding readiness.

Keep registry pulls separate from local builds. Prepare the executor explicitly
even though its image is not an ordinary Compose service. Exclude disabled
optional services and Docker-only controller/executor artifacts on Podman.
Reuse matching prepared artifacts on unchanged starts without periodic work.

If completion records are needed, scope them to the selected engine/store,
architecture, effective image selection, refresh generation, and configuration.
Recheck image presence after store deletion/pruning; a record is not proof an
artifact still exists. Account for adding full mode or enabling a feature after
lite preparation. Serialize conflicting preparation, write records atomically,
and never mark a partial pull/build failure complete. Fail startup visibly on
required refresh failure, including when an older cached tag remains available.

Acceptance: fake engine/registry tests exercise the actual Make entrypoints
from populated old stores, including unchanged moving tags, refresh revisions,
mode/feature transitions, explicit overrides, pruning, failures/retries, and
parallel-Make ordering. Verify no registry pulls target local derived images
and no disabled/Docker-only artifacts are prepared on inapplicable paths.
Run `make check`, render the affected matrix, and exercise available cached-image
lite/full startups with health and targeted logs. Update
[README upgrade instructions](../../README.md#upgrading-the-stack),
[the upgrade checklist](../onyx_patches_upgrade.md),
[Podman lifecycle](../podman_suport.md), and
[resource policy](../resource_minimization.md) in this phase. Update other
owning guides whenever implementation changes their contracts.

### G2: end-user delivery and lifecycle after C3

Independently review C1-C3 and all fixes/fixups along the complete
`down -> git pull -> up` path, including first install, engine preflight,
generated files, registry pulls, local builds, stopped-container creation,
Podman health translation, and full-mode readiness staging. Assess concurrency,
state complexity, privacy routing, secret boundaries, and accumulated test gaps.
Exit when cached installations receive required artifacts, failures remain
visible without stale fallback, unchanged starts reuse matching work, and the
documented Docker/Podman contracts hold. Missing live evidence must remain
explicit; do not present model rendering as engine qualification.

## C4: freshness reporting and final integration acceptance

Provide an explicit online maintainer audit reporting owner-selected versions,
resolved registry digests, available updates, advisories, and unresolved
applicability. Keep it outside deterministic checks and ordinary startup.
Distinguish system packages from bundled OpenSSL, compiled Go dependencies,
and Python wheels; an OS refresh cannot update all of these.

Add offline executable checks for ownership coverage, upstream synchronization
fixtures, consistent image consumers, complete build identities, and revision
bumps against an explicit review base. Require no private configuration or
network access. Test the behavior and machine-consumed contracts, not prose.

Use fake engine/registry commands to exercise the README upgrade sequence from
a populated old image store. Cover changed/unchanged tags, source/build changes,
OS revision changes, retries after failures, missing images despite completion
records, mode/feature transitions, engine/store switches, caller overrides,
configuration refresh with unchanged Onyx version, and preparation ordering.
This consolidates the phase-local coverage; it does not postpone C1-C3 tests.

Acceptance: use recorded upstream/registry/advisory fixtures to test the online
audit's report and error behavior without Internet in deterministic checks.
Distinguish unavailable evidence from a clean audit and upstream-pending
findings from authorized version changes. Keep audit output free of secrets.
Run `make check` and complete the applicable integration matrix below. Update
[the upgrade checklist](../onyx_patches_upgrade.md) with the audit's standing
usage and validation, and `make help` only if adding a user-facing workflow or
key diagnostic.

### G3: final accumulated review and documentation handoff

Independently review C1-C4 and all fixes/fixups against the baseline, including
complete consumers and call chains rather than just final diffs. Analyze fix
causes and recurring assumptions for wider audit, simplification, and regression
coverage; revisit parsing/control-flow/state complexity without adding a new
metrics dependency. Verify evidence matches the exact reviewed source and
selected artifacts. Later material changes reopen affected gates and checks.
Verify every initial reconciliation row has a final disposition and evidence,
with outstanding upstream exposure and omitted validation explicitly carried
forward. Do not equate completion of the delivery tooling with fixing every
reported vulnerability.

Exit when material findings are resolved, required available checks pass,
unavailable checks and residual risks are recorded, README's unchanged workflow
matches implemented delivery, and permanent behavior resides in owning guides
with necessary AGENTS.md links. A blocked required gate remains incomplete.
Replace implemented detail in this plan with canonical links before acceptance.
After review/merge, preserve the accepted plan under `docs/plans/implemented/`
according to repository policy; review the final move snapshot and resulting
commit and combined changes as well. Do not edit earlier frozen plans.

## Design choices to resolve before implementation

- Choose whether to retain `OS_SECURITY_UPDATE_REVISION` as the shared refresh
  generation or introduce a clearer shared name with no redundant counters.
- Decide how registry digests are recorded. An explicit pull of the same
  upstream-selected moving tag preserves version authority but can deliver
  different bytes from those reviewed. Recording multi-architecture digests
  would deliver reviewed bytes while retaining upstream's selected tag; assess
  that option without independently advancing upstream application versions.
- Choose the minimum local completion-record format and engine/store identity.
  Keep disposable preparation state out of committed pins and private data.
- Define the review-base input and component-input coverage for bump checks;
  ordinary `make check` must work without assuming a particular remote branch
  or a complete Git history.

## Validation and documentation acceptance

- Run `make check` for implementation changes. Run the focused image gates for
  each affected family; reserve the aggregate gate for broad release work.
- Validate refreshed OS artifacts with `make test-patch-images` and
  `make test-security-images`. Deliberately review package/library assertions;
  changing a test floor does not establish a fixed selected artifact.
- Render Make-selected Docker/Podman lite/full models with optional Tailscale
  and Tor on/off and affected routing/executor overlays. Verify effective image
  selection and unchanged networks, mounts, entrypoints, and staged lifecycle.
- Exercise normal Make startup with previously cached images on available
  engines/platforms. Inspect health and targeted logs. Run affected image and
  live contracts required by the owning documents, including RAG/OpenSearch,
  Tor, browser/search, and executor checks where their artifacts change.
- Use synthetic/disposable fixtures and supported Make/Compose configuration
  passing. Do not read private environment files or private document stores.
  Record unavailable engines, credentials, services, or live checks explicitly.
- Update the owning documentation in each implementation phase. Keep README
  upgrade commands unchanged and describe their actual delivery guarantee.
  Add AGENTS.md guidance only where supported workflows or repository-wide
  invariants change. Do not modify frozen implemented plans.

No independent Onyx dependency upgrades, storage migrations, scheduled updater,
runtime package manager, or broad cache deletion are authorized by this plan.
Local phase and fixup commits follow AGENTS.md; remote publication remains a
separate user decision. Implementation and validation results should be reviewed
before marking phases complete.
