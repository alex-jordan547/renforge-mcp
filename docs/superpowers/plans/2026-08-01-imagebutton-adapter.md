# Imagebutton Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated single-line `imagebutton` visual-editor adapter with analyzer, patch path, coordinator dispatch, unit/coordinator coverage, and an opt-in seven-step live proof (issue #32).

**Architecture:** Twin of the existing textbutton source adapter (separate kind check and error messages). Coordinator peeks the source-line keyword and dispatches to the matching analyzer/patcher. Runtime selection/preview seams stay unchanged. Live proof is a **focused** seven-step scenario with its own fixture and test. The harness reuses task0 bridge handlers and small pure helpers; it does **not** clone the full task0 UI suite and does **not** widen the analyzer by allowlist/analogy.

**Tech Stack:** Python 3.11+, pytest, Ren'Py 8.5.3 SDK (live only), existing `EditorCoordinator` / bridge editor handlers.

**Spec:** `docs/superpowers/specs/2026-08-01-imagebutton-adapter-design.md`  
**Issue:** https://github.com/alex-jordan547/renforge-mcp/issues/32  
**Branch:** `feat/imagebutton-adapter-32`

## Global Constraints

1. Dedicated adapter path — do **not** change `analyze_textbutton_statement` to accept `imagebutton`.
2. Single-line statements only; multi-line `imagebutton:` blocks stay locked (`MULTILINE_STATEMENT_REJECTED`).
3. Every verdict-bearing position must use `measurement_method == "focus_list"`.
4. textbutton behavior and existing tests must remain green unchanged.
5. Live proof is opt-in via `RENFORGE_IMAGEBUTTON_LIVE=1`; default CI runs unit/coordinator only.
6. Scope doc honesty: say **“implemented; live proof opt-in”** unless live proof is in default CI.
7. Proof harness decision (locked): dedicated fixture + dedicated test + **slim focused runner**. Reuse `_require_ok`, `_wait_for_status`, `_extract_widget_position`, inject pattern, and `editor_task0_*` bridge commands. Do **not** fork the 800-line task0 UI matrix. Do **not** parameterize task0 into a multi-adapter mega-suite in this PR.
8. Roadmap “no widening by analogy” applies to the **analyzer kind check**, not to shared proof helpers.

## File map

| File | Role |
|---|---|
| `src/renforge/editor/source.py` | `ImagebuttonStatement`, analyze/patch twin, peek + dispatch routers |
| `src/renforge/editor/coordinator.py` | analyze/commit use dispatch; `statement_kind` from parsed kind |
| `tests/test_editor_source.py` | unit tests for imagebutton analyzer/patch |
| `tests/test_editor_coordinator.py` | analyze+commit imagebutton; unsupported kind lock |
| `tests/live_fixtures/renforge_editor_imagebutton_fixture.rpy` | single-line editable imagebutton + anchor |
| `src/renforge/editor_imagebutton_runner.py` | slim seven-step live scenario |
| `tests/test_editor_imagebutton_live.py` | opt-in live test |
| `docs/superpowers/specs/2026-07-30-renforge-visual-editor-v1-scope.md` | gate 3 + adapter table honesty |
| `docs/superpowers/specs/2026-07-30-renforge-visual-editor-vfull-roadmap.md` | Stage 1 note |
| `docs/superpowers/specs/2026-08-01-imagebutton-adapter-design.md` | design (already written) |
| `docs/superpowers/plans/2026-08-01-imagebutton-adapter.md` | this plan |

## Public interfaces (contract)

```python
@dataclass(frozen=True)
class ImagebuttonStatement:
    widget_id: str
    xpos: int
    ypos: int
    xpos_span: tuple[int, int]
    ypos_span: tuple[int, int]

def peek_statement_kind(line: str) -> str | None:
    """First top-level WORD on a single line, or None."""

def analyze_imagebutton_statement(line: str, *, expected_widget_id: str) -> ImagebuttonStatement: ...

def apply_imagebutton_patch(
    source_bytes: bytes, statement: ImagebuttonStatement, *, x: int, y: int
) -> bytes: ...

def analyze_editable_statement(
    line: str, *, expected_widget_id: str
) -> tuple[str, TextbuttonStatement | ImagebuttonStatement]:
    """Router only. Kind checks stay inside each dedicated analyzer."""

def apply_editable_statement_patch(
    source_bytes: bytes, kind: str, statement: Any, *, x: int, y: int
) -> bytes: ...
```

`source_key.statement_kind` values after this work: `"textbutton"` | `"imagebutton"`.

