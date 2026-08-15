# Cross-runtime permissions validation

## Purpose

This procedure qualifies fresh host-bind creation and stopped-stack handoff
between the supported container runtimes. It covers:

- rootless Podman and ordinary Docker on macOS;
- rootless Podman, rootless Docker, and rootful Docker on native Linux; and
- lite and full stack modes independently.

Every case also exercises native Tor, a connected Mysterium VPN, PostgreSQL,
and live SearXNG/Obscura search. Full-mode cases additionally exercise the
running OpenSearch API, its indexed workload, and Onyx's OpenSearch client.

Every directed handoff is a separate case. The source runtime must create its
state from a new `docker-data` tree containing only the controlled funded Myst
fixture, shut down cleanly, and release the shared-data claim. The target
runtime must then start and stop the same case without a manual ownership,
mode, or extended-attribute repair.

This is a destructive qualification procedure for disposable stack state. Do
not run it in a checkout whose `.env.wrapper`, `docker-data`, `doc-drop`, named
volumes, or Onyx deployment state must be preserved. Read
[`docs/podman_suport.md`](podman_suport.md), especially **Shared Docker data**,
**macOS mounts, ownership, and attributes**, and **Live compatibility
checklist**, before investigating or changing a failed case. The functional
gates retain the contracts in
[`native_tor_support.md`](native_tor_support.md),
[`vpn_routing_and_proxies.md`](vpn_routing_and_proxies.md),
[`request_handling.md`](request_handling.md), and
[`local_docs_rag_search.md`](local_docs_rag_search.md); use those documents for
route-specific diagnosis rather than weakening a failed gate.

## Supported matrix

Hold the stack mode constant across a handoff. Run every listed transition once
with `lite` and once with `full`.

### Native Linux

| Fresh-state source | Target: rootless Podman | Target: rootless Docker | Target: rootful Docker |
| --- | --- | --- | --- |
| rootless Podman | — | required | required |
| rootless Docker | required | — | required |
| rootful Docker | required | required | — |

This is six directed transitions per mode and twelve Linux cases in total. Do
not reuse one source run for both targets: the first target would alter the
state seen by the second target and would no longer test a direct handoff from
the stated source.

### macOS

| Fresh-state source | Target: rootless Podman | Target: Docker Desktop |
| --- | --- | --- |
| rootless Podman | — | required |
| Docker Desktop | required | — |

This is two directed transitions per mode and four macOS cases in total.
Rootless Docker is supported only on native Linux and is therefore not a
missing macOS matrix cell. Docker Desktop's Linux VM is the ordinary-Docker
macOS runtime; it is not evidence for the native-Linux rootless-Docker path.

The complete qualification contains sixteen cases. Mode switching, concurrent
multi-engine operation, Tailscale, configured upstream proxies, and migration
of the rootless-Docker-only database volumes are outside this procedure.

## Storage behavior under test

The expected handoff is not identical for all stores:

- Rootless Podman and ordinary Docker reuse the stopped
  `docker-data/postgres` bind. In full mode they also reuse
  `docker-data/opensearch` and `docker-data/minio`.
- Native-Linux ordinary Docker must leave the PostgreSQL tree and, in full
  mode, the MinIO tree owned by the invoking host user so rootless Podman can
  write them. Its tracked shutdown path must likewise return the Myst tree,
  whose writer requires container root, to the invoking host UID/GID before
  releasing the shared-data claim.
- Rootless Docker cannot map non-root image users onto those same binds safely.
  It therefore uses engine-managed named volumes for PostgreSQL and SearXNG
  cache and, in full mode, OpenSearch and MinIO. Those stores are intentionally
  separate from the host binds. Rootless-Docker handoffs validate successful
  lifecycle and compatible shared host binds, not database/index/object-store
  continuity with another runtime.
