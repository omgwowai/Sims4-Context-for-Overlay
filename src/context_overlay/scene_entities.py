"""Anchor-independent, bounded active-lot discovery (local archival extension).

Enumeration completeness describes membership/IDs, not spatial field quality.
No history, camera, persistence, or game mutation is involved in this read.
"""

import time

from context_overlay.model import copy_data, entity, envelope, field, new_id
from context_overlay.nearby import MAX_SCANNED


LOCAL_EXTENSION = "scene_archive/2"
MAX_ERRORS = 100
SPATIAL_FIELDS = ("position", "level", "routing_surface", "room")


def collect(adapter, session_id, provenance, kinds):
    packet = envelope("scene_entities", session_id)
    packet.update(request_id=new_id(), read_started=adapter.clock(),
                  read_started_unix_ms=time.time() * 1000,
                  provenance=provenance, local_extension=LOCAL_EXTENSION)
    started = time.monotonic()
    provider_scope = copy_data(adapter.scope())
    scope = dict(provider_scope, kind="active_lot_instantiated", off_lot="excluded",
                 uninstantiated="excluded", levels="all", rooms="all",
                 membership_source="GameObject.is_on_active_lot")
    packet.update(scope=scope, provider_scope=provider_scope,
                  query={"kinds": list(kinds), "anchor_required": False,
                         "order": "kind_then_numeric_id", "limit": MAX_SCANNED,
                         "radius": None, "result_limit": None})
    coverage = {"complete": False, "enumeration_complete": True, "data_complete": True,
                "scanned_count": 0, "candidate_count": 0, "duplicate_count": 0,
                "unresolved_count": 0, "excluded_count": 0,
                "reasons": {}, "scan_limit": MAX_SCANNED,
                "enumeration_source": "services.object_manager().values()",
                "errors": [], "errors_omitted_count": 0}
    results, seen = [], set()

    def issue(reason, exc=None, reference=None, unresolved=False):
        coverage["reasons"][reason] = coverage["reasons"].get(reason, 0) + 1
        if unresolved:
            coverage["unresolved_count"] += 1
        else:
            coverage["data_complete"] = False
        error = {"reason": reason, "entity": reference}
        if exc is not None:
            error.update(exception_type=type(exc).__name__, message=str(exc))
        if len(coverage["errors"]) < MAX_ERRORS:
            coverage["errors"].append(error)
        else:
            coverage["errors_omitted_count"] += 1

    try:
        for obj in adapter.scene_objects():
            if coverage["scanned_count"] >= MAX_SCANNED:
                coverage["enumeration_complete"] = False
                issue("scan_limit")
                break
            coverage["scanned_count"] += 1
            reference = None
            stage = "kind_read_failed"
            try:
                kind = "sim" if getattr(obj, "is_sim", False) else "object"
                if kind not in kinds:
                    coverage["excluded_count"] += 1
                    continue
                stage = "eligibility_read_failed"
                eligible = adapter.nearby_eligible(obj)
                if type(eligible) is not bool:
                    raise ValueError("World eligibility is not a boolean")
                if not eligible:
                    coverage["excluded_count"] += 1
                    continue
                stage = "identity_id_read_failed"
                identifier = obj.sim_info.sim_id if kind == "sim" else obj.id
                if (isinstance(identifier, bool) or not isinstance(identifier, (int, str))
                        or not str(identifier).isdigit() or not 0 < int(identifier) < 2 ** 64):
                    raise ValueError("Entity ID must be an unsigned 64-bit integer")
                reference = entity(kind, identifier)
                stage = "lot_membership_unavailable"
                on_lot = obj.is_on_active_lot()
                if type(on_lot) is not bool:
                    raise ValueError("Active-lot membership is not a boolean")
                if not on_lot:
                    coverage["excluded_count"] += 1
                    continue
            except Exception as exc:
                issue(stage, exc, reference, unresolved=True)
                continue
            if reference["key"] in seen:
                coverage["duplicate_count"] += 1
                continue
            seen.add(reference["key"])
            coverage["candidate_count"] += 1
            read_started = adapter.clock()
            wall_started = time.time() * 1000
            try:
                labelled = copy_data(adapter.reference(obj))
                if any(labelled.get(key) != reference[key] for key in ("key", "id", "kind")):
                    raise ValueError("Label reference changed the entity identity")
                reference = labelled
                identity_status = field(True, source="EAAdapter.reference")
            except Exception as exc:
                # A failed name lookup must never hide a known instance ID.
                identity_status = field(status="error", reason="identity_label_read_failed")
                issue("identity_label_read_failed", exc, reference)
            try:
                spatial = adapter.nearby_spatial(obj)
                spatial["room"] = adapter.nearby_room(obj, spatial)
                spatial = copy_data(spatial)
                if any(not isinstance(spatial.get(key), dict) for key in SPATIAL_FIELDS):
                    raise ValueError("Incomplete spatial packet")
            except Exception as exc:
                spatial = {key: field(status="error", reason="spatial_read_failed")
                           for key in SPATIAL_FIELDS}
                issue("spatial_read_failed", exc, reference)
            for key in SPATIAL_FIELDS:
                if spatial[key].get("status") != "available":
                    issue(key + "_unavailable", reference=reference)
            results.append({"entity": reference, "identity_status": identity_status,
                            "spatial": spatial, "read_started": read_started,
                            "read_finished": adapter.clock(),
                            "read_started_unix_ms": wall_started,
                            "read_finished_unix_ms": time.time() * 1000})
    except Exception as exc:
        coverage["enumeration_complete"] = False
        issue("enumeration_failed", exc)
    try:
        packet["provider_scope_finished"] = copy_data(adapter.scope())
        if packet["provider_scope_finished"] != provider_scope:
            coverage["enumeration_complete"] = False
            issue("scope_changed")
    except Exception as exc:
        coverage["enumeration_complete"] = False
        issue("scope_read_failed", exc)
    coverage["complete"] = coverage["enumeration_complete"] and not coverage["unresolved_count"]
    results.sort(key=lambda row: (row["entity"]["kind"], int(row["entity"]["id"])))
    packet.update(results=results, count=len(results), matched_count=len(results),
                  matched_count_exact=coverage["complete"],
                  truncated=not coverage["enumeration_complete"], coverage=coverage,
                  read_finished=adapter.clock(), read_finished_unix_ms=time.time() * 1000,
                  read_elapsed_ms=(time.monotonic() - started) * 1000,
                  status="complete" if coverage["complete"] and coverage["data_complete"] else "partial")
    return copy_data(packet)
