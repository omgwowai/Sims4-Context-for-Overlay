"""Request-time approximate frustum query; no EA imports or retained snapshots."""

import math
import time

from context_overlay.model import copy_data, entity, envelope, field, new_id


DEFAULT_VERTICAL_FOV = 45.0
DEFAULT_ASPECT_RATIO = 16.0 / 9.0
MAX_SCANNED = 10000


class CameraViewError(ValueError):
    def __init__(self, code, message, details=None):
        self.code, self.details = code, details or {}
        super().__init__(message)


def _finite(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        return False


def validate(kinds, vertical_fov, aspect_ratio, far):
    if (not isinstance(kinds, (list, tuple)) or not 1 <= len(kinds) <= 2
            or any(kind not in ("sim", "object") for kind in kinds) or len(set(kinds)) != len(kinds)):
        raise CameraViewError("invalid_request", "kinds must select distinct sim/object types")
    fov = DEFAULT_VERTICAL_FOV if vertical_fov is None else vertical_fov
    aspect = DEFAULT_ASPECT_RATIO if aspect_ratio is None else aspect_ratio
    if not _finite(fov) or not 0 < fov < 180:
        raise CameraViewError("invalid_request", "vertical_fov must be finite and between 0 and 180 degrees")
    if not _finite(aspect) or aspect <= 0:
        raise CameraViewError("invalid_request", "aspect_ratio must be finite and positive")
    if far is not None and (not _finite(far) or far <= 0):
        raise CameraViewError("invalid_request", "far must be None or a finite positive depth")
    tangent_y = math.tan(math.radians(fov) / 2)
    tangent_x = tangent_y * aspect
    if not all(_finite(value) and value > 0 for value in (tangent_x, tangent_y)):
        raise CameraViewError("invalid_request", "FOV/aspect combination is not representable")
    return {"kinds": list(kinds), "vertical_fov": float(fov), "aspect_ratio": float(aspect),
            "horizontal_fov": math.degrees(2 * math.atan(tangent_x)),
            "parameter_sources": {"vertical_fov": "default_approximation" if vertical_fov is None else "caller",
                                  "aspect_ratio": "default_approximation" if aspect_ratio is None else "caller"},
            "near": 0.0, "far": float(far) if far is not None else None,
            "unit": "game_world_units", "angle_unit": "degrees", "far_basis": "camera_forward_depth",
            "selection": "proxy_sphere_or_position", "order": "distance_then_kind_then_numeric_id",
            "occlusion_checked": False, "approximate": True}


def vector(value):
    result = tuple(float(value[axis]) for axis in ("x", "y", "z"))
    if not all(math.isfinite(item) for item in result):
        raise ValueError("Nonfinite vector")
    return result


def length(value):
    # Python 3.7 math.hypot accepts only two arguments.
    return math.hypot(math.hypot(value[0], value[1]), value[2])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0])


def _unit(value):
    size = length(value)
    if not math.isfinite(size) or size <= 1e-12:
        raise ValueError("Degenerate direction")
    return tuple(item / size for item in value)


def _xyz(value):
    return dict(zip(("x", "y", "z"), value))


class Frustum:
    def __init__(self, camera, query):
        try:
            self.position = vector(camera["position"])
            target = vector(camera["target"])
            self.forward = _unit(tuple(b-a for a, b in zip(self.position, target)))
            # No roll is exposed by EA. A deterministic Z-up reference handles vertical views.
            vertical = abs(self.forward[1]) > 0.999999
            reference = (0.0, 0.0, 1.0) if vertical else (0.0, 1.0, 0.0)
            self.right = _unit(_cross(self.forward, reference))
            self.up = _unit(_cross(self.right, self.forward))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise CameraViewError("camera_unavailable", "Camera position/direction is invalid",
                                  {"reason": str(exc)}) from None
        self.far = query["far"]
        ty = math.tan(math.radians(query["vertical_fov"]) / 2)
        tx = ty * query["aspect_ratio"]
        self.sx, self.cx = tx / math.hypot(1, tx), 1 / math.hypot(1, tx)
        self.sy, self.cy = ty / math.hypot(1, ty), 1 / math.hypot(1, ty)
        camera.update(forward=_xyz(self.forward), right=_xyz(self.right), up=_xyz(self.up),
                      orientation_basis="z_up_fallback" if vertical else "world_y_up_no_roll")

    def test(self, position, radius):
        delta = tuple(a-b for a, b in zip(vector(position), self.position))
        x, y, depth = (_dot(delta, axis) for axis in (self.right, self.up, self.forward))
        distance = length(delta)
        planes = [depth, depth*self.sx + x*self.cx, depth*self.sx - x*self.cx,
                  depth*self.sy + y*self.cy, depth*self.sy - y*self.cy]
        if self.far is not None:
            planes.append(self.far - depth)
        if not all(math.isfinite(value) for value in planes + [distance]):
            raise ValueError("Nonfinite frustum calculation")
        # Testing sphere vs each plane is conservative near corners, intentionally.
        if any(value < -radius - 1e-9 for value in planes):
            return None
        return {"distance": distance, "depth": depth,
                "containment": "inside" if all(value >= radius for value in planes) else "intersects"}