- Myst identity, Tor state, Onyx file-system data, the tokenizer cache, and the
  document source remain compatible host binds where selected. Myst and both
  Tor roles are mandatory in this procedure: Tor egress carries search, the
  onion role exercises persistent Tor identity, and Teep is routed through
  Myst so a real inference exercises the VPN namespace.
- The marker at `docker-data/host-services/shared-data-engine` distinguishes
  `podman`, `docker-rootless`, and `docker-rootful`. A successful matching
  `make down-*` removes it before the target runtime starts.

Never attempt to make these models look alike with recursive `chown`, `chmod`,
`xattr`, `:U`, `--userns=host`, or copied database trees. A case passes only
when the tracked startup and shutdown paths perform every required fixup.

## Test hosts and checkout preparation

Use a dedicated non-root account on each host. The current reference hosts are:

- macOS: the clean source checkout at
  `reference_repos/PrivateOnyxApp.clean`; and
- Linux: `ssh` to a Linux machine (typically `debvm` or `debvm23`).

The source checkout is read-only reference material. Make a disposable clone
or worktree-equivalent copy for validation rather than starting the stack in
the reference checkout. Put the same commit under test on both hosts and record
this output in the result header:

```bash
git rev-parse HEAD
git status --short
uname -a
id
```

The initial status must contain no tracked changes. Ignored runtime files may
appear later and must never be staged. Do not copy an existing `docker-data`
directory or engine volume into either validation checkout; the narrow funded
Myst fixture procedure below is the only permitted state seed.

Create a test-only `.env.wrapper` from `.env.wrapper.example`. Enable both Tor
roles and the Myst route while keeping unrelated optional publication and code
egress disabled:

```env
MYST_VPN_ENABLED=true
TEEP_ROUTE_THROUGH_MYST_VPN=true
TOR_EGRESS_ENABLED=true
TOR_ONION_SERVICE_ENABLED=true
TAILSCALE_FUNNEL_ENABLED=false
ONYX_CODE_INTERPRETER_ENABLE_NETWORK=false
EGRESS_UPSTREAM_PROXY_URL=""
```

Tor is not chained through Myst. With this configuration, SearXNG/Obscura
search crosses the Tor Unix SOCKS path, while Teep provider traffic crosses
Myst. Both routes must be tested separately; a healthy Myst container does not
prove Tor search, and a successful Tor search does not prove Myst carried a
Teep request.

Use non-production credentials. Lite mode needs at least one configured Teep
provider key for stack preflight. For a portable full-mode run, use the
stack-owned Teep embedding path rather than a host MLX installation:

```env
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="http://host.docker.internal:8337/v1/embeddings"
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL="neardirect:Qwen/Qwen3-Embedding-0.6B"
```

This full-mode selection requires a working NearAI key and performs a real
embedding request during startup. If another provider or a test embedding
endpoint is selected, record it and qualify the same endpoint under every
runtime; do not change it between cases. Never include `.env.wrapper`, API
keys, generated Onyx environment files, or raw private application data in the
evidence bundle.

Use the default empty `./doc-drop` source or a dedicated non-private fixture.
Keep the same test configuration, source commit, and fixture across all cases.
Maintain a reproducible test-only Onyx onboarding checklist that creates a
scratch admin and configures the same Teep chat model. Apply it whenever a leg
uses a newly initialized PostgreSQL store. Never reuse production Onyx data or
store the scratch password in evidence. A rootless Podman/ordinary-Docker
handoff must retain the existing scratch account and model configuration;
handoffs into or out of rootless Docker may require onboarding the newly
selected isolated database.

### Funded Myst fixture

A functional Myst tunnel cannot be created from a completely empty data tree
without registration and funding. Prepare one dedicated, funded Myst identity
per validation host through the supported `make vpn-signup-*` workflow, stop
the standalone signup project with `make vpn-signup-stop`, and store its
`myst-data` tree as a private validation fixture outside the repository. Use a
test identity with enough balance for the complete matrix, not an operator's
ordinary wallet. If the fixture contains more than one identity, set
`MYST_VPN_IDENTITY` explicitly.

