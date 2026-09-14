"""Bounded asynchronous persistence. A failed write is never acknowledged."""

import json
import hashlib
import os
import queue
import shutil
import sys
import threading
import time
from pathlib import Path

from context_overlay.model import encode


class StorageError(RuntimeError):
    pass


class Journal:
    def __init__(self, directory, capacity=2048, opener=None, max_bytes=2048 * 1024 * 1024,
                 reserve_bytes=1024 * 1024 * 1024, queue_bytes=32 * 1024 * 1024):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "journal.jsonl"
        self._queue = queue.Queue(maxsize=capacity)
        self._opener = opener or open
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._next_seq = 0
        self._durable_seq = 0
        self._error = None
        self._inflight = None
        self._rejected = None
        self._exports = {}
        self._max_bytes = max_bytes
        self._reserve_bytes = reserve_bytes
        self._queue_limit_bytes = queue_bytes
        self._pending_bytes = 0
        self._accepted_bytes = self.path.stat().st_size if self.path.exists() else 0
        self._written_bytes = self._accepted_bytes
        self._thread = threading.Thread(target=self._run, name="ContextOverlayWriter", daemon=True)
        self._thread.start()

    def _fail(self, message):
        with self._lock:
            if self._error is None:
                self._error = str(message)

    def _put(self, job):
        disk_bytes = len(job[2].encode("utf-8")) + 1
        memory_bytes = sys.getsizeof(job[2]) + 128
        job = job + (disk_bytes, memory_bytes)
        with self._lock:
            if self._error or self._closing.is_set():
                raise StorageError(self._error or "Journal is closing")
            if self._accepted_bytes + disk_bytes > self._max_bytes:
                self._rejected = job
                self._error = "Run output byte budget reached; recording paused"
                raise StorageError(self._error)
            if self._pending_bytes + memory_bytes > self._queue_limit_bytes:
                self._rejected = job
                self._error = "Persistence queue byte budget reached; recording paused"
                raise StorageError(self._error)
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                self._rejected = job
                self._error = "Persistence queue full; recording paused"
                raise StorageError(self._error)
            self._accepted_bytes += disk_bytes
            self._pending_bytes += memory_bytes

    def append(self, record):
        with self._lock:
            sequence = self._next_seq + 1
            item = dict(record, sequence=sequence)
            try:
                text = encode(item)
            except Exception as exc:
                self._fail("Serialization failed: " + str(exc))
                raise StorageError(self._error)
            self._put(("record", sequence, text))
            self._next_seq = sequence
            return sequence

    def export(self, packet):
        # Names come from our UUID request IDs, not arbitrary console paths.
        request_id = packet["request_id"]
        if len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
            raise ValueError("Invalid export request ID")
        text = encode(packet)
        with self._lock:
            self._exports[request_id] = "queued"
        self._put(("export", request_id, text))
        return str(self.directory / ("context-" + request_id + ".json"))

    @staticmethod
    def _sync(stream):
        stream.flush()
        os.fsync(stream.fileno())

    def _run(self):
        try:
            with self._opener(str(self.path), "a", encoding="utf-8", newline="\n") as stream:
                while not self._closing.is_set() or not self._queue.empty():
                    if self._error:
                        return
                    try:
                        job = self._queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    self._inflight = job
                    kind, identity, text, disk_bytes, memory_bytes = job
                    if shutil.disk_usage(str(self.directory)).free < self._reserve_bytes + disk_bytes:
                        raise StorageError("Disk reserve would be exceeded; recording paused")
                    if kind == "record":
                        stream.write(text + "\n")
                        self._sync(stream)
                        with self._lock:
                            self._durable_seq = identity
                    else:
                        destination = self.directory / ("context-" + identity + ".json")
                        pending = destination.with_suffix(".pending")
                        with self._opener(str(pending), "w", encoding="utf-8", newline="\n") as output:
                            output.write(text + "\n")
                            self._sync(output)
                        os.replace(str(pending), str(destination))
                        with self._lock:
                            self._exports[identity] = "written"
                    with self._lock:
                        self._pending_bytes -= memory_bytes
                        self._written_bytes += disk_bytes
                    self._inflight = None
                    self._queue.task_done()
        except Exception as exc:
            self._fail("Write failed: {}: {}".format(type(exc).__name__, exc))

    def status(self):
        with self._lock:
            return {"state": "failed" if self._error else ("closing" if self._closing.is_set() else "ready"),
                    "accepted_sequence": self._next_seq, "durable_sequence": self._durable_seq,
                    "queued": self._queue.qsize(), "inflight": self._inflight is not None,
                    "rejected_retained": self._rejected is not None,
                    "pending_bytes": self._pending_bytes, "queue_budget_bytes": self._queue_limit_bytes,
                    "accepted_output_bytes": self._accepted_bytes, "written_output_bytes": self._written_bytes,
                    "run_output_budget_bytes": self._max_bytes, "disk_reserve_bytes": self._reserve_bytes,
                    "error": self._error, "exports": dict(self._exports)}

    def flush(self, timeout=5.0):
        """For offline checks / explicit shutdown, never for a per-frame callback."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.status()
            if state["error"]:
                raise StorageError(state["error"])
            if (not state["queued"] and not state["inflight"]
                    and state["durable_sequence"] == state["accepted_sequence"]
                    and all(value == "written" for value in state["exports"].values())):
                return
            time.sleep(0.005)
        raise StorageError("Timed out waiting for persistence")

    def close(self, wait=False):
        self._closing.set()
        if wait:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise StorageError("Writer did not close")
        return self.status()


def replay(path, include_observations=True):
    """Strict replay: report damaged tails, conflicting sequences and sessions."""
    events = {}
    observations = []
    errors = []
    seen = {}
    session_id = None
    next_sequence = 1
    with open(str(path), "rb") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                record = json.loads(line.decode("utf-8"))
                sequence = record["sequence"]
                current_session = record["session_id"]
                if session_id is None:
                    session_id = current_session
                if current_session != session_id:
                    raise ValueError("Mixed sessions")
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
                    raise ValueError("Invalid sequence")
                digest = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")).digest()
                if sequence in seen:
                    if seen[sequence] != digest:
                        raise ValueError("Conflicting duplicate sequence")
                    continue
                if sequence != next_sequence:
                    raise ValueError("Missing or reordered sequence")
                seen[sequence] = digest
                next_sequence += 1
                if record["kind"] == "event_revision":
                    event = record["event"]
                    previous = events.get(event["event_id"])
                    if previous is not None and event["revision"] <= previous["revision"]:
                        raise ValueError("Non-increasing event revision")
                    events[event["event_id"]] = event
                elif include_observations:
                    observations.append(record)
            except (ValueError, KeyError, TypeError) as exc:
                errors.append({"line": line_number, "error": str(exc)})
                break
    return {"session_id": session_id, "events": list(events.values()),
            "observations": observations, "errors": errors, "complete": not errors}
