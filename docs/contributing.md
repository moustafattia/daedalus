# Contributing to Daedalus

So you want to hack on Daedalus? Welcome. This doc covers how to run tests, add a new runtime, add a workflow stage, and keep the docs in sync.

---

## Quick start

```bash
# Clone
git clone https://github.com/attmous/daedalus.git
cd daedalus

# Install contributor/test deps
python3 -m pip install -r requirements-dev.txt

# Install (into your Hermes home)
./scripts/install.sh

# Run tests
pytest

# Run one test file
pytest tests/test_stall_detection.py -v

# Run with coverage
pytest --cov=daedalus --cov-report=term-missing
```

The public onboarding path should stay green too:

```bash
pytest tests/test_public_onboarding_smoke.py -v
pytest tests/test_official_plugin_layout.py -v
pytest tests/test_pip_plugin_packaging.py -v
```

If you touch files loaded at runtime via `Path(__file__).parent` — prompts,
skills, workflow templates, or `plugin.yaml` — keep the package metadata in
`pyproject.toml` / `MANIFEST.in` and the packaging test green. `daedalus/projects/**`
is placeholder-only in the public repo and is intentionally not shipped in the
public plugin payload.

---

## Test conventions

### File naming

- `test_<module_name>.py` — unit tests for a single module
- `test_<feature>_phase_<letter>.py` — integration tests for a feature phase
- `test_workflows_change_delivery_<topic>.py` — workflow-specific tests

### Test categories

| Category | Count | Example |
|---|---|---|
| Unit tests | ~40 | `test_config_snapshot.py`, `test_stall_detection.py` |
| Integration tests | ~25 | `test_external_reviewer_phase_b.py` |
| Formatter tests | ~10 | `test_formatters_shadow_report.py` |
| Schema tests | ~8 | `test_workflow_change_delivery_schema.py` |

### Running the full suite

```bash
pytest -x  # stop on first failure
pytest -n auto  # parallel (requires pytest-xdist)
```

---

## Adding a new runtime

1. **Implement the Protocol** in `daedalus/runtimes/your_runtime.py`:
   ```python
   from runtimes import register

   @register("your-kind")
   class YourRuntime:
       def ensure_session(self, *, worktree, session_name, model, resume_session_id): ...
       def run_prompt(self, *, worktree, session_name, prompt, model): ...
       def assess_health(self, session_meta, *, worktree, now_epoch): ...
       def close_session(self, *, worktree, session_name): ...
       def last_activity_ts(self) -> float | None: ...
   ```

2. **Add to schema** in `daedalus/workflows/change_delivery/schema.yaml`:
   ```yaml
   runtimes:
     your-runtime:
       kind: your-kind
       timeout-seconds: 1200
   ```

3. **Add tests** in `tests/test_workflows_change_delivery_runtimes_your_runtime.py`.

4. **Document** in `docs/concepts/runtimes.md`.

---

## Adding a workflow stage

The current change-delivery workflow has stages: `implementing` → `awaiting_claude_prepublish` → `ready_to_publish` → `under_review` → `approved` → `merged`.

To add a new stage:

1. **Add the state** to the workflow state machine in `daedalus/workflows/change_delivery/workflow.py`.
2. **Add the transition logic** in `daedalus/workflows/change_delivery/dispatch.py`.
3. **Add the action type** in `daedalus/workflows/change_delivery/actions.py`.
4. **Update the schema** in `daedalus/workflows/change_delivery/migrations.py` (if new DB columns needed).
5. **Add tests** in `tests/test_workflows_change_delivery_actions.py`.
6. **Document** in `docs/concepts/lanes.md` and `docs/concepts/actions.md`.

---

## Keeping docs in sync

Every code change that affects operator-facing behavior must update docs:

| Change type | Docs to update |
|---|---|
| New slash command | `docs/operator/slash-commands.md`, `docs/operator/cheat-sheet.md` |
| New concept | `docs/concepts/<new-concept>.md`, `docs/architecture.md` |
| Schema change | `docs/concepts/lanes.md`, `docs/concepts/actions.md` |
| Config change | `daedalus/workflows/change_delivery/workflow.template.md`, `docs/examples/change-delivery.workflow.md`, `docs/concepts/hot-reload.md`, `docs/operator/cheat-sheet.md` |
| Rename/refactor | All docs + ADR in `docs/adr/` |

---

## Code style

- **Type hints everywhere.** `from __future__ import annotations` at the top of every file.
- **No external deps in core.** The runtime must work with stdlib + SQLite only. Rich is allowed for TUI rendering.
- **Fail soft.** Every subscriber, webhook, and observer must catch its own exceptions. Never let a side-effect failure crash the tick.
- `--json` is the default operator dialect. Humans read formatters, scripts read JSON.

---

## Where to get help

- Read the operator cheat sheet: `docs/operator/cheat-sheet.md`
- Check the architecture doc: `docs/architecture.md`
- Run `/daedalus doctor` inside Hermes
- Open an issue with the output of `/daedalus status --format json`