The fixture must have been created by the same non-root validation account on
the same host. Do not fix a cross-host copy with recursive `chown`, `chmod`, or
extended-attribute removal; create a separate host-local fixture instead.
Protect the fixture and every active copy as wallet material. Never print or
archive its identity, balance, provider, keystore contents, or payment data.

For each case, create a new `docker-data` tree and restore only the funded
fixture as `docker-data/myst-data` before source startup. No other directory,
marker, database, Tor state, engine volume, or prior case data may be restored.
This is the sole seed exception to the fresh-state rule. The source and target
must use the same active copy without operator replacement or repair between
legs; normal Myst runtime writes are expected. Only one case may use a given
funded identity at a time.

## Runtime identity gates

Run these checks before the matrix and again after any daemon, Podman machine,
Docker Desktop, context, or socket restart. Record the client, server, and
Compose versions.

### Native Linux

The provided VM is expected to expose `default` and `rootless` Docker contexts.
Do not rely on their names alone:

```bash
docker context ls
docker --context default info --format '{{json .SecurityOptions}}'
docker --context rootless info --format '{{json .SecurityOptions}}'
docker --context default version
docker --context rootless version
docker compose version
podman version
podman info --format '{{.Host.Security.Rootless}}'
podman info --format '{{.Host.RemoteSocket.Path}}'
podman compose version
```

Require all of the following:

- the `default` Docker result does not contain `name=rootless` or
  `name=userns`;
- the `rootless` Docker result contains `name=rootless`;
- both Docker endpoints are local Unix sockets and meet the repository's
  Engine/API and Compose minimums;
- Podman's server is Linux, its rootless field is `true`, its local API socket
  is active, and `podman images` succeeds; and
- the Podman Compose provider is the required current Docker Compose provider,
  not `podman-compose`.

Use per-command `DOCKER_CONTEXT` selection during validation so the operator's
global context is not changed:

```bash
# Rootless Docker
env -u DOCKER_HOST DOCKER_CONTEXT=rootless \
  make <target> CONTAINER_BIN=docker

# Rootful Docker
env -u DOCKER_HOST DOCKER_CONTEXT=default \
  make <target> CONTAINER_BIN=docker

# Rootless Podman
env -u DOCKER_HOST -u DOCKER_CONTEXT \
  make <target> CONTAINER_BIN=podman
```

If the Linux host uses different local context names, substitute the recorded
names consistently. A remote Docker context is not supported. Do not set
`PRIVATE_ONYX_DOCKER_ENGINE_MODE` to force a classification during live
validation; detection of the selected daemon is part of the test.

### macOS

Record the active Docker Desktop context and the selected rootless Podman
machine connection:

```bash
docker context show
docker info --format '{{json .SecurityOptions}}'
docker version
docker compose version
podman system connection list
podman version
podman info --format '{{.Host.Security.Rootless}}'
podman machine inspect
podman images
podman compose version
```

Require a running Docker Desktop Linux engine and a running rootless Podman
machine whose local forwarded API socket is usable. Replace
`<desktop-context>` below with the recorded Docker Desktop context:

```bash
# Docker Desktop
env -u DOCKER_HOST DOCKER_CONTEXT=<desktop-context> \
  make <target> CONTAINER_BIN=docker

# Rootless Podman
env -u DOCKER_HOST -u DOCKER_CONTEXT \
  make <target> CONTAINER_BIN=podman
```

Do not restart or recreate a user-owned Podman machine as part of this
procedure. If a restart is needed for qualification, first finish and archive
the active case, then repeat the runtime identity gates.

## Case naming and evidence

Use an unambiguous case identifier:

```text
<os>-<mode>-<source>-to-<target>
```

Examples are `linux-full-podman-to-docker-rootless` and
`macos-lite-docker-desktop-to-podman`. Store evidence outside the repository on
the same local filesystem as the checkout. Access to that directory should be
limited to the validation account because even metadata and logs may disclose
private stack details.

For every case, retain:

