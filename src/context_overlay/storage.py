"""Bounded asynchronous persistence. A failed write is never acknowledged."""

import os
import queue
import shutil
import sys
import threading
import time
from pathlib import Path

from context_overlay.model import encode, utc_now


def atomic_json(path, value):
    """Small independent status files must survive a failed journal queue."""
    path = Path(path)
    pending = path.with_suffix(path.suffix + ".pending")
    with pending.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(encode(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(pending), str(path))


class StorageError(RuntimeError):
    pass


class StorageBusy(StorageError):
    """Recoverable producer rejection; the shared journal remains healthy."""
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
        self._durable_offset = 0
        self._error = None
        self._io_failed = False
        self._first_failure = None
        self._diagnostic_error = None
        self._last_diagnostic = 0
        self._batch_count = self._synced_records = 0
        self._queue_peak = self._memory_peak = 0
        self._last_accepted_at = self._last_durable_at = None
        self._inflight = None
        self._rejected = None
        self._accepted_exports = self._written_exports = 0
        self._max_bytes = max_bytes
        self._reserve_bytes = reserve_bytes
        self._queue_limit_bytes = queue_bytes
        self._pending_bytes = 0
        self._accepted_bytes = self.path.stat().st_size if self.path.exists() else 0
        self._written_bytes = self._accepted_bytes
        self._thread = threading.Thread(target=self._run, name="ContextOverlayWriter", daemon=True)
        self._thread.start()

    def _fail(self, message, kind="admission"):
        with self._lock:
            if kind == "io":
                self._io_failed = True
            if self._error is None:
                self._error = str(message)
                self._first_failure = {"recorded_at": utc_now(), "kind": kind, "message": self._error,
                                       "accepted_sequence": self._next_seq, "durable_sequence": self._durable_seq,
                                       "queued": self._queue.qsize(), "pending_bytes": self._pending_bytes}

    def _put(self, job, recoverable=False):
        disk_bytes = len(job[2].encode("utf-8")) + 1
        memory_bytes = sys.getsizeof(job[2]) + 128
        job = job + (disk_bytes, memory_bytes)
        with self._lock:
            if self._error or self._closing.is_set():
                raise StorageError(self._error or "Journal is closing")
            if self._accepted_bytes + disk_bytes > self._max_bytes:
                if recoverable:
                    raise StorageBusy("Run output byte budget reached")
                self._rejected = job
                self._fail("Run output byte budget reached; recording paused")
                raise StorageError(self._error)
            if self._pending_bytes + memory_bytes > self._queue_limit_bytes:
                if recoverable:
                    raise StorageBusy("Persistence queue byte budget reached")
                self._rejected = job
                self._fail("Persistence queue byte budget reached; recording paused")
                raise StorageError(self._error)
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                if recoverable:
                    raise StorageBusy("Persistence queue full")
                self._rejected = job
                self._fail("Persistence queue full; recording paused")
                raise StorageError(self._error)
            self._accepted_bytes += disk_bytes
            self._pending_bytes += memory_bytes
            self._queue_peak = max(self._queue_peak, self._queue.qsize())
            self._memory_peak = max(self._memory_peak, self._pending_bytes)
            self._last_accepted_at = utc_now()

    def append(self, record, recoverable=False):
        with self._lock:
            sequence = self._next_seq + 1
            item = dict(record, sequence=sequence)
            try:
                text = encode(item)
            except Exception as exc:
                if recoverable:
                    raise StorageBusy("Serialization failed: " + str(exc))
                self._fail("Serialization failed: " + str(exc))
                raise StorageError(self._error)
            self._put(("record", sequence, text), recoverable=recoverable)
            self._next_seq = sequence
            return sequence

    def export(self, packet):
        # Names come from our UUID request IDs, not arbitrary console paths.
        request_id = packet["request_id"]
        if len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
            raise ValueError("Invalid export request ID")
        text = encode(packet)
        with self._lock:
            self._put(("export", request_id, text))
            self._accepted_exports += 1
        return str(self.directory / ("context-" + request_id + ".json"))

    @staticmethod
    def _sync(stream):
        stream.flush()
        os.fsync(stream.fileno())

    def _run(self):
        deferred = None
        try:
            with self._opener(str(self.path), "a", encoding="utf-8", newline="\n") as stream:
                while deferred is not None or not self._closing.is_set() or not self._queue.empty():
                    # An admission failure stops producers, not the draining of
                    # previously accepted records. Only an I/O failure stops us.
                    if deferred is not None:
                        job, deferred = deferred, None
                    else:
                        try:
                            job = self._queue.get(timeout=0.1)
                        except queue.Empty:
                            self._diagnostics()
                            continue
                    kind, identity, text, disk_bytes, memory_bytes = job
                    batch = [job]
                    if kind == "record":
                        until = time.monotonic() + 0.02
                        while len(batch) < 128 and disk_bytes < 512 * 1024:
                            remaining = until - time.monotonic()
                            if remaining <= 0:
                                break
                            try:
                                candidate = self._queue.get(timeout=remaining)
                            except queue.Empty:
                                break
                            if candidate[0] != "record" or disk_bytes + candidate[3] > 512 * 1024:
                                deferred = candidate
                                break
                            batch.append(candidate)
                            disk_bytes += candidate[3]
                            memory_bytes += candidate[4]
                    self._inflight = batch
                    if shutil.disk_usage(str(self.directory)).free < self._reserve_bytes + disk_bytes:
                        raise StorageError("Disk reserve would be exceeded; recording paused")
                    if kind == "record":
                        stream.write("".join(item[2] + "\n" for item in batch))
                        self._sync(stream)
                    else:
                        destination = self.directory / ("context-" + identity + ".json")
                        pending = destination.with_suffix(".pending")
                        with self._opener(str(pending), "w", encoding="utf-8", newline="\n") as output:
                            output.write(text + "\n")
                            self._sync(output)
                        os.replace(str(pending), str(destination))
                    with self._lock:
                        self._pending_bytes -= memory_bytes
                        self._written_bytes += disk_bytes
                        if kind == "record":
                            self._durable_seq = batch[-1][1]
                            self._durable_offset = stream.tell()
                            self._batch_count += 1
                            self._synced_records += len(batch)
                        else:
                            self._written_exports += 1
                        self._last_durable_at = utc_now()
                    self._inflight = None
                    for _ in batch:
                        self._queue.task_done()
                    self._diagnostics()
        except Exception as exc:
            self._fail("Write failed: {}: {}".format(type(exc).__name__, exc), "io")
        finally:
            # Keep a dequeued export/batch boundary visible after an I/O failure.
            self._deferred = deferred
            self._diagnostics(force=True)

    def _diagnostics(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_diagnostic < 1:
            return
        try:
            atomic_json(self.directory / "persistence-status.json", dict(self.status(), updated_at=utc_now()))
            self._diagnostic_error = None
        except Exception as exc:
            self._diagnostic_error = "{}: {}".format(type(exc).__name__, exc)
        self._last_diagnostic = now

    def status(self):
        with self._lock:
            return {"state": "failed" if self._error else ("closing" if self._closing.is_set() else "ready"),
                    "accepted_sequence": self._next_seq, "durable_sequence": self._durable_seq,
                    "durable_byte_offset": self._durable_offset,
                    "queued": self._queue.qsize(), "inflight": self._inflight is not None,
                    "rejected_retained": self._rejected is not None,
                    "pending_bytes": self._pending_bytes, "queue_budget_bytes": self._queue_limit_bytes,
                    "accepted_output_bytes": self._accepted_bytes, "written_output_bytes": self._written_bytes,
                    "run_output_budget_bytes": self._max_bytes, "disk_reserve_bytes": self._reserve_bytes,
                    "first_failure": dict(self._first_failure) if self._first_failure else None,
                    "io_failed": self._io_failed, "diagnostic_error": self._diagnostic_error,
                    "queue_peak": self._queue_peak, "pending_bytes_peak": self._memory_peak,
                    "record_batches": self._batch_count, "synced_records": self._synced_records,
                    "last_accepted_at": self._last_accepted_at, "last_durable_at": self._last_durable_at,
                    "error": self._error, "pending_exports": self._accepted_exports - self._written_exports,
                    "written_exports": self._written_exports}

    def flush(self, timeout=5.0):
        """For offline checks / explicit shutdown, never for a per-frame callback."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.status()
            if state["pending_bytes"] == 0:
                if state["error"]:
                    raise StorageError(state["error"])
                return
            if state["io_failed"]:
                raise StorageError(state["error"])
            time.sleep(0.005)
        raise StorageError("Timed out waiting for persistence")

    def close(self, wait=False):
        self._closing.set()
        if wait:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise StorageError("Writer did not close")
        return self.status()
