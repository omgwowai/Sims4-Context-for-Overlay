"""Invoke from the consumer MOD's simulation-thread callback, including when paused."""

from my_overlay_mod.vendor.context_overlay_client import Client


client = Client()


def observe_scene(vertical_fov=None, aspect_ratio=None, far=None):
    # None uses approximate FOV/aspect defaults; far=None adds no distance cutoff.
    # API errors (camera unavailable, zone mismatch, not ready) are not empty views.
    return client.get_camera_view(vertical_fov=vertical_fov, aspect_ratio=aspect_ratio, far=far)


def read_selected(view, entity_key):
    selected = next(row for row in view["results"] if row["entity"]["key"] == entity_key)
    reference = selected["entity"]
    # Scope of get_context is still the active lot, and this read occurs later.
    # Off-lot entities can have out_of_scope fields; the view is not a detail cache.
    return client.get_context(reference["kind"], reference["id"], include_history=False,
                              expected_session_id=view["session_id"])


# Inspect view['coverage']['complete'], not just view['count'] or ['truncated'].
# camera.freshness describes last observed sync, not the age of a rendered frame.
# query.approximate remains True even for complete enumeration and caller-set FOV.