- commit, OS, account, engine, Compose, and selected-context identity;
- the exact source and target Make invocations and their exit status;
- `make ps-lite` or `make ps-full` after each successful start;
- the shared-data marker after each start and after each shutdown;
- the fixed-root ownership/attribute checks described below;
- endpoint probe status without response bodies;
- hashed Tor identity continuity, redacted Myst connection success, and the
  routed Teep vector-shape result;
- PostgreSQL transaction and continuity-token results;
- live SearXNG result counts plus Onyx `web_search` tool/citation counts and
  sanitized route outcome;
- in full mode, both OpenSearch integration-target results and the sentinel
  continuity result; and
- only the bounded, relevant service log excerpts needed to explain a failure,
  with secrets redacted.

Do not collect recursive directory listings, database contents, document
names, request URLs, or an entire unredacted log bundle.

## Fresh-case reset

Perform this reset before every directed transition. A case is invalid if it
inherits containers, prior-case `docker-data`, project volumes, or a live
wrapper-owned host process from another case.

1. Confirm the preceding case completed both matching `make down-*` calls. If
   shutdown failed, stop and diagnose it; do not delete the marker or state to
   manufacture a clean result.
2. Prove that no runtime has an Onyx Compose container. On Linux, all three
   commands must print nothing:

   ```bash
   docker --context default ps -aq \
     --filter label=com.docker.compose.project=onyx
   docker --context rootless ps -aq \
     --filter label=com.docker.compose.project=onyx
   podman ps -aq --filter label=com.docker.compose.project=onyx
   ```

   On macOS, run the equivalent Docker Desktop and Podman commands. Also check
   that no standalone or integrated `myst-client-vpn` exists; the funded
   fixture is host data and no Myst container may survive between cases.
3. List Compose project volumes separately in every engine store:

   ```bash
   docker --context default volume ls \
     --filter label=com.docker.compose.project=onyx
   docker --context rootless volume ls \
     --filter label=com.docker.compose.project=onyx
   podman volume ls --filter label=com.docker.compose.project=onyx
   ```

   On macOS, use Docker Desktop and Podman only. Review the output and remove
   each exact project volume by name from its owning engine. This intentionally
   deletes test data, including the rootless-Docker-only PostgreSQL, SearXNG,
   OpenSearch, and MinIO volumes. It is permitted only on the dedicated
   validation engines after the container check is empty. Never use a broad
   `system prune`, Podman machine reset, wildcard, or volume deletion copied
   from another engine's output. Re-run the listings and require them to be
   empty.
4. If a previous failed preflight left a volume labeled
   `io.private-onyx.role=postgres-init` or
   `io.private-onyx.role=opensearch-init`, archive the failure evidence first.
   Remove only the exact abandoned staging container and volume after proving
   that no case is active.
5. Move a failed scratch `docker-data` tree only into a separate access-
   controlled quarantine on the same filesystem. It contains a funded Myst
   wallet and must not enter the ordinary evidence directory. A passed case
   needs only the bounded metadata and functional evidence defined here; clean
   up its disposable wallet copy through an explicit, reviewed action after
   the result is accepted.
6. Create a new host-user-owned `docker-data` tree for the case and restore
   only the host-local funded fixture at `docker-data/myst-data`. Require every
   other expected stack path, including `host-services`, `postgres`,
   `file-system`, `searxng-cache`, `tor`, and all full-mode roots, to be absent.
   The wrapper must create those bind roots as the invoking user. Also require:

   ```bash
   make shared-data-engine-status
   ```

   to print `unclaimed`. The status command is non-mutating. Recheck that it
   did not create any path and that `myst-data` remains the only child of
   `docker-data` before startup.

The generated Onyx deployment environment and the test-only `.env.wrapper`
stay constant across cases. They are configuration, not storage evidence.

## Procedure for one directed handoff

Substitute the case's mode, source selector, and target selector from the
runtime identity section.

### 1. Start from fresh state under the source runtime

