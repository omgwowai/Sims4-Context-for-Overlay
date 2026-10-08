"""Offline regression cases for sends, event-backed Context and derived evidence."""

import copy
import gc
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from support import MemoryJournal
from context_overlay.balloons import BalloonCapture, SOURCE, proto_snapshot
from context_overlay.collector import Collector
from context_overlay.ea_adapter import EAAdapter
from context_overlay.hooks import Hooks
from context_overlay.model import entity
from context_overlay.recorder import Recorder
from context_overlay.experience.experience_view import build_experiences
from context_overlay.experience.experience_recap import markdown, resolve
from context_overlay.experience.experience_quality import quality_report
from test_experience_recap import make
from test_experience_view import action
from test_filter_events import event


class Request:
    def __init__(self, sim):
        self._sim = sim
        self.icon = SimpleNamespace(type=1, group=2, instance=18446744073709550001)
        self.icon_object = self.overlay = self.category_icon = self.icon_info = self.rel_track = None
        self.view_offset = None
        self.balloon_type, self.priority, self.duration = 7, 1, 3.0
        self.delay, self.delay_randomization = None, 0


class SlottedElement:
    __slots__ = ("_element_handle", "_parent_handle")


class SlottedRequest(SlottedElement):
    # EA BalloonRequest and its Element base have no __weakref__ or __dict__.
    __slots__ = ("_sim_ref", "icon", "icon_object", "overlay", "balloon_type",
                 "priority", "duration", "delay", "delay_randomization",
                 "category_icon", "view_offset", "rel_track", "icon_info")

    def __init__(self, sim):
        self._sim_ref = lambda: sim
        for key, value in vars(Request(sim)).items():
            if key != "_sim":
                setattr(self, key, value)

    @property
    def _sim(self):
        return self._sim_ref()


def balloon(identifier="balloon", cause=None):
    result = event(identifier, "balloon.sent", {
        "balloon_type": {"name": "THOUGHT", "value": 7},
        "icon": {"type": "1", "group": "2", "instance": "18446744073709550001"},
        "client_visibility": "unverified", "icon_semantics": "unmapped"})
    result.update(roles=[{"role": "subject", "entity_key": "sim:1"}], cause=cause)
    return result


