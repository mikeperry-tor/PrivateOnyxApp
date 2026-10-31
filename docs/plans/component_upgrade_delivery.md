# Component version ownership and end-user upgrade delivery

Status: proposed; implementation has not started.

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

## Phase 1: dependency ownership and maintainer synchronization

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

## Phase 2: complete identities for local builds and generated files

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

## Phase 3: deliver reviewed updates through normal startup

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

## Phase 4: freshness reporting and regression protection

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
runtime package manager, broad cache deletion, or Git commits are authorized by
this plan. Implementation and validation results should be reviewed before
marking phases complete.
