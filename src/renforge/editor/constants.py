from __future__ import annotations

from typing import Final

PROTOCOL_NAME: Final = "renforge-editor"
PROTOCOL_VERSION: Final = 1

AUTH_FRAME_MAX_BYTES: Final = 4 * 1024
COMMAND_FRAME_MAX_BYTES: Final = 1024 * 1024
MAX_STRING_BYTES: Final = 4 * 1024
MAX_PATH_BYTES: Final = 1024
MAX_INTENTS: Final = 256
MAX_DIAGNOSTICS_BYTES: Final = 64 * 1024

TRANSACTION_DIRNAME: Final = "editor-transactions"
RENFORGE_DIRNAME: Final = ".renforge"

COMMIT_STATES: Final = {
    "staged",
    "publishing",
    "published",
    "committed",
    "rolled_back",
    "rollback_conflict",
    "failed",
}