Run the source runtime's `make up-lite` or `make up-full`. Do not run Compose
directly and do not pre-create bind roots by hand.

Require:

- the command exits zero;
- `docker-data` and the selected mode's expected bind roots now exist and are
  owned by the invoking host user;
- `make shared-data-engine-status`, using the same runtime selector, prints
  exactly `podman`, `docker-rootless`, or `docker-rootful` as appropriate;
- the same selector's `make ps-lite` or `make ps-full` reports every selected
  service running and every health-checked service healthy; and
- endpoint probes return success without saving response bodies:

  ```bash
  curl --fail --location --silent --show-error --output /dev/null \
    http://localhost:3000/
  curl --fail --silent --show-error --output /dev/null \
    http://localhost:8080/
  ```

For full mode, also probe the document publisher's `/_health` path on the
configured `HOST_PORT_ONYX_RAG_DOC_WEB` and require the staged embedding
readiness in `make up-full` to have completed. These examples use the default
WebUI and SearXNG ports; substitute the test configuration's recorded ports.
The WebUI probe follows redirects and its final response must be successful.

Do not mark a case passed merely because containers exist. The Make target's
bounded wait and post-wait assertion must succeed, followed by every functional
gate below.

#### Tor and Myst gates

Using the same source selector:

1. Require `tor`, `tor-frontend-gateway`, `myst-client-vpn`, and both final-hop
   policy paths to be running and healthy. Confirm that exactly one Tor
   container serves both enabled roles.
2. Run `make tor-onion-address`. Verify that it returns one syntactically valid
   v3 onion hostname, but store only a cryptographic hash of the hostname in
   evidence. This hash is the Tor identity continuity token for the target
   leg; never inspect or copy the onion private keys.
3. Run `make vpn-connection-info` and require `Connected`. Inspect the result
   interactively, then record only success and a redacted provider/country
   classification. Do not retain identity, provider identifier, balance, or
   connection-detail output.
4. Send one fixed, non-sensitive embedding request to the Teep host endpoint
   using the configured `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL`. Parse the
   JSON in a pipe and require exactly one non-empty, finite numeric vector;
   discard the vector and response body. Because
   `TEEP_ROUTE_THROUGH_MYST_VPN=true`, this real provider request proves the
   connected Myst namespace carries application traffic. Container health or
   `vpn-connection-info` alone is not a substitute.

The Tor and Myst paths are independent. Do not infer Myst function from the
search gate below: with `TOR_EGRESS_ENABLED=true`, browser search deliberately
bypasses Myst and uses Tor.

#### PostgreSQL gate

Resolve the `relational_db` container by its Compose project and service labels
through the selected engine. Inside it, use `psql` with the container's
existing `POSTGRES_USER` and `POSTGRES_DB` environment; do not print database
credentials. Require:

- `pg_isready` and `SELECT 1` succeed;
- a transaction can create a scratch
  `permissions_cross_validation` table when absent, insert a row containing
  the non-secret case ID and a random source token, commit, and read the exact
  row back; and
- the row remains readable immediately before source shutdown.

The scratch table is allowed only in this disposable validation database. Its
source token is the PostgreSQL continuity token used after handoff. Application
startup or container health without a committed SQL read/write transaction is
not a PostgreSQL pass.

#### Live search gates

First issue a normal SearXNG JSON search through the published
`HOST_PORT_SEARXNG`, using `curl --get`, `format=json`, and a fixed public,
non-sensitive query such as `IETF RFC 9110 HTTP semantics`. Parse the response
in a pipe without saving result titles, snippets, or URLs. Require a valid JSON
object and at least one structurally valid result with a non-empty title and
HTTP(S) URL.

This request must use the normal `/search` interface. A request to `/`, a
container healthcheck, cached evidence from another case, or a direct request
to a search provider is not a search pass. The configured route makes this an
end-to-end SearXNG -> Obscura -> browser bridge -> final-hop policy -> Tor
test. Confirm the relevant sanitized logs show a completed provider attempt
and no direct fallback, permission error, or missing Tor socket.

