"""Script loader entry point; installation is idempotent."""

import traceback
from context_overlay.game_runtime import initialize, log

log("BOOTSTRAP")
try:
    initialize()
except Exception:
    log("INITIALIZATION FAILED: " + traceback.format_exc())
