#!/usr/bin/env bash
# Run every opt-in editor live suite and separate real failures from flakes.
#
# Each suite is gated behind its own RENFORGE_*_LIVE variable, read from the
# skipif marker in the test file itself so this script needs no list to keep in
# sync. A suite that fails is retried once: only a suite that fails twice is
# reported as a failure. Flaky suites are listed separately, because a job that
# goes red on noise gets ignored, and an ignored job is the problem this exists
# to solve.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

failed=()
flaky=()
passed=()

run_suite() {
    local file="$1" var="$2"
    env "$var=1" PYTHONPATH=src uv run pytest -q "$file" >/tmp/live_out 2>&1
}

for file in tests/test_editor_*_live.py; do
    name="$(basename "$file" .py)"
    var="$(grep -oE 'RENFORGE_[A-Z0-9_]+' "$file" | head -1)"
    if [ -z "$var" ]; then
        echo "::warning::$name has no RENFORGE_*_LIVE gate, skipped"
        continue
    fi

    echo "::group::$name ($var)"
    if run_suite "$file" "$var"; then
        tail -1 /tmp/live_out
        passed+=("$name")
    else
        tail -20 /tmp/live_out
        echo "--- retrying once ---"
        if run_suite "$file" "$var"; then
            tail -1 /tmp/live_out
            flaky+=("$name")
        else
            tail -20 /tmp/live_out
            failed+=("$name")
        fi
    fi
    echo "::endgroup::"
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