Provider CAPTCHA, access denial, HTTP 429, parser mismatch, and timeout are
valid visible attempt outcomes but do not by themselves prove functional
search. Up to three different fixed, non-sensitive queries may be tried
sequentially so round-robin can select available providers. At least one must
return a real result. If none does, record the environment as not qualified;
do not replace it with an empty-success or direct-network test.

Then log in to the scratch Onyx instance and submit a fixed prompt that
explicitly requires `web_search` for the same public topic. Require the tool
trace to show `web_search`, at least one real result to reach the model, and a
cited answer whose link remains structurally valid. This second test exercises
the Onyx search client, `searxng-service-gateway`, database-backed user/model
configuration, Teep inference through Myst, and the same Tor-routed search
backend. A plausible uncited answer from model memory is a failure.

Record only pass/fail, result count, tool name, and citation count. Do not
archive the prompt transcript, result titles, URLs, model reasoning, or raw
tool trace. Both the direct SearXNG request and the Onyx `web_search` request
are required on every source and target leg.

#### Full-mode OpenSearch gates

For every full-mode source and target start, run both existing live targets
with the same runtime selector:

```text
make integration-opensearch
make integration-opensearch-onyx
```

The first target validates the running cluster, security/audit settings,
plugins, indexed keyword and vector workloads, concurrency, reindexing, and
failure counters. The second exercises the pinned Onyx schema and client from
`api_server`, including hybrid search and reindexing. Both must exit zero;
OpenSearch container health or a successful root request alone is not enough.

Before source shutdown, create one case-named, single-document OpenSearch
sentinel index through the authenticated in-container API, refresh it, and
prove an exact search returns that document. Use the container's existing
`OPENSEARCH_INITIAL_ADMIN_PASSWORD` without emitting it. Record only the index
name, document count, and success status; the marker document must contain no
private data. This index is the OpenSearch continuity token for the target
leg.

### 2. Record source-runtime storage assertions

On native Linux, record numeric UID/GID and modes for these fixed roots without
recursively printing their contents:

```text
docker-data
docker-data/host-services
docker-data/file-system
docker-data/postgres
docker-data/searxng-cache
docker-data/myst-data
docker-data/tor/config
docker-data/tor/state
docker-data/tor/docker-runtime       # native Linux Docker with Tor egress
docker-data/model-cache              # full only
docker-data/opensearch               # full only
docker-data/minio                    # full only
```

After either rootless Podman or rootful Docker has populated the shared
PostgreSQL bind, require every entry in that tree to have the invoking host
UID/GID. In full mode, apply the same requirement to the shared MinIO tree.
Use a `find -xdev` mismatch query that prints at most the first failing path;
an empty result is a pass. Record OpenSearch ownership separately, since its
image UID contract differs from PostgreSQL and MinIO.

When the source is rootless Docker, inspect the relevant containers' mounts
and require `Type=volume` at:

```text
relational_db  /var/lib/postgresql/data
searxng-core   /var/cache/searxng
opensearch     /usr/share/opensearch/data   # full only
minio          /data                        # full only
```

The corresponding PostgreSQL, OpenSearch, and MinIO host-bind roots may have
been prepared, but they must not contain rootless Docker's database, index, or
object-store files. Numeric subordinate-ID ownership in those host binds is a
failure, not an expected rootless artifact.

Inspect the Tor and Myst mounts rather than assuming the Compose model was
applied:

- `myst-client-vpn` must mount the same case-local
  `docker-data/myst-data` bind at both documented container destinations. It
  must be able to update its runtime state without changing the bind source to
  an engine volume or leaving an inaccessible subordinate-ID tree.
- Tor must mount the case-local `docker-data/tor/state` bind at
  `/var/lib/tor`, with its selected effective UID/GID matching the platform
  contract. On native Linux, require every persistent Tor state entry to remain
  owned by the invoking host UID/GID after each engine start.
