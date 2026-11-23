"""Central tool registry, MCP JSON Schema exporter, and uniform dispatcher (12 atomic tools)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .core.types import err_result
from .eda_campaign import tool_clean_pt_netlist
from .iface_extractor import tool_extract_interfaces
from .mutation_runner import tool_run_mutation
from .repo_indexer import tool_index_repo, tool_resolve_deps
from .result_normalizer import tool_normalize_sim_result
from .sandbox_copier import tool_copy_sandbox
from .self_auditor import tool_audit_harness
from .sim_runner import tool_run_sim
from .tb_emitter import tool_emit_testbench, tool_validate_contract
from .tb_linter import tool_lint_tb
from .visual_renderer import tool_render_visual_report

TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "index_repo": {
        "fn": tool_index_repo,
        "description": "Syntax-parse an RTL repo into a module, instantiation, and header index.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string", "description": "Absolute or relative path to the RTL project root to index."}}, "required": ["project_root"],
            "additionalProperties": False
            },
    },
    "resolve_deps": {
        "fn": tool_resolve_deps,
        "description": "Compute BFS transitive dependency closure and DAG edges for a target module.",
        "inputSchema": {
            "type": "object",
            "properties": {"target_module": {"type": "string", "description": "Name of the module whose transitive dependency closure should be computed."}, "repo_index": {"type": "object", "description": "Optional repo index output from index_repo (recomputed if omitted)."}, "project_root": {"type": "string", "description": "Path to the RTL project root."}},
            "required": ["target_module"],
            "additionalProperties": False
            },
    },
    "copy_sandbox": {
        "fn": tool_copy_sandbox,
        "description": "Copy resolved RTL/header files into an isolated sandbox with SHA-256 checksums. Preserves relative layout; refuses to target the project root or an ancestor of it, and only auto-cleans marked sandbox directories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string", "description": "Path to the source RTL project root."},
                "relative_files": {"type": "array", "items": {"type": "string"}, "description": "Project-relative paths of the RTL/header files to stage into the sandbox."},
                "sandbox_dir": {"type": "string", "description": "Target sandbox directory (must not be the project root or an ancestor)."},
                "clean": {"type": "boolean", "description": "Clean an existing marked sandbox directory before copying (default false)."},
                "force": {"type": "boolean", "description": "Allow cleaning a directory that carries the sandbox marker."},
            },
            "required": ["project_root", "relative_files", "sandbox_dir"],
            "additionalProperties": False
            },
    },
    "extract_interfaces": {
        "fn": tool_extract_interfaces,
        "description": "Parse RTL module ports (builtin or optional slang AST) and classify AXI4-Lite, AXI4-Stream, APB, SV interface/modport, clk/rst, and sideband ports.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rtl_file": {"type": "string", "description": "Path to the RTL source file to parse."},
                "module_name": {"type": "string", "description": "Optional module name to extract; extracts all modules when omitted."},
                "emit_draft_yaml": {"type": "string", "description": "Optional path to write a draft boundary contract YAML for downstream tools."},
                "staged_rtl_files": {"type": "array", "items": {"type": "string"}, "description": "Optional additional RTL files compiled together (e.g. packages) for cross-file imports."},
                "parser_backend": {"type": "string", "description": "Port parser backend: auto (default), builtin, or slang (requires slang on PATH)."},
            },
            "required": ["rtl_file"],
            "additionalProperties": False
            },
    },
    "validate_contract": {
        "fn": tool_validate_contract,
        "description": "Validate YAML/JSON boundary contract (including APB, bitfields, and apb-regfile-spec/v2) and optionally cross-check against live RTL.",
        "inputSchema": {"type": "object", "properties": {"spec_path": {"type": "string", "description": "Path to the boundary contract YAML/JSON (or apb-regfile-spec/v2) to validate."}, "rtl_file": {"type": "string", "description": "Optional RTL file to cross-check the contract against."}}, "required": ["spec_path"],
            "additionalProperties": False
            },
    },
    "emit_testbench": {
        "fn": tool_emit_testbench,
        "description": "Compile a validated contract into SV wrapper, SVA+bind checker, and cocotb testbench (with write/readback & stress profile). With `protocol` set, instead emit a self-checking SystemVerilog testbench + Verilator runner natively for apb/axi4/ahb_lite/ddr3/ddr4/hbm/pcie (no spec_path needed).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec_path": {"type": "string", "description": "Path to the validated contract (with AXI4-Lite boundary) to generate a testbench for. Required unless `protocol` is set."},
                "out_dir": {"type": "string", "description": "Output directory for the generated wrapper/SVA/bind/cocotb artifacts."},
                "rtl_file": {"type": "string", "description": "Optional RTL file of the DUT to compile against. Required when `protocol` is set."},
                "protocol": {"type": "string", "enum": ["auto", "apb", "axi4", "ahb_lite", "ddr3", "ddr4", "hbm", "pcie"], "description": "Emit a native multi-bus self-checking testbench for this protocol instead of the AXI4-Lite contract path. 'auto' picks the first supported interface found in rtl_file."},
            },
            "required": ["out_dir"],
            "additionalProperties": False
            },
    },
    "normalize_sim_result": {
        "fn": tool_normalize_sim_result,
        "description": "Normalize cocotb JUnit XML, check JSON, SVA logs, and Verilator multi-suite logs into a strict VerificationReport JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "DUT module name recorded in the normalized report."},
                "cocotb_results_json": {"type": "string", "description": "Path to cocotb JUnit XML results to normalize."},
                "sva_log_path": {"type": "string", "description": "Path to SVA assertion log to normalize."},
                "verilator_log_path": {"type": "string", "description": "Path to Verilator multi-suite regression log to normalize."},
                "expected_suites": {"type": "array", "items": {"type": "string"}, "description": "Optional suite names that must all appear and PASS in the normalized log."},
                "coverage_summary": {"type": "object", "description": "Optional coverage summary to embed in the report."},
                "mutation_summary": {"type": "object", "description": "Optional mutation test summary to embed in the report."},
                "out_report_json": {"type": "string", "description": "Optional path to write the normalized VerificationReport JSON."},
            },
            "required": ["module_name"],
            "additionalProperties": False
            },
    },
    "render_visual_report": {
        "fn": tool_render_visual_report,
        "description": "Render a standalone SVG dependency topology plus an HTML rule-audit dashboard with consistency self-check.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "report": {"type": "object", "description": "Normalized VerificationReport JSON (from normalize_sim_result) to render."},
                "dep_graph": {"type": "object", "description": "Dependency graph with a dependency_closure list of module names."},
                "extracted_interfaces": {"type": "array", "items": {"type": "object"}, "description": "Extracted protocol interfaces (from extract_interfaces) to annotate the topology."},
                "out_html_path": {"type": "string", "description": "Output path for the standalone HTML dashboard (SVG is written alongside it)."},
            },
            "required": ["report", "dep_graph", "extracted_interfaces", "out_html_path"],
            "additionalProperties": False
            },
    },
    "lint_tb": {
        "fn": tool_lint_tb,
        "description": "Static anti-pitfall linter for SV testbenches & scripts: clocking races (C1-C2), AXI handshake deadlocks (R1-R4), and silent-pass patterns (S1-S3).",
        "inputSchema": {
            "type": "object",
            "properties": {"target_path": {"type": "string", "description": "SV/script file or directory to lint for clocking races, handshake deadlocks, and silent-pass patterns."}, "rules": {"type": "array", "items": {"type": "string"}, "description": "Optional subset of rule groups to run: clocking, handshake, silent_pass."}, "strict": {"type": "boolean", "description": "Treat warnings as failures (default false: only errors fail)."}},
            "required": ["target_path"],
            "additionalProperties": False
            },
    },
    "run_sim": {
        "fn": tool_run_sim,
        "description": "Run Verilator (>= 5.040) + slang simulation with stdbuf log retention and per-suite identity verification.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sim_script": {"type": "string", "description": "Path to the simulation entry script (e.g. sim.sh), run from its own directory."},
                "suite": {"type": "string", "description": "Suite name to run (default all)."},
                "expected_suites": {"type": "array", "items": {"type": "string"}, "description": "Optional list of suites that must all appear and PASS (per-suite identity check)."},
                "timeout_sec": {"type": "integer", "description": "Simulation timeout in seconds (default 300)."},
                "seed": {"type": "integer", "description": "Optional SEED environment value passed to the sim script."},
                "wait_cycles": {"type": "integer", "description": "Optional WAIT environment value passed to the sim script."},
                "require_slang": {"type": "boolean", "description": "Require slang on PATH (E021 if missing, default false)."},
            },
            "required": ["sim_script"],
            "additionalProperties": False
            },
    },
    "run_mutation": {
        "fn": tool_run_mutation,
        "description": "Run parameterized RTL mutation tests with try/finally guaranteed restore and residual-mutation guard.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string", "description": "Working directory containing the RTL to mutate (isolated sandbox recommended)."},
                "mutation_spec": {"type": "string", "description": "Path to the mutation spec JSON/YAML listing mutations to apply."},
                "test_cmd": {"type": "array", "items": {"type": "string"}, "description": "Command (argv list) run after each mutation; non-zero exit means the mutation was killed."},
                "min_score": {"type": "number", "description": "Minimum acceptable mutation kill score percentage (default 80)."},
                "timeout_sec": {"type": "integer", "description": "Per-mutation test command timeout in seconds (default 120)."},
                "only_id": {"type": "string", "description": "Optional single mutation id to run (for targeted verification)."},
            },
            "required": ["workdir", "mutation_spec", "test_cmd"],
            "additionalProperties": False
            },
    },
    "clean_pt_netlist": {
        "fn": tool_clean_pt_netlist,
        "description": "Strip unresolved parameterized blackbox instances (RDBUS/CLKCROSSBUS/CLKCROSSDFF) from a DC Verilog netlist and insert scalar tie-offs so PrimeTime link_design succeeds.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "in_verilog": {"type": "string", "description": "Input DC-exported Verilog netlist path containing unresolved blackbox instances."},
                "out_verilog": {"type": "string", "description": "Output path for the cleaned netlist (parent dirs are created as needed)."},
                "blackbox_modules": {"type": "array", "items": {"type": "string"}, "description": "Blackbox module names to strip (default: RDBUS, CLKCROSSBUS, CLKCROSSDFF)."},
            },
            "required": ["in_verilog", "out_verilog"],
            "additionalProperties": False
            },
    },
    "audit_harness": {
        "fn": tool_audit_harness,
        "description": "Run fault-injection self-audit verifying that lint, mutation residual guard, normalizer, and sandbox fail loudly.",
        "inputSchema": {"type": "object", "properties": {"strict": {"type": "boolean", "description": "Fail (instead of reporting) when any fault-injection check fails (default true)."}}, "required": [],
            "additionalProperties": False
            },
    },
}


def list_tool_schemas() -> List[Dict[str, Any]]:
    return [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]} for k, v in TOOL_REGISTRY.items()]


def call_tool(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name not in TOOL_REGISTRY:
        return err_result(tool_name, f"Unknown tool '{tool_name}'.")
    fn: Callable[..., Dict[str, Any]] = TOOL_REGISTRY[tool_name]["fn"]
    # 参数名校验：schema 声明 additionalProperties: false，未知参数名应给出
    # 明确错误，而非让 fn(**params) 抛模糊的 TypeError（agent 传错参数名时
    # 能立即定位，而不是看到 "got an unexpected keyword argument"）。
    schema_props = TOOL_REGISTRY[tool_name].get("inputSchema", {}).get("properties", {})
    unknown = [k for k in params if k not in schema_props]
    if unknown:
        return err_result(
            tool_name,
            f"Unknown parameter(s) {unknown} for tool '{tool_name}'. "
            f"Valid parameters: {sorted(schema_props.keys())}",
        )
    try:
        return fn(**params)
    except Exception as exc:
        return err_result(tool_name, str(exc))