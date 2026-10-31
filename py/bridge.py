"""kube-noob in the browser: the Toolkit the MCP server serves, run by Pyodide in a Web Worker.

Offline lock on, learning off, no cluster, no web: the page sends nothing anywhere. Only tools that are read-only and
offline are exposed; their names, descriptions and JSON schemas come from the same ToolSpecs as the MCP server.
"""

from __future__ import annotations

import json

from kube_noob import __version__
from kube_noob.config import load_settings
from kube_noob.errors import KubeNoobError
from kube_noob.knowledge_status import line as knowledge_line
from kube_noob.toolkit import Toolkit
from pydantic import ValidationError

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
        validated = spec.model.model_validate(json.loads(arguments or "{}"))
        result = spec.fn(validated)
        if isinstance(result, dict):
            result.setdefault("knowledgeAsOf", _knowledge)
        return json.dumps({"ok": True, "result": result}, default=str)
    except json.JSONDecodeError as exc:
        return json.dumps({"ok": False, "error": f"arguments are not valid JSON: {exc}"})
    except ValidationError as exc:
        return json.dumps({"ok": False, "error": f"invalid arguments: {str(exc)[:2000]}"})
    except (KubeNoobError, ValueError, KeyError) as exc:
        return json.dumps({"ok": False, "error": str(exc)[:2000]})