- Native Linux Docker, including rootless Docker, must use the host-owned
  `docker-data/tor/docker-runtime` transient SOCKS bind. Docker Desktop and
  Podman must instead use their engine-local `tor-runtime` volume. Only Tor may
  mount it read-write; the public and host policy containers mount it read-only.

Record fixed-root numeric metadata for `myst-data` before and after each leg,
but do not recursively list or hash wallet files. Functional connection and
the routed Teep request are authoritative: any ownership state that prevents
the target Myst process from reading or updating its state fails the case.
Never repair it between legs.

On macOS, record only mount-root attribute names and fixed-root modes. For the
PostgreSQL mount root, distinguish:

- Docker Desktop metadata: `com.docker.grpcfuse.ownership`; and
- Podman override metadata: `user.containers.override_stat`.

The tracked Podman preflight may remove the unsafe Podman override from the
PostgreSQL mount root. It must not recursively strip valid per-file attributes
or Docker Desktop ownership metadata. Do not repair a result with manual
`xattr`, `chown`, or `chmod` commands.

Before source shutdown, record a non-content identity for each initialized
shared-store marker that exists:

```text
docker-data/postgres/PG_VERSION
docker-data/opensearch/nodes          # full only
docker-data/minio/.minio.sys          # full only, when created by the image
docker-data/tor/state/onion-service   # metadata only; never inspect keys
```

Use the host's numeric inode/stat output and, for `PG_VERSION`, a checksum. Do
not hash an entire database or object tree. These identities distinguish reuse
from replacement without exposing application records.

### 3. Shut down the source runtime

Run the source selector's matching `make down-lite` or `make down-full`.
Require all of the following:

- the command exits zero;
- no Onyx Compose container remains in that source engine, including `tor`,
  `tor-frontend-gateway`, and `myst-client-vpn`;
- `make shared-data-engine-status` prints `unclaimed`; and
- in macOS Podman full mode, the wrapper-owned host document server is no
  longer live and no valid live ownership record remains.

Do not switch runtime selectors until these checks pass. A failed `down` is a
case failure even if the target could be forced to start.

### 4. Start the unchanged state under the target runtime

Without moving, copying, deleting, chowning, chmodding, or editing
`docker-data`, run the target selector's matching `make up-*` target. Use the
same `.env.wrapper`, mode, ports, and document fixture.

Repeat every startup, marker, service-health, endpoint, fixed-root, and
runtime-specific storage assertion and every Tor, Myst, PostgreSQL, search, and
full-mode OpenSearch functional gate from steps 1 and 2. In addition:

- require the marker to name the target runtime, proving that shutdown released
  the source claim and target classification was not forced;
- require the target's hashed onion hostname to equal the source token; a new
  hostname means persistent Tor state was replaced or unreadable;
- require Myst to reconnect with the same configured validation identity and
  repeat the real Teep embedding request through the VPN route, without
  recording identity or provider details;
- for a rootless Podman/ordinary-Docker handoff, require the existing shared
  PostgreSQL cluster and, in full mode, OpenSearch and MinIO stores to start in
  place rather than be replaced or reinitialized; compare the recorded marker
  identities after target startup, read the source PostgreSQL token, and find
  the source OpenSearch sentinel document before creating target tokens;
- when rootless Docker is the target, require its fresh named volumes and leave
  the existing ordinary-Docker/Podman database binds unchanged, including the
  recorded marker identities; its isolated PostgreSQL and OpenSearch stores
  must not contain the source tokens, then must pass new target read/write and
  indexed-search transactions; and
- when rootless Docker is the source, recognize that the target is expected to
  initialize previously unused database binds. The source PostgreSQL and
  OpenSearch tokens must be absent, after which the target must create and read
  its own. This is not cross-engine database migration.

