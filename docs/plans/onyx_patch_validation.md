# Onyx runtime patch validation observer

Status: planned follow-up; implementation has not started. This work is not a
prerequisite for [Onyx patch reorganization](onyx_patch_reorg.md).

## Objective and scope

Close specific gaps in evidence from actual running API, Beat, worker, and child
processes when existing tests, native logs, task status, or fixture counters cannot
answer the required question. This observer is test instrumentation, not a
production patch loader, health service, or missing-bootstrap enforcement system.
Do not add runtime startup guards, child-launch adapters, command rewrites, or a
completion protocol. Any runtime enforcement proposal remains separately scoped.

Start with unanswered validation questions, not a commitment to instrument every
process. Candidate questions are whether a live PDFium child completed and returned
its result, whether Beat installed the expected schedule after reload, and whether
an unchanged synthetic PDF took the freshness path. A successful parent operation
can conceal child fallback; a correct generated schedule can conceal stale
installed entries. Origin snapshots answer discovery questions but do not alone
prove behavior. Reuse controlled tests for edge cases and final patch composition.

Read the current `AGENTS.md`, [patch information](../onyx_patch_info.md),
[upgrade checklist](../onyx_patches_upgrade.md),
[resource policy](../resource_minimization.md),
[local RAG](../local_docs_rag_search.md), and
[Podman support](../podman_suport.md) before implementation. Read
[request handling](../request_handling.md), [routing](../vpn_routing_and_proxies.md),
[internal network security](../internal_network_security.md), and
[native Tor](../native_tor_support.md) for affected fixture routes and allowances.
Use the current tree and selected local images; do not upgrade dependencies or
change application behavior to accommodate the observer.

The reorganization retains automatic discovery/origin checks in fresh interpreters,
strict-failure and composition tests, native spawn/PDF transport tests under both
API and background bootstraps, and representative live requests and queued work.
It records the limits of that evidence. Completing this observer or proving every
live PID's module origins is not a condition of completing that reorganization.
If the reorganization is already complete, qualify the current layout; do not
reconstruct the old package layout just to create a historical baseline.

## Feasibility and implementation sequence

1. **Identify evidence gaps.** Review existing image validators, attributable
   startup/task logs, terminal status, and synthetic fixture counters. Record the
   exact claim each cannot prove, its importance, and the smallest additional
   observation needed. Do not duplicate evidence they already provide.
2. **Prove one bounded prototype.** Start with a high-value gap, preferably
   successful isolated PDFium execution and native result transport. Demonstrate
   automatic origin snapshots, Make-selected mount injection/removal, output
   ownership, fixture setup/cleanup, and minimal evidence transport. Preserve
   descriptors, pickle payloads, native recovery, and timeouts. A failure here is
   an observer feasibility blocker, not a patch-reorganization blocker.
3. **Assess cost and accept scope.** Measure observer overhead against the same
   uninstrumented fixture, including cold/warm API and background PDF children.
   Demonstrate required worker-thread coverage. Filtering records from
   `sys.setprofile` does not eliminate callback overhead for unrelated execution;
   assess the observation mechanism's actual cost, not just output volume. Keep
   origin-only processes free of profiling. Select further boundaries only when
   their evidence justifies the implementation and maintenance cost.
4. **Implement and qualify selected boundaries.** Keep reusable code under
   `tests/patch_activation_probe.py` and reuse the existing image harness. Complete
   the controlled observer/reader tests and selected live rows below. Expand only
   after the preceding minimal mechanism works; do not build a general tracing,
   fixture-stack, task-dispatch, or negative-work tracking platform.
5. **Document and hand off.** Record accepted boundaries, durable helper paths,
   commands, costs, evidence, and unresolved gaps. Restore the original service
   configuration and running/stopped state, removing only test-owned resources.

Use authorization present in the implementing session. Do not read or source
private environment files or enumerate private documents. Pass configuration
through supported Make/Compose mechanisms. Prefer synthetic/public inputs and
disposable storage; obtain narrow authorization if a named synthetic file must
use a private document directory. Run engines serially where resources are shared,
and report actual engine/OS execution separately from rendered-model coverage.
Use the reorganization plan's [live environment requirements](onyx_patch_reorg.md#required-live-activation-and-scoped-behavior-validation)
for engine discovery, VM access, and stopped-stack handoff. Record prerequisites,
input, expected observable, cleanup, and pass/fail/unavailable for selected rows.
Preserve existing provider budgets and Tor allowances.

