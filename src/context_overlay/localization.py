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
    return {name: getattr(message, name) for name in TOKEN_FIELDS + ("type", "text_string", "sim_list", "tokens", "hash")
            if hasattr(message, name)}


def snapshot_token(token, depth=0, budget=None):
    budget = [MAX_NODES, MAX_TEXT] if budget is None else budget
    budget[0] -= 1
    if depth >= MAX_DEPTH or budget[0] < 0:
        return {"type": "INVALID", "error": "localization_structure_limit"}
    values = _fields(token)
    kind = values.get("type", "INVALID")
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
        result["sim_list"] = [snapshot_token(item, depth + 1, budget) for item in values["sim_list"][:MAX_TOKENS]]
        if len(values["sim_list"]) > MAX_TOKENS:
            result["error"] = "localization_token_limit"
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
    def __init__(self, strings=None):
        if strings is None:
            try:
                data = pkgutil.get_data("context_overlay", "strings_zh.json")
            except OSError:
                data = None
            strings = json.loads(data.decode("utf-8")) if data else {}
        self.strings = strings

    def name(self, localized, fallback=None):
        return self.from_evidence(snapshot_localized(localized), fallback)

    def from_evidence(self, evidence, fallback=None, depth=0, budget=None):
        budget = [MAX_NODES] if budget is None else budget
        budget[0] -= 1
        key = hash_key(evidence.get("hash"))
        result = {"hash": key, "localization": evidence}
        if budget[0] < 0:
            return dict(result, text=fallback, status="unmapped", reason="localization_work_limit")
        if depth >= MAX_DEPTH or evidence.get("error"):
            return dict(result, text=fallback, status="unmapped", reason=evidence.get("error", "localization_depth_limit"))
        if "raw_text" in evidence:
            if len(evidence["raw_text"]) > MAX_TEXT:
                return dict(result, text=fallback, status="unmapped", reason="localization_text_limit")
            return dict(result, text=evidence["raw_text"], status="raw_text")
        template = self.strings.get(key)
        if not key or key == "0x00000000":
            return dict(result, text=fallback, status="no_display_name", reason="no_localized_string_key")
        if template is None:
            return dict(result, text=fallback, status="unmapped", reason="string_key_missing")
        if len(template) > MAX_TEXT:
            return dict(result, text=fallback, status="unmapped", reason="localization_text_limit")
        missing = []
        text = self._template(template, evidence.get("tokens", []), missing, depth, budget)
        result.update(text=text, status="unresolved_tokens" if missing else "resolved")
        if not text.strip() and not missing:
            result.update(text=fallback, status="empty_display_name", reason="localized_text_empty", template=template)
        if "{" in template:
            result.update(template=template, unresolved=missing)
        if fallback is not None:
            result["fallback"] = fallback
        return result

    @staticmethod
    def _unresolved(expression, missing, reason):
        missing.append({"expression": expression, "reason": reason})
        return "〈未解析：" + expression[:100] + "〉"

    def _template(self, template, tokens, missing, depth, budget):
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
            match = re.fullmatch(r"([MF]?)(\d+)\.(.*)", expression, re.S)
            if not match:
                value = self._unresolved(expression, missing, "unsupported_expression")
            else:
                selector, index, attr = match.groups()
                index = int(index) if len(index) <= 3 else MAX_TOKENS
                token = tokens[index] if index < min(len(tokens), MAX_TOKENS) else None
                if token is None or token.get("error"):
                    value = self._unresolved(expression, missing, "missing_or_incomplete_token")
                elif selector:
                    # Custom pronouns and neutral gender require the full client grammar.
                    if token.get("type") != "SIM" or token.get("packed_pronouns") or "is_female" not in token or token.get("gender_flags") == 0:
                        value = self._unresolved(expression, missing, "unsupported_or_missing_gender")
                    else:
                        chosen = bool(token["is_female"]) == (selector == "F")
                        value = self._template(attr, tokens, missing, depth + 1, budget) if chosen else ""
                else:
                    value = self._value(token, attr, missing, depth + 1, budget)
                    if value is None:
                        value = self._unresolved(expression, missing, "unsupported_or_missing_token_field")
            output.append(value)
            length += len(value)
            position = end
            if length > MAX_TEXT:
                return self._unresolved("文本长度", missing, "localization_text_limit")
        if length > MAX_TEXT:
            return self._unresolved("文本长度", missing, "localization_text_limit")
        return "".join(output)

    def _key_text(self, key, missing, depth, budget):
        result = self.from_evidence({"hash": key, "tokens": []}, depth=depth, budget=budget)
        if result["status"] != "resolved":
            return None
        return result["text"]

    def _value(self, token, attr, missing, depth, budget):
        kind = token.get("type")
        if attr == "String":
            if kind == "RAW_TEXT":
                return token.get("raw_text")
            if kind == "STRING" and token.get("text_string"):
                result = self.from_evidence(token["text_string"], depth=depth, budget=budget)
                if result["status"] in ("resolved", "raw_text", "unresolved_tokens"):
                    missing.extend(result.get("unresolved", []))
                    return result["text"]
        if (attr == "Number" and kind == "NUMBER" and isinstance(token.get("number"), (int, float))
                and math.isfinite(token["number"])):
            return "{:g}".format(token["number"])
        if kind == "SIM":
            if attr == "SimFirstName":
                return token.get("first_name") or None
            if attr == "SimLastName":
                return token.get("last_name") or None
            if attr in ("SimName", "SimFullName"):
                if token.get("full_name_key"):
                    return self._key_text(token["full_name_key"], missing, depth, budget)
                if token.get("name_prefix_key") or token.get("name_prefix_string"):
                    return None  # Client-specific prefix formatting is not verified.
                return " ".join(item for item in (token.get("first_name"), token.get("last_name")) if item) or None
        if kind == "OBJECT" and attr in ("ObjectName", "ObjectDescription"):
            custom, key = ("custom_name", "catalog_name_key") if attr == "ObjectName" else ("custom_description", "catalog_description_key")
            return token.get(custom) or self._key_text(token.get(key), missing, depth, budget)
        return None