After the continuity assertions, insert and read a target PostgreSQL token. In
full mode, create, refresh, search, and then delete a target OpenSearch
sentinel. Delete the source OpenSearch sentinel only after its expected
presence or absence has been proved and only when that source store is mounted
by the target. If rootless Docker isolates the source store, leave its sentinel
for the case's explicit volume/data cleanup. The existing integration targets
still run independently on both legs.

Search only the relevant stateful-service log excerpts for permission and
mount failures. At minimum, investigate any occurrence of:

```text
Permission denied
Operation not permitted
AccessDeniedException
failed to bind service
wrong ownership
read-only file system
Failed to parse/validate config
Could not bind to UNIX socket
VPN readiness failed
database files are incompatible
```

A known OpenSearch warning that memory could not be locked under a rootless
runtime is not a permissions-handoff failure when the service is healthy and
the documented rootless memlock limit is in effect.

### 5. Shut down the target runtime

Run the target selector's matching `make down-*` and repeat the shutdown
assertions from step 3. Capture only the approved metadata evidence. Do not put
the final `docker-data` tree in the ordinary evidence directory because it
contains the active Myst wallet and Tor onion identity. Keep a failed tree in
the restricted quarantine defined above; clean up a passed disposable copy
only through the validation host's explicit reviewed cleanup process.

Reset every engine's project volumes before beginning the next directed case.

## Acceptance criteria

A directed case passes only when:

1. it began with a newly created `docker-data` containing only the controlled
   host-local Myst fixture, no shared-data marker, no Onyx containers, and no
   project volumes in any participating engine;
2. the source runtime was positively identified and completed `up`, health and
   endpoint checks, Tor/Myst routing checks, PostgreSQL transaction, both live
   search gates, storage assertions, and matching `down` without manual repair;
3. the target runtime was positively identified and completed the same checks
   against the unchanged source-created host state;
4. both shutdowns released their exact marker and left no selected-engine
   containers or wrapper-owned full-mode host process;
5. full mode passed both OpenSearch integration targets on each leg and the
   expected shared or isolated sentinel behavior;
6. no unexpected subordinate-ID, root-owned, unreadable, read-only, or unsafe
   mount-root attribute state was observed; and
7. all evidence identifies one source commit and one unchanged test
   configuration without exposing credentials or private contents.

The feature is cross-runtime qualified only when all twelve Linux cases and all
four macOS cases pass. Record failures as failures; do not convert a failed
case to “not applicable” because another transition or the reverse direction
passed.

## Failure handling

On failure, stop that case before any repair. Record:

- the failing command and exit status;
- selected runtime identity and shared-data marker;
- `make ps-*` plus the relevant engine's project container list;
- numeric metadata for only the fixed bind roots;
- mount type/source/destination for the failing service; and
- a bounded, redacted log excerpt from the failing stateful service or startup
  preflight.

Preserve failed containers, volumes, host-process records, and `docker-data`
until the failure is understood. Keep wallet and onion state in the restricted
quarantine, never the ordinary evidence bundle. If preserving the state blocks
later matrix work, use another dedicated host or explicitly close the failure
before cleanup; never use `make adopt-shared-data-engine` to bypass a reported
writer or stale ownership state during qualification.

After a code or configuration fix, rerun the failed directed case from fresh
state and then rerun its reverse direction. If the fix changes common bind
preparation, ownership, Compose lifecycle, or storage overlays, rerun the full
OS/mode matrix.

## Result summary

Use one row per directed case:

| Case | Source/target lifecycle | Tor/Myst | PostgreSQL | Search | OpenSearch | Permissions | Result/evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `linux-lite-podman-to-docker-rootless` |  |  |  |  | N/A |  |  |

The final report must also state:

- the commit and test-configuration fingerprint used on both hosts;
- all runtime and Compose versions and Docker context endpoints;
- whether a daemon, Docker Desktop, or Podman machine was restarted during the
  run and which cases were repeated afterward;
- the exact cases omitted, if any, and why; and
- the final state of each engine, shared-data marker, wrapper-owned host
  process, scratch checkout, and retained evidence directory.
