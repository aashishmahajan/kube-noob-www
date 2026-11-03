"""kube-noob in the browser: the Toolkit the MCP server serves, run by Pyodide in a Web Worker.

Offline lock on, learning off, no cluster, no web: the page sends nothing anywhere. Only tools that are read-only and
offline are exposed; their names, descriptions and JSON schemas come from the same ToolSpecs as the MCP server.

The arguments envelope is read through a bracket count, not straight through ``json.loads``. This runtime is
Python 3.14 on WebAssembly, where ``sys.getrecursionlimit()`` does not bound ``json.loads`` and a deeply nested
document overflows the C stack as a fatal JavaScript ``RangeError`` rather than raising ``RecursionError`` -- so
``except json.JSONDecodeError`` cannot catch it and the worker dies instead of returning an error. ``MAX_ARGUMENTS``
bounds the size and not the depth: 512 KB holds well over a hundred thousand brackets. Counting first is the only
thing that refuses it here.
"""

from __future__ import annotations

import json

from kube_noob import __version__
from kube_noob.config import load_settings
from kube_noob.errors import KubeNoobError
from kube_noob.knowledge_status import line as knowledge_line
from kube_noob.toolkit import Toolkit
from pydantic import ValidationError

try:  # the product gained this in 0.7.2; the site also builds from older tags
    from kube_noob.sanitize import MAX_JSON_DEPTH, JsonTooDeep, loads_bounded
except ImportError:  # pragma: no cover - exercised only against a product release that predates the guard
    MAX_JSON_DEPTH = 64

    class JsonTooDeep(ValueError):  # type: ignore[no-redef]
        """Stand-in for the product's own, so the handler below is the same either way."""

    def _nesting_ok(text: str, limit: int = MAX_JSON_DEPTH) -> bool:
        """The same count as ``kube_noob.sanitize.json_nesting_ok``, inlined for older product releases.

        Deliberately the simple character loop rather than the product's two-pass version: the arguments envelope
        is at most MAX_ARGUMENTS, and a copy that is obviously equivalent is worth more here than a fast one.
        Delete this branch once the site no longer builds from a tag older than 0.7.2.
        """
        depth = 0
        in_string = escaped = False
        for ch in text:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch in "{[":
                depth += 1
                if depth > limit:
                    return False
            elif ch in "}]":
                depth -= 1
        return True

    def loads_bounded(data: str, limit: int = MAX_JSON_DEPTH):  # type: ignore[misc]
        if not _nesting_ok(data, limit):
            raise JsonTooDeep(f"JSON nested too deeply (more than {limit} levels)")
        return json.loads(data)

BROWSER_TOOLS = (
    "run_protocol",
    "validate_manifests",
    "audit_manifests",
    "red_team",
    "fix_manifests",
    "clarify_requirements",
    "generate_manifests",
    "generate_helm_chart",
    "search_knowledge",
    "get_knowledge",
    "api_history",
    "explain_rule",
    "list_rules",
    "diagnose",
    "known_issues",
    "explain_denial",
    "check_network_policy",
)
MAX_ARGUMENTS = 512 * 1024

_settings = load_settings(
    overrides={"offline": True, "dataDir": "/tmp/kube-noob", "learningEnabled": False, "webEnabled": False},  # noqa: S108 - Pyodide in-memory FS, private to the tab
)
_toolkit = Toolkit(_settings, shared=True)
_specs = {s.name: s for s in _toolkit.specs() if s.name in BROWSER_TOOLS and s.read_only and not s.open_world}
_knowledge = knowledge_line(_settings.data_dir)


def info() -> str:
    return json.dumps({"version": __version__, "knowledgeAsOf": _knowledge, "tools": sorted(_specs)})


def tools() -> str:
    """Tool definitions in the shape the Claude Messages API takes (name, description, input_schema)."""
    out = []
    for name in BROWSER_TOOLS:
        spec = _specs.get(name)
        if spec is None:
            continue
        out.append(
            {
                "name": "kube_noob_" + name,
                "title": spec.title,
                "description": spec.description,
                "input_schema": spec.model.model_json_schema(by_alias=True),
            }
        )
    return json.dumps(out)


def call(name: str, arguments: str) -> str:
    """Run one tool. Returns JSON: {"ok": true, "result": ...} or {"ok": false, "error": "..."}."""
    name = name.removeprefix("kube_noob_")
    spec = _specs.get(name)
    if spec is None:
        return json.dumps({"ok": False, "error": f"unknown or unavailable tool: {name}"})
    if len(arguments) > MAX_ARGUMENTS:
        return json.dumps({"ok": False, "error": f"arguments larger than {MAX_ARGUMENTS} bytes"})
    try:
        validated = spec.model.model_validate(loads_bounded(arguments or "{}"))
        result = spec.fn(validated)
        if isinstance(result, dict):
            result.setdefault("knowledgeAsOf", _knowledge)
        return json.dumps({"ok": True, "result": result}, default=str)
    except JsonTooDeep as exc:
        # Before the count, this input killed the worker with a fatal RangeError instead of answering.
        return json.dumps({"ok": False, "error": str(exc)})
    except json.JSONDecodeError as exc:
        return json.dumps({"ok": False, "error": f"arguments are not valid JSON: {exc}"})
    except ValidationError as exc:
        return json.dumps({"ok": False, "error": f"invalid arguments: {str(exc)[:2000]}"})
    except (KubeNoobError, ValueError, KeyError) as exc:
        return json.dumps({"ok": False, "error": str(exc)[:2000]})
