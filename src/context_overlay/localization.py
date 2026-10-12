"""Bounded, deterministic rendering of EA LocalizedString evidence outside UI.

The client owns the complete localization grammar. Unsupported tokens stay
explicitly unresolved; no participant positions or names are guessed.
"""

import json
import math
import pkgutil
import re


MAX_DEPTH = 8
MAX_TOKENS = 64
MAX_TEXT = 16384
MAX_NODES = 256
TOKEN_TYPES = {0: "INVALID", 1: "SIM", 2: "STRING", 3: "RAW_TEXT", 4: "NUMBER",
               5: "OBJECT", 6: "DATE_AND_TIME", 7: "RICHDATA", 8: "STRING_LIST", 9: "SIM_LIST"}
TOKEN_FIELDS = ("first_name", "last_name", "full_name_key", "is_female", "gender_flags",
                "packed_pronouns", "age_flags", "sim_id", "raw_text", "number", "catalog_name_key",
                "catalog_description_key", "custom_name", "custom_description",
                "name_prefix_key", "name_prefix_string")
DATE_FIELDS = ("seconds", "minutes", "hours", "date", "month", "full_year", "date_and_time_format_hash")
FORMAT_PROFILE = "context_overlay_zh_CN_v1"
OBJECT_NAME_STATUSES = ("resolved", "raw_text", "english_fallback", "reviewed_override")
VALUE_TYPES = {
    "String": ("RAW_TEXT", "STRING"), "Number": ("NUMBER",), "Money": ("NUMBER",),
    "SimFirstName": ("SIM",), "SimLastName": ("SIM",), "SimName": ("SIM",),
    "SimFullName": ("SIM",), "ObjectName": ("SIM", "OBJECT"),
    "ObjectDescription": ("OBJECT",), "ObjectCatalogName": ("OBJECT",),
    "ObjectCatalogDescription": ("OBJECT",), "TimeShort": ("DATE_AND_TIME",),
    "DayOfWeekShort": ("DATE_AND_TIME",), "DayOfWeekLong": ("DATE_AND_TIME",),
}


def gap_category(reason):
    if reason in ("missing_token", "incomplete_token", "token_type_mismatch", "missing_token_field", "invalid_token_value"):
        return "parameter_evidence"
    if reason in ("unsupported_expression", "unsupported_token_format", "unsupported_or_missing_gender"):
        return "grammar_support"
    if "limit" in reason:
        return "budget"
    return "resource_or_format"


def hash_key(value):
    try:
        number = int(value, 0) if isinstance(value, str) else int(value)
        if not 0 <= number <= 0xFFFFFFFF:
            return None
        return "0x{:08X}".format(number)
    except (TypeError, ValueError, OverflowError):
        return None


def _fields(message):
    if isinstance(message, dict):
        return message
    if hasattr(message, "ListFields"):
        return {descriptor.name: value for descriptor, value in message.ListFields()}
    return {name: getattr(message, name) for name in TOKEN_FIELDS + DATE_FIELDS + ("type", "text_string", "sim_list", "date_and_time", "tokens", "hash")
            if hasattr(message, name)}


def snapshot_token(token, depth=0, budget=None, default_type="INVALID"):
    budget = [MAX_NODES, MAX_TEXT] if budget is None else budget
    budget[0] -= 1
    if depth >= MAX_DEPTH or budget[0] < 0:
        return {"type": "INVALID", "error": "localization_structure_limit"}
    values = _fields(token)
    kind = values.get("type", default_type)
    if not isinstance(kind, str):
        # Prefer the actual game's descriptor; the generated reference may lag.
        descriptor = getattr(token, "DESCRIPTOR", None)
        if descriptor is not None:
            enum = descriptor.fields_by_name["type"].enum_type.values_by_number
            kind = enum[kind].name if kind in enum else "UNKNOWN"
        else:
            kind = TOKEN_TYPES.get(kind, "UNKNOWN")
    result = {"type": kind}
    for name in TOKEN_FIELDS:
        if name not in values:
            continue
        value = values[name]
        if isinstance(value, str):
            if len(value) > budget[1]:
                result["error"] = "localization_text_limit"
            result[name] = value[:max(0, budget[1])]
            budget[1] -= len(result[name])
        elif isinstance(value, (int, float, bool)) and (not isinstance(value, float) or math.isfinite(value)):
            result[name] = str(value) if name == "sim_id" else value
    if "text_string" in values:
        result["text_string"] = snapshot_localized(values["text_string"], depth + 1, budget)
    if "sim_list" in values:
        # SubTokenData has no type field. Its parent SIM_LIST supplies the type.
        result["sim_list"] = [snapshot_token(item, depth + 1, budget, "SIM") for item in values["sim_list"][:MAX_TOKENS]]
        if len(values["sim_list"]) > MAX_TOKENS:
            result["error"] = "localization_token_limit"
    if "date_and_time" in values:
        budget[0] -= 1
        if budget[0] < 0 or depth + 1 >= MAX_DEPTH:
            result["error"] = "localization_structure_limit"
        else:
            fields = _fields(values["date_and_time"])
            result["date_and_time"] = {name: fields[name] for name in DATE_FIELDS
                                       if name in fields and isinstance(fields[name], int)}
    return result


