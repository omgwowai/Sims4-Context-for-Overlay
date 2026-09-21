"""Build an auditable offline experience view from a stable single-session journal.

The view is a lossy, rule-derived input for consumers, not a public API or a
transcript of perception/thought. Full evidence stays in the original journal.
"""

from collections import defaultdict
import copy
import hashlib
import json

from .filter_events import actor, compact_size, filter_events, identity, ref, target, tick
from .experience_policy import (CATALOG, RESOURCE_SHA256, USES, classify, compact, family,
                               game_event, importance, kind, label, resource)
from .experience_digest import digest


POLICY_VERSION = "experience_view_v1_4"


def uid(prefix, event):
    return prefix + hashlib.sha256(event["event_id"].encode("utf-8")).hexdigest()[:16]


def roles(event):
    return event.get("roles") or event.get("facts", {}).get("roles") or []


def role_keys(event):
    return [{"entity_key": row["entity_key"], "role": row["role"]} for row in roles(event)]


def same_visit(left, right):
    return left.get("zone_visit") is not None and left.get("zone_visit") == right.get("zone_visit")


def started(event):
    return tick(event.get("started_time")) is not None


def occurrence(event):
    facts = event.get("facts", {})
    queued = [o["game_time"] for o in event.get("observations", [])
              if o.get("phase") == "queued" and tick(o.get("game_time")) is not None]
    return {"action": compact(facts), "action_tuning": facts.get("tuning_name"), "roles": role_keys(event),
            "first_observed": event.get("first_observed_time"),
            "queued": min(queued, key=tick) if queued else None,
            "started": event.get("started_time"), "ended": event.get("ended_time"),
            "execution": "started" if started(event) else "start_not_observed",
            "exit": event.get("facts", {}).get("finishing_type"),
            "outcome": event.get("outcome"),
            "result_branch": event.get("facts", {}).get("outcome_result")}


def score_excerpt(candidate):
    score = candidate.get("score_components") or {}
    values = score.get("values") or {}
    contributions = [compact(row) for row in values.get("commodity_scores", [])
                     if isinstance(row, dict) and type(row.get("score")) in (int, float) and row["score"] != 0]
    contributions.sort(key=lambda row: -abs(row["score"]))
    native = score.get("native_text")
    return {"source": score.get("source"), "nonzero_commodity_contributions": contributions[:3],
            "view_omitted_contributions": max(0, len(contributions) - 3),
            "native_text": native[:1200] if isinstance(native, str) else None,
            "native_text_truncated": isinstance(native, str) and len(native) > 1200,
            "missing": score.get("missing", []),
            "interpretation": "engine_scoring_evidence_not_thought_or_causal_percentage"}


def choice_excerpt(stage, alternatives=2):
    candidates = stage.get("candidates") or []
    winners = [c for c in candidates if c.get("selected") is True]
    if len(winners) != 1:
        return None
    others = [c for c in candidates if c is not winners[0]]
    others.sort(key=lambda c: -(c.get("weight") if type(c.get("weight")) in (int, float) else 0))

    def item(candidate):
        return {"action": compact(candidate.get("action")), "target": compact(candidate.get("target")),
                "probability": candidate.get("probability"), "weight": candidate.get("weight"),
                "score": score_excerpt(candidate)}

    return {"stage_id": stage.get("stage_id"), "kind": stage.get("kind"),
            "selected": item(winners[0]), "alternatives": [item(c) for c in others[:alternatives]],
            "pool_count": stage.get("pool_count"), "source_omitted_count": stage.get("omitted_count"),
            "view_omitted_candidates": max(0, len(others) - alternatives),
            "probability_basis": stage.get("probability_basis"),
            "scope": "probabilities_apply_only_to_this_recorded_stage"}


