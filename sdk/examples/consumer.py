"""Copy under your MOD namespace; replace my_overlay_mod with your package."""

from my_overlay_mod.vendor.context_overlay_client import Client, ContextOverlayError


client = Client()


def capture_on_game_thread(sim_id):
    """Invoke from your own interaction/alarm; then pass packet to your worker."""
    try:
        packet = client.get_context("sim", str(sim_id),
            fields=["identity", "time", "needs", "buffs", "interactions"], history_limit=15)
    except ContextOverlayError as exc:
        return {"available": False, "error": exc.to_dict()}
    return {"available": True, "partial": packet["status"] == "partial", "packet": packet}


def result_still_in_same_run_on_game_thread(packet):
    """Also check request_id/target freshness in your own UI before displaying."""
    status = client.get_status()
    return status["ready"] and status["session_id"] == packet["session_id"]


class HistoryPanelData:
    """Data-only example for a window owned by the consumer MOD."""
    def __init__(self):
        self.query = None

    def open_on_game_thread(self, sim_id, from_ticks=None):
        self.close_on_game_thread()
        self.query = client.history("sim", str(sim_id), page_size=15,
                                    event_types=["interaction"], from_ticks=from_ticks)
        return self.query.page

    def next_on_game_thread(self):
        return self.query.next_page() if self.query is not None else None

    def close_on_game_thread(self):
        if self.query is not None:
            self.query.close()
            self.query = None
