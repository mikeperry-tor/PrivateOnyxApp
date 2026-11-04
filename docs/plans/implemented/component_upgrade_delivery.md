# Component version ownership and end-user upgrade delivery

Status: Implemented and archived on 2026-11-04 after user-requested branch
review. G0 and C1-C4 implementation, branch-review fixes and accumulated source
reviews passed through `b7c133a9`; final documentation review passed through
`43c97714`. Final deterministic validation passed at `b7c133a9`. Initial
selected-image and native Docker fixture evidence is recorded separately below and was not repeated for the branch-review fixes. Podman,
native Linux/other-architecture and configured operator-stack qualification
remain incomplete and are not implied by source approvals.
Implementation baseline: `3f54b2a4d8647047a78ed3eb68f1fb7765aaf0fe`.
Commits between that baseline and G0 changed contributor instructions and this
plan only.

### Branch-review acceptance (2026-11-04)

The original implementation and its fixups were reviewed against `main` for
completeness, correctness, coverage, complexity and canonical-documentation
handoff. The following additional fixups preserve unsquashed branch history:

| Fixup | Scope |
| --- | --- |
| `619291c3` | Preserve controlled audit CLI failure reasons. |
| `aed4b67f` | Correct canonical executor library facts and link delivery ownership. |
| `1d19e8e6` | Deliver the published revision and synchronized selections through actual Make recursion. |
| `aa80d82a` | Share the reviewed immutable Onyx source across synchronization, audit and configuration. |
| `75faa1c6` | Acquire full-mode validation prerequisites after builds/configuration, with Docker/Podman separation. |
| `9e67ffb5` | Remove obsolete upstream installer scripts and their unused settings. |
| `329a0f7a` | Run full-mode host-data preparation once through the tokenizer prerequisite. |
| `a9698499` | Rebuild Obscura at fixed caller destinations during broad upgrade. |
| `0e9f823a` | Remove duplicate cached-image inspections while retaining acquisition verification. |
| `b7c133a9` | Update the existing lifecycle contract test to follow the tokenizer prerequisite chain. |

Every listed fixup received independent exact-commit review. `/root/fix_design`
reviewed the scoped designs and first eight fixups. Fresh accumulated reviews by
`/root/integration_review_1` and `/root/integration_review_2` covered the original
implementation and fixes through `a9698499`; the former renewed the complete
upgrade/startup/helper gate through `b7c133a9`. The later two fixups do not change
the latter's ownership, audit, identity, image-gate or canonical-documentation
scope. No significant source findings or compelling simplifications remain.

`make check ENV_FILE=.env.wrapper.example` passed at `b7c133a9`: 865 tests in
960.151 seconds with 32 expected image-only skips, compilation, help and diff
checks. The completed run had permission for the suite's temporary loopback and
Unix-socket fixtures. The explicit-base revision check against `main` and
committed-diff whitespace check also passed. New regression coverage exercises
actual upgrade recursion, fixed output destinations, acquisition failure/retry,
Podman artifact omission, reviewed-source invalidation and single preparation
calls. Checks used public settings and disposable fixtures; no operator stack
or new selected-image/live qualification was performed. Current behavior is
owned by [the upgrade checklist](../../onyx_patches_upgrade.md) and
[patch information](../../onyx_patch_info.md), not this acceptance record.

### Consolidated acceptance evidence (2026-11-02 through 2026-11-04)

Local phase commits: G0 `d7a03d5e`, C1 `74df89b7`, C2 `76d30981`,
C3 `83a1db94`, C4 `58c15f2e`. Reviewed fixups: C1 `69c6c553`,
`64689baa`, `f749d4fd`, `fb4c1484`; C2 `bc7ebcbb`, `d29c4cbd`, `64470e1c`,
`4c95083a`; C4 `306c48da`, `a8db95ee`, `881c835a`. Independent reviewer
`/root/g0_design` approved each exact SHA and the accumulated implementation
against the baseline. `/root/c1_evidence` independently examined source
compatibility and reviewed C4's audit/consumer coverage. No material source
findings remain. The reviewer renewed accumulated source gates through
`881c835a` and approved the consolidated image/package/audit/network evidence
and documentation handoff against the baseline. Exact commit reviews remain
required for later changes; unavailable qualification is not waived.

Review covers owner authority and bounded compatibility; recursive COPY/context
and immutable base identities; configuration staging and secret preservation;
Make recursion and explicit overrides; engine/store locking, acquisition and
platform selection; tokenizer and Podman/full lifecycle; privacy routing;
public audit authority, evidence gaps and documentation handoff. Complexity
review retained native identities and narrow state/locks rather than receipts
or a general workflow framework. Repeated input/authority assumptions produced
regressions for recursive COPY inputs, exact catalog reconciliation, Tor's
selected version and public environment propagation. Metadata review also
distinguished MinIO's official release source from unverified mirror/base labels;
correcting that metadata changes no selected image or local build identity.

Available validation, using public example settings and disposable fixtures:

- Final `make check ENV_FILE=.env.wrapper.example` passed at `881c835a` on
  2026-11-04: 860 tests in 1013.074 seconds, 32 expected image-only skips,
  compilation, help and diff checks. The selected-image patch gate separately
  exercised the skipped SearXNG parser and SDK dependencies. After the final
  metadata/test fixes, all 21 focused validation/recursion/pin/ownership tests
  and exact-source synchronization/revision checks also passed. Only this
  evidence-document change follows the full check; it requires diff review.
- All eight selected local images built successfully with generation
  `20261102.2` and unchanged committed build inputs during validation.
  `make test-patch-images`, `make test-obscura-image`, `make test-tor-image`,
  `make test-security-images`, and `make test-opensearch-image` passed on
  Docker Desktop Engine 29.9.2, API 1.56, Linux/arm64. The Tor validator's stale
  version assertion was corrected in reviewed fixup `4c95083a` before rerun.
  The final OpenSearch gate also passed with a nonexistent generated Onyx env
  path, proving metadata validation does not require that private file.
- Ownership/COPY contracts passed across 54 Make-selected effective models
  (48 macOS combinations and six Linux engine/root combinations). These
  establish model coverage, not native platform qualification.
- C3 passed 31 preparer tests and eight actual Make-entrypoint fake-engine
  scenarios, including parallel Make, cached unchanged tags, failures/retries,
  pruning, explicit overrides, mode/profile changes and Podman full staging.
  Native cached-image preparation passed with acquisition disabled. A native
  four-image registry fixture pulled four images initially and acquired none
  on repetition; it created no containers.
- Native `make integration-network-isolation` passed all four disposable cases
  and two DNS groups on Docker Desktop, including internal IPv4/IPv6,
  root/nonroot, raw-socket denial and DNS forwarding. This is not native Linux
  or complete configured-stack qualification.
- Exact-release `component-sync` reported no owner drift; explicit-base
  revision enforcement passed. Actual online `make component-audit` completed
  with `evidence_collected`, all 20 registry records and no missing required
  or optional public sources. Discovery candidates remain unapproved changes;
  collected evidence does not establish advisory applicability or cleanliness.
- Myst/Teep CLI, namespace-holder sleep, Python TLS/CA and socat TCP-copy
  probes passed against selected images. OpenSearch's disposable workload and
  restart recovery passed; no operator data or live RAG was used.

| Selected local artifact (Linux/arm64) | Exact local image ID |
| --- | --- |
| `local/private-onyx-myst:71672d8a93e6` | `sha256:122d46a8cf7283f83a00ed878324f61c89f9f384bea33267de47ab711b7de6b2` |
| `local/private-onyx-teep:7aa27982ce9f` | `sha256:2325d91e0b3e88cb0f9b387c0e327e05e97773040d70777f2ec3eacfae382a1f` |
| `local/private-onyx-tailscale:38ffbc247be3` | `sha256:56d7995b17cf9c896605cd0d2aa711739d4d86e1e8513e68944b70f0e0a81472` |
| `local/private-onyx-tor:0.4.9.14-d05989d8ca0e` | `sha256:8c9b3c8be78c1437d6d3377bebcfbcbdf84dd18f13592dfe9ad0a2af063f4444` |
| `local/private-onyx-obscura:0.2.5-stealth-4dae50aa73d8` | `sha256:78b9e1448cc74e44e7f6f51b889fc3b5030fcbad79d5462480019a6bb2c25c60` |
| `local/private-onyx-searxng:2026.7.15-7b2199ecd-719051487c02` | `sha256:0730bce37492d216e8dcec77b7fb892b3e600d0090deb82c55bcc980ba2a764f` |
| `local/private-onyx-code-interpreter:0.4.9-b7faa1f50170` | `sha256:ac5ab270a037d0f038b952bc3a34b64d2e05b14fa58f95b093ef2b7276e39daf` |
| `local/private-onyx-python-executor:0.4.9-57f29c2de069` | `sha256:2f55c883e86b98892261db305eef2e5cdcacef273733bece77dc1fb0d561e0e8` |

Controller/executor contain Debian OpenSSL/provider `3.5.7-1~deb13u3` and
CA bundle `20250419+deb13u1`; executor cryptography 50.0.2 bundles OpenSSL
4.0.3 and passed dependency, AES and encrypted-PDF checks. Tailscale reports
1.104.4 / Go 1.27.3, OpenSSL `3.5.9-r0` and CA `20261020-r0`.
Tor reports 0.4.9.14; hardened runtime, ownership, Unix socket and authenticated
control contracts passed. Floors and image gates are regression evidence,
not a whole-application security assessment.

Validation limitation discovered and fixed: recursive Make validation could
previously fall back to the default operator env despite a public parent
setting. Synthetic fixtures reproduced this; `a8db95ee` explicitly forwards
`ENV_FILE` and removes the generated private env from OpenSearch metadata
inspection. Earlier helper output was discarded without inspecting private
values. We cannot assert that the earlier helper never parsed a private file.
No operator stack was started or private runtime state modified.

Unavailable validation: Podman is not installed; native Linux-host and other
architecture qualification are unavailable. No configured operator lite/full
startup, private-document RAG/cache, VPN, live browser/search, Tailscale/onion
exposure, executor command/network integration or Teep inference was run.
Those require an available runtime/configured stack and, where applicable,
credentials, external services or funding. Disposable fixtures and effective
models do not replace these checks. Upstream-pending advisories, complete
installed-dependency assessment and MinIO mirror-build provenance remain open below.
Implementation and branch review are complete. This accepted record is archived
at the user's request; outstanding qualification and dependency assessments above
remain open and are not prerequisites newly satisfied by archiving. Standing
upgrade and qualification requirements remain in the canonical owning guides.

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
plan is already present at that baseline; C1-C4 are now implemented.
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
[the upgrade checklist](../../onyx_patches_upgrade.md),
[Podman support](../../podman_suport.md),
[resource minimization](../../resource_minimization.md),
[internal network security](../../internal_network_security.md), and
[routing](../../vpn_routing_and_proxies.md). Consult
[native Tor](../../native_tor_support.md) and
[local RAG](../../local_docs_rag_search.md) for affected lifecycle paths.
Update those authorities alongside implementation, then link to their current
contracts here rather than retaining duplicate specifications in this plan.

## Starting gaps addressed

At the implementation baseline, the Makefile's broad upgrade flow refreshed locks and built artifacts without
automatically synchronizing all committed pins. Wrapper nginx and MinIO
overrides could therefore lag their selected Onyx release. PostgreSQL, Redis,
and OpenSearch inherited upstream deployment values.

Baseline startup reused existing local image references. That delivered changed
content-derived tags, but could not establish freshness behind unchanged registry
tags. Myst and Teep image selection did not cover every local build
input. Generated Onyx deployment readiness was tied to the application tag,
which could not represent every configuration refresh.

## Review sequence and commit boundaries