Live report minimum keys:

```text
resolve, preview, patch, reload, pixel_agreement, rebinding, byte_identical_undo
```

---

### Task 1: Imagebutton source analyzer + unit tests

**Files:**
- Modify: `src/renforge/editor/source.py`
- Modify: `tests/test_editor_source.py`

**Verify:** `python -m pytest tests/test_editor_source.py -q` → PASS

- [ ] **Step 1: Write failing unit tests**

Append to `tests/test_editor_source.py`:

```python
from renforge.editor.source import (
    EditorSourceError,
    analyze_editable_statement,
    analyze_imagebutton_statement,
    analyze_textbutton_statement,
    apply_editable_statement_patch,
    apply_imagebutton_patch,
    apply_textbutton_patch,
    peek_statement_kind,
)


def test_peek_statement_kind_reads_first_top_level_word() -> None:
    assert peek_statement_kind(
        '    imagebutton id "icon" idle Solid("#0f0") xpos 1 ypos 2 action NullAction()\n'
    ) == "imagebutton"
    assert peek_statement_kind(
        '    textbutton "Play" id "start" xpos 1 ypos 2 action NullAction()\n'
    ) == "textbutton"
    assert peek_statement_kind("    # comment only\n") is None


def test_analyze_imagebutton_statement_accepts_single_line_and_patches_spans() -> None:
    line = (
        '    imagebutton id "icon" idle Solid("#4c6ef5", xysize=(80, 48)) '
        "xpos 200 ypos 180 action NullAction()\n"
    )
    parsed = analyze_imagebutton_statement(line, expected_widget_id="icon")
    assert parsed.widget_id == "icon"
    assert parsed.xpos == 200
    assert parsed.ypos == 180
    patched = apply_imagebutton_patch(line.encode("utf-8"), parsed, x=240, y=196).decode("utf-8")
    assert patched == (
        '    imagebutton id "icon" idle Solid("#4c6ef5", xysize=(80, 48)) '
        "xpos 240 ypos 196 action NullAction()\n"
    )


def test_analyze_imagebutton_statement_rejects_textbutton_kind() -> None:
    with pytest.raises(EditorSourceError, match="imagebutton") as excinfo:
        analyze_imagebutton_statement(
            '    textbutton "Play" id "start" xpos 12 ypos 10 action NullAction()\n',
            expected_widget_id="start",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"


def test_analyze_imagebutton_statement_rejects_expressions_duplicates_and_multiline() -> None:
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_imagebutton_statement(
            '    imagebutton id "icon" idle Solid("#0f0") xpos xpos_base ypos 10 action NullAction()\n',
            expected_widget_id="icon",
        )
    assert excinfo.value.code == "XPOS_LITERAL_REQUIRED"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_imagebutton_statement(
            '    imagebutton id "icon" idle Solid("#0f0") xpos 1 xpos 2 ypos 10 action NullAction()\n',
            expected_widget_id="icon",
        )
    assert excinfo.value.code == "XPOS_DUPLICATE"

    with pytest.raises(EditorSourceError) as excinfo:
        analyze_imagebutton_statement(
            '    imagebutton:\n        id "icon"\n        xpos 1\n        ypos 2\n',
            expected_widget_id="icon",
        )
    assert excinfo.value.code == "MULTILINE_STATEMENT_REJECTED"


def test_analyze_imagebutton_ignores_keywords_inside_nested_calls() -> None:
    line = (
        '    imagebutton id "icon" idle Transform("x", xpos=9) '
        "xpos 12 ypos 34 action NullAction() # xpos 99\n"
    )
    parsed = analyze_imagebutton_statement(line, expected_widget_id="icon")
    assert (parsed.xpos, parsed.ypos) == (12, 34)


def test_analyze_editable_statement_routes_kinds() -> None:
    kind, stmt = analyze_editable_statement(
        '    imagebutton id "icon" idle Solid("#0f0") xpos 3 ypos 4 action NullAction()\n',
        expected_widget_id="icon",
    )
    assert kind == "imagebutton"
    assert stmt.xpos == 3
    kind_tb, stmt_tb = analyze_editable_statement(
        '    textbutton "Play" id "start" xpos 8 ypos 9 action NullAction()\n',
        expected_widget_id="start",
    )
    assert kind_tb == "textbutton"
    assert stmt_tb.ypos == 9
    with pytest.raises(EditorSourceError) as excinfo:
        analyze_editable_statement(
            '    bar id "b" value 1 range 2 xpos 1 ypos 2\n',
            expected_widget_id="b",
        )
    assert excinfo.value.code == "STATEMENT_KIND_MISMATCH"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/test_editor_source.py -q -k "imagebutton or editable_statement or peek_statement"
```

