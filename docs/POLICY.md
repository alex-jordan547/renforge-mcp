# Runtime policy

MCP `ToolAnnotations` are **static, whole-tool hints**. They help a client
decide whether to offer `renforge_eval` or `renforge_control` at all. They
cannot say that `renforge_control(action="advance")` is a recoverable mutation
while `renforge_control(action="quit")` is destructive, or that a
`renforge_run_scenario` call is only as dangerous as its highest-risk step.

RenForge therefore evaluates an **operation-level policy** after the MCP
backend has validated the tool arguments and **before** the implementation
runs. Denied calls return a structured `POLICY_DENIED` payload and never reach
`live.eval_expr`, `live.control`, `live.saves`, or `live.run_scenario`.

## Risk classes

| Class | Meaning | Default when `RENFORGE_POLICY=enforce` |
| --- | --- | --- |
| `observational` | Read-only (for example `saves action=list`) | Allowed |
| `mutating` | Recoverable live-state change (`advance`, `save`) | Allowed |
| `destructive` | Replaces or discards live state (`quit`, `load`, `reload_script`, `quick_load`) | Denied unless authorized |
| `open_world` | Arbitrary Python / unconstrained side effects (`eval`, scenario `eval` steps) | Denied unless authorized |
| `malformed` | Parameters do not identify a known operation | Always denied in enforce mode |
| `unmanaged` | Tools outside this model | Allowed (compatibility) |

Covered tools:

- `renforge_control` — classified by `action`
- `renforge_saves` — classified by `action`
- `renforge_eval` — always `open_world` when `expr` is a non-empty string
- `renforge_run_scenario` — classified by the highest-risk step, including nested `control` actions

## Authorization

When enforcement is on, a denied operation can still run if:

1. The call sets `authorize=true` (MCP-client opt-in for **this** invocation), or
2. The operation id is listed in `RENFORGE_POLICY_ALLOW` (comma-separated), or
3. Its risk class is listed in `RENFORGE_POLICY_ALLOW_RISK`

Unknown / malformed operations **cannot** be authorized. Fix the parameters
instead.

Allowlist examples:

```text
RENFORGE_POLICY_ALLOW=renforge_eval,renforge_control.quit
RENFORGE_POLICY_ALLOW_RISK=destructive
```

A tool-level id such as `renforge_control` allows every classified action of
that tool. A scenario step uses ids such as `renforge_run_scenario.eval` or
`renforge_run_scenario.control.quit`.

Project file `<project>/.renforge/policy.json`:

```json
{
  "mode": "enforce",
  "allow": ["renforge_eval"],
  "allow_risk": []
}
```

`RENFORGE_POLICY` and `RENFORGE_POLICY_ALLOW*` environment variables override
the file. MCP clients can set them next to the `uvx` / `renforge serve`
command. Invalid mode strings and unreadable policy files **fail closed** to
`enforce`.

## Refusal payload

```json
{
  "ok": false,
  "error": "policy_denied",
  "code": "POLICY_DENIED",
  "policy": {
    "operation": "renforge_eval",
    "risk": "open_world",
    "reason": "Arbitrary Python can touch the filesystem, processes, network, and game state.",
    "next_step": "Retry with authorize=true, add this operation to RENFORGE_POLICY_ALLOW, or set RENFORGE_POLICY=off for trusted local automation.",
    "mode": "enforce",
    "decision": "deny"
  }
}
```

Activity log entries record `policy.operation`, `risk`, `decision`, and `mode`.
Sensitive parameters (`expr`, `steps`, `value`, `text`, `extra_info`) are
redacted on denied calls and on allowed destructive / open-world calls.

## Compatibility and release plan

| Release | Default | Behavior |
| --- | --- | --- |
| Current (0.7.x) | `RENFORGE_POLICY=off` | Classify and record; **never deny**. Existing agents and CI keep working. |
| Opt-in now | `RENFORGE_POLICY=enforce` | Destructive and open-world operations require `authorize=true` or an allowlist. |
| Later minor | Changelog notice before considering a default of `enforce` | Automation must set `RENFORGE_POLICY=off` or an allowlist before that switch. |

Do not fold this contract into metadata-only MCP annotation work. Annotations
remain discovery-time hints; this module is invocation-time enforcement.
