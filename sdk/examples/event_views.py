"""Call tick() once per MOD/UI callback; never block waiting for a view.

Vendor context_overlay_client under your own MOD namespace. Keep the request
open while opening another layer with its source_snapshot_id.
"""

from my_overlay_mod.vendor.context_overlay_client import Client


class EventViewReader:
    def __init__(self, sim_id, session_id, on_page, on_error, view="recap", source_snapshot_id=None):
        self.client = Client()
        self.session_id = session_id
        self.on_page, self.on_error = on_page, on_error
        self.request = self.client.query_event_view(view, "sim", str(sim_id), expected_session_id=session_id,
                                                   source_snapshot_id=source_snapshot_id, page_size=20)
        self.cursor = None
        self.complete = self.closed = False

    def tick(self):
        if self.complete or self.closed:
            return
        if self.request["state"] == "building":
            self.request = self.client.get_event_view_status(self.request["request_id"], expected_session_id=self.session_id)
            return
        if self.request["state"] == "failed":
            self.on_error(self.request["error"])
            self.complete = True
            return
        page = self.client.get_event_view_page(self.cursor or self.request["cursor"], expected_session_id=self.session_id)
        self.on_page(page)
        self.cursor = page["next_cursor"]
        self.complete = self.cursor is None

    def close(self):
        if not self.closed:
            self.client.close_event_view(self.request["request_id"], expected_session_id=self.session_id)
            self.closed = True