def snapshot_localized(value, depth=0, budget=None):
    budget = [MAX_NODES, MAX_TEXT] if budget is None else budget
    budget[0] -= 1
    if depth >= MAX_DEPTH or budget[0] < 0:
        return {"hash": None, "tokens": [], "error": "localization_structure_limit"}
    if isinstance(value, str):
        result = {"raw_text": value[:max(0, budget[1])]}
        if len(value) > budget[1]:
            result["error"] = "localization_text_limit"
        budget[1] -= len(result["raw_text"])
        return result
    if isinstance(value, int):
        return {"hash": hash_key(value), "tokens": []}
    fields = _fields(value) if value is not None else {}
    result = {"hash": hash_key(fields.get("hash", getattr(value, "_string_id", None))),
              "tokens": [snapshot_token(token, depth + 1, budget) for token in fields.get("tokens", ())[:MAX_TOKENS]]}
    if len(fields.get("tokens", ())) > MAX_TOKENS:
        result["error"] = "localization_token_limit"
    return result


class Localizer:
    def __init__(self, strings=None, metadata=None, english_strings=None, english_metadata=None, overrides=None):
        if strings is None:
            try:
                data = pkgutil.get_data("context_overlay", "strings_zh.json")
            except OSError:
                data = None
            strings = json.loads(data.decode("utf-8")) if data else {}
            try:
                data = pkgutil.get_data("context_overlay", "string_sources.json")
            except OSError:
                data = None
            metadata = json.loads(data.decode("utf-8")) if data else None
        if english_strings is None:
            try:
                data = pkgutil.get_data("context_overlay", "strings_en.json")
            except OSError:
                data = None
            english_strings = json.loads(data.decode("utf-8")) if data else {}
        if english_metadata is None:
            try:
                data = pkgutil.get_data("context_overlay", "string_sources_en.json")
            except OSError:
                data = None
            english_metadata = json.loads(data.decode("utf-8")) if data else None
        if overrides is None:
            try:
                data = pkgutil.get_data("context_overlay", "object_name_overrides.json")
            except OSError:
                data = None
            overrides = json.loads(data.decode("utf-8")) if data else None
        self.strings = strings
        self.metadata = metadata or {}
        self.english_strings = english_strings or {}
        self.english_metadata = english_metadata or {}
        self.object_name_overrides = overrides or {}
        if self.metadata and self.metadata.get("format") != "string_sources_v1":
            raise ValueError("Unsupported string source metadata")
        if self.english_metadata and self.english_metadata.get("format") != "string_sources_v1":
            raise ValueError("Unsupported English string source metadata")
        if self.object_name_overrides and self.object_name_overrides.get("format") != "object_name_overrides_v1":
            raise ValueError("Unsupported object name override metadata")

    def name(self, localized, fallback=None):
        return self.from_evidence(snapshot_localized(localized), fallback)

    def object_name(self, localized, fallback=None, definition_id=None, tuning_name=None):
        """Resolve a world object name with reviewed and technical fallbacks."""
        evidence = snapshot_localized(localized)
        # Object labels are the one text role for which an official English
        # resource is an intentional fallback.  This must also cover a direct
        # localized key, not only a wrapper such as {0.ObjectName}.
        result = self.from_evidence(evidence, fallback, allow_english=True)
        token = (evidence.get("tokens") or [{}])[0]
        if token.get("type") != "OBJECT" or token.get("custom_name"):
            return result

        override = self._definition_override(definition_id)
        if override:
            return self._override_result(result, override, "definition_id", str(definition_id),
                                          definition_id, tuning_name)
        if result.get("status") in OBJECT_NAME_STATUSES:
            return result
        if fallback:
            technical = dict(result)
            technical["text"] = "未命名对象（{}）".format(fallback)
            technical["status"] = "technical_fallback"
            technical["reason"] = "no_official_object_name"
            technical["fallback"] = fallback
            source = dict(technical.get("source", {}))
            source.update({"kind": "technical_object_fallback",
                           "definition_id": str(definition_id) if definition_id is not None else None,
                           "tuning_name": tuning_name,
                           "original_status": result.get("status")})
            technical["source"] = source
            return technical
        return result

    def _metadata_source(self, metadata, key, language):
        source = metadata.get("keys", {}).get(key) if metadata else None
        if isinstance(source, int):
            source = metadata["groups"][source]
        if not source:
            return None
        return dict(source, game_version=metadata.get("provenance", {}).get("game_version"),
                    sources=list(source["sources"]), language=language, third_party_overrides="not_verified")

    def _definition_override(self, definition_id):
        if definition_id is None:
            return None
        return self.object_name_overrides.get("definitions", {}).get(str(definition_id))

    def _catalog_override(self, key):
        if not key:
            return None
        return self.object_name_overrides.get("catalog_keys", {}).get(key)

    @staticmethod
    def _override_result(result, override, basis, value, definition_id=None, tuning_name=None):
        text = override.get("text")
        if not isinstance(text, str) or not text.strip():
            return result
        resolved = dict(result)
        resolved["text"] = text
        resolved["status"] = override.get("status", "reviewed_override")
        resolved["reason"] = override.get("reason", "project_object_name_override")
        source = dict(resolved.get("source", {}))
        source.update({"kind": "object_name_override", "basis": basis, "key": value,
                       "definition_id": str(definition_id) if definition_id is not None else None,
                       "tuning_name": tuning_name})
        if "source" in override:
            source["override_source"] = override["source"]
        resolved["source"] = source
        return resolved

    def from_evidence(self, evidence, fallback=None, depth=0, budget=None, allow_english=False, object_key=False):
        budget = [MAX_NODES] if budget is None else budget
        budget[0] -= 1
        key = hash_key(evidence.get("hash"))
        result = {"hash": key, "localization": evidence}
        if object_key:
            override = self._catalog_override(key)
            if override:
                return self._override_result(result, override, "catalog_name_key", key)
        source = self._metadata_source(self.metadata, key, "CHS_CN")
        blocked_reason = None
        if source:
            result["string_source"] = source
            if source["status"] != "selected":
                blocked_reason = ("string_key_overridden" if source["status"] == "overridden_only"
                                  else "string_resource_conflict")
                if not allow_english:
                    return dict(result, text=fallback, status="unmapped", reason=blocked_reason)
        if budget[0] < 0:
            return dict(result, text=fallback, status="unmapped", reason="localization_work_limit")
        if depth >= MAX_DEPTH or evidence.get("error"):
            return dict(result, text=fallback, status="unmapped", reason=evidence.get("error", "localization_depth_limit"))
        if "raw_text" in evidence:
            if len(evidence["raw_text"]) > MAX_TEXT:
                return dict(result, text=fallback, status="unmapped", reason="localization_text_limit")
            return dict(result, text=evidence["raw_text"], status="raw_text")
        if not key or key == "0x00000000":
            return dict(result, text=fallback, status="no_display_name", reason="no_localized_string_key")
        template = self.strings.get(key)
        language = "CHS_CN"
        if allow_english and (blocked_reason or template is None or not template.strip()):
            english_template = self.english_strings.get(key)
            english_source = self._metadata_source(self.english_metadata, key, "ENG_US")
            english_blocked = english_source and english_source["status"] != "selected"
            if isinstance(english_template, str) and english_template.strip() and not english_blocked:
                template = english_template
                language = "ENG_US"
                result["string_source"] = english_source or {
                    "language": language, "sources": [], "third_party_overrides": "not_verified"}
                result["fallback_from"] = {"language": "CHS_CN", "key": key,
                                            "reason": blocked_reason or (
                                                "string_key_missing" if self.strings.get(key) is None
                                                else "localized_text_empty")}
                if source:
                    result["fallback_from"]["string_source"] = source
            elif blocked_reason:
                return dict(result, text=fallback, status="unmapped", reason=blocked_reason)
        if template is None:
            return dict(result, text=fallback, status="unmapped", reason="string_key_missing")
        if len(template) > MAX_TEXT:
            return dict(result, text=fallback, status="unmapped", reason="localization_text_limit")
        missing = []
        nested_fallbacks = []
        text = self._template(template, evidence.get("tokens", []), missing, depth, budget,
                              nested_fallbacks=nested_fallbacks, allow_english=allow_english)
        status = "unresolved_tokens" if missing else ("english_fallback" if language == "ENG_US" else "resolved")
        if not missing and nested_fallbacks:
            if any(item.get("status") == "english_fallback" for item in nested_fallbacks):
                status = "english_fallback"
            result["fallback_sources"] = nested_fallbacks
        result.update(text=text, status=status,
                      format_profile=FORMAT_PROFILE)
        if not text.strip() and not missing:
            result.update(text=fallback, status="empty_display_name", reason="localized_text_empty", template=template)
        result["template"] = template
        if "{" in template:
            result["unresolved"] = missing
        if fallback is not None:
            result["fallback"] = fallback
        return result

    @staticmethod
    def _unresolved(expression, missing, reason, **details):
        missing.append(dict(expression=expression, reason=reason, category=gap_category(reason), **details))
        return "〈未解析：" + expression[:100] + "〉"

    def _template(self, template, tokens, missing, depth, budget, nested_fallbacks=None, allow_english=False):
        if depth >= MAX_DEPTH:
            return self._unresolved("嵌套文本", missing, "localization_depth_limit")
        output, position, length = [], 0, 0
        while position < len(template):
            budget[0] -= 1
            if budget[0] < 0:
                return self._unresolved("文本展开", missing, "localization_work_limit")
            start = template.find("{", position)
            if start < 0:
                output.append(template[position:])
                length += len(template) - position
                break
            output.append(template[position:start])
            length += start - position
            end, balance = start + 1, 1
            while end < len(template) and balance:
                balance += (template[end] == "{") - (template[end] == "}")
                end += 1
            if balance:
                output.append(self._unresolved(template[start:], missing, "malformed_template"))
                break
            expression = template[start + 1:end - 1]
            match = re.fullmatch(r"([MFmf]?)(\d+)\.(.*)", expression, re.S)
            if not match:
                value = self._unresolved(expression, missing, "unsupported_expression")
            else:
                selector, index, attr = match.groups()
                index = int(index) if len(index) <= 3 else MAX_TOKENS
                token = tokens[index] if index < min(len(tokens), MAX_TOKENS) else None
                if token is None or token.get("error"):
                    value = self._unresolved(expression, missing,
                        "missing_token" if token is None else "incomplete_token", token_index=index)
                elif selector:
                    # Custom pronouns and neutral gender require the full client grammar.
                    # EA emits strings such as "|||||" when no custom forms are
                    # present. Those empty slots must use the normal fallback.
                    custom_pronouns = any(part.strip() for part in token.get("packed_pronouns", "").split("|"))
                    if token.get("type") != "SIM" or custom_pronouns or "is_female" not in token or token.get("gender_flags") in (0, 0x3000):
                        value = self._unresolved(expression, missing, "unsupported_or_missing_gender")
                    else:
                        chosen = bool(token["is_female"]) == (selector.upper() == "F")
                        value = self._template(attr, tokens, missing, depth + 1, budget,
                                               nested_fallbacks=nested_fallbacks,
                                               allow_english=allow_english) if chosen else ""
                else:
                    value = self._value(token, attr, missing, depth + 1, budget,
                                        nested_fallbacks=nested_fallbacks, allow_english=allow_english)
                    if value is None:
                        expected = VALUE_TYPES.get(attr)
                        reason = ("unsupported_token_format" if expected is None else
                                  "token_type_mismatch" if token.get("type") not in expected else "missing_token_field")
                        value = self._unresolved(expression, missing, reason, token_index=index,
                            actual_type=token.get("type"), expected_types=list(expected) if expected else [])
            output.append(value)
            length += len(value)
            position = end
            if length > MAX_TEXT:
                return self._unresolved("文本长度", missing, "localization_text_limit")
        if length > MAX_TEXT:
            return self._unresolved("文本长度", missing, "localization_text_limit")
        return "".join(output)

    def _key_text(self, key, missing, depth, budget, nested_fallbacks=None, allow_english=False, object_key=False):
        result = self.from_evidence({"hash": key, "tokens": []}, depth=depth, budget=budget,
                                    allow_english=allow_english, object_key=object_key)
        if result["status"] not in OBJECT_NAME_STATUSES:
            return None
        if nested_fallbacks is not None and result["status"] != "resolved":
            nested_fallbacks.append({"hash": result.get("hash"), "status": result.get("status"),
                                     "text": result.get("text"), "string_source": result.get("string_source"),
                                     "source": result.get("source"), "fallback_from": result.get("fallback_from")})
        return result["text"]

    def _value(self, token, attr, missing, depth, budget, nested_fallbacks=None, allow_english=False):
        kind = token.get("type")
        if attr == "String":
            if kind == "RAW_TEXT":
                return token.get("raw_text")
            if kind == "STRING" and token.get("text_string"):
                result = self.from_evidence(token["text_string"], depth=depth, budget=budget)
                if result["status"] in ("resolved", "raw_text", "unresolved_tokens"):
                    missing.extend(result.get("unresolved", []))
                    return result["text"]
        if (attr in ("Number", "Money") and kind == "NUMBER" and isinstance(token.get("number"), (int, float))
                and not isinstance(token["number"], bool) and math.isfinite(token["number"])):
            if attr == "Number":
                return "{:g}".format(token["number"])
            # Deterministic Chinese output, not a claim of client-identical
            # formatting. Do not guess rounding for fractional currency.
            amount = token["number"]
            if amount == int(amount):
                return "{} 模拟币".format(int(amount))
            return self._unresolved(attr, missing, "unsupported_token_format")
        if kind == "DATE_AND_TIME" and attr in ("TimeShort", "DayOfWeekShort", "DayOfWeekLong"):
            data = token.get("date_and_time") or {}
            # Custom client format hashes and real-calendar dates require a
            # separate adapter. EA DateAndTime emits month/year zero and a
            # weekday in date, with Sunday represented by 7.
            if data.get("date_and_time_format_hash") not in (None, 0):
                return self._unresolved(attr, missing, "unsupported_token_format")
            if attr == "TimeShort":
                hour, minute = data.get("hours"), data.get("minutes")
                if (type(hour) is int and type(minute) is int and 0 <= hour < 24 and 0 <= minute < 60):
                    return "{:02d}:{:02d}".format(hour, minute)
                if hour is not None and minute is not None:
                    return self._unresolved(attr, missing, "invalid_token_value")
            elif data.get("month") == 0 and data.get("full_year") == 0:
                day = data.get("date")
                if type(day) is int and 1 <= day <= 7:
                    return ("周" if attr == "DayOfWeekShort" else "星期") + "一二三四五六日"[day - 1]
            elif "month" in data and "full_year" in data:
                return self._unresolved(attr, missing, "unsupported_token_format")
        if kind == "SIM":
            if attr == "SimFirstName":
                return token.get("first_name") or None
            if attr == "SimLastName":
                return token.get("last_name") or None
            if attr in ("SimName", "SimFullName", "ObjectName"):
                if token.get("full_name_key"):
                    return self._key_text(token["full_name_key"], missing, depth, budget,
                                          nested_fallbacks=nested_fallbacks)
                if token.get("name_prefix_key") or token.get("name_prefix_string"):
                    return None  # Client-specific prefix formatting is not verified.
                return " ".join(item for item in (token.get("first_name"), token.get("last_name")) if item) or None
        if kind == "OBJECT" and attr in ("ObjectName", "ObjectDescription", "ObjectCatalogName", "ObjectCatalogDescription"):
            custom, key = ("custom_name", "catalog_name_key") if attr in ("ObjectName", "ObjectCatalogName") else ("custom_description", "catalog_description_key")
            object_name_key = attr in ("ObjectName", "ObjectCatalogName")
            if attr.startswith("ObjectCatalog"):
                return self._key_text(token.get(key), missing, depth, budget,
                                      nested_fallbacks=nested_fallbacks,
                                      allow_english=allow_english or object_name_key,
                                      object_key=object_name_key)
            return token.get(custom) or self._key_text(token.get(key), missing, depth, budget,
                                                       nested_fallbacks=nested_fallbacks,
                                                       allow_english=allow_english or object_name_key,
                                                       object_key=object_name_key)
        return None