## Process evidence collection

This table preserves the candidate live-process evidence inventory. During the
feasibility milestone, select only rows with an identified gap in existing
validation. Add consolidated results and links to durable test implementations
here; the full table is not a mandatory implementation checklist. Runtime records
are disposable test output, not a production activation protocol.

The table specifies evidence, not a requirement to profile every listed process
or boundary. Default to automatic startup-origin snapshots plus attributable
native logs, terminal task status, and fixture counters. During the baseline
feasibility milestone, identify the particular claims those sources cannot prove
and add execution observation only for those gaps. Successful isolated PDFium
execution is one such gap because the native child suppresses its output. Keep
general behavioral assertions in deterministic and selected-image tests.

| Process/path | Observation boundary and input | Required evidence and correlation |
| --- | --- | --- |
| API Alembic/Uvicorn, lite and full | Cold Make startup using the native commands; observe application entry and a bounded synthetic chat/tool/follow-up at the real patched caller | Preexisting bootstrap/package origins in each interpreter; distinguish migration and serving PIDs; serving-process contract results correlated with the fixture request, health, and targeted logs |
| Background supervisor/control programs | Inspect derived commands and the actual supervisor, entrypoint, and watchdog processes | Exact exclusions, `python -S` where specified, and lightweight process imports/mappings; no application bootstrap in controls. Do not require an observer record from a `-S` process |
| Beat | Inspect the effective `scheduler.schedule` after native `_try_updating_schedule` completes during initial setup and reload; use a bounded return-boundary observation only where attributable native evidence is insufficient | Beat PID, preexisting background module origins, expected installed task identifiers/names/cadences/queues and reload interval; distinguish materialized, generated, and installed schedules. Correct `_generate_schedule` output or task-count logs alone cannot prove installed state |
| Six retained worker programs | Capture native worker startup origins for every retained worker; correlate representative queued work on affected ingestion paths through native logs/status | One activation record per retained worker PID, matched to supervisor program and thread-pool command; task IDs for synthetic ingestion work. No unrelated task fixture is required for each queue, no hidden restart/import failures, and no invented prefork children |
| Spawned indexing/doc-fetch children | Observe native child task entry and the Web Connector/indexing boundaries during one queued synthetic PDF job | Child PID/parent PID, fixture attempt/task correlation, preexisting origins, and affected contract results from the executing child; a separately launched probe interpreter is insufficient |
| Enabled bots | Observe native installed script entry; use controlled dependencies for both bot entrypoint image tests and existing enabled bots for live evidence | Bot PID/role, preexisting background origins and native background code-tool unavailability; preserve Slack/Discord request semantics without requiring credentials for controlled entrypoint tests |
| Isolated PDF extraction children, API and background | Observe entry and successful return of the real `_extract_pdf_text_pdfium` during API PDF retrieval and the queued ingestion fixture, plus each parent's native isolated-call return | Separate API/background child PID/parent PID and run correlation, preexisting origins, expected synthetic-text match as a boolean, successful native result transport, and elapsed time. Parent fallback, a child start record, or stderr alone cannot satisfy either case |
| Restart/recreation | Repeat representative API, worker, spawn, and PDF observations after restart and full down/up | New run/container identities and corresponding process records; stale records and reused numeric PIDs cannot satisfy a new run |

Before implementing execution observation, complete the selected rows above with
a finite inventory of the exact module/callable and entry or return boundary for
each proposed execution observation. Record the specific missing evidence, why native logs,
fixture counters, or terminal status cannot supply it, the allowlisted fixture
correlation fields, and the minimal output schema and success/failure assertions.
Link each row to its durable probe and controlled test. Startup snapshots need
only origin/process records and do not justify execution profiling. The initial
candidate execution boundaries are PDFium child success/native parent transport,
effective Beat schedule inspection, and any otherwise unobservable positive
freshness decision; add others only for a demonstrated missing claim in this
table. A process family does not itself justify adding call/return observers.

