"""Repeatable event traversal over immutable source bytes, without eager decoding."""

from collections.abc import Sequence


class EventSequence(Sequence):
    """Own only ordered IDs; the backing mapping resolves one event at a time.

    Used inside a derivation worker. Callers must not mutate source events while
    deriving. Public results are detached by the existing result-copy boundary.
    """
    def __init__(self, by_id, identifiers=None, checkpoint=lambda: None):
        self.by_id = by_id
        self.identifiers = tuple(by_id if identifiers is None else identifiers)
        self.checkpoint = checkpoint

    def __len__(self):
        return len(self.identifiers)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return EventSequence(self.by_id, self.identifiers[index], self.checkpoint)
        self.checkpoint()
        return self.by_id[self.identifiers[index]]

    def __iter__(self):
        for i, identifier in enumerate(self.identifiers):
            if i % 32 == 0:
                self.checkpoint()
            yield self.by_id[identifier]


def event_index(events):
    return events.by_id if isinstance(events, EventSequence) else {e["event_id"]: e for e in events}


def select_events(events, predicate):
    if isinstance(events, EventSequence):
        return EventSequence(events.by_id, (e["event_id"] for e in events if predicate(e)), events.checkpoint)
    return [e for e in events if predicate(e)]


def ordered_events(events, key):
    if isinstance(events, EventSequence):
        # Sort compact keys, preserving source order on ties just like sorted().
        rows = [(key(e), e["event_id"]) for e in events]
        rows.sort(key=lambda row: row[0])
        return EventSequence(events.by_id, (row[1] for row in rows), events.checkpoint)
    return sorted(events, key=key)
