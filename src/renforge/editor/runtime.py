from __future__ import annotations

from typing import Any, Protocol


class RuntimeProbe(Protocol):
    def observe(self, runtime_key: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        """Return an independent observation for a runtime key."""

    def attest(
        self,
        *,
        transaction_id: str,
        script_generation: int,
        deadline: float,
        expected_targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Perform reload attestation for a published transaction."""