Use fixture-scoped HTTP counters for total requests/body transfers and, where
available, fixture-scoped embedding counters for embedding work. Combine these
with native task completion and controlled composition coverage instead of
tracing every request, parser, embedding, and indexing call. If counters cannot
isolate the synthetic job, record the exact missing correlation before proposing
a narrowly scoped observer. Do not build a general negative-work event tracker.
Any boundary added after feasibility must first satisfy the same inventory,
schema, and minimal-probe requirements; do not expand profiling to compensate
for an unresolved fixture or lifecycle setup problem.

Implement a narrowly scoped observer as reusable test code under
`tests/patch_activation_probe.py`, shared with the existing image validators.
Its responsibility is one module-origin snapshot per process and only the specific
execution boundaries whose occurrence existing attributable logs and native task
status cannot establish. Processes needing only a startup snapshot must not also
install a profiler. Keep edge cases and general behavioral qualification in
deterministic/image tests; do not build a generic tracing, task-dispatch, or
assertion framework into the observer. Use fixture HTTP counters and native
terminal task/attempt status instead of reconstructing task completion from Python
execution events. For missing in-process evidence, the proposed injection is a
test-only read-only
`/app/usercustomize.py` mount of that observer, on the existing production `/app`
path, plus a dedicated test-output directory. Before adopting it, verify ordinary
automatic `usercustomize` discovery in each selected interpreter, user-site
enablement, and absence of an existing conflicting module. Do not enable user-site
loading, change application commands, or add bootstrap/package search paths to
make the probe work. If the selected image does not support this mechanism,
resolve that specific observation gap before adopting the observer; do not claim
the live row passed or introduce an activation launcher.

During observer feasibility, record and verify the exact Make-supported invocation
that adds the test-only observer/output mounts and the invocation that removes them.
Use fixed mounts and one bounded fixture-run observation window, with no dynamic
mount switching or observer control protocol. This reduces integration work but
does not remove the need to verify Make selection: its startup and shutdown
Compose lists are constructed separately. Keep the integration test-scoped;
do not introduce broad Makefile parameterization.
Render the affected effective model, preserving the Make-selected production
layers, commands, routes, and patch paths. Confirm output-directory ownership for
the selected engine and record how services are recreated without instrumentation
and restored to their initial state. Do not assume an extra overlay survives the
Makefile's Compose selection or bypass Make with an independently assembled stack.
The working mount injection/removal mechanism and authenticated synthetic fixture
setup/cleanup recipe are prerequisites for observer qualification, not for patch
reorganization. Reuse the reorganization plan's [fixture setup and isolation](onyx_patch_reorg.md#fixture-setup-and-isolation)
requirements and record concrete commands/helper paths here. If either needs
broader infrastructure, report the observer blocker; do not replace the selected
production launch path with a convenient test command.

The observer first snapshots already-loaded bootstrap/package origins using
stdlib-only code, without importing application modules itself. It must never
import the bootstrap, call installers, or replace production callables. Use narrowly filtered
Python call/return observation for the table's audited boundaries where needed,
including worker threads; observe frame globals and return-contract booleans
without importing missing target modules. Keep the boundary list explicit and
test-only and preserve any existing profiler by refusing conflicting instrumentation
where profiling is needed. For selected execution observations, keep observation
active throughout the bounded fixture run, including initial ingestion and
unchanged recrawl when qualifying freshness. End it by stopping the instrumented
processes. There is no rearming mechanism, sampling cutoff after the first success, or in-process
cleanup protocol. This is an observer, not a second bootstrap or installer registry.

Test the observer and its reader before relying on their evidence. Use small
controlled fixtures for the selected boundaries, worker-thread execution, and
missing required events. Prove unrelated tasks, stale run/container records,
malformed records, and incomplete observation windows
cannot pass. Cover instrumentation-conflict refusal. Use native terminal status
for task completion; do not build a general generator/exception lifecycle tracker.
Where a required boundary uses a return-value assertion, test its actual success
and failure forms. A call/return event alone is not proof of task completion or
successful PDF transport.