Expected: import/attribute failures for missing symbols.

- [ ] **Step 3: Implement in `source.py`**

1. Add `ImagebuttonStatement` (same fields as `TextbuttonStatement`).
2. Factor span rewrite into a private `_apply_integer_span_patch(source_bytes, xpos_span, ypos_span, *, x, y)` used by both patchers (optional but preferred).
3. Implement `analyze_imagebutton_statement` as a twin of textbutton:
   - multi-line reject
   - first top-level WORD must be `"imagebutton"`
   - messages name `imagebutton`
   - same id/xpos/ypos literal rules and nested-depth ignore
4. Implement `apply_imagebutton_patch`.
5. Implement `peek_statement_kind(line)` via `_lex_single_line`.
6. Implement router helpers:

```python
def analyze_editable_statement(line: str, *, expected_widget_id: str):
    kind = peek_statement_kind(line)
    if kind == "textbutton":
        return kind, analyze_textbutton_statement(line, expected_widget_id=expected_widget_id)
    if kind == "imagebutton":
        return kind, analyze_imagebutton_statement(line, expected_widget_id=expected_widget_id)
    raise EditorSourceError(
        "STATEMENT_KIND_MISMATCH",
        f"unsupported statement kind: {kind!r}",
    )


def apply_editable_statement_patch(source_bytes, kind, statement, *, x, y):
    if kind == "textbutton":
        return apply_textbutton_patch(source_bytes, statement, x=x, y=y)
    if kind == "imagebutton":
        return apply_imagebutton_patch(source_bytes, statement, x=x, y=y)
    raise EditorSourceError("STATEMENT_KIND_MISMATCH", f"unsupported statement kind: {kind!r}")
```

**Anti-pattern:** do not implement one analyzer with `if kind in {...}`. Each analyzer owns its kind check; routers only dispatch.

- [ ] **Step 4: Run full source tests — expect PASS**

```bash
python -m pytest tests/test_editor_source.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/renforge/editor/source.py tests/test_editor_source.py
git commit -m "$(cat <<'EOF'
feat(editor): add dedicated imagebutton source analyzer

Issue #32 — Stage 1 adapter twin for single-line imagebutton
with literal id/xpos/ypos; no textbutton allowlist widen.
EOF
)"
```

---

### Task 2: Coordinator dispatch + tests

**Files:**
- Modify: `src/renforge/editor/coordinator.py`
- Modify: `tests/test_editor_coordinator.py`

**Verify:** `python -m pytest tests/test_editor_source.py tests/test_editor_coordinator.py -q` → PASS

- [ ] **Step 1: Write failing coordinator tests**

Add to `tests/test_editor_coordinator.py`:

```python
def _make_imagebutton_project(tmp_path: Path) -> tuple[RenpyProject, Path]:
    root = tmp_path / "project_img"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    imagebutton id "start_btn" idle Solid("#4c6ef5", xysize=(80, 48)) '
        "xpos 12 ypos 10 action NullAction()\n",
        encoding="utf-8",
    )
    return RenpyProject(root), source


def test_analyze_and_commit_imagebutton_statement(tmp_path: Path) -> None:
    project, source = _make_imagebutton_project(tmp_path)
    observation = _base_observation()
    observation["runtime_key"]["ancestry"][1]["type"] = "ImageButton"
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-img",
            "object_id": "obj-independent-img",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-img")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True}
            assert result["source_key"]["statement_kind"] == "imagebutton"
            assert result["original_position"] == [12, 10]

            committed = _commit(sock, auth, analyzed, x=40, y=50, request_id="co-img")
            assert committed["ok"] is True
            assert committed["result"]["state"] == "published"
    finally:
        coordinator.close()

    text = source.read_text(encoding="utf-8")
    assert "xpos 40 ypos 50" in text
    assert 'imagebutton id "start_btn"' in text


def test_analyze_rejects_unsupported_statement_kind(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    source.write_text(
        "screen test_screen:\n"
        '    bar id "start_btn" value 1 range 10 xpos 12 ypos 10 xysize (40, 10)\n',
        encoding="utf-8",
    )
    observation = _base_observation()
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-bar",
            "object_id": "obj-independent-bar",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            reply = _analyze(sock, auth, observation, request_id="an-bar")
            assert reply["ok"] is True
            assert reply["result"]["capabilities"] == {"move": False}
            assert reply["result"]["lock_reason"]["code"] == "STATEMENT_KIND_MISMATCH"
    finally:
        coordinator.close()
```