class BalloonCaptureTests(unittest.TestCase):
    def setUp(self):
        self.sim = SimpleNamespace(id=1, is_sim=True, local=True)
        self.journal = MemoryJournal()
        self.recorder = Recorder(self.journal, "run")
        self.recorder.begin_zone({"zone_id": "1"})
        self.cause = None
        self.adapter = SimpleNamespace(
            reference=lambda sim: entity("sim", sim.id, "Sim"),
            event_reference=lambda obj: entity("sim", obj.id, "Icon subject"),
            in_scope=lambda obj: obj.local,
            sim_minutes_to_ticks=lambda minutes: int(minutes * 1500),
            clock=lambda: {"ticks": "100"}, scope=lambda: {"zone_id": "1"})
        self.runtime = SimpleNamespace(adapter=self.adapter, recorder=self.recorder, closed=False,
            session_id="run", sources=SimpleNamespace(frames=[], cause=lambda **kw: self.cause),
            fail=self.recorder.fail)
        self.capture = BalloonCapture(self.runtime)
        self.capture.coverage["send"] = {"state": "installed"}
        self.capture.type_names = {7: "THOUGHT"}

    def send(self, request=None, result=True):
        self.capture.on_distribute((request or Request(self.sim),), {}, result)

    def current(self):
        return self.capture.read(entity("sim", 1))["value"]

    def test_only_successful_local_sends_create_events(self):
        self.send(result=False)
        self.send(result=None)
        self.sim.local = False
        self.send()
        self.sim.local = True
        self.runtime.sources.frames.append({"from_load": True})
        self.send()
        self.runtime.sources.frames.clear()
        self.assertEqual(self.journal.records, [])
        request = Request(self.sim)
        request.icon_object = SimpleNamespace(id=2, icon_info=(2, 99))
        self.send(request)
        row = self.journal.records[0]["event"]
        self.assertEqual(row["category"], "balloon.sent")
        self.assertEqual(row["entities"], ["sim:1"])
        self.assertEqual(row["payload"]["icon"]["instance"], "18446744073709550001")
        self.assertEqual(row["payload"]["client_visibility"], "unverified")
        self.assertEqual(self.current()["events"][0]["event_id"], row["event_id"])

    def test_context_uses_recorded_events_and_reports_fifo_gap_without_writes(self):
        self.recorder.enabled = False
        self.send()
        self.assertEqual(self.journal.records, [])
        self.assertEqual(self.capture.read(entity("sim", 1))["status"], "disabled")
        self.recorder.enabled = True
        self.send()
        observed = self.current()["events"][0]
        observed["payload"]["icon"]["instance"] = "changed"
        self.assertNotEqual(self.current()["events"][0]["payload"]["icon"]["instance"], "changed")
        before = len(self.journal.records)
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.balloon_capture = self.capture
        adapter.object_for = lambda target: self.sim
        adapter.in_scope = self.adapter.in_scope
        adapter.reference = self.adapter.reference
        adapter.clock, adapter.scope = self.adapter.clock, self.adapter.scope
        adapter.sim_minutes_to_ticks = self.adapter.sim_minutes_to_ticks
        packet = Collector(adapter, self.recorder).collect(entity("sim", 1), fields=["balloons"], include_history=False)
        self.assertEqual(packet["snapshot"]["balloons"]["value"]["events"][0]["event_id"], observed["event_id"])
        self.assertIn("未确认屏幕显示", packet["rendered"]["current"][0]["text"])
        self.assertEqual(len(self.journal.records), before)
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)
        self.recorder.index.remove(observed["event_id"])
        missing = self.current()
        self.assertEqual(missing["events"], [])
        self.assertTrue(missing["retention_gap"])
        self.assertFalse(missing["complete"])
        self.assertEqual(missing["durable_query"]["fields"], ["balloon.sent"])
        self.sim.local = False
        self.assertEqual(adapter.read(entity("sim", 1), "balloons")["status"], "out_of_scope")

    def test_query_limit_and_zone_lifetime_do_not_delete_records(self):
        for _ in range(55):
            self.send()
        value = self.capture.read(entity("sim", 1), {"limit": 2})["value"]
        self.assertEqual(len(value["events"]), 2)
        self.assertEqual(value["total_matches"], 55)
        self.assertTrue(value["has_more"])
        options = dict(value["history_query"])
        options.pop("kind")
        options.pop("identifier")
        page = self.recorder.query_history("sim:1", **options)
        rows = list(page["events"])
        while page["next_cursor"]:
            page = self.recorder.history_page(page["next_cursor"])
            rows += page["events"]
        self.recorder.close_query(page["cursor"])
        self.assertEqual(len(rows), 55)
        self.assertEqual(len({e["event_id"] for e in rows}), 55)
        self.capture.close()
        self.assertFalse(hasattr(self.capture, "rows"))
        self.assertEqual(len(self.recorder.events), 55)
        self.recorder.begin_zone({"zone_id": "2"})
        other = BalloonCapture(self.runtime)
        other.coverage["send"] = {"state": "installed"}
        self.assertEqual(other.read(entity("sim", 1))["value"]["events"], [])

    def test_partial_fields_and_unknown_types_remain_observed(self):
        request = Request(self.sim)
        request.icon = object()
        request.balloon_type = 999
        self.send(request)
        payload = self.current()["events"][0]["payload"]
        self.assertEqual(payload["balloon_type"], {"value": 999, "name": None})
        self.assertIn("icon", [row["field"] for row in payload["unavailable_fields"]])
        self.assertEqual(len(self.recorder.events), 1)

    def test_sentiment_request_accepts_unset_random_delay_and_keeps_track(self):
        request = SlottedRequest(self.sim)
        request.balloon_type = 3
        request.delay = request.delay_randomization = None
        request.rel_track = SimpleNamespace(guid64=247956)
        self.adapter.resource = lambda value, **kw: {"id": str(value.guid64), "resource_kind": kw["resource_kind"]}
        self.capture.type_names[3] = "SENTIMENT"
        self.send(request)
        payload = self.current()["events"][0]["payload"]
        self.assertIsNone(payload["delay_seconds"])
        self.assertIsNone(payload["delay_randomization_seconds"])
        self.assertNotIn("unavailable_fields", payload)
        self.assertEqual(payload["relationship_track"]["id"], "247956")
        self.assertEqual(payload["balloon_type"]["name"], "SENTIMENT")
        request.delay_randomization = "invalid"
        self.send(request)
        self.assertIn("delay_randomization", [r["field"] for r in self.current()["events"][0]["payload"]["unavailable_fields"]])

    def test_failed_recorder_has_no_independent_context_copy(self):
        self.recorder.fail("disk unavailable")
        for identifier in (1, 2, 3):
            self.send(Request(SimpleNamespace(id=identifier, is_sim=True, local=True)))
        self.assertEqual(self.capture.read(entity("sim", 1))["status"], "error")
        self.assertFalse(hasattr(self.capture, "rows"))
        self.assertEqual(self.journal.records, [])

    def test_window_is_game_time_based_half_open_and_subject_only(self):
        now = [10000]
        self.adapter.clock = lambda: {"ticks": str(now[0])}
        self.send()
        first = next(iter(self.recorder.events))
        now[0] = 11000
        self.cause = {"event_id": "run:chat", "actor": entity("sim", 1)}
        self.send(Request(SimpleNamespace(id=2, is_sim=True, local=True)))
        self.cause = None
        self.send()
        interval = self.capture.read(entity("sim", 1), {"from_ticks": 10000, "to_ticks": 11000})["value"]
        self.assertEqual([e["event_id"] for e in interval["events"]], [first])
        now[0] = 12000
        recent = self.capture.read(entity("sim", 1), {"past_sim_minutes": 1})["value"]
        self.assertEqual(len(recent["events"]), 1)
        self.assertEqual(recent["events"][0]["first_observed_time"]["ticks"], "11000")
        self.assertEqual(self.current()["total_matches"], 2)
        # A paused game keeps the same window, irrespective of wall-clock time.
        with patch("context_overlay.balloons.time.monotonic", return_value=999999):
            self.assertEqual(self.current()["total_matches"], 2)
        now[0] = 30000
        self.assertEqual(self.current()["events"], [])

    def test_creation_provenance_survives_delay_without_keeping_request_alive(self):
        request = Request(self.sim)
        self.cause = {"event_id": "run:chat", "actor": entity("sim", 1), "basis": "resolver.interaction"}
        self.capture.on_requests((object(),), {}, [request])
        self.cause = None
        self.send(request)
        self.assertEqual(self.current()["events"][0]["cause"]["event_id"], "run:chat")
        del request
        gc.collect()
        self.assertEqual(len(self.capture.pending), 0)
        self.assertEqual(self.capture.pending_bytes, 0)

    def test_ea_slotted_delayed_request_preserves_each_send_and_exact_cause(self):
        request = SlottedRequest(self.sim)
        self.cause = {"event_id": "run:chat", "actor": entity("sim", 1), "basis": "resolver.interaction"}
        self.capture.on_requests((object(),), {}, [request])
        self.cause["event_id"] = "mutated"
        self.cause = None
        self.send(request)
        self.send(request)
        self.send(SlottedRequest(self.sim))
        rows = self.current()["events"]
        self.assertIsNone(rows[0]["cause"])
        self.assertEqual([row["cause"]["event_id"] for row in rows[1:]], ["run:chat", "run:chat"])
        self.assertEqual(len({row["event_id"] for row in rows}), 3)
        status = self.capture.status()
        self.assertEqual(status["provenance_unavailable"], 0)
        self.assertEqual(status["pending_strong_references"], 1)
        self.assertEqual(status["provenance_strong_fallbacks"], 1)
        self.capture.close()
        self.assertEqual(self.capture.pending, {})
        self.assertEqual(self.capture.pending_bytes, 0)

    def test_provenance_expires_without_extending_on_send_or_losing_send_facts(self):
        request = SlottedRequest(self.sim)
        self.cause = {"event_id": "run:chat"}
        with patch("context_overlay.balloons.time.monotonic", return_value=100):
            self.capture.on_requests((), {}, [request])
        self.cause = None
        with patch("context_overlay.balloons.time.monotonic", return_value=399):
            self.send(request)
        with patch("context_overlay.balloons.time.monotonic", return_value=400):
            self.capture.prune_pending()
            self.send(request)
        self.assertEqual(self.capture.pending, {})
        self.assertEqual(self.capture.pending_bytes, 0)
        self.assertEqual(self.capture.status()["provenance_expired"], 1)
        self.assertIsNone(self.current()["events"][0]["cause"])
        self.assertEqual(len(self.recorder.events), 2)

    def test_provenance_capacity_and_metadata_budget_release_old_requests(self):
        self.cause = {"event_id": "run:chat"}
        requests = [SlottedRequest(self.sim) for _ in range(3)]
        with patch("context_overlay.balloons.PENDING_CAPACITY", 2):
            self.capture.on_requests((), {}, requests)
        self.assertIsNone(self.capture.request_provenance(requests[0]))
        self.assertEqual(len(self.capture.pending), 2)
        self.assertEqual(self.capture.provenance_evicted, 1)
        # Same-instance replacement doesn't grow the byte charge.
        before = self.capture.pending_bytes
        self.capture.on_requests((), {}, [requests[1]])
        self.assertEqual(self.capture.pending_bytes, before)
        self.capture.close()
        with patch("context_overlay.balloons.PENDING_BYTES", 1):
            self.capture.on_requests((), {}, requests)
        self.assertEqual(self.capture.pending_bytes, 0)
        self.assertEqual(len(self.capture.pending), 0)
        self.assertEqual(self.capture.provenance_evicted, 4)

    def test_weak_request_identity_does_not_use_equality_or_hash(self):
        class EqualRequest(Request):
            __hash__ = None

            def __eq__(self, other):
                return True

        first, second = EqualRequest(self.sim), EqualRequest(self.sim)
        self.cause = {"event_id": "run:chat"}
        self.capture.on_requests((), {}, [first])
        self.cause = None
        self.send(second)
        self.assertIsNone(self.current()["events"][0]["cause"])
        self.send(first)
        self.assertEqual(self.current()["events"][0]["cause"]["event_id"], "run:chat")
        del first
        gc.collect()
        self.assertEqual(len(self.capture.pending), 0)

    def test_delayed_loading_request_is_not_reclassified_after_load_frame_ends(self):
        request = SlottedRequest(self.sim)
        self.runtime.sources.frames.append({"from_load": True})
        self.capture.on_requests((), {}, [request])
        self.runtime.sources.frames.clear()
        self.send(request)
        self.assertEqual(self.current()["events"], [])
        self.assertEqual(self.journal.records, [])
        self.send(SlottedRequest(self.sim))
        self.assertEqual(len(self.current()["events"]), 1)

    def test_hook_preserves_return_and_game_exception(self):
        class Sender(Request):
            def distribute(self):
                if self.priority < 0:
                    raise ValueError("game failure")
                return self.priority == 1
        hooks = Hooks(self.recorder.fail)
        hooks.after(Sender, "distribute", self.capture.on_distribute)
        self.addCleanup(hooks.remove)
        request = Sender(self.sim)
        self.assertIs(request.distribute(), True)
        request.priority = 0
        self.assertIs(request.distribute(), False)
        request.priority = -1
        with self.assertRaisesRegex(ValueError, "game failure"):
            request.distribute()
        self.assertEqual(len(self.recorder.events), 1)

    def test_proto_keeps_large_ids_and_reports_repeated_truncation(self):
        proto = SimpleNamespace(ListFields=lambda: [
            (SimpleNamespace(name="object_id", type=4, label=1), 18446744073709550001),
            (SimpleNamespace(name="values", type=5, label=3), list(range(35)))])
        value = proto_snapshot(proto)
        self.assertEqual(value["object_id"], "18446744073709550001")
        self.assertEqual(len(value["values"]), 32)
        self.assertEqual(value["_truncated_fields"], ["values"])