Follow [independent workflows and plan structure](../../../AGENTS.md#independent-subagent-workflows).
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

The implemented ownership, exact-source reconciliation, compatibility boundary
and refresh-generation policy are documented in
[version ownership and reconciliation](../../onyx_patches_upgrade.md#version-ownership-and-reconciliation).
The [inventory](../../../tools/components/ownership.json) records consumed owners,
reviewed registry selections and the explicit revision-input boundary. Phase
acceptance evidence is consolidated above and in the initial reconciliation.

### Initial image and library reconciliation

Initial primary evidence was checked on **2026-11-02** by
`/root/c1_evidence` (source compatibility, primary advisory/support research)
and the coordinator (registry resolution). Selected artifact probes and final
dispositions were updated on **2026-11-03**.
The exact Onyx owner source is v4.8.8,
`f400d3154a13ac1f3ee730896f69fbc25eb5d8f7`, specifically its
[deployment Compose](https://github.com/onyx-dot-app/onyx/blob/f400d3154a13ac1f3ee730896f69fbc25eb5d8f7/deployment/docker_compose/docker-compose.yml).
Registry digests are resolved multiarchitecture selections; native observations
below used the exact selected digest on Linux/arm64. Unavailable dependency
applicability and live evidence remains unresolved. This table is batch acceptance evidence, not a clean security audit.
Source reviews and selected-image contracts passed as recorded above; unavailable
platform/live qualification remains explicit.

| ID | Owner and exact selected artifact/source | Primary evidence and applicability (verified 2026-11-02) | Disposition, responsible phase, required checks and current limitation |
| --- | --- | --- | --- |
| R1 | Onyx v4.8.8: `docker.io/library/nginx:1.25.5-alpine`; resolved `sha256:516475cc129da42866742567714ddc681e5eed7b9ee0b9e9c015e464b4221a00`. | [nginx advisories](https://nginx.org/en/security_advisories.html) include affected ranges containing 1.25.5. Fixed ingress gateways use HTTP/1.1 TCP backends; no configured TLS/HTTP3/AIO/slice/SSI/DAV handlers. Generated Onyx API configuration uses rewrite. Complete compiled-module/configuration applicability is unresolved. | **Upstream-pending / unresolved applicability.** All three nginx consumers reconcile and effective models pass. Selected binary is nginx 1.25.5, Alpine 3.19.1, compiled OpenSSL 3.1.4 / APK `3.1.4-r6`. No independent application upgrade. Complete module/advisory assessment and live ingress checks remain required at owner update. |
| R2 | Onyx v4.8.8: inherited `docker.io/library/postgres:15.2-alpine`; resolved `sha256:d9c304353c031b21e9a7e33dc4781e272a9fa802a2ab9703fe4199d72ba1422c`. | [PostgreSQL 15 security](https://www.postgresql.org/support/security/15/) lists fixes after 15.2, including schema/role/RLS findings fixed in 15.3. Exact role/query prerequisites need deployment assessment. | **Upstream-pending.** Effective inherited selections pass. Selected binary is PostgreSQL 15.2, Alpine 3.17.3, OpenSSL APK `3.0.8-r3`; no data-server startup or restart was performed. Retain owner version/storage major; assess role/query prerequisites and live database recovery at the next owner-approved update. |
| R3 | Onyx v4.8.8: inherited `docker.io/library/redis:7.4-alpine`; current registry `sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f`. | Primary [RESTORE](https://github.com/redis/redis/security/advisories/GHSA-c8h9-259x-jff4), [Lua](https://github.com/redis/redis/security/advisories/GHSA-8ghh-qpmp-7826), and [unblock](https://github.com/redis/redis/security/advisories/GHSA-93m2-935m-8rj3) advisories list relevant affected ranges and no concrete patched version in their current metadata. Cluster is not configured; that does not exclude non-cluster findings. | **Unresolved applicability / upstream-pending.** Exact selected digest reports Redis 7.4.12, Alpine 3.21.9 and OpenSSL `3.3.7-r2`. C3 unchanged-tag refresh and failure/retry fixtures pass. Assess current advisory prerequisites/patched ranges against these exact bytes; configured full-mode cache/startup qualification remains unavailable. |
| R4 | Onyx v4.8.8: inherited `docker.io/opensearchproject/opensearch:3.6.0`; resolved `sha256:b5dd1512af2a99748c942cfbbd7f32162623336b210667d0fc6333c6321f171d`. | [October zstd advisory](https://github.com/opensearch-project/OpenSearch/security/advisories/GHSA-68xw-fqpj-h922) explicitly excludes 3.x. Newer releases alone do not establish exposure. Bundled JDK/plugin/dependency applicability remains unverified. | **No applicable finding for cited zstd advisory; remaining dependency evidence unresolved.** Exact selected OpenSearch image gate, disposable workload and restart recovery passed (26 plugins, 128-dimensional vector workload). Bundled JDK/plugin advisory inventory and configured Onyx/RAG integration remain required; no independent owner-version upgrade. |
| R5 | Onyx v4.8.8: `docker.io/onyxdotapp/minio:RELEASE.2025-07-23T15-54-02Z-cpuv1@sha256:7330be2e7320a7a699b36f72bd06a94d8a21ec9f6f397345d9c38fc6751141f5`; official release source `7ced9663e6a791fef9dc6be798ff24cda9c730ac`. | [Unsigned-trailer signature bypass](https://github.com/minio/minio/security/advisories/GHSA-hv4r-mvr4-25vw) affects all OSS releases; published fix is AIStor. [Session-policy bypass](https://github.com/minio/minio/security/advisories/GHSA-jjjj-jwhf-8rgr) was fixed after the selected July2025 release. Restricted private reachability reduces exposure but does not patch either issue. | **Upstream-pending exposure; mirror/source identity unresolved.** Selected binary reports the [official July2025 release](https://github.com/minio/minio/releases/tag/RELEASE.2025-07-23T15-54-02Z), commit [`7ced9663e6a791fef9dc6be798ff24cda9c730ac`](https://github.com/minio/minio/commit/7ced9663e6a791fef9dc6be798ff24cda9c730ac), Go 1.24.5 and RHEL 8.10. The release/commit pair matches upstream; `fb4c1484` corrects prior unverified source metadata to this official release commit. Matching self-report does not establish the mirror build source; verify publisher/source relation before claiming provenance. Retain owner artifact without product/license/storage migration; assess session-policy use and full object-store/RAG integration at owner update. |
| R6 | Wrapper: `docker.io/library/alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f`. | [Alpine support](https://alpinelinux.org/releases/), verified 2026-11-02: 3.20 regular support ended 2026-04-01; 3.22 main is supported through 2027-05-01. The holder requires `sleep infinity`. | **C2 supported-base remediation qualified on available Docker platform.** Alpine 3.22.7 namespace-holder sleep and disposable network-isolation fixtures passed. Effective models pass; Podman and native Linux/other-architecture lifecycle qualification remain unavailable. |
| R7 | Wrapper: `docker.io/library/python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337`; `docker.io/library/python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8`. | [Bookworm LTS](https://www.debian.org/releases/bookworm/) continues through June2028 on supported architectures; [Python 3.12 policy](https://www.python.org/downloads/release/python-31212/) is security-only through October2028. Slim serves policy proxies and host-isolation validation; Alpine serves doc-drop, embedding shim and Obscura fixture validation. | **Selected refresh qualified on available Docker platform; complete advisory/live assessment unresolved.** Both report Python 3.12.16; Slim OpenSSL is 3.0.22 (`3.0.22-1~deb12u2`, CA `20250419~deb12u2`), Alpine is 3.23.4 / OpenSSL `3.5.9-r0` / CA `20261020-r0`. Deterministic proxy/shim and affected image/network fixtures passed. Full configured RAG/browser and other-engine checks remain unavailable; no blanket fixed claim. |
| R8 | Wrapper: `docker.io/alpine/socat:1.8.0.3@sha256:beb4a68d9e4fe6b0f21ea774a0fde6c31f580dde6368939ed70100c5385b015e`. | [Current publisher Dockerfile](https://raw.githubusercontent.com/alpine-docker/multi-arch-libs/master/socat/Dockerfile) uses moving Alpine and APK installation. Current source cannot prove historical image provenance/package versions. Fixed bridges use TCP and local health commands. | **Provenance/advisory applicability remains unresolved.** Selected digest reports socat 1.8.0.3-r1, Alpine 3.23.4, OpenSSL `3.5.6-r0`, CA bundle `20260413-r0`. Actual TCP-copy and model/health fixtures passed. Complete publisher/application advisory review and every configured fixed bridge on supported engines remain next actions; these TCP fixtures do not establish SSL remediation. |
| R9 | Tailscale: `docker.io/tailscale/tailscale:v1.104.4@sha256:ed6480b33c6eb67c9cbd203ed42b9fb57e361986725b6856def71b7a3d9e276c`; source `27047635f237d1f3e0b08c0f487674757f30d2f7`. | Verified 2026-11-02: [TS-2026-013](https://tailscale.com/security-bulletins#ts-2026-013) requires compiled Go certificate distrust; CA-package date alone cannot close it. [Selected release](https://github.com/tailscale/tailscale/releases/tag/v1.104.4) requires Go 1.27.3. Independent exact-source review found unchanged publication builder/Alpine 3.22 base/Serve substitution, existing env contract and unchanged actual platform set. New boot-timeout option retains 60 seconds. Single HTTP mount excludes multi-capability/Unix-backend triggers; operator-enabled SSH needs separate assessment. | **C2 application remediation qualified on available Docker platform.** Selected security gate, binary Go 1.27.3, static linkage/containerboot and package/trust assertions passed. Enabled ingress needs credentials and was not tested. Operator SSH and complete advisory applicability remain separate; no whole-application clean claim. |
| R10 | Wrapper refresh generation `20261102.2` applies to all eight selected local build identities recorded above. | [OpenSSL published advisory timeline](https://www.openssl-library.org/news/timeline/) establishes releases after historical package floors; revision labels provide no package evidence. | **Generation `.2` built and qualified on available Docker platform.** All eight selected IDs are recorded above; patch/security and affected image gates passed. Actual package/library versions are recorded rather than inferred from revision labels. Podman/native Linux/other architectures and configured live checks remain unavailable; floors do not prove every advisory fixed. |
| R11 | Executor `cryptography==50.0.2`, hashed lock regenerated for Python 3.11/Linux through `make upgrade-python-deps PYTHON_DEPS_COMPONENTS=executor`; selected wheel contract is bundled OpenSSL 4.0.3. | Verified 2026-11-02: [cryptography changelog](https://cryptography.io/en/latest/changelog/) and [platform support](https://cryptography.io/en/50.0.2/installation/) support the same Python/runtime line. [OpenSSL advisory](https://mirror.openssl-library.org/news/secadv/20260929.txt) fixes affected 4.0.2 ranges in 4.0.3. Concrete PDF X.509/CRL exploit prerequisites remain unestablished; AES/PDF checks establish compatibility. Debian [CRL fix](https://security-tracker.debian.org/tracker/CVE-2026-35189) needs `3.5.7-1~deb13u3`; Alpine floor is 3.5.9-r0. | **C2 wrapper remediation qualified on available Docker platform.** Exact selected executor passed system OpenSSL/provider `3.5.7-1~deb13u3`, bundled OpenSSL 4.0.3, dependency consistency, AES and encrypted-PDF checks. Live executor command/network qualification remains unavailable; exploit prerequisites and unrelated advisories remain separate. |
| R12 | Onyx v4.8.8 default 0.4.7; bounded controller/executor selection 0.4.9, source `e53850d68701e5e62440a36ed311164dafc89465`; controller digest `sha256:7626d1f139e56d640cd0e333a90f632e26dc986db8641d6dbce45dc494e03580`; executor upstream digest `sha256:2c605c21ca26f2e25cd1a6d563ca7a3d6ca061b86dec9db0aa0abfd049b06ce9`. | Exact [Onyx env.template](https://github.com/onyx-dot-app/onyx/blob/f400d3154a13ac1f3ee730896f69fbc25eb5d8f7/deployment/docker_compose/env.template) permits a separate release selection. Independent source call-chain review and failure behavior are recorded in the accepted G0 decision above. | **Exact-tuple source compatibility and generation `.2` patch/security image contracts passed.** Changed owner/default/selected releases reopen bounded compatibility review. Live executor command/network qualification remains unavailable. No silent downgrade or permanent allow-newer exception. |
| R13 | SearXNG `2026.7.15-7b2199ecd`, Python3.14 build stage; Obscura 0.2.5 source `bd9529418b7fa975b50eabeac415cfaaa865a588`, runtime digest `sha256:354c3d2a0fa2dd2939debe6903aa26c35d121329245dfa42aa7d1c292a209814`, Rust1.94.0-bookworm digest `sha256:365468470075493dc4583f47387001854321c5a8583ea9604b297e67f01c5a4f`; Tor0.4.9.14 digest `sha256:38f57fbef35b372f5eac374fa3d44a4edf6c5e2ff2a6883ace851617c09f16bb`; Myst source `74d144d4261adbb9feb84825946078b8161ebe46`; Teep source `d9f59d839eb887b50f5afd234e192eac5ec45a2d`; Myst/Teep Go1.26-alpine and Alpine3.22 build/runtime bases. | [SearXNG](https://github.com/searxng/searxng/security/advisories) and [Obscura](https://github.com/h4ckf0r0day/obscura/security/advisories) currently publish no own advisories; absence does not establish bundled dependency cleanliness. Independent [Tor 0.4.9.15 review](https://lists.torproject.org/mailman3/hyperkitty/list/tor-announce%40lists.torproject.org/thread/WXHTSV43NO5UZQ7Q634S5NS5MIRL5JSJ/) supports retaining 0.4.9.14 for this client-only configuration: relay/directory defenses and unused control commands do not apply; remaining dependency evidence is separate. C2 identities include all source/base/copied/context inputs; native source/runtime review is incomplete. | **Identity remediation and available focused qualification passed; complete dependency/live assessment unresolved.** All selected source builds succeeded; patch/Obscura/Tor gates and Myst/Teep CLI probes passed. Source/base/recursive COPY/context contracts pass. Exact-source SBOM/advisory review and configured browser/search, VPN/Myst namespace and Teep inference remain next actions; no automatic unrelated upgrade. |

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
[the upgrade checklist](../../onyx_patches_upgrade.md) in C1: owner/version
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
[the upgrade procedure](../../onyx_patches_upgrade.md) and applicable
[AGENTS.md workflow guidance](../../../AGENTS.md) in this phase; update README only
for lasting user-facing consequences. Independently review C1's exact snapshot
and resulting commit before building on its authority decisions.

## C2: complete identities for local builds and generated files

Current behavior is documented in
[image and generated-configuration identities](../../onyx_patches_upgrade.md#image-and-generated-configuration-identities),
[Podman image preparation](../../podman_suport.md#image-preparation), and
[resource policy](../../resource_minimization.md#container-health-and-startup).

Acceptance requires complete committed COPY/base/context identities, preserved
explicit overrides, native cache/base policy, unchanged-version configuration
refresh, missing/modified artifact recovery, secret preservation and failure/retry
coverage. Run `make check` and affected selected-image gates. Source acceptance
and unavailable or pending artifact evidence remain distinct.

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

Current behavior is documented in
[ordered startup delivery](../../onyx_patches_upgrade.md#ordered-startup-delivery),
[the unchanged end-user upgrade workflow](../../../README.md#upgrading-the-stack),
[Podman lifecycle](../../podman_suport.md), and
[resource policy](../../resource_minimization.md#container-health-and-startup).

Acceptance uses actual Make-entrypoint fake-engine fixtures for populated old
stores, unchanged-tag digest refreshes and generation changes, optional roles,
lite/full and engine transitions, explicit overrides, pruning, failures/retries,
parallel Make, tokenizer platform selection, and Podman staged startup. Render
native effective models and run available cached-image startup/health/image
contracts separately. Fixture/model evidence is not live qualification.

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

Current audit scope, invocation and limitations are documented in
[public freshness audit](../../onyx_patches_upgrade.md#public-freshness-audit).
Offline contracts cover ownership, qualified default consumers, build bases and
recursive COPY inputs; explicit-base revision and actual startup fixtures
complete the delivery checks. The standalone OpenSearch gate validates the
reviewed effective reference without acquisition or stale-tag substitution.

Acceptance requires recorded public-response fixtures, visible incomplete
coverage, `make check`, applicable image/live checks, and a final disposition for
each initial reconciliation row. Security assessment and installed dependencies
remain unresolved when evidence is unavailable; discovery never authorizes an
independent owner-version change.

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

## Accepted design choices

The accepted G0 decisions above resolve the original generation, immutable
registry selection, native presence, locking and explicit review-base choices.
Standing behavior is maintained in the linked owning guides.

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
