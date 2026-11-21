"""Central tool registry, MCP JSON Schema exporter, and uniform dispatcher."""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .core.types import err_result
from .iface_extractor import tool_extract_interfaces
from .repo_indexer import tool_index_repo, tool_resolve_deps
from .result_normalizer import tool_normalize_sim_result
from .sandbox_copier import tool_copy_sandbox
from .tb_emitter import tool_emit_testbench, tool_validate_contract
from .visual_renderer import tool_render_visual_report

TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "index_repo": {
        "fn": tool_index_repo,
        "description": "Syntax-parse an RTL repo into a module, instantiation, and header index.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}}, "required": ["project_root"]},
    },
    "resolve_deps": {
        "fn": tool_resolve_deps,
        "description": "Compute BFS transitive dependency closure and DAG edges for a target module.",
        "inputSchema": {"type": "object", "properties": {"target_module": {"type": "string"}, "repo_index": {"type": "object"}, "project_root": {"type": "string"}}, "required": ["target_module"]},
    },
    "copy_sandbox": {
        "fn": tool_copy_sandbox,
        "description": "Copy resolved RTL/header files into an isolated sandbox with SHA-256 checksums. Preserves relative layout; refuses to target the project root or an ancestor of it, and only auto-cleans marked sandbox directories.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}, "relative_files": {"type": "array"}, "sandbox_dir": {"type": "string"}, "clean": {"type": "boolean"}, "force": {"type": "boolean"}}, "required": ["project_root", "relative_files", "sandbox_dir"]},
    },
    "extract_interfaces": {
        "fn": tool_extract_interfaces,
        "description": "Parse RTL module ports and classify AXI4-Lite, AXI4-Stream, clk/rst, and sideband ports.",
        "inputSchema": {"type": "object", "properties": {"rtl_file": {"type": "string"}, "module_name": {"type": "string"}, "emit_draft_yaml": {"type": "string"}, "staged_rtl_files": {"type": "array"}}, "required": ["rtl_file"]},
    },
    "validate_contract": {
        "fn": tool_validate_contract,
        "description": "Validate YAML/JSON boundary contract and optionally cross-check against live RTL.",
        "inputSchema": {"type": "object", "properties": {"spec_path": {"type": "string"}, "rtl_file": {"type": "string"}}, "required": ["spec_path"]},
    },
    "emit_testbench": {
        "fn": tool_emit_testbench,
        "description": "Compile a validated contract into SV wrapper, SVA+bind checker, and cocotb testbench.",
        "inputSchema": {"type": "object", "properties": {"spec_path": {"type": "string"}, "out_dir": {"type": "string"}, "rtl_file": {"type": "string"}}, "required": ["spec_path", "out_dir"]},
    },
    "normalize_sim_result": {
        "fn": tool_normalize_sim_result,
        "description": "Normalize cocotb JUnit XML (results.xml), check JSON, and SVA assertion logs into a strict VerificationReport JSON.",
        "inputSchema": {"type": "object", "properties": {"module_name": {"type": "string"}, "cocotb_results_json": {"type": "string"}, "sva_log_path": {"type": "string"}, "out_report_json": {"type": "string"}}, "required": ["module_name"]},
    },
    "render_visual_report": {
        "fn": tool_render_visual_report,
        "description": "Render a standalone SVG dependency topology plus an HTML rule-audit dashboard.",
        "inputSchema": {"type": "object", "properties": {"report": {"type": "object"}, "dep_graph": {"type": "object"}, "extracted_interfaces": {"type": "array"}, "out_html_path": {"type": "string"}}, "required": ["report", "dep_graph", "extracted_interfaces", "out_html_path"]},
    },
}


def list_tool_schemas() -> List[Dict[str, Any]]:
    return [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]} for k, v in TOOL_REGISTRY.items()]


def call_tool(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name not in TOOL_REGISTRY:
        return err_result(tool_name, f"Unknown tool '{tool_name}'.")
    fn: Callable[..., Dict[str, Any]] = TOOL_REGISTRY[tool_name]["fn"]
    try:
        return fn(**params)
    except Exception as exc:
        return err_result(tool_name, str(exc))
