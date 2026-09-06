# AuraBot architecture optimization — implementation ledger

Approved plan: user message dated 2026-09-06; branch codex/architecture-optimization, base 2b504dc.

## Binding decisions
- Fresh application data; no old schema/interface compatibility. Delete only identified AuraBot runtime data; keep models/cache/workspaces/shared data.
- Main Agent owns Task business entry; only title/status snapshot SSE bypasses it. Recent 100, profile-scoped.
- Preserve live speech timing, PREPARE/RESPOND/CONTINUE, input/grant/effect fences, ACK delivery, three-second proposal freeze/resume/cancel; Task concurrency 5.
- No infinite projection retries. Durable Task truth survives errors and is reconciled at normal startup.
- No new dependencies/frameworks; updated source files need responsibility/process/module comments.

## Work and ownership
- [x] Frontend: retire legacy business graph, Profile-only storage, snapshot SSE client/dependencies/tests.
- [x] Task/Memory: retire old ShortTerm, simplify TaskStore, status SSE and manager snapshot/reconcile interface, retrieval wrappers/packing.
- [x] Speech: sole Profile speech ownership, safe input reset, per-frame static I/O removal, lifecycle tests.
- [x] Root: Context fact upsert, Runtime Task operations/proposals, startup reconciliation, integration.
- [x] Final: baseline comparison, review, all checks, desktop smoke, authorized data cleanup, docs/report.

## Baseline
- Frontend typecheck and 187 tests / 59 files passed.
- Backend 251 tests passed outside the sandbox; the initial restricted run had one local port-binding error.

## Rulings
- Work in current clean checkout on a dedicated branch to preserve existing installed runtimes and app workspace integration.
- Scope is approved by user; no pause between implementation stages. Each file has one editing owner; integration follows their output.

## Final verification (2026-09-06)
- Backend: 219 unittest cases passed, including Task/Pi smoke, voice races, projection errors and startup reconciliation.
- Frontend: typecheck, 90 tests in 25 files, build, and 4 real Electron/Python/AudioWorklet smoke tests passed.
- Pi extension: typecheck and 5 tests passed.
- Cross-reviews completed; identified reset race and causal/projection ordering issues fixed and rechecked.
- Gateway tests now use isolated temporary Task databases, removing dependence on a stale shared schema-v2 fixture.
- Authorized desktop-v2 data reset completed after path/process inspection; models/cache/workspaces preserved.
- Production source: 986 added, 11,005 removed, net 10,019 removed; tests net 4,261 removed separately.
- Measurements and before/after diagrams: ../architecture-optimization.md.
- Work remains on codex/architecture-optimization in the original checkout, uncommitted.