When qualifying unchanged recrawls, establish observation before dispatch and retain it
through correlated terminal attempt/task completion and completion of associated
fixture child work. Record that this window was fully observed, alongside fixture
HTTP counters and the required positive freshness-path evidence. Apply the
[freshness traffic contract](onyx_patch_reorg.md#freshness-traffic-contract): account for native
connectivity GETs separately and prove absence of the scrape-stage PDF GET,
parsing, and embedding work. Zero total PDF body transfers is not the unchanged
connector contract. Use counters and controlled boundary assertions wherever
they provide the required evidence, rather than requiring per-call trace events.
Missing or prematurely stopped observation fails validation rather than proving work was
skipped. The same fixed observation window covers both ingestion and recrawl;
collecting initial ingestion evidence must not disable the recrawl check.

Write bounded per-process records to the dedicated test-output directory, using
a fresh run identifier, container identity, PID/parent PID, process role, module
origins, allowlisted synthetic task identifiers, and contract booleans. Never dump
arguments, frame locals, prompts, environment variables, or document contents.
The external test reader fails on missing, stale, malformed, or failing records;
production startup does not depend on them. Direct file output is necessary for
PDF child evidence: the native runner redirects both stdout and stderr, and the
parent also discards child stderr. Preserve those descriptors and the pickle
payload unchanged. Use serialized synthetic PDF probes and process lineage for
correlation; ordinary user jobs do not count as fixtures.

Keep observer-enabled before/after comparisons equivalent, but do not treat that
comparison alone as evidence of normal runtime behavior. Before and after observer
implementation, run the corresponding synthetic API retrieval or queued-ingestion
smoke without observer mounts, using the same fixture recipe and native
completion/retrieval assertions. Qualify both API and background PDF child contexts
when selecting the PDF row; one bootstrap environment cannot stand in for the other.
Reset only test-owned fixture resources as needed to exercise initial ingestion
again. Record completion and elapsed times, and measure cold/warm native PDF child
timeout behavior separately without instrumentation. Investigate failures or
timeouts seen only with instrumentation as possible observer regressions and
isolate their cause before attributing them to the application or its patches.
Neither waive required evidence nor extend production deadlines to accommodate
the probe. Reduce observation to the missing-evidence boundaries or report the
validation blocker. Uninstrumented success cannot replace positive child-activation
evidence. Remove test mounts and output after collecting evidence and restore
normal service configuration. Retain the reusable
observer and assertions under `tests/`. `docker exec python ...` remains useful
supplementary evidence, not proof about an existing API, Beat, or worker process.

## Acceptance and documentation

- Every implemented observation has a demonstrated evidence gap, an explicit
  boundary, a minimal record schema, and controlled success/failure tests.
- Automatic discovery is observed without importing the bootstrap or installing
  patches. Missing or wrong-origin bootstrap records fail validation; they do not
  establish that the application would fail closed without this test harness.
- Selected PDF cases distinguish API and background bootstraps and prove child
  success plus native parent result transport. Parent fallback is not a pass.
- Selected Beat cases inspect installed entries after initial setup and reload,
  including task names, identifiers, native cadence values, queues, and reload
  interval. Characterize stale same-name entries in disposable fixtures without
  changing native persistence or clearing the live scheduler store.
- Selected freshness cases use the linked traffic contract and complete correlated
  windows; missing records cannot establish absence of work. Preserve the RAG
  plan's sentinel, native hash, and secondary-index distinctions and isolation rules.
- Observer/reader tests reject unrelated jobs, stale or malformed records,
  incomplete windows, conflicts, and failed return contracts. Positive assertions
  are attributable to the actual process under test, not a separate exec probe.
- Equivalent instrumented/uninstrumented fixtures establish overhead and normal
  behavior. Observer-only failures are investigated without extending production
  deadlines. Report limitations and deferred boundaries without claiming coverage.
- Make-selected injection and removal work on qualified engines. Repeat selected
  live evidence after restart and full down/up once on the final observer tree;
  rerun only when subsequent changes invalidate that evidence. Preserve original
  launch commands, queues, routes, state, and initial running/stopped status.

Once implemented, `docs/onyx_patches_upgrade.md` owns repeatable observer usage,
prerequisites, overhead limits, and interpretation. `docs/onyx_patch_info.md` owns
the distinction between discovery evidence, behavioral evidence, native child
recovery, and runtime enforcement. Update subsystem documents only where their
standing validation workflows change, linking rather than duplicating the observer
specification. Keep consolidated acceptance evidence in this plan; do not describe
planned instrumentation as deployed or make all future focused patch changes run
the full candidate process matrix.
