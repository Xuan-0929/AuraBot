# Task 1 report — Configuration, Workspace Registry, and Task Domain Admission

## Delivered

- Added typed `MainAgentSettings` plus a shared `WorkspaceRegistry`.  Workspace ids are
  canonical identifiers, not paths; unknown, path-shaped, and symlinked workspace roots are
  rejected.
- Retained `app`, `main_agent`, `memory`, `task`, and `dev` YAML sections.  The example and
  test fixture now declare the default application data directory and workspace list.
- Replaced client-controlled Task `workingDirectory` creation input with `workspaceId`,
  `ownerProfileId`, and `idempotencyKey`.  `workingDirectory` is forbidden on the public create
  schema and omitted from Task responses.
- Centralized Task admission in `TaskManager`: it normalizes and validates title, prompt,
  owner, idempotency key, workspace, and timeout before the Store sees a request.  The Manager
  resolves the registered workspace to the internally persisted process cwd.
- Persisted `workspace_id`, `owner_profile_id`, and `idempotency_key`; added owner/key duplicate
  return semantics and an unreleased-product SQLite v1→v2 migration that preserves old rows and
  events.
- Preserved existing queue, pause/resume/cancel, waiting-question, recovery, and Pi process
  semantics.  Updated the Task Runtime architecture note for schema v2.

## Changed files

- `backend/main_agent/__init__.py`
- `backend/main_agent/settings.py`
- `backend/config.py`
- `backend/task/models.py`
- `backend/task/settings.py`
- `backend/task/manager.py`
- `backend/task/store.py`
- `backend/app/gateway/task_schemas.py`
- `config.example.yaml`
- `backend/tests/fixtures/runtime_config.yaml`
- `backend/tests/test_main_agent_settings.py`
- `backend/tests/test_config.py`
- `backend/tests/test_task_gateway.py`
- `backend/tests/task/test_models.py`
- `backend/tests/task/test_store.py`
- `backend/tests/task/test_manager.py`
- `backend/tests/task/test_process.py`
- `docs/modules/task-agent.md`

## Strict TDD evidence

### RED

Command:

```sh
uv run --project backend python -m unittest \
  backend.tests.test_main_agent_settings \
  backend.tests.task.test_manager \
  backend.tests.test_task_gateway
```

Observed expected failure before implementation:

```text
ModuleNotFoundError: No module named 'backend.main_agent'
TypeError: TaskRecord.__init__() got an unexpected keyword argument 'workspace_id'
FAILED (errors=15)
```

### GREEN

Targeted command:

```sh
uv run --project backend python -m unittest \
  backend.tests.test_config \
  backend.tests.test_main_agent_settings \
  backend.tests.task.test_models \
  backend.tests.task.test_store \
  backend.tests.task.test_manager \
  backend.tests.task.test_process \
  backend.tests.test_task_gateway
```

Observed:

```text
Ran 53 tests in 2.837s
OK
```

Final verification command:

```sh
git diff --check && uv run --project backend python -m unittest discover -s backend/tests
```

Observed:

```text
Ran 225 tests in 8.055s
OK
```

The run emits existing intentional Gateway-error stack traces for tests that assert sanitized
500 responses, a Starlette `TestClient` deprecation warning, and one asyncio slow-task diagnostic
from the offline Pi smoke test; none causes a test failure.

## Self-review

- Verified Task creation no longer exposes a caller-selected cwd.  Only `TaskManager` converts a
  registered workspace id to the internal cwd persisted for Pi startup.
- Verified REST and direct Manager paths hit the same validation: direct Manager tests cover all
  domain fields and duplicate idempotency; a real injected Manager REST test covers missing and
  unknown workspaces.
- Verified idempotency is scoped to `owner_profile_id` and enforced by both Store lookup and a
  partial unique SQLite index.  Empty v1 legacy keys are intentionally excluded from that index.
- Verified migration keeps v1 data and adds deterministic legacy values rather than deleting the
  unreleased Task database.
- Reviewed all modified/new Python source files for required top-of-file responsibility, flow,
  and module-relationship comments.

## Concerns

None.  `app.workspaces` deliberately uses a list of `{id, path}` objects so duplicate ids can be
rejected rather than silently overwritten by YAML mapping semantics.

---

## Review-remediation addendum

### Findings fixed

- Workspace roots now inspect every lexical path component before canonicalization and reject an
  ancestor symlink such as `./link/project`, not only a symlink at the final root.  The same
  boundary is covered for direct registry construction, config parsing, and TaskManager settings.
- `MainAgentSettings` now requires non-empty string values for `app.data_dir` and
  `main_agent.profile_dir`; YAML `null`, lists, mappings, and empty strings are rejected instead
  of being converted to path-like text.
- The SQLite v1→v2 migration now validates the complete expected legacy table columns and
  required indexes before altering data.  It then validates the complete v2 columns/indexes before
  setting `PRAGMA user_version=2`.  Malformed v1 schemas remain at version 1 and fail clearly.
