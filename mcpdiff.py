"""mcp-contract-diff: classify breaking changes between two MCP `tools/list` snapshots.

Zero dependencies. No network. The whole point is that an MCP server can rename a
tool, add a required argument, or narrow an enum between two releases, and every
client that cached the old contract breaks silently. This module turns two captured
`tools/list` payloads into a per-field change list with a severity and a witness path.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

BREAKING = "breaking"
COMPATIBLE = "compatible"
INFO = "info"

SEVERITY_ORDER = {INFO: 0, COMPATIBLE: 1, BREAKING: 2}


class InputError(Exception):
    """Raised for malformed input; the CLI maps it to exit code 2."""


# --------------------------------------------------------------------------- #
# Loading / normalising
# --------------------------------------------------------------------------- #

def load_tools(payload: Any) -> Dict[str, dict]:
    """Accept a JSON-RPC response, a bare result, or a list; return name -> tool."""
    if isinstance(payload, list):
        tools = payload
    elif isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        tools = payload["result"].get("tools")
    elif isinstance(payload, dict) and "tools" in payload:
        tools = payload["tools"]
    else:
        raise InputError("expected a tools/list response, a {'tools': [...]} object, or a list")

    if not isinstance(tools, list):
        raise InputError("'tools' must be a list")

    index: Dict[str, dict] = {}
    for pos, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise InputError("tool #%d is not an object" % pos)
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise InputError("tool #%d has no string 'name'" % pos)
        if name in index:
            raise InputError("duplicate tool name %r" % name)
        schema = tool.get("inputSchema")
        if schema is None:
            schema = {"type": "object", "properties": {}}
        if not isinstance(schema, dict):
            raise InputError("tool %r: inputSchema must be an object" % name)
        index[name] = {"name": name, "description": tool.get("description") or "", "inputSchema": schema}
    return index


def _type_set(schema: dict) -> Optional[frozenset]:
    t = schema.get("type")
    if t is None:
        return None
    if isinstance(t, str):
        return frozenset([t])
    if isinstance(t, list) and all(isinstance(x, str) for x in t):
        return frozenset(t)
    return None


def _enum_set(schema: dict) -> Optional[frozenset]:
    values = schema.get("enum")
    if values is None:
        return None
    try:
        return frozenset(json.dumps(v, sort_keys=True) for v in values)
    except TypeError:
        return None


def _required(schema: dict) -> set:
    req = schema.get("required") or []
    if not isinstance(req, list):
        return set()
    return {r for r in req if isinstance(r, str)}


def _props(schema: dict) -> Dict[str, dict]:
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        return {}
    return {k: v for k, v in props.items() if isinstance(v, dict)}


# --------------------------------------------------------------------------- #
# Diffing
# --------------------------------------------------------------------------- #

def _add(changes: List[dict], kind: str, severity: str, tool: str, path: str, detail: str) -> None:
    changes.append({"kind": kind, "severity": severity, "tool": tool, "path": path, "detail": detail})


def _diff_property(tool: str, prop: str, old: dict, new: dict, path: str, changes: List[dict]) -> None:
    old_t, new_t = _type_set(old), _type_set(new)
    if old_t and new_t and old_t != new_t:
        lost = old_t - new_t
        if lost:
            _add(changes, "TYPE_NARROWED", BREAKING, tool, path + ".type",
                 "type %s -> %s (no longer accepts %s)" % (sorted(old_t), sorted(new_t), sorted(lost)))
        else:
            _add(changes, "TYPE_WIDENED", COMPATIBLE, tool, path + ".type",
                 "type %s -> %s" % (sorted(old_t), sorted(new_t)))

    old_e, new_e = _enum_set(old), _enum_set(new)
    if old_e is None and new_e is not None:
        _add(changes, "ENUM_INTRODUCED", BREAKING, tool, path + ".enum",
             "free-form value restricted to %d allowed value(s)" % len(new_e))
    elif old_e is not None and new_e is not None and old_e != new_e:
        removed = old_e - new_e
        if removed:
            _add(changes, "ENUM_NARROWED", BREAKING, tool, path + ".enum",
                 "removed enum value(s): %s" % ", ".join(sorted(removed)))
        added = new_e - old_e
        if added:
            _add(changes, "ENUM_WIDENED", COMPATIBLE, tool, path + ".enum",
                 "added enum value(s): %s" % ", ".join(sorted(added)))

    old_ap, new_ap = old.get("additionalProperties"), new.get("additionalProperties")
    if old_ap is not False and new_ap is False:
        _add(changes, "ADDITIONAL_PROPERTIES_FORBIDDEN", BREAKING, tool, path + ".additionalProperties",
             "unknown properties are now rejected")

    if "default" in old and "default" in new:
        if json.dumps(old["default"], sort_keys=True) != json.dumps(new["default"], sort_keys=True):
            _add(changes, "DEFAULT_CHANGED", COMPATIBLE, tool, path + ".default",
                 "default %s -> %s (callers omitting the field change behaviour)"
                 % (json.dumps(old["default"]), json.dumps(new["default"])))
    elif "default" in old and "default" not in new:
        _add(changes, "DEFAULT_REMOVED", COMPATIBLE, tool, path + ".default",
             "default %s removed" % json.dumps(old["default"]))

    if old.get("description") != new.get("description"):
        _add(changes, "DESCRIPTION_CHANGED", INFO, tool, path + ".description",
             "description text changed")


def _diff_tool(name: str, old: dict, new: dict, changes: List[dict]) -> None:
    base = "tools[%s].inputSchema" % name
    old_schema, new_schema = old["inputSchema"], new["inputSchema"]

    old_req, new_req = _required(old_schema), _required(new_schema)
    added = new_req - old_req
    if added:
        _add(changes, "REQUIRED_PARAM_ADDED", BREAKING, name, base + ".required",
             "new required argument(s): %s" % ", ".join(sorted(added)))
    relaxed = old_req - new_req
    if relaxed:
        _add(changes, "REQUIRED_RELAXED", COMPATIBLE, name, base + ".required",
             "argument(s) no longer required: %s" % ", ".join(sorted(relaxed)))

    old_ap, new_ap = old_schema.get("additionalProperties"), new_schema.get("additionalProperties")
    if old_ap is not False and new_ap is False:
        _add(changes, "ADDITIONAL_PROPERTIES_FORBIDDEN", BREAKING, name, base + ".additionalProperties",
             "unknown top-level arguments are now rejected")

    old_props, new_props = _props(old_schema), _props(new_schema)
    for prop in sorted(set(old_props) - set(new_props)):
        _add(changes, "PARAM_REMOVED", BREAKING, name, base + ".properties." + prop,
             "argument %r was removed; callers that still send it now fail" % prop)
    for prop in sorted(set(new_props) - set(old_props)):
        sev = BREAKING if prop in new_req else COMPATIBLE
        _add(changes, "PARAM_ADDED", sev, name, base + ".properties." + prop,
             "argument %r added%s" % (prop, " and is required" if prop in new_req else ""))
    for prop in sorted(set(old_props) & set(new_props)):
        _diff_property(name, prop, old_props[prop], new_props[prop],
                       base + ".properties." + prop, changes)

    if (old.get("description") or "") != (new.get("description") or ""):
        _add(changes, "DESCRIPTION_CHANGED", INFO, name, "tools[%s].description" % name,
             "tool description text changed")


def diff(old: Dict[str, dict], new: Dict[str, dict]) -> List[dict]:
    changes: List[dict] = []
    for name in sorted(set(old) - set(new)):
        _add(changes, "TOOL_REMOVED", BREAKING, name, "tools[%s]" % name,
             "tool no longer present in tools/list")
    for name in sorted(set(new) - set(old)):
        _add(changes, "TOOL_ADDED", COMPATIBLE, name, "tools[%s]" % name,
             "new tool present in tools/list")
    for name in sorted(set(old) & set(new)):
        _diff_tool(name, old[name], new[name], changes)
    changes.sort(key=lambda c: (-SEVERITY_ORDER[c["severity"]], c["tool"], c["kind"], c["path"]))
    return changes


def summarise(changes: Iterable[dict]) -> Dict[str, int]:
    out = {"breaking": 0, "compatible": 0, "info": 0}
    for c in changes:
        out[c["severity"]] += 1
    return out


def build_report(old_payload: Any, new_payload: Any, old_label: str, new_label: str) -> dict:
    old, new = load_tools(old_payload), load_tools(new_payload)
    changes = diff(old, new)
    counts = summarise(changes)
    return {
        "schema_version": 1,
        "old": {"label": old_label, "tool_count": len(old)},
        "new": {"label": new_label, "tool_count": len(new)},
        "verdict": "breaking" if counts["breaking"] else ("compatible" if counts["compatible"] else "identical"),
        "counts": counts,
        "changes": changes,
    }
