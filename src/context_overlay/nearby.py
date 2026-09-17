"""Bounded, request-time spatial query. No game imports or retained snapshots."""

import bisect
import math

from context_overlay.model import copy_data, entity, envelope, field, new_id


MAX_RESULTS = 64
MAX_SCANNED = 10000


class NearbyError(ValueError):
    def __init__(self, code, message, details=None):
        self.code, self.details = code, details or {}
        super().__init__(message)


def validate(kinds, radius, metric, same_level, same_room, include_self, limit):
    if (not isinstance(kinds, (list, tuple)) or not 1 <= len(kinds) <= 2
            or any(kind not in ("sim", "object") for kind in kinds) or len(set(kinds)) != len(kinds)):
        raise NearbyError("invalid_request", "kinds must select distinct sim/object types")
    if radius is not None:
        try:
            valid = (not isinstance(radius, bool) and isinstance(radius, (int, float))
                     and math.isfinite(radius) and 0 <= radius <= 1000000)
        except OverflowError:
            valid = False
        if not valid:
            raise NearbyError("invalid_request", "radius must be finite and between 0 and 1000000 world units")
    if metric not in ("horizontal", "euclidean"):
        raise NearbyError("invalid_request", "metric must be horizontal or euclidean")
    if any(not isinstance(value, bool) for value in (same_level, same_room, include_self)):
        raise NearbyError("invalid_request", "Spatial flags must be booleans")
    if radius is None and not same_room:
        raise NearbyError("invalid_request", "Supply radius or require same_room")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        raise NearbyError("invalid_request", "limit must be an integer between 1 and 64")
    return {"kinds": list(kinds), "radius": float(radius) if radius is not None else None,
            "metric": metric, "same_level": same_level, "same_room": same_room,
            "include_self": include_self, "limit": limit, "unit": "game_world_units",
            "distance_basis": "entity_position", "order": "distance_then_kind_then_numeric_id"}


def _comparison(left, right, key):
    a, b = left[key], right[key]
    if a["status"] != "available" or b["status"] != "available":
        return field(status="unsupported", reason=key + "_unavailable", source="spatial_comparison")
    return field(a["value"] == b["value"], source="spatial_comparison." + key)


def _same_room(left, right):
    room = _comparison(left, right, "room")
    level = _comparison(left, right, "level")
    if room["status"] != "available" or level["status"] != "available":
        return field(status="unsupported", reason="room_or_level_unavailable", source="spatial_comparison")
    return field(room["value"] and level["value"], source="zone_id + level + game_room_id")


def _partial(spatial):
    return any(value.get("status") != "available" for value in spatial.values())


def collect(adapter, target, session_id, provenance, query):
    packet = envelope("nearby_entities", session_id)
    packet.update(request_id=new_id(), target=target, scope=adapter.scope(),
                  provenance=provenance, query=query, read_started=adapter.clock())
    origin = adapter.object_for(target)
    if origin is None:
        raise NearbyError("target_unavailable", "The center Sim has no current instance")
    if not adapter.in_scope(origin):
        raise NearbyError("target_out_of_scope", "The center Sim must be on the active lot")
    center = adapter.nearby_spatial(origin)
    center["room"] = adapter.nearby_room(origin, center)
    required = ["position"] + (["level"] if query["same_level"] or query["same_room"] else [])
    if query["same_room"]:
        required.append("room")
    unavailable = [key for key in required if center[key]["status"] != "available"]
    if unavailable:
        raise NearbyError("spatial_unavailable", "Required center spatial data is unavailable",
                          {"fields": unavailable, "spatial": center, "target": target})
    packet["origin"] = center
    coverage = {"complete": True, "enumeration_complete": True, "scanned_count": 0,
                "candidate_count": 0, "unresolved_count": 0, "reasons": {}, "scan_limit": MAX_SCANNED}
    selected, seen = [], set()
    matches = 0

    def gap(reason):
        coverage["unresolved_count"] += 1
        coverage["reasons"][reason] = coverage["reasons"].get(reason, 0) + 1

    try:
        for obj in adapter.nearby_objects():
            if coverage["scanned_count"] >= MAX_SCANNED:
                coverage["enumeration_complete"] = False
                coverage["reasons"]["scan_limit"] = 1
                break
            coverage["scanned_count"] += 1
            try:
                kind = "sim" if getattr(obj, "is_sim", False) else "object"
                if kind not in query["kinds"] or (obj is origin and not query["include_self"]):
                    continue
                if not adapter.nearby_eligible(obj):
                    continue
                identifier = int(obj.sim_info.sim_id if kind == "sim" else obj.id)
                key = (kind, identifier)
                if key in seen:
                    continue
                seen.add(key)
                coverage["candidate_count"] += 1
                spatial = adapter.nearby_spatial(obj)
                if spatial["position"]["status"] != "available":
                    gap("position_unavailable")
                    continue
                a, b = center["position"]["value"], spatial["position"]["value"]
                dx, dy, dz = b["x"] - a["x"], b["y"] - a["y"], b["z"] - a["z"]
                horizontal = math.hypot(dx, dz)
                distance = {"horizontal": horizontal, "vertical": abs(dy),
                            "euclidean": math.hypot(horizontal, dy)}
                if not all(math.isfinite(value) for value in distance.values()):
                    gap("distance_unavailable")
                    continue
                if query["radius"] is not None and distance[query["metric"]] > query["radius"]:
                    continue
                same_level = _comparison(center, spatial, "level")
                if query["same_level"]:
                    if same_level["status"] != "available":
                        gap("level_unavailable")
                        continue
                    if not same_level["value"]:
                        continue
                if query["same_room"]:
                    spatial["room"] = adapter.nearby_room(obj, spatial)
                    same_room = _same_room(center, spatial)
                    if same_room["status"] != "available":
                        gap("room_unavailable")
                        continue
                    if not same_room["value"]:
                        continue
                matches += 1
                # Retain at most limit game references, only for this synchronous call.
                rank = (distance[query["metric"]], kind, identifier)
                record = (rank, obj, spatial, distance, same_level)
                index = bisect.bisect_left([item[0] for item in selected], rank)
                if index < query["limit"]:
                    selected.insert(index, record)
                    if len(selected) > query["limit"]:
                        selected.pop()
            except Exception:
                gap("candidate_read_failed")
    except Exception:
        coverage["enumeration_complete"] = False
        coverage["reasons"]["enumeration_failed"] = 1

    results, partial = [], _partial(center)
    for rank, obj, spatial, distance, same_level in selected:
        if not query["same_room"]:
            spatial["room"] = adapter.nearby_room(obj, spatial)
        try:
            reference = adapter.reference(obj)
            identity_status = field(True, source="EAAdapter.reference")
        except Exception:
            reference = entity(rank[1], rank[2])
            identity_status = field(status="error", reason="identity_label_read_failed")
        partial = partial or _partial(spatial) or identity_status["status"] != "available"
        results.append({"entity": reference, "identity_status": identity_status, "distance": distance,
                        "spatial": spatial, "relative": {
                            "same_lot": field(True, source="active_lot_scope"),
                            "same_level": same_level, "same_room": _same_room(center, spatial),
                            "same_routing_surface": _comparison(center, spatial, "routing_surface")}})
    coverage["complete"] = coverage["enumeration_complete"] and coverage["unresolved_count"] == 0
    packet.update(results=results, count=len(results), matched_count=matches,
                  matched_count_exact=coverage["complete"], truncated=matches > query["limit"],
                  coverage=coverage, read_finished=adapter.clock(),
                  status="partial" if partial or not coverage["complete"] else "complete")
    return copy_data(packet)