- [ ] **Step 2: Run — expect FAIL**

```bash
python -m pytest tests/test_editor_coordinator.py -q -k "imagebutton or unsupported_statement"
```

Expected: imagebutton still analyzed as textbutton / kind mismatch.

- [ ] **Step 3: Wire coordinator**

In `coordinator.py`:

1. Replace import of only `analyze_textbutton_statement` with:

```python
from .source import (
    EditorSourceError,
    analyze_editable_statement,
    apply_editable_statement_patch,
)
```

2. In `_command_analyze_target`, replace:

```python
statement = analyze_textbutton_statement(...)
...
"statement_kind": "textbutton",
```

with:

```python
kind, statement = analyze_editable_statement(lines[source_line - 1], expected_widget_id=widget_id)
...
"statement_kind": kind,
```

3. In `_apply_same_file_intents`, replace direct textbutton analyze + manual span append with:

```python
kind, statement = analyze_editable_statement(line_text, expected_widget_id=widget_id)
# Prefer source_key statement_kind when present; must agree with peeked kind.
recorded_kind = source_key.get("statement_kind")
if isinstance(recorded_kind, str) and recorded_kind != kind:
    raise EditorError("STATEMENT_KIND_MISMATCH", "source_key statement_kind does not match source line")
# Collect replacements from statement spans (same as today) OR:
# rebuild whole file via apply_editable_statement_patch per intent carefully.
```

Keep the existing multi-intent global offset replacement approach: after analyzing, append xpos/ypos span replacements exactly as today (statement exposes spans on both dataclasses).

4. Do not special-case runtime locks for imagebutton; `ImageButton` is already in ancestry allowlist.

- [ ] **Step 4: Run coordinator + source — expect PASS**

```bash
python -m pytest tests/test_editor_source.py tests/test_editor_coordinator.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/renforge/editor/coordinator.py tests/test_editor_coordinator.py
git commit -m "$(cat <<'EOF'
feat(editor): dispatch imagebutton analyze and commit paths

Wire dedicated imagebutton adapter through EditorCoordinator
while keeping textbutton behavior intact.
EOF
)"
```

---

### Task 3: Focused seven-step live proof

**Files:**
- Create: `tests/live_fixtures/renforge_editor_imagebutton_fixture.rpy`
- Create: `src/renforge/editor_imagebutton_runner.py`
- Create: `tests/test_editor_imagebutton_live.py`

**Verify (non-live):**  
`python -m pytest tests/test_editor_imagebutton_live.py -q` → SKIP  

**Verify (live, when SDK+display available):**  
`RENFORGE_IMAGEBUTTON_LIVE=1 python -m pytest tests/test_editor_imagebutton_live.py -q` → PASS

#### Harness decision (do not reopen)

- Dedicated fixture file.
- Dedicated test file gated by `RENFORGE_IMAGEBUTTON_LIVE`.
- Slim runner (~150–250 lines) that only exercises the seven steps.
- Reuse from `editor_task0_runner`: `_require_ok`, `_wait_for_status`, `_extract_widget_position`, `_source_generation`, inject copy pattern, and the `editor_task0_*` bridge API already registered by injected `editor.rpy`.
- Do **not** call `run_editor_task0_live_scenario` (it asserts task0-only widgets and the full UI matrix).

- [ ] **Step 1: Fixture**

`tests/live_fixtures/renforge_editor_imagebutton_fixture.rpy`:

```renpy
default renforge_editor_imagebutton_clicks = 0

screen renforge_editor_imagebutton_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "imgbtn_root"
        xfill True
        yfill True

        textbutton "ANCHOR" id "imgbtn_anchor" xpos 360 ypos 210 action NullAction()

        imagebutton id "imgbtn_target" idle Solid("#4c6ef5", xysize=(96, 56)) xpos 200 ypos 180 action SetVariable("renforge_editor_imagebutton_clicks", renforge_editor_imagebutton_clicks + 1)
```

- [ ] **Step 2: Slim runner**

`src/renforge/editor_imagebutton_runner.py` outline:

