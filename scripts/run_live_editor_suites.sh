#!/usr/bin/env bash
# Run every opt-in editor live suite and separate real failures from flakes.
#
# The accepted suite set is a literal table: glob-driven discovery could add,
# drop, or silently rename suites without anyone noticing, so the exact
# file/gate pairs are enumerated here and compared against tests/ before any
# suite runs. A missing, extra, duplicate, nonexistent, or gate-mismatched
# suite fails this script before the first pytest invocation.
#
# A suite that fails is retried once: only a suite that fails twice is
# reported as a failure. Each attempt is captured in its own mktemp file and
# that file is printed on failure. Flaky suites are listed separately,
# because a job that goes red on noise gets ignored, and an ignored job is
# the problem this exists to solve.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

# Literal acceptance table: one "file gate" pair per suite, in suite order.
# The pre-run validation below enforces sync with tests/test_editor_*_live.py.
SUITES="
test_editor_align_live.py RENFORGE_ALIGN_LIVE
test_editor_anchor_live.py RENFORGE_ANCHOR_LIVE
test_editor_animated_live.py RENFORGE_ANIMATED_LIVE
test_editor_bar_live.py RENFORGE_BAR_LIVE
test_editor_bar_resize_live.py RENFORGE_BAR_RESIZE_LIVE
test_editor_button_live.py RENFORGE_BUTTON_LIVE
test_editor_crop_live.py RENFORGE_CROP_LIVE
test_editor_failed_gate_live.py RENFORGE_FAILED_GATE_LIVE
test_editor_hit_sentinel_live.py RENFORGE_HIT_SENTINEL_LIVE
test_editor_imagebutton_live.py RENFORGE_IMAGEBUTTON_LIVE
test_editor_loop_live.py RENFORGE_LOOP_LIVE
test_editor_multiline_textbutton_live.py RENFORGE_MULTILINE_TEXTBUTTON_LIVE
test_editor_offset_live.py RENFORGE_OFFSET_LIVE
test_editor_pos_live.py RENFORGE_POS_LIVE
test_editor_rotation_live.py RENFORGE_ROTATION_LIVE
test_editor_slider_live.py RENFORGE_SLIDER_LIVE
test_editor_style_color_live.py RENFORGE_STYLE_COLOR_LIVE
test_editor_task0_live.py RENFORGE_TASK0_LIVE
test_editor_vbar_live.py RENFORGE_VBAR_LIVE
test_editor_viewport_live.py RENFORGE_VIEWPORT_LIVE
test_editor_zorder_live.py RENFORGE_ZORDER_LIVE
"

gate_fail() {
    echo "::error title=Live suite gate::$1" >&2
    exit 1
}

# Flatten the table into alternating file/gate words. Bash 3.2 (still the
# default on macOS) has no associative arrays, so pairs are tracked as
# space-delimited membership strings.
pairs=()
for word in $SUITES; do
    pairs+=("$word")
done

count=${#pairs[@]}
if [ $((count % 2)) -ne 0 ]; then
    gate_fail "malformed suite table: odd word count"
fi
if [ "$count" -eq 0 ]; then
    gate_fail "empty suite table"
fi

seen_files=" "
seen_gates=" "
i=0
while [ "$i" -lt "$count" ]; do
    file="${pairs[$i]}"
    gate="${pairs[$((i + 1))]}"

    case "$seen_files" in
        *" $file "*) gate_fail "duplicate suite in table: $file" ;;
    esac
    seen_files="${seen_files}${file} "

    case "$seen_gates" in
        *" $gate "*) gate_fail "duplicate gate in table: $gate" ;;
    esac
    seen_gates="${seen_gates}${gate} "

    if [ ! -f "tests/$file" ]; then
        gate_fail "table lists nonexistent suite: tests/$file"
    fi

    found="$(grep -oE 'RENFORGE_[A-Z0-9_]+' "tests/$file" | head -1)"
    if [ "$found" != "$gate" ]; then
        gate_fail "gate mismatch for tests/$file: table says $gate, file says ${found:-<none>}"
    fi

    i=$((i + 2))
done

# Every live suite on disk must be listed in the table.
for path in tests/test_editor_*_live.py; do
    if [ ! -e "$path" ]; then
        continue
    fi
    base="$(basename "$path")"
    case "$seen_files" in
        *" $base "*) ;;
        *) gate_fail "unlisted live suite on disk: $path" ;;
    esac
done

suite_count=$((count / 2))
echo "live suite gate: $suite_count suites validated against tests/"

failed=()
flaky=()
passed=()

run_suite() {
    # $1 file, $2 gate, $3 log file
    env "$2=1" PYTHONPATH=src uv run pytest -q "tests/$1" >"$3" 2>&1
}

i=0
while [ "$i" -lt "$count" ]; do
    file="${pairs[$i]}"
    gate="${pairs[$((i + 1))]}"
    name="$(basename "$file" .py)"

    echo "::group::$name ($gate)"
    first_log="$(mktemp)" || gate_fail "mktemp failed for $name attempt 1"
    if run_suite "$file" "$gate" "$first_log"; then
        tail -1 "$first_log"
        rm -f "$first_log"
        passed+=("$name")
    else
        tail -20 "$first_log"
        echo "--- retrying once ---"
        retry_log="$(mktemp)" || gate_fail "mktemp failed for $name attempt 2"
        if run_suite "$file" "$gate" "$retry_log"; then
            tail -1 "$retry_log"
            rm -f "$first_log" "$retry_log"
            flaky+=("$name")
        else
            tail -20 "$retry_log"
            echo "$name failed twice; attempt logs: $first_log $retry_log"
            failed+=("$name")
        fi
    fi
    echo "::endgroup::"

    i=$((i + 2))
done

echo
echo "passed: ${#passed[@]}  flaky: ${#flaky[@]}  failed: ${#failed[@]}"

# Guard every array expansion with a length check: under `set -u`, bash 3.2
# (still the default on macOS) treats "${empty[@]}" as an unbound variable and
# aborts — which would fail the job on an all-green run.
if [ ${#flaky[@]} -gt 0 ]; then
    for name in "${flaky[@]}"; do
        echo "::warning title=Flaky live suite::$name failed then passed on retry"
    done
fi

if [ ${#failed[@]} -gt 0 ]; then
    for name in "${failed[@]}"; do
        echo "::error title=Live suite failed::$name failed twice"
    done
    exit 1
fi

exit 0
