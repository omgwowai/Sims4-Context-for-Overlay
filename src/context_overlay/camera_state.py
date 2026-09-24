"""Observe camera synchronization without polling or retaining game objects."""

import time

from context_overlay.model import utc_now


def signature(camera):
    return (str(camera._zone_id),
            tuple(float(getattr(camera._camera_position, axis)) for axis in ("x", "y", "z")),
            tuple(float(getattr(camera._target_position, axis)) for axis in ("x", "y", "z")),
            camera._follow_mode)


class CameraSync:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_signature = None
        self.updated_at = None
        self.monotonic = None

    def observe(self, camera):
        # Invalidate first: an unsuccessful observation must not retain old timing.
        self.reset()
        self.last_signature = signature(camera)
        self.updated_at = utc_now()
        self.monotonic = time.monotonic()

    def freshness(self, camera):
        if self.last_signature is not None and signature(camera) == self.last_signature:
            return {"status": "observed", "updated_at": self.updated_at,
                    "age_seconds": max(0.0, time.monotonic() - self.monotonic),
                    "source": "camera.update after-call observation"}
        return {"status": "unknown", "updated_at": None, "age_seconds": None,
                "source": "EA camera state; may predate observation or come from save restoration"}


SYNC = CameraSync()


def install(hooks, camera, zone_class):
    SYNC.reset()
    hooks.after(camera, "update", lambda args, kwargs, result: SYNC.observe(camera))
    hooks.before(camera, "deserialize", lambda args, kwargs: SYNC.reset())
    hooks.before(zone_class, "on_teardown", lambda args, kwargs: SYNC.reset())