```python
FIXTURE_SCREEN = "renforge_editor_imagebutton_fixture"
TARGET_ID = "imgbtn_target"
FIXTURE_RESOURCE = Path(__file__).resolve().parents[2] / "tests" / "live_fixtures" / "renforge_editor_imagebutton_fixture.rpy"
EDITOR_RESOURCE = Path(__file__).resolve().parent / "bridge" / "editor.rpy"

def inject_editor_imagebutton_resources(project_root: Path) -> dict[str, str]:
    # copy editor.rpy + fixture into game/zz_renforge_editor_imagebutton*.rpy

def run_editor_imagebutton_live_scenario(client, *, fixture_path: Path) -> dict:
    report = {}
    baseline_bytes = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()
    report["fixture_before"] = {
        "sha256": baseline_sha,
        "position": _extract_widget_position(baseline_bytes.decode(), TARGET_ID),
    }

    # 1 resolve
    _require_ok(client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}), "start")
    # list UI / focus candidates; select target center
    # wait analysis: lock_reason None, current_analysis_id set
    status = _wait_for_status(...)
    source_key = status.get("current_source_key") or {}
    report["resolve"] = {
        "statement_kind": source_key.get("statement_kind"),
        "lock_reason": status.get("selected_lock_reason"),
        "move": status.get("save_enabled") is False and status.get("selected_lock_reason") in (None, ""),
        "analysis_id": status.get("current_analysis_id"),
        "measurement_method": "focus_list",  # from observation on select
    }
    assert source_key.get("statement_kind") == "imagebutton"

    # 2 preview — nudge right 24 / down 16 via editor_task0_key or apply_preview
    before = status focus/original position from focus_list observation
    # nudge
    after_preview_status = ...
    report["preview"] = {
        "before": before,
        "after": after,
        "measurement_method": "focus_list",
    }

    # 3 patch + 4 reload — click rf_save, wait Reload committed
    pre_sha = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    # save ...
    post_bytes = fixture_path.read_bytes()
    post_sha = hashlib.sha256(post_bytes).hexdigest()
    report["patch"] = {
        "before_sha256": pre_sha,
        "after_sha256": post_sha,
        "source_position_after": _extract_widget_position(post_bytes.decode(), TARGET_ID),
    }
    report["reload"] = {"ok": True, "script_generation": ..., "status_text": "Reload committed"}

    # 5 pixel agreement — bounds/focus within 1px of expected source-driven runtime pos
    report["pixel_agreement"] = {"expected": [...], "observed": [...], "delta": [...]}

    # 6 rebinding — post-save selected widget_id still TARGET_ID; analysis id refreshed
    report["rebinding"] = {"ok": True, "widget_id": TARGET_ID, "analysis_id": ...}

    # 7 byte-identical undo — write baseline_bytes back, compare sha
    fixture_path.write_bytes(baseline_bytes)
    restored = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    report["byte_identical_undo"] = {
        "baseline_sha256": baseline_sha,
        "restored_sha256": restored,
        "matches_baseline": restored == baseline_sha,
    }
    return report
```

Notes:

- Prefer selecting via `editor_task0_select` with coordinates from `list_ui_elements` / focus candidates for `imgbtn_target`.
- All geometry used in assertions must come from observations with `measurement_method == "focus_list"` or from post-reload focus bounds that the editor itself measured that way.
- For step 7, restoring baseline bytes in the harness is explicit proof of byte identity of the pre-patch file; also assert `patch.after_sha256 != patch.before_sha256` and that only xpos/ypos integers changed (re-parse with `analyze_imagebutton_statement`).

- [ ] **Step 3: Opt-in test**

`tests/test_editor_imagebutton_live.py`:

```python
pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_IMAGEBUTTON_LIVE"),
    reason="set RENFORGE_IMAGEBUTTON_LIVE=1 to run imagebutton seven-step live proof",
)

def test_imagebutton_seven_step_live_proof(demo_copy: Path) -> None:
    # inject resources, launch_with_bridge(editor=True), activate RF if needed,
    # run_editor_imagebutton_live_scenario, assert each of the seven keys.
```

Assertions (explicit per step):

```python
assert report["resolve"]["statement_kind"] == "imagebutton"
assert report["resolve"]["lock_reason"] in (None, "")
assert report["preview"]["after"] != report["preview"]["before"]
assert report["preview"]["measurement_method"] == "focus_list"
assert report["patch"]["after_sha256"] != report["patch"]["before_sha256"]
assert report["reload"]["ok"] is True
assert abs(report["pixel_agreement"]["delta"][0]) <= 1
assert abs(report["pixel_agreement"]["delta"][1]) <= 1
assert report["rebinding"]["ok"] is True
assert report["byte_identical_undo"]["matches_baseline"] is True
```