def collect(adapter, session_id, provenance, query):
    started = time.perf_counter()
    packet = envelope("camera_view", session_id)
    packet.update(request_id=new_id(), provenance=provenance, query=query, read_started=adapter.clock())
    scope = adapter.camera_scope()
    camera = adapter.camera_snapshot(scope["zone_id"])
    frustum = Frustum(camera, query)
    packet.update(scope=scope, camera=camera)
    coverage = {"complete": True, "enumeration_complete": True, "scan_limit": MAX_SCANNED,
                "scanned_count": 0, "candidate_count": 0, "unresolved_count": 0,
                "reasons": {}, "selection_counts": {}, "bounds_fallback_reasons": {}}
    results, seen, partial = [], set(), False

    def gap(reason):
        coverage["unresolved_count"] += 1
        coverage["reasons"][reason] = coverage["reasons"].get(reason, 0) + 1

    try:
        for obj in adapter.camera_objects():
            if coverage["scanned_count"] >= MAX_SCANNED:
                coverage["enumeration_complete"] = False
                coverage["reasons"]["scan_limit"] = 1
                break
            coverage["scanned_count"] += 1
            try:
                kind = "sim" if getattr(obj, "is_sim", False) else "object"
                if kind not in query["kinds"] or not adapter.camera_eligible(obj, scope["zone_id"]):
                    continue
                identifier = int(obj.sim_info.sim_id if kind == "sim" else obj.id)
                if not 0 < identifier < 2 ** 64:
                    raise ValueError("Invalid entity identity")
                key = kind, identifier
                if key in seen:
                    continue
                seen.add(key)
                coverage["candidate_count"] += 1
                spatial = adapter.nearby_spatial(obj)
                spatial.pop("room", None)  # No native room calls for a view query.
                if spatial["position"]["status"] != "available":
                    gap("position_unavailable")
                    continue
                bounds = adapter.camera_bounds(obj)
                method = bounds["method"]
                coverage["selection_counts"][method] = coverage["selection_counts"].get(method, 0) + 1
                for reason in bounds.get("fallback_reasons", []):
                    counts = coverage["bounds_fallback_reasons"]
                    counts[reason] = counts.get(reason, 0) + 1
                hit = frustum.test(spatial["position"]["value"], bounds["radius"])
                if hit is None:
                    continue
            except Exception:
                gap("candidate_read_failed")
                continue
            try:
                reference = adapter.reference(obj)
                identity_status = field(True, source="EAAdapter.reference")
            except Exception:
                reference = entity(kind, identifier)
                identity_status = field(status="error", reason="identity_label_read_failed")
            try:
                same_lot = field(bool(obj.is_on_active_lot()), source="GameObject.is_on_active_lot")
            except Exception:
                same_lot = field(status="unsupported", reason="lot_membership_unavailable")
            partial = partial or identity_status["status"] != "available" or same_lot["status"] != "available"
            partial = partial or any(item["status"] != "available" for item in spatial.values())
            results.append(dict(hit, entity=reference, identity_status=identity_status, spatial=spatial,
                                bounds=bounds, same_lot=same_lot,
                                context_scope="active_lot_instantiated"))
    except Exception:
        coverage["enumeration_complete"] = False
        coverage["reasons"]["enumeration_failed"] = 1
    results.sort(key=lambda row: (row["distance"], row["entity"]["kind"], int(row["entity"]["id"])))
    coverage["complete"] = coverage["enumeration_complete"] and coverage["unresolved_count"] == 0
    packet.update(results=results, count=len(results), matched_count=len(results),
                  matched_count_exact=coverage["complete"], truncated=False, coverage=coverage,
                  read_finished=adapter.clock(), execution_ms=(time.perf_counter()-started)*1000,
                  status="partial" if partial or not coverage["complete"] else "complete")
    return copy_data(packet)
