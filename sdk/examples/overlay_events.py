"""Copy into your MOD, adjust the import, and invoke only on the game thread.

No model, network, scheduler or UI dependencies. Producer payload is private.
"""

from my_overlay_mod.vendor.context_overlay_client import Client


client = Client()
PRODUCER = "example.overlay"


def record_result(context_packet, payload, submission_id, associate=True):
    """Keep submission_id and content identical when retrying a busy write.

    Your callback must also decide whether this result is still timely.
    Travel preserves session_id; compare the input scope/time to current
    context when the result only applies to the lot where it was requested.
    session_changed means discard the old result, never retarget it to a new run.
    """
    return client.append_event(PRODUCER, payload,
        entities=[context_packet["target"]["key"]] if associate else [],
        idempotency_key=submission_id, expected_session_id=context_packet["session_id"])


def read_own_global_records(session_id):
    """Includes records without entities; bounded by provider query budgets."""
    with client.history(None, None, producers=[PRODUCER], expected_session_id=session_id) as query:
        while True:
            yield query.page["history"]["events"]
            if not query.has_more:
                break
            query.next_page()


class IncrementalReader:
    """Call step from your alarm/UI callback; at most one page per callback.

    handle(events) should be idempotent by (event_id, revision). A failure does
    not commit progress. On history_gap, explicitly choose reset('retained') or
    reset('now'); never silently skip. On session_changed create a new reader
    for the new session. Close before abandoning this reader.
    During travel, wait for ready and keep the checkpoint if session matches.
    If a page expires, call retry_batch() to replay from the last committed
    checkpoint; handlers must tolerate already-processed revisions. reset()
    deliberately discards the checkpoint and is for an explicit fresh start.
    """
    def __init__(self, session_id, origins=None, producers=None, start="retained"):
        self.session_id = session_id
        self.filters = dict(origins=origins, producers=producers)
        self.start, self.checkpoint, self.batch = start, None, None
        self.advance = False

    def step(self, handle):
        if self.batch is None:
            options = dict(self.filters, start=self.start) if self.checkpoint is None else {}
            self.batch = client.changes(self.checkpoint, expected_session_id=self.session_id,
                                        page_size=50, representation="raw", **options)
        if self.advance:
            self.batch.next_page()
            self.advance = False
        handle(self.batch.page["history"]["events"])
        if self.batch.has_more:
            self.advance = True
            return False
        completed = self.batch.checkpoint
        self.batch.close()
        self.checkpoint, self.batch = completed, None
        return True

    def close(self):
        if self.batch is not None:
            self.batch.close()
            self.batch = None
        self.advance = False

    def retry_batch(self):
        """Release an expired/abandoned batch while keeping committed progress."""
        self.close()

    def reset(self, start="retained"):
        if start not in ("retained", "now"):
            raise ValueError("Choose retained or now explicitly")
        self.close()
        self.checkpoint, self.start = None, start


def read_game_context(sim_id):
    return client.get_context("sim", str(sim_id), origins=["game"], history_limit=15)