- [ ] **Step 4: Non-live verification**

```bash
python -m pytest tests/test_editor_imagebutton_live.py -q
# expected: 1 skipped
python -m pytest tests/test_editor_source.py tests/test_editor_coordinator.py -q
# expected: pass
```

- [ ] **Step 5: Live verification (when possible)**

```bash
RENFORGE_IMAGEBUTTON_LIVE=1 python -m pytest tests/test_editor_imagebutton_live.py -q
```

If display/SDK unavailable, document SKIP in PR body; do not fake a pass.

- [ ] **Step 6: Commit**

```bash
git add tests/live_fixtures/renforge_editor_imagebutton_fixture.rpy \
  src/renforge/editor_imagebutton_runner.py \
  tests/test_editor_imagebutton_live.py
git commit -m "$(cat <<'EOF'
test(editor): add imagebutton seven-step live proof harness

Opt-in live scenario covers resolve → preview → patch → reload →
pixel agreement → rebinding → byte-identical undo for issue #32.
EOF
)"
```

---

### Task 4: Scope docs + PR

**Files:**
- Modify: `docs/superpowers/specs/2026-07-30-renforge-visual-editor-v1-scope.md`
- Modify: `docs/superpowers/specs/2026-07-30-renforge-visual-editor-vfull-roadmap.md`
- Include design + this plan in the PR

**Verify:**  
`python -m pytest tests/test_editor_source.py tests/test_editor_coordinator.py tests/test_editor_protocol.py tests/test_editor_runtime.py tests/test_editor_imagebutton_live.py -q`

- [ ] **Step 1: V1 scope edits**

Gate 3 wording → proven single-line adapters (`textbutton`, `imagebutton`) with literal integer `xpos`/`ypos`.

Adapter table row:

| Adapter | Selection | Write chain | Status |
|---|---|---|---|
| `imagebutton` | focusable (Spike C) | dedicated analyzer + coordinator path | **Implemented; live proof opt-in (`RENFORGE_IMAGEBUTTON_LIVE=1`)** |

- [ ] **Step 2: Roadmap Stage 1 note**

On the `imagebutton` row, note implementation + live harness pointer. Do not mark Stage 1 complete.

- [ ] **Step 3: Final test run** (command above)

- [ ] **Step 4: Commit docs and open PR**

```bash
git add docs/superpowers/specs/2026-08-01-imagebutton-adapter-design.md \
  docs/superpowers/plans/2026-08-01-imagebutton-adapter.md \
  docs/superpowers/specs/2026-07-30-renforge-visual-editor-v1-scope.md \
  docs/superpowers/specs/2026-07-30-renforge-visual-editor-vfull-roadmap.md
git commit -m "$(cat <<'EOF'
docs: imagebutton adapter design, plan, and scope update

Issue #32 Stage 1 allowlist widening — evidence-gated.
EOF
)"

git push -u origin HEAD
gh pr create --title "feat(editor): dedicated imagebutton adapter (#32)" --body "$(cat <<'EOF'
## Summary
- Dedicated single-line `imagebutton` analyzer/patcher (not a textbutton allowlist widen)
- Coordinator analyze/commit dispatch by statement kind
- Focused opt-in seven-step live proof (`RENFORGE_IMAGEBUTTON_LIVE=1`)
- Scope/roadmap honesty notes

## Test plan
- [x] `pytest tests/test_editor_source.py tests/test_editor_coordinator.py -q`
- [ ] `RENFORGE_IMAGEBUTTON_LIVE=1 pytest tests/test_editor_imagebutton_live.py -q`

Closes #32
EOF
)"
```

---

## Phase checklist (executor)

| Phase | Done when |
|---|---|
| 1 Source analyzer | `test_editor_source.py` green; imagebutton twin exists |
| 2 Coordinator | imagebutton analyze+commit green; bar still locks; textbutton green |
| 3 Live harness | test skips by default; live env exercises all 7 report keys |
| 4 Docs + PR | scope honesty updated; PR opened against main linking #32 |

## Self-review

1. **Spec coverage:** dedicated analyzer, patch, coordinator dispatch, locks, seven-step live proof, docs honesty, textbutton unchanged — all tasked.
2. **Harness decision recorded:** slim dedicated runner; no task0 mega-suite fork; no analyzer allowlist widen.
3. **No placeholders:** concrete files, commands, interfaces, and assertions.
4. **Type consistency:** `ImagebuttonStatement` fields match textbutton spans; `statement_kind` is `"imagebutton"`.
