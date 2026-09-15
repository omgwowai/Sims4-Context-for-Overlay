"""Call from your MOD's simulation-thread callback. No import-time game access.

Change the SDK import to your own vendored package when distributing your MOD.
"""

from context_overlay_client import Client


client = Client()


def find_nearby(center="active", radius=8.0, kinds=("sim", "object")):
    # Inspect coverage.complete as well as truncated; partial is not an empty success.
    return client.get_nearby_entities(center, kinds=kinds, radius=radius,
                                      same_level=True, limit=32)


def read_selected(nearby_packet, entity_key):
    # Keep the chosen identity and session; a later active Sim may be different.
    reference = next(row["entity"] for row in nearby_packet["results"]
                     if row["entity"]["key"] == entity_key)
    fields = (["identity", "location", "interactions", "needs", "buffs"]
              if reference["kind"] == "sim" else ["identity", "location", "object_states"])
    return client.get_context(reference["kind"], reference["id"], fields=fields,
                              include_history=False,
                              expected_session_id=nearby_packet["session_id"])


# This second read is newer than the nearby packet. If the consumer requires
# current proximity, rerun find_nearby; session equality alone does not prove it.
