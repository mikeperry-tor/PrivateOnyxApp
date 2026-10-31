# Component version ownership and end-user upgrade delivery

Status: proposed; implementation has not started.

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

C1 must reconcile the findings that prompted this plan, alongside the recurring
ownership checks. The table below is the investigation starting point, not a
fresh security assessment or a list of approved upgrades. All rows are pending
verification against the implementation-time selected sources and artifacts.
Do not infer installed package versions from a tag, a revision date, or a
passing historical image gate.

| ID | Finding at planning time | Owner and required disposition |
| --- | --- | --- |
| R1 | nginx `1.25.5-alpine`, including two hard-coded frontend gateways | Onyx owns the version. Compare all three consumers with the selected release, synchronize duplicates, and assess advisories against actual modules/configuration. Retain an affected owner-selected version as upstream-pending. |
| R2 | PostgreSQL `15.2-alpine`, inherited in lite/full | Onyx owns the version. Verify the inherited effective selection and advisory applicability; do not independently advance it or change the storage major version. |
| R3 | Redis moving tag `7.4-alpine` in full mode | Onyx owns the tag. Establish its reviewed digest/package version and applicability, including cluster-only findings versus this deployment. C3 must deliver the selected refresh even when the tag text is unchanged. |
| R4 | OpenSearch `3.6.0`, with newer releases available | Onyx owns the version. Verify release alignment and concrete advisory applicability. Newer releases alone do not establish exposure or authorize an independent upgrade. |
| R5 | MinIO Onyx mirror of `RELEASE.2025-07-23T15-54-02Z-cpuv1` | Onyx owns the image. Verify mirror digest/source identity and published affected ranges, including signature-bypass findings. Distinguish community and AIStor fixes; do not silently migrate product, license, or storage. Record unresolved owner-selected exposure. |
| R6 | Namespace-holder Alpine `3.20` support status | Wrapper owns the base. Verify support and retained executable/package needs, select a supported base deliberately, and qualify namespace/readiness behavior on each engine. |
| R7 | Shared moving Python `3.12-slim-bookworm` / `3.12-alpine` images | Wrapper owns the bases. Establish Python, distribution, and package versions/digests; enumerate all consumers. Qualify refreshes through C2-C3 without implying a Python major/minor or distribution migration. |
| R8 | socat `1.8.0.3`, with newer published images | Wrapper owns the image. Verify application/base-package advisories and candidate image provenance; qualify any selected refresh across fixed bridges, health commands, and engine-specific consumers. |
| R9 | Tailscale `1.102.3` plus derived Alpine packages | Tailscale owns the application release; wrapper owns its OS refresh. Check current bulletins against configured Funnel/Serve use separately from optional SSH features. Review static Go dependencies and system trust/package updates independently. |
| R10 | OS refresh revision `20261004` and historical package floors | Wrapper owns the refresh. Inspect actual selected controller/executor/Tailscale package evidence and repositories, publish a revision bump with the applicable C1-C2 work, and qualify new artifacts. A newer label alone is not security evidence. |
| R11 | Executor cryptography `50.0.1` / bundled OpenSSL `4.0.2` and fixed image assertions | Executor Python dependencies have a separate owner from Debian OpenSSL. Verify advisory applicability and a suitable wheel/library update, regenerate the affected hashed lock through the supported flow, and deliberately update assertions only alongside validated artifacts. OS upgrades cannot replace the bundled copy. |
| R12 | Controller/executor `0.4.9` versus Onyx Compose default `0.4.7` | Resolve release authority and compatibility from exact upstream sources, as required above; neither a silent downgrade nor a permanent unexplained override closes the row. |
| R13 | Other pinned source/runtime/build artifacts: SearXNG, Obscura/Rust, Tor, Myst, Teep | Check owner-selected pins, support/advisory status, bundled dependencies, and build-input coverage. No specific vulnerability was established for every artifact in the initial discussion; do not turn missing evidence into a clean bill of health or an automatic upgrade. |

For each row, record the exact owner release/source, selected tag and resolved
digest or package/library version, primary advisory/support evidence with its
verification date, actual applicability, decision and rationale, responsible
phase, required checks, and validation result or limitation. Use one consolidated
acceptance table in this active plan during implementation; do not introduce a
permanent vulnerability database or duplicate inventory solely for this batch.

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
