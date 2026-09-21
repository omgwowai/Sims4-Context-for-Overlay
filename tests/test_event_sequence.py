"""Lazy traversal must preserve eager semantics and release decoded source bodies."""

import copy
import gc
import hashlib
import json
from collections.abc import Mapping
import unittest
import weakref

from support import ROOT
from test_filter_events import repeated_micro_fixture
from context_overlay.experience.event_sequence import EventSequence, ordered_events, select_events
from context_overlay.experience.filter_events import compact_size, filter_events
from context_overlay.experience.experience_recap import build_recap, digest


class Event(dict):
    pass


class FreshEvents(Mapping):
    def __init__(self, events):
        self.raw = {e['event_id']: json.dumps(e) for e in events}
        self.references = []

    def __len__(self):
        return len(self.raw)

    def __iter__(self):
        return iter(self.raw)

    def __getitem__(self, key):
        value = Event(json.loads(self.raw[key]))
        self.references.append(weakref.ref(value))
        return value


class EventSequenceTests(unittest.TestCase):
    def test_sort_select_and_slice_do_not_retain_decoded_event_bodies(self):
        original = repeated_micro_fixture()
        backing = FreshEvents(original)
        checks = []
        events = EventSequence(backing, checkpoint=lambda: checks.append(True))
        self.assertEqual(backing.references, [])
        ordered = ordered_events(events, lambda e: 0)  # Stable ties preserve source order.
        selected = select_events(ordered, lambda e: True)
        self.assertEqual(list(selected[:2]), original[:2])
        self.assertEqual(list(ordered), original)
        self.assertEqual(list(ordered), original)
        gc.collect()
        self.assertTrue(all(ref() is None for ref in backing.references))
        self.assertTrue(checks)

    def test_streamed_hash_size_filter_and_recap_match_eager_input(self):
        original = repeated_micro_fixture()
        events = EventSequence(FreshEvents(original))
        self.assertEqual(compact_size(events), compact_size(original))
        expected_hash = hashlib.sha256(json.dumps(original, ensure_ascii=False, allow_nan=False,
            separators=(',', ':'), sort_keys=True).encode('utf-8')).hexdigest()
        self.assertEqual(digest(events), expected_hash)
        self.assertEqual(filter_events(events, 'run'), filter_events(original, 'run'))
        source = {'complete': True, 'event_scope': 'all', 'session_id': 'run', 'sha256': 'a' * 64}
        eager = build_recap(dict(source, events=original), 'sim:1', '1.126.73.1030')
        lazy = build_recap(dict(source, events=events), 'sim:1', '1.126.73.1030')
        self.assertEqual(lazy, eager)
        stable = copy.deepcopy(eager)
        eager['audit']['evidence'].clear()
        original[0]['facts']['tuning_name'] = 'changed'
        self.assertEqual(build_recap(dict(source, events=events), 'sim:1', '1.126.73.1030'), stable)

    def test_cancellation_is_checked_during_repeated_traversal(self):
        def cancelled():
            raise RuntimeError('cancelled')
        events = EventSequence(FreshEvents(repeated_micro_fixture()), checkpoint=cancelled)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            list(events)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            ordered_events(events, lambda e: 0)

    def test_duplicate_ids_are_rejected_before_index_reuse(self):
        original = repeated_micro_fixture()
        backing = FreshEvents(original)
        events = EventSequence(backing, [original[0]['event_id']] * 2)
        with self.assertRaisesRegex(ValueError, 'unique flat latest'):
            filter_events(events, 'run')