class BalloonViewTests(unittest.TestCase):
    def test_linked_and_unlinked_balloons_keep_each_occurrence_and_evidence(self):
        parent = action("eat", ("13433", "generic_consume_food"))
        linked = balloon(cause={"event_id": parent["event_id"], "actor": {"key": "sim:1"}})
        unlinked = balloon("independent")
        events = [parent, linked, unlinked]
        result = build_experiences(events, "run", "sim:1")
        signals = [u for u in result["organized"]["facts"] if u["type"] == "balloon.sent"]
        self.assertEqual(len(signals), 2)
        self.assertEqual(sum("activity" in u for u in signals), 1)
        self.assertEqual(result["consumer_packet"]["activities"][0]["results"], [])
        self.assertEqual(len(result["consumer_packet"]["activities"][0]["balloons"]), 1)
        bundle = make(events)
        rows = bundle["recap"]["balloons"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(bundle["recap"]["results"], [])
        for row in rows:
            evidence = resolve(bundle, bundle["snapshot_id"], row["ref"], "evidence")["items"]
            self.assertEqual(len(evidence), 1)
        self.assertIn("气泡请求", markdown(bundle))
        self.assertEqual(quality_report(bundle)["totals"]["recap_sections"]["balloons"], 2)
        cross_visit = copy.deepcopy(linked)
        cross_visit["zone_visit"] = 2
        units = build_experiences([parent, cross_visit], "run")["organized"]["facts"]
        self.assertNotIn("activity", units[0])


if __name__ == "__main__":
    unittest.main()