- Replaced the minimal pseudo-v1 test with a real v1 DDL fixture containing queued, running, and
  waiting Tasks plus replay events.  The migration test verifies durable rows/events remain
  readable and that all three records are returned as recovery candidates.

### Additional changed files

- `backend/main_agent/settings.py`
- `backend/task/store.py`
- `backend/tests/test_main_agent_settings.py`
- `backend/tests/task/test_manager.py`
- `backend/tests/task/test_store.py`
- `backend/tests/test_task_gateway.py`

### Remediation RED evidence

Command:

```sh
uv run --project backend python -m unittest \
  backend.tests.test_main_agent_settings \
  backend.tests.task.test_manager \
  backend.tests.task.test_store
```

Observed before the fixes:

```text
WorkspaceRegistryError not raised for an ancestor symlink.
ValueError not raised for null/empty/list/mapping path settings.
TaskStoreSchemaError not raised for an incomplete user_version=1 schema.
FAILED (failures=10, errors=2)
```

The test-harness errors were corrected (missing test-only `replace` import and a non-UUID fixture
question id) without changing production behavior.  A second focused RED test then established the
index-validation gap:

```sh
uv run --project backend python -m unittest \
  backend.tests.task.test_store.TaskStoreTests.test_rejects_v1_schema_missing_required_queue_index
```

```text
AssertionError: TaskStoreSchemaError not raised
FAILED (failures=1)
```

### Remediation GREEN evidence

Targeted command:

```sh
uv run --project backend python -m unittest \
  backend.tests.test_main_agent_settings \
  backend.tests.task.test_manager \
  backend.tests.task.test_store
```

```text
Ran 26 tests in 1.689s
OK
```

Final command:

```sh
git diff --check && uv run --project backend python -m unittest discover -s backend/tests
```

```text
Ran 230 tests in 8.443s
OK
```

### Remediation self-review and concerns

- The schema validator intentionally checks both columns and named non-auto indexes.  SQLite's
  internal UNIQUE/PRIMARY KEY auto-index names remain excluded because they are implementation
  generated; all product-owned indexes are exact and required.
- The existing expected Gateway-error log output, Starlette `TestClient` deprecation warning, and
  offline Pi smoke slow-task diagnostic still occur during the full suite; no test failed.
- No remaining concerns.

---

## Second review-remediation addendum

### Finding fixed

The migration gate no longer treats matching column and index *names* as a valid schema.  It now
checks the complete supported contract before a v1 database can be altered and again before its
version is bumped: `PRAGMA table_info` affinity, nullability, defaults, and PK positions;
`PRAGMA foreign_key_list`; `PRAGMA index_list` uniqueness/origin/partial values;
`PRAGMA index_xinfo` ordered key columns; and normalized `sqlite_master` SQL for required
`CHECK`/`AUTOINCREMENT` syntax and the v2 idempotency partial-index predicate.  Fresh v2 files
and v1-migrated v2 files have intentionally different defaults for the three new v2 columns, so
the validator accepts only those two exact default variants.

### Changed files

- `backend/task/store.py`
- `backend/tests/task/test_store.py`

### Strict TDD evidence

#### RED

Command:

```sh
uv run --project backend python -m unittest \
  backend.tests.task.test_store.TaskStoreTests.test_rejects_v1_schema_with_same_named_index_on_wrong_columns \
  backend.tests.task.test_store.TaskStoreTests.test_rejects_v1_schema_with_same_columns_but_missing_check_constraint \
  backend.tests.task.test_store.TaskStoreTests.test_rejects_v2_schema_with_partial_idempotency_index_missing_predicate
```

Observed before implementation:

```text
FFF
AssertionError: TaskStoreSchemaError not raised
Ran 3 tests in 0.010s
FAILED (failures=3)
```

This demonstrates that a same-name queue index with wrong key order, a same-column-name v1
table missing `CHECK(timeout_seconds > 0)`, and a v2 index missing its `WHERE` predicate were
all formerly accepted.

#### GREEN

Targeted command:

```sh
uv run --project backend python -m unittest backend.tests.task.test_store
```

```text
Ran 12 tests in 0.036s
OK
```

Final verification command:

```sh
uv run --project backend python -m unittest discover -s backend/tests
git diff --check
```

```text
Ran 233 tests in 8.163s
OK
```

### Self-review and concerns

- The index validator includes SQLite-generated UNIQUE indexes as structural contracts by their
  origin, uniqueness, partial flag, and ordered keys, while deliberately not depending on their
  unstable auto-generated names.
- It rejects unexpected indexes and foreign keys as well as missing or weakened required ones,
  preventing a lookalike v1 schema from being marked v2.
- Expected Gateway-error stack traces, one Starlette `TestClient` deprecation warning, and the
  offline Pi smoke slow-task diagnostic remain normal test output; the full suite passed.
- No remaining concerns.