class Builder:
    def __init__(self, events, session_id, entity_key, game_version):
        self.events, self.session_id, self.entity_key, self.game_version = events, session_id, entity_key, game_version
        filtered = filter_events(events, session_id, copy_result=False)
        self.omitted = {row["event_id"]: row for row in filtered["filtering"]["omitted"]}
        self.folds = filtered["filtering"]["folds"]
        if game_version is not None and game_version != CATALOG["game_version"]:
            self.omitted, self.folds = {}, []
        self.by_id = {e["event_id"]: e for e in events}
        self.alias = {e["event_id"]: "e" + str(i + 1) for i, e in enumerate(events)}
        self.semantic = {e["event_id"]: classify(e, game_version) for e in events}
        self.important = {e["event_id"]: importance(e, game_version) for e in events if importance(e, game_version)}
        for identifier in self.important:
            self.omitted.pop(identifier, None)
        self.selected = {e["event_id"] for e in events if entity_key is None or entity_key in e.get("entities", [])}
        self.units, self.sources, self.routes = {}, defaultdict(set), defaultdict(set)
        self.dispositions, self.link_audit = {}, []
        self.interactions = {e["event_id"]: e for e in events if game_event(e) and kind(e) == "interaction"}
        self.protected = {(e.get("cause") or {}).get("event_id") for e in events if game_event(e)}
        self.instances = defaultdict(list)
        for event in self.interactions.values():
            self.instances[(event.get("zone_visit"), actor(event), event.get("facts", {}).get("interaction_id"))].append(event)
        self.membership, self.parent, self.provider_choices = {}, {}, defaultdict(list)
        self.association_issues = defaultdict(set)

    def evidence(self, event):
        return self.alias[event["event_id"]]

    def record(self, unit, event):
        self.sources[unit["id"]].add(event["event_id"])
        evidence = self.evidence(event)
        if evidence not in unit["evidence"]:
            unit["evidence"].append(evidence)

    def add(self, prefix, event, lane, identity_key=None, **values):
        unit = dict(id=uid(prefix, {"event_id": identity_key} if identity_key else event), lane=lane, zone_visit=event.get("zone_visit"),
                    evidence=[], **values)
        self.units[unit["id"]] = unit
        self.record(unit, event)
        return unit

    def cause(self, event):
        cause = event.get("cause") or {}
        source = self.interactions.get(cause.get("event_id"))
        if source is None or not same_visit(event, source):
            return None
        cause_actor = (cause.get("actor") or {}).get("key")
        if cause_actor is not None and cause_actor != actor(source):
            return None
        return self.membership.get(source["event_id"])

    def provider(self, decision, stage):
        candidates = [c for c in stage.get("candidates", []) if c.get("selected") is True]
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        payload = decision.get("payload") or {}
        owner = (payload.get("actor") or {}).get("key")
        choices = self.instances.get((decision.get("zone_visit"), owner, candidate.get("interaction_id")), [])
        if len(choices) != 1 or owner is None:
            return None
        parent = choices[0]
        at, start, end = tick(payload.get("selection_time") or decision.get("first_observed_time")), tick(parent.get("started_time")), tick(parent.get("ended_time"))
        if (not same_visit(decision, parent) or identity(candidate.get("action") or {}) != identity(parent["facts"])
                or (candidate.get("target") or {}).get("key") != target(parent)
                or start is None or at is None or at < start or (end is not None and at > end)):
            return None
        return parent

    def assemble_activities(self):
        # Source links stay typed. Merely sharing a connected component never merges activities.
        for event in self.interactions.values():
            identifier, facts = event["event_id"], event.get("facts", {})
            parent = self.interactions.get(facts.get("parent_event_id"))
            if (parent is not None and parent["event_id"] != identifier and same_visit(event, parent) and actor(event) is not None
                    and actor(event) == actor(parent)
                    and tick(event.get("first_observed_time")) is not None
                    and tick(parent.get("first_observed_time")) is not None
                    and tick(event["first_observed_time"]) >= tick(parent["first_observed_time"])):
                self.parent[identifier] = parent["event_id"]
                self.link_audit.append({"child": self.evidence(event), "parent": self.evidence(parent),
                                        "basis": "recorded_parent_event_id", "merged": False})

        providers = {}
        for event in self.events:
            if not game_event(event) or kind(event) != "autonomy.decision":
                continue
            payload = event.get("payload") or {}
            child = self.interactions.get(payload.get("interaction_event_id"))
            if (child is None or not same_visit(event, child) or actor(child) is None
                    or actor(child) != (payload.get("actor") or {}).get("key")
                    or child["facts"].get("decision_event_id") != event["event_id"]
                    or identity(child["facts"]) != identity((payload.get("selected") or {}).get("action") or {})):
                continue
            stages = [stage for stage in payload.get("stages", []) if stage.get("kind") == "mixer_provider"]
            if len(stages) > 1:
                self.association_issues[child["event_id"]].add("ambiguous_provider_stages")
                continue
            for stage in stages:
                provider = self.provider(event, stage)
                if provider is not None and provider["event_id"] != child["event_id"]:
                    providers[child["event_id"]] = provider["event_id"]
                else:
                    self.association_issues[child["event_id"]].add("provider_identity_or_interval_invalid")

        merge_to = {}
        for event in self.interactions.values():
            identifier, semantic = event["event_id"], self.semantic[event["event_id"]]
            if not started(event):
                continue
            for parent_id, basis in ((self.parent.get(identifier), "recorded_parent_event_id"),
                                     (providers.get(identifier), "recorded_mixer_provider_instance")):
                parent = self.interactions.get(parent_id)
                if parent is None or not started(parent) or tick(event["started_time"]) < tick(parent["started_time"]):
                    continue
                if basis == "recorded_mixer_provider_instance" and tick(parent.get("ended_time")) is not None:
                    if tick(event.get("ended_time")) is None or tick(event["ended_time"]) > tick(parent["ended_time"]):
                        self.association_issues[identifier].add("child_outside_provider_execution")
                        continue
                same_family = family(event, self.game_version) is not None and family(event, self.game_version) == family(parent, self.game_version)
                phase = semantic in ("activity_phase", "micro") and same_family
                if phase and basis == "recorded_mixer_provider_instance" and target(event) is not None and target(event) != target(parent):
                    self.association_issues[identifier].add("phase_target_disagrees_with_provider")
                    continue
                topic = (semantic in ("social_content", "conversation") and self.semantic[parent_id] == "conversation"
                         and basis == "recorded_mixer_provider_instance")
                if phase or topic:
                    merge_to[identifier] = parent_id
                    self.link_audit.append({"child": self.evidence(event), "parent": self.evidence(parent),
                                            "basis": basis, "merged": True})
                    break
                self.association_issues[identifier].add("not_a_compatible_activity_phase")

        # A reviewed posture provider is concurrent infrastructure, not another
        # action or a continuation. It may lack parent/provider instance links.
        # Require the complete same execution on one uniquely identified object;
        # nearby/overlapping intervals and open executions remain separate details.
        executions = defaultdict(list)
        for event in self.interactions.values():
            facts = event.get("facts", {})
            key = self.support_execution_key(event)
            if (key is not None and self.semantic[event["event_id"]] == "action"
                    and facts.get("visible") is True and event["event_id"] not in self.omitted):
                executions[key].append(event)
        for event in self.interactions.values():
            identifier, facts = event["event_id"], event.get("facts", {})
            if self.semantic[identifier] != "activity_support":
                continue
            key = self.support_execution_key(event)
            if (key is None or facts.get("visible") is not False or facts.get("is_super") is not True
                    or (facts.get("trigger") or {}).get("name") != "POSTURE_GRAPH" or event.get("tier") != "internal"):
                self.association_issues[identifier].add("posture_support_execution_unverified")
                continue
            candidates = executions[key]
            if len(candidates) != 1:
                self.association_issues[identifier].add("posture_support_root_not_unique")
                continue
            parent = candidates[0]
            if (facts.get("parent_event_id") not in (None, parent["event_id"])
                    or providers.get(identifier) not in (None, parent["event_id"])):
                self.association_issues[identifier].add("posture_support_link_conflict")
                continue
            merge_to[identifier] = parent["event_id"]
            self.link_audit.append({"child": self.evidence(event), "parent": self.evidence(parent),
                                   "basis": "reviewed_posture_provider_same_execution", "merged": True})

        seeds = {identifier for identifier in self.interactions
                 if self.semantic[identifier] in ("action", "social_content", "conversation")}
        for identifier in seeds:
            event = self.interactions[identifier]
            if identifier in merge_to or identifier in self.omitted:
                continue
            if self.entity_key is not None and self.entity_key in event.get("entities", []) and self.entity_key not in (actor(event), target(event)):
                continue  # Index membership alone does not prove participation.
            self.membership[identifier] = self.add("a", event, "activities", category=self.semantic[identifier],
                time=event.get("started_time") or event.get("first_observed_time"),
                **occurrence(event), step_count=1, continuation_step_count=1, topics=[], effects=[], decisions=[], product_links=[])["id"]
            if identifier in self.important:
                self.units[self.membership[identifier]]["importance"] = self.important[identifier]

        for identifier, event in self.interactions.items():
            cursor, seen = identifier, set()
            while cursor in merge_to and cursor not in seen:
                seen.add(cursor)
                cursor = merge_to[cursor]
            if cursor in seen or cursor not in self.membership:
                continue
            root = self.membership[cursor]
            self.membership[identifier] = root
            if identifier != cursor:
                unit = self.units[root]
                self.record(unit, event)
                if self.semantic[identifier] == "social_content":
                    unit["topics"].append(dict(occurrence(event), evidence=self.evidence(event)))
                elif identifier not in self.omitted:
                    unit["step_count"] += 1
                # Provider children are concurrent details; only actual continuation phases extend boundaries.
                if self.parent.get(identifier) == merge_to.get(identifier) and self.semantic[identifier] == "activity_phase":
                    unit["continuation_step_count"] += 1
                    ends = [tick(unit.get("ended")), tick(event.get("ended_time"))]
                    if None in ends:
                        unit["ended"] = None
                    elif ends[1] > ends[0]:
                        unit["ended"] = event["ended_time"]

        # Routers carry decision provenance to concrete descendants, without creating extra activities.
        for identifier, root in self.membership.items():
            cursor, seen = identifier, set()
            while cursor in self.parent and cursor not in seen:
                seen.add(cursor)
                cursor = self.parent[cursor]
                if self.semantic[cursor] == "router":
                    self.routes[cursor].add(root)

    def support_execution_key(self, event):
        key = (event.get("zone_visit"), actor(event), target(event), family(event, self.game_version),
               tick(event.get("started_time")), tick(event.get("ended_time")))
        if None in key or not str(key[2]).startswith("object:") or key[-1] <= key[-2]:
            return None
        return key

    def attach(self, unit, event, slot):
        root = self.cause(event)
        if root is not None:
            unit["activity"] = root
            unit["association"] = {"basis": "recorded_cause_event_id", "cause": (event.get("cause") or {}).get("event_id")}
            self.units[root][slot].append(unit["id"])
            # The final projection follows this dependency without importing every other effect of the activity.

    def assemble_states(self):
        active = {}
        ordered = sorted(self.events, key=lambda e: tick(e.get("first_observed_time")) or 0)
        for event in ordered:
            if not game_event(event) or kind(event) != "buffs" or event["event_id"] in self.omitted:
                continue
            semantic = self.semantic[event["event_id"]]
            use = USES[semantic]
            if use in ("omit", "review"):
                continue
            subjects = [r["entity_key"] for r in roles(event) if r["role"] == "subject"]
            before, after = event.get("before"), event.get("after")
            if len(subjects) != 1 or event.get("zone_visit") is None or tick(event.get("first_observed_time")) is None:
                continue
            if bool(before) == bool(after):
                continue  # Unknown transition shape, preserved in review below.
            value = after or before
            series = (event["zone_visit"], subjects[0], identity(value))
            at = event["first_observed_time"]
            if after:
                if series in active:
                    prior = self.units[active.pop(series)]
                    prior["boundary"] = "ambiguous_repeated_add"
                    self.record(prior, event)
                lane = "background" if use == "background" else "details" if use == "merge" else "states"
                unit = self.add("s", event, lane,
                    category=semantic, subject=subjects[0], state=compact(value), time=at, started=at,
                    ended=None, boundary="end_not_observed", activity_links=[])
                active[series] = unit["id"]
            else:
                lane = "background" if use == "background" else "details" if use == "merge" else "states"
                unit = self.units[active.pop(series)] if series in active else self.add("s", event,
                    lane, category=semantic, subject=subjects[0],
                    state=compact(value), time=at, started=None, ended=None, boundary="start_not_observed", activity_links=[])
                unit["ended"] = at
                if unit["started"] is not None:
                    unit["boundary"] = "paired_observations"
                self.record(unit, event)
            root = self.cause(event)
            if root is not None:
                unit["activity_links"].append({"activity": root, "transition": "added" if after else "removed",
                    "basis": "recorded_cause_event_id", "evidence": self.evidence(event)})

        # Mood changes use the same subject/visit isolation. No arbitrary short-duration threshold.
        active = {}
        for event in ordered:
            if not game_event(event) or kind(event) != "mood.changed":
                continue
            subjects = [r["entity_key"] for r in roles(event) if r["role"] == "subject"]
            payload = event.get("payload") or {}
            if len(subjects) != 1 or event.get("zone_visit") is None or tick(event.get("first_observed_time")) is None:
                continue
            if not isinstance(payload.get("new_mood"), dict) or not isinstance(payload.get("old_mood"), dict):
                continue
            series = (event["zone_visit"], subjects[0])
            previous = self.units.get(active.get(series))
            old = {"mood": compact(payload["old_mood"]), "intensity": payload.get("old_intensity")}
            new = {"mood": compact(payload["new_mood"]), "intensity": payload.get("new_intensity")}
            if previous is not None:
                if previous["state"] == old:
                    previous.update(ended=event["first_observed_time"], boundary="paired_observations")
                else:
                    previous["boundary"] = "discontinuous_observations"
                self.record(previous, event)
            unit = self.add("m", event, "states", category="mood", subject=subjects[0],
                state=new, previous=old, time=event["first_observed_time"], started=event["first_observed_time"],
                ended=None, boundary="end_not_observed", activity_links=[])
            root = self.cause(event)
            if root is not None:
                unit["activity_links"].append({"activity": root, "transition": "changed",
                    "basis": "recorded_cause_event_id", "evidence": self.evidence(event)})
            active[series] = unit["id"]

    def coalesce_moods(self):
        groups = defaultdict(list)
        for unit in self.units.values():
            if unit.get("category") == "mood":
                groups[(unit["zone_visit"], unit["subject"])].append(unit)
        action_times = {(e.get("zone_visit"), owner, tick(e.get("started_time")))
                        for e in self.interactions.values() for owner in (actor(e), target(e)) if owner is not None}
        for key, rows in groups.items():
            rows.sort(key=lambda row: tick(row["started"]))
            for index in range(len(rows) - 1):
                first, second = rows[index:index + 2]
                if first["id"] not in self.units or second["id"] not in self.units:
                    continue
                at = tick(first["started"])
                if (at != tick(second["started"]) or tick(first["ended"]) != at
                        or first["previous"] != second["state"] or first["state"] != second["previous"]
                        or first["activity_links"] or second["activity_links"] or (key[0], key[1], at) in action_times
                        or (self.sources[first["id"]] | self.sources[second["id"]]) & self.protected):
                    continue
                # Fold the excursion into the restored state. Its evidence remains accessible.
                second["previous"] = first["previous"]
                second["same_tick_excursion_folded"] = True
                for identifier in self.sources[first["id"]]:
                    self.record(second, self.by_id[identifier])
                del self.units[first["id"]]
                del self.sources[first["id"]]

    def assemble_decisions(self):
        for event in self.events:
            if not game_event(event) or kind(event) != "autonomy.decision":
                continue
            payload = event.get("payload") or {}
            stages = payload.get("stages") or []
            semantic = self.semantic[event["event_id"]]
            # Extract upper-level evidence even from events omitted by the earlier filter.
            for stage in stages:
                if stage.get("kind") != "mixer_provider":
                    continue
                parent = self.provider(event, stage)
                root = self.membership.get(parent["event_id"]) if parent is not None else None
                excerpt = choice_excerpt(stage)
                if root is not None and excerpt is not None:
                    self.provider_choices[root].append({"event": event, "time": payload.get("selection_time"), "choice": excerpt})

            if semantic in ("micro", "activity_phase", "posture", "gesture", "conversation") and event["event_id"] not in self.protected:
                self.dispositions[event["event_id"]] = "execution_choice_details"
                continue
            if event["event_id"] in self.omitted:
                continue
            child = self.interactions.get(payload.get("interaction_event_id"))
            linked = (child is not None and same_visit(event, child)
                      and actor(child) is not None
                      and (payload.get("actor") or {}).get("key") == actor(child)
                      and child["facts"].get("decision_event_id") == event["event_id"]
                      and identity((payload.get("selected") or {}).get("action") or {}) == identity(child["facts"]))
            roots = ({self.membership[child["event_id"]]} if linked and child["event_id"] in self.membership
                     else self.routes.get(child["event_id"], set()) if linked else set())
            choices = [choice_excerpt(stage) for stage in stages if stage.get("kind") == "interaction"]
            choices = [choice for choice in choices if choice is not None]
            is_choice = payload.get("is_script_request") is False and any((c.get("pool_count") or 0) > 1 for c in choices)
            unit = self.add("d", event, "decisions" if semantic in ("action", "social_content", "router") else "review",
                category="choice" if is_choice else "submission", time=payload.get("selection_time") or event.get("first_observed_time"),
                actor=compact(payload.get("actor")), selected=compact(payload.get("selected")), choices=choices,
                is_script_request=payload.get("is_script_request"), mode=payload.get("mode"),
                execution="started" if any(self.units[root]["execution"] == "started" for root in roots) else "start_not_confirmed",
                selected_step_started=bool(linked and started(child)),
                submission_result=compact(payload.get("execution_result")),
                related_activities=sorted(roots), link_basis="recorded_execution_and_parent_links" if roots else None)
            for root in roots:
                self.units[root]["decisions"].append(unit["id"])

        for root, rows in self.provider_choices.items():
            rows.sort(key=lambda row: tick(row["time"]) or 0)
            samples = [rows[0]] if len(rows) == 1 else [rows[0], rows[-1]]
            first = rows[0]["event"]
            unit = self.add("p", first, "decisions", identity_key=root, category="activity_provider_scoring", time=rows[0]["time"],
                related_activities=[root], observed_count=len(rows), sample_policy="first_and_last_observation",
                samples=[{"time": row["time"], "choice": row["choice"], "evidence": self.evidence(row["event"])} for row in samples],
                omitted_observation_details=max(0, len(rows) - len(samples)))
            # All samples remain recoverable by ID; first/last is an explicit lossy presentation policy.
            for row in rows:
                self.record(unit, row["event"])
            self.units[root]["decisions"].append(unit["id"])

    def assemble_facts(self):
        consumed = set().union(*self.sources.values()) if self.sources else set()
        protected = {(e.get("cause") or {}).get("event_id") for e in self.events
                     if game_event(e) and kind(e) != "autonomy.decision"}
        numeric = {}
        for event in self.events:
            identifier, semantic = event["event_id"], self.semantic[event["event_id"]]
            if identifier in consumed or kind(event) == "autonomy.decision" and game_event(event):
                continue
            if identifier in self.omitted:
                self.dispositions[identifier] = "detail_filter:" + self.omitted[identifier]["rule"]
                continue
            if not game_event(event):
                self.add("x", event, "external", category="opaque_external_event", time=event.get("first_observed_time"),
                         producer=event.get("producer"), entities=event.get("entities", []))
                continue
            use, root = USES[semantic], self.cause(event)
            if use == "omit" and identifier not in protected:
                if semantic != "reaction_callback" or root is not None:
                    self.dispositions[identifier] = "semantic_detail:" + semantic
                    continue
            if kind(event) == "interaction":
                reasons = ["classification_missing"] if use == "review" else ["association_missing"]
                if use == "omit" and identifier in protected:
                    reasons.append("protected_detail")
                self.add("r", event, "details" if use in ("merge", "omit") else "review", category=semantic, time=event.get("started_time") or event.get("first_observed_time"),
                         reason=reasons[0], review_reasons=reasons,
                         association_issues=sorted(self.association_issues[identifier]) or ["no_verified_activity_root"],
                         **occurrence(event))
                continue
            lane = "background" if use == "background" else "facts" if use in ("core", "context", "merge") else "review"
            if use == "merge" and root is None:
                lane = "details"
            if kind(event) in ("buffs", "mood.changed"):
                lane = "review"  # Unsupported shape or missing subject/time did not form a state interval.
            payload = event.get("payload")
            if kind(event) == "statistic.direct" and isinstance(payload, dict) and set(payload) - {"statistic", "before", "after", "scope_evidence"}:
                lane = "review"
            if kind(event) == "statistic.direct" and lane != "review" and isinstance(payload, dict):
                series = (root, event.get("zone_visit"), json.dumps(role_keys(event), sort_keys=True), identity(resource(event)))
                if series in numeric:
                    unit = self.units[numeric[series]]
                    unit["observations"].append({"time": event.get("first_observed_time"), "before": payload.get("before"),
                        "after": payload.get("after"), "evidence": self.evidence(event)})
                    self.record(unit, event)
                    continue
                unit = self.add("n", event, lane, category=semantic, type=kind(event), time=event.get("first_observed_time"),
                    roles=role_keys(event), statistic=compact(resource(event)), not_a_net_delta=True,
                    observations=[{"time": event.get("first_observed_time"), "before": payload.get("before"),
                                   "after": payload.get("after"), "evidence": self.evidence(event)}])
                numeric[series] = unit["id"]
            else:
                unit = self.add("f", event, lane, category=semantic, type=kind(event), time=event.get("first_observed_time"),
                    roles=role_keys(event), payload=compact(payload), before=compact(event.get("before")), after=compact(event.get("after")))
            self.attach(unit, event, "effects")
            if root is None and (event.get("cause") or {}).get("event_id"):
                unit["unresolved_cause_event_id"] = event["cause"]["event_id"]
                unit.setdefault("review_reasons", []).append("association_missing")
            if lane == "review":
                unit.setdefault("review_reasons", []).append("classification_missing" if use == "review" else "unsupported_observation_shape")

    def link_products(self):
        products = defaultdict(list)
        for event in self.events:
            if game_event(event) and kind(event) == "crafting.completed":
                key = ((event.get("payload") or {}).get("crafted_object") or {}).get("key")
                root = self.cause(event)
                if key is not None and root is not None:
                    products[(event.get("zone_visit"), key)].append((event, root))
        for identifier, root in self.membership.items():
            event = self.interactions[identifier]
            if family(event, self.game_version) != "eating":
                continue
            at = tick(event.get("started_time") or event.get("first_observed_time"))
            choices = [(source, maker) for source, maker in products.get((event.get("zone_visit"), target(event)), [])
                       if at is not None and tick(source.get("first_observed_time")) is not None
                       and tick(source["first_observed_time"]) <= at and maker != root]
            if len(choices) == 1:
                source, maker = choices[0]
                link = {"activity": maker, "object": target(event), "basis": "recorded_crafted_object_identity",
                        "evidence": self.evidence(source)}
                if link not in self.units[root]["product_links"]:
                    self.units[root]["product_links"].append(link)
                    self.record(self.units[root], source)

    def result(self):
        lanes = ("activities", "states", "facts", "decisions", "background", "details", "review", "external")
        included = {identifier for identifier, ids in self.sources.items() if ids & self.selected}
        # Include dependencies and evidence from outside the requested entity index.
        while True:
            before = len(included)
            for identifier in list(included):
                unit = self.units[identifier]
                dependencies = list(unit.get("related_activities", []))
                dependencies += [link["activity"] for link in unit.get("activity_links", [])]
                dependencies += [link["activity"] for link in unit.get("product_links", [])]
                if unit.get("activity"):
                    dependencies.append(unit["activity"])
                included.update(key for key in dependencies if key in self.units)
            if len(included) == before:
                break
        view = {lane: [] for lane in lanes}
        for identifier in included:
            unit = self.units[identifier]
            projected = {k: v for k, v in unit.items() if k != "lane"}
            for field in ("effects", "decisions"):
                if field in projected:
                    projected[field] = [key for key in projected[field] if key in included]
            view[unit["lane"]].append(projected)
        for units in view.values():
            units.sort(key=lambda unit: (tick(unit.get("time")) or 0, unit["id"]))
        used = set().union(*(self.sources[key] for key in included)) if included else set()
        memberships = defaultdict(list)
        for key in sorted(included):
            for identifier in self.sources[key]:
                memberships[identifier].append(key)
        evidence = {}
        for event in self.events:
            identifier = event["event_id"]
            if identifier not in self.selected | used:
                continue
            row = dict(ref(event), semantic_role=self.semantic[identifier], units=memberships[identifier],
                       outside_entity_index=identifier not in self.selected)
            if resource(event):
                row["resource_identity"] = [kind(event)] + list(identity(resource(event)))
            if identifier in self.important:
                row["importance"] = self.important[identifier]
            if not row["units"]:
                row["disposition"] = self.dispositions.get(identifier, "unresolved")
            if kind(event) == "interaction" and game_event(event):
                row["occurrence"] = occurrence(event)
            evidence[self.evidence(event)] = row
        source_selected = [e for e in self.events if e["event_id"] in self.selected]
        # The consumer packet excludes audit and review, with omissions disclosed explicitly.
        packet = {lane: view[lane] for lane in lanes if lane not in ("review", "external", "details")}
        packet_ids = {unit["id"] for units in packet.values() for unit in units}
        for unit in packet["activities"]:
            unit["effects"] = [key for key in unit["effects"] if key in packet_ids]
        packet["scope"] = {"session_id": self.session_id, "entity_key": self.entity_key,
            "policy_version": POLICY_VERSION, "experimental": True, "unknown_units_in_review": len(view["review"]),
            "detail_units_available": len(view["details"]),
            "external_events_excluded": len(view["external"]), "not_player_knowledge": True,
            "intervals": "paired_observations_within_one_visit_not_continuous_state_reconstruction",
            "evidence_lookup": "audit.evidence; source journal required for full detail"}
        counts = {lane: len(view[lane]) for lane in lanes}
        metrics = {"source_events": len(self.events), "selected_events": len(self.selected), "units_by_lane": counts,
            "selected_events_json_bytes": compact_size(source_selected), "consumer_packet_json_bytes": compact_size(packet),
            "selected_evidence_with_units": len(self.selected & used),
            "selected_evidence_without_units": len(self.selected - used),
            "source_tokens": None, "consumer_tokens": None, "token_encoding": None}
        consumer = digest(packet, self.game_version)
        wanted = set()
        def entity_keys(value):
            if isinstance(value, str) and value.startswith(("sim:", "object:")):
                wanted.add(value)
            elif isinstance(value, list):
                for item in value:
                    entity_keys(item)
            elif isinstance(value, dict):
                for item in value.values():
                    entity_keys(item)
        entity_keys(consumer)
        entities = {}
        for event in self.events:
            if event["event_id"] not in used or not game_event(event):
                continue
            facts = event.get("facts") or {}
            candidates = event.get("participants", []) + facts.get("participants", []) + [facts.get("actor"), facts.get("target")]
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("key") in wanted:
                    name = label(candidate.get("name"))
                    entry = entities.setdefault(candidate["key"], {"names_observed": [], "name_evidence": []})
                    names = entry["names_observed"]
                    if name and name not in names:
                        names.append(name)
                    name_evidence = compact(candidate)
                    if name and name_evidence not in entry["name_evidence"]:
                        entry["name_evidence"].append(name_evidence)
        consumer["entities"] = entities
        metrics["organized_json_bytes"] = compact_size(packet)
        metrics["consumer_packet_json_bytes"] = compact_size(consumer)
        metrics["consumer_sections"] = {key: len(value) for key, value in consumer.items() if isinstance(value, list)}
        alias_to_id = {alias: identifier for identifier, alias in self.alias.items()}
        links = []
        for link in self.link_audit:
            if link["child"] in evidence and link["parent"] in evidence:
                child_root = self.membership.get(alias_to_id[link["child"]])
                parent_root = self.membership.get(alias_to_id[link["parent"]])
                links.append(dict(link, merged=link["merged"] and child_root is not None and child_root == parent_root))
        return {"kind": "experimental_experience_view", "policy_version": POLICY_VERSION,
            "resource_policy": {"sha256": RESOURCE_SHA256, "reference_game_version": CATALOG["game_version"],
                "declared_game_version": self.game_version, "scope": CATALOG["scope"]},
            "consumer_packet": consumer, "organized": packet, "details": view["details"], "review": view["review"], "external": view["external"],
            "audit": {"evidence": evidence, "links": links,
                "filter_folds": [fold for fold in self.folds if fold["representative"]["event_id"] in self.selected | used]}, "metrics": metrics}


def build_experiences(events, session_id, entity_key=None, game_version=None):
    builder = Builder(events, session_id, entity_key, game_version)
    builder.assemble_activities()
    builder.assemble_states()
    builder.coalesce_moods()
    builder.assemble_decisions()
    builder.assemble_facts()
    builder.link_products()
    return copy.deepcopy(builder.result())
