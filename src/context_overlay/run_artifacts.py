"""Bounded, atomic on-disk event layers; only plain data enters the worker."""

import hashlib
import os
from pathlib import Path
import shutil
import threading
import time

from context_overlay.event_views import CheckedList
from context_overlay.experience.experience_recap import build_recap, markdown
from context_overlay.model import copy_data, new_id, utc_now
from context_overlay.storage import atomic_json
from context_overlay.view_source import ViewError, packed, read_prefix


def export_layers(directory, session_id, head, targets, coverage, game_version, checkpoint,
                  source_limit=128 * 1024 * 1024, memory_limit=512 * 1024 * 1024,
                  output_limit=256 * 1024 * 1024):
    directory = Path(directory)
    root = directory / "views"
    root.mkdir(exist_ok=True)
    if head.get("durable_byte_offset", 0) > source_limit:
        raise ViewError("view_budget", "Journal prefix exceeds the export source budget")
    data = read_prefix(directory / "journal.jsonl", session_id, head.get("durable_sequence"),
                       head.get("durable_byte_offset"), checkpoint, memory_limit)
    if data["memory_bytes"] + 2 * data["latest_event_bytes"] > memory_limit:
        raise ViewError("view_budget", "Insufficient memory for exported experience views")
    suffix = new_id()
    pending = root / (".pending-" + suffix)
    destination = root / ("snapshot-{}-{}".format(data["as_of_sequence"], suffix[:8]))
    pending.mkdir()
    files, total = {}, [0]

    def write(name, pieces):
        target = pending / name
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        with target.open("wb") as stream:
            for piece in pieces:
                checkpoint()
                total[0] += len(piece)
                size += len(piece)
                if total[0] > output_limit:
                    raise ViewError("view_budget", "Export output exceeds the disk byte budget")
                stream.write(piece)
                digest.update(piece)
            stream.flush()
            os.fsync(stream.fileno())
        files[name] = {"bytes": size, "sha256": digest.hexdigest()}

    try:
        events = data["events"]
        write("events.jsonl", (packed(events[key]) + b"\n" for key in events))
        loaded = {"complete": True, "event_scope": "all", "session_id": session_id,
                  "sha256": data["sha256"], "events": CheckedList(events.values(), checkpoint)}
        people = []
        for key, name in sorted(targets.items()):
            checkpoint()
            identifier = key.partition(":")[2]
            if not key.startswith("sim:") or not identifier.isdigit():
                raise ValueError("Export targets must be Sim instance keys")
            selected = events.selected(key)
            row = {"entity_key": key, "name": name, "events": len(selected)}
            people.append(row)
            if not selected:
                row["state"] = "entity_not_recorded"
                continue
            bundle = build_recap(loaded, key, game_version)
            folder = "sim-" + identifier
            accounted = {r["event_id"] for r in bundle["audit"]["evidence"].values()}
            if not set(selected) <= accounted:
                raise ValueError("Organization omitted an input event from its audit")

            def organized():
                for uid, unit in bundle["units"].items():
                    yield packed({"item_id": uid, "kind": "unit", "lane": bundle["ledger"][uid]["lane"], "unit": unit}) + b"\n"
                for ref, evidence in bundle["audit"]["evidence"].items():
                    if not evidence["units"]:
                        yield packed({"item_id": "standalone:" + evidence["event_id"], "kind": "standalone",
                            "event_id": evidence["event_id"], "revision": evidence["revision"],
                            "category": evidence["semantic_role"], "recap_disposition": evidence["disposition"],
                            "evidence_ref": ref}) + b"\n"
            write(folder + "/organized.jsonl", organized())
            write(folder + "/recap.json", [packed(bundle["recap"]) + b"\n"])
            write(folder + "/details.bundle.json", [packed(bundle) + b"\n"])
            warning = ("采集正常结束；仅代表已接入事件的观测范围。" if coverage.get("capture_complete") is True else
                       "这只是固定截点的片段；采集尚未结束或存在缺口，不能作为完整一局。")
            write(folder + "/recap.md", [("> " + warning + "\n\n" + markdown(bundle)).encode("utf-8")])
            row.update(state="ready", folder=folder, organized=len(bundle["units"]) + sum(not r["units"] for r in bundle["audit"]["evidence"].values()),
                       recap=sum(len(bundle["recap"][section]) for section in
                           ("activities", "results", "relationship_observations", "states", "review_actions")),
                       snapshot_id=bundle["snapshot_id"])
        manifest = {"format": "run_event_layers_v1", "session_id": session_id, "created_at": utc_now(),
                    "source": {"path": "../../journal.jsonl", "sha256": data["sha256"],
                               "sequence": data["as_of_sequence"], "byte_offset": data["byte_offset"],
                               "records": len(data["records"]), "events": len(events)},
                    "coverage": coverage, "target_scope": "recorded_members_of_observed_active_households",
                    "people": people, "files": files}
        index = ["# 本次采集的分层输出", "", "采集完整：{}。来源截至 sequence {}。".format(
                 "是" if coverage.get("capture_complete") is True else "否／尚未结束", data["as_of_sequence"]),
                 "", "原始记录在 [journal.jsonl](../../journal.jsonl)，此快照的字节截点与哈希在 [manifest.json](manifest.json)。",
                 "完整最新事件：[events.jsonl](events.jsonl)。每个人物目录另含 organized.jsonl、recap.json 和 details.bundle.json。", ""]
        for person in people:
            label = str(person["name"] or person["entity_key"]).replace("[", "(").replace("]", ")").replace("\n", " ")
            index.append("- [{}]({}/recap.md)：{} 个关联事件，{} 个阅读项。".format(label, person["folder"], person["events"], person["recap"])
                         if person["state"] == "ready" else "- {}：此截点没有关联事件。".format(label))
        write("README.md", [("\n".join(index) + "\n").encode("utf-8")])
        atomic_json(pending / "manifest.json", manifest)
        checkpoint()
        os.replace(str(pending), str(destination))
        result = {"state": "ready", "session_id": session_id, "source_sequence": data["as_of_sequence"],
                  "source_sha256": data["sha256"], "directory": destination.name,
                  "index": destination.name + "/README.md", "capture_complete": coverage.get("capture_complete"),
                  "people": people, "output_bytes": total[0]}
        atomic_json(root / "latest.json", result)
        # Retain the previous successful snapshot too. Never touch raw logs.
        previous = sorted((p for p in root.iterdir() if p.is_dir() and p.name.startswith("snapshot-")),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        for old in previous[2:]:
            if old.resolve().parent == root.resolve():
                shutil.rmtree(str(old))
        return result
    finally:
        if pending.exists() and pending.resolve().parent == root.resolve() and pending.name.startswith(".pending-"):
            shutil.rmtree(str(pending))


class RunArtifacts:
    def __init__(self, directory, session_id, game_version, source_limit, memory_limit, seconds=120):
        self.directory, self.session_id, self.game_version = Path(directory), session_id, game_version
        self.source_limit, self.memory_limit, self.seconds = source_limit, memory_limit, seconds
        self._lock, self._cancel = threading.RLock(), threading.Event()
        self._thread = None
        self._status = {"state": "not_started"}

    def status(self):
        with self._lock:
            return copy_data(self._status)

    def start(self, head, targets, coverage):
        if self._thread is not None and self._thread.is_alive():
            return dict(self.status(), already_building=True)
        head, targets, coverage = copy_data([head, targets, coverage])
        self._cancel.clear()
        with self._lock:
            self._status = {"state": "building", "source_sequence": head.get("durable_sequence")}
        def run():
            started, yielded = time.monotonic(), [time.monotonic()]
            def checkpoint():
                now = time.monotonic()
                if self._cancel.is_set() or now - started > self.seconds:
                    raise ViewError("view_budget", "Export cancelled or time budget exceeded")
                if now - yielded[0] >= .003:
                    time.sleep(.001)
                    yielded[0] = time.monotonic()
            try:
                result = export_layers(self.directory, self.session_id, head, targets, coverage,
                                       self.game_version, checkpoint, self.source_limit, self.memory_limit)
            except Exception as exc:
                result = {"state": "failed", "source_sequence": head.get("durable_sequence"),
                          "error": {"code": getattr(exc, "code", "export_failed"), "message": str(exc)}}
            result["build_ms"] = round((time.monotonic() - started) * 1000, 3)
            try:
                atomic_json(self.directory / "view-export-status.json", result)
            except Exception as exc:
                result["diagnostic_error"] = str(exc)
            with self._lock:
                self._status = result
        self._thread = threading.Thread(target=run, name="ContextOverlayExports", daemon=True)
        self._thread.start()
        return self.status()

    def finish(self, head, targets, coverage):
        if self._thread is not None and self._thread.is_alive():
            self._cancel.set()
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("Previous view export did not cancel before shutdown")
        self.start(head, targets, coverage)
        self._thread.join(timeout=self.seconds + 5)
        if self._thread.is_alive():
            self._cancel.set()
            raise RuntimeError("Final view export did not finish before its deadline")
        return self.status()