---

## Fourth review-remediation addendum

### Finding fixed

Structural DDL validation now uses a separate, lightweight token stream rather than searching the
human-readable comment-free SQL string.  The tokenizer removes comments and whitespace, emits
words and symbols outside quotes, and emits each quoted literal or identifier as one opaque typed
token.  Required `CHECK`/`AUTOINCREMENT` expressions are matched as complete token sequences, so
quoted text cannot forge executable syntax.  Partial-index validation compares the full tokenized
`WHERE` suffix, including the exact empty-string literal token for `idempotency_key <> ''`.

### Changed files

- `backend/task/store.py`
- `backend/tests/task/test_store.py`

### Strict TDD evidence

#### RED

Command:

```sh
uv run --project backend python -m unittest \
  backend.tests.task.test_store.TaskStoreTests.test_rejects_v1_schema_with_quoted_check_text_forgery \
  backend.tests.task.test_store.TaskStoreTests.test_structural_sql_tokens_keep_doubled_quoted_content_as_single_tokens
```

Observed before implementation:

```text
FE
AssertionError: TaskStoreSchemaError not raised
AttributeError: type object 'TaskStore' has no attribute '_structural_sql_tokens'
Ran 2 tests in 0.009s
FAILED (failures=1, errors=1)
```

The malicious v1 DDL had no executable `CHECK(timeout_seconds > 0)`; instead it used a different
CHECK whose two string operands contained that text.  The old quote-preserving substring search
accepted and upgraded it.

#### GREEN

Targeted command:

```sh
uv run --project backend python -m unittest backend.tests.task.test_store
```

```text
Ran 17 tests in 0.071s
OK
```

Final verification command:

```sh
uv run --project backend python -m unittest discover -s backend/tests
git diff --check
```

```text
Ran 238 tests in 8.852s
OK
```

### Self-review and concerns

- The token scanner remains deliberately small: it recognizes SQLite comments, normal words and
  symbols, and the four SQLite DDL quoting forms.  It decodes doubled single quotes, double
  quotes, and backticks; focused tests cover all three while preserving their embedded `--` text.
- The forged v1 fixture asserts both a clear `TaskStoreSchemaError` and that `PRAGMA user_version`
  remains `1`; valid realistic v1 migration remains covered by the existing fixture test.
- Expected Gateway-error traces, the Starlette deprecation warning, and one offline Pi slow-task
  diagnostic were emitted during the full suite; no tests failed.
- No remaining concerns.

---

## Third review-remediation addendum

### Finding fixed

`sqlite_master.sql` is now normalized by a small SQLite-DDL scanner rather than by raw substring
matching.  It removes `--` and `/* ... */` comments only outside quotes, drops only outside
whitespace, and lowercases only unquoted SQL tokens.  Single-quoted literals, double-quoted and
backtick identifiers, and square-bracket identifiers remain intact, including their embedded
comment markers.  Required table constraints and the partial idempotency-index predicate are
therefore evaluated against comment-free DDL and cannot be forged inside a comment.

### Changed files

- `backend/task/store.py`
- `backend/tests/task/test_store.py`

### Strict TDD evidence

#### RED

Command:

```sh
uv run --project backend python -m unittest \
  backend.tests.task.test_store.TaskStoreTests.test_rejects_v1_schema_with_block_commented_check_constraint \
  backend.tests.task.test_store.TaskStoreTests.test_rejects_v1_schema_with_line_commented_check_constraint \
  backend.tests.task.test_store.TaskStoreTests.test_sql_normalizer_preserves_quoted_comment_markers
```

Observed before implementation:

```text
FFE
AssertionError: TaskStoreSchemaError not raised
AttributeError: type object 'TaskStore' has no attribute '_comment_free_normalized_sql'
Ran 3 tests in 0.012s
FAILED (failures=2, errors=1)
```

The two malformed legacy fixtures had no executable timeout `CHECK` but contained the required
text inside a block or line comment; both were previously upgraded.  The third RED test defined
the required quote-preservation behavior.

#### GREEN

Targeted command:

```sh
uv run --project backend python -m unittest backend.tests.task.test_store
```

```text
Ran 15 tests in 0.048s
OK
```

Final verification command:

```sh
uv run --project backend python -m unittest discover -s backend/tests
git diff --check
```

```text
Ran 236 tests in 8.936s
OK
```

### Self-review and concerns

- The scanner is intentionally linear and stateful, not a general SQL parser.  It handles the
  SQLite DDL quoting forms needed here, including doubled single/double/backtick quote escapes,
  while treating comment delimiters inside quoted content as literal text.
- Both malicious v1 fixtures assert that initialization raises `TaskStoreSchemaError` before any
  migration and that `PRAGMA user_version` remains `1`.
- Existing expected Gateway-error traces, the Starlette deprecation warning, and one offline Pi
  slow-task diagnostic appeared in the full run; no tests failed.
- No remaining concerns.
