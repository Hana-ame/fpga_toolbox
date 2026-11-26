"""Central tool registry, MCP JSON Schema exporter, and uniform dispatcher (13 atomic tools)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

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
                "clean": {"type": "boolean", "description": "Clean an existing MARKED sandbox directory before copying (default TRUE: omitting this parameter wipes stale files from a previously created marked sandbox). Unmarked non-empty directories are always refused unless force=true."},
                "force": {"type": "boolean", "description": "DESTRUCTIVE: allow cleaning a non-empty directory that has NO sandbox marker (i.e. possibly the user's own files). Only pass when you truly intend to wipe that directory."},
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
                "timeout_sec": {"type": "integer", "minimum": 1, "description": "Simulation timeout in seconds (default 300)."},
                "seed": {"type": "integer", "description": "Optional SEED environment value passed to the sim script."},
                "wait_cycles": {"type": "integer", "description": "Optional WAIT environment value passed to the sim script."},
                "require_slang": {"type": "boolean", "description": "Require slang on PATH (E021 if missing, default false)."},
                "log_dir": {"type": "string", "description": "Directory to persist the FULL simulation log (stdout+stderr) as sim_<suite>.log. Without it only 4000/2000-char tails are returned."},
                "waveform": {"type": "boolean", "description": "Enable waveform capture: sets WAVE=1 for the sim script so verilator is built with --trace/--trace-fst (default false)."},
                "waveform_format": {"type": "string", "enum": ["vcd", "fst"], "description": "Waveform format when waveform=true (default vcd)."},
                "require_waveform": {"type": "boolean", "description": "Fail with E024 unless a non-empty waveform file was produced. Guards against verilator silently ignoring $dumpvars without --trace."},
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
                "timeout_sec": {"type": "integer", "minimum": 1, "description": "Per-mutation test command timeout in seconds (default 120)."},
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
    # 复制一份再操作：下面的 boolean 字符串强转会原地写 params[pname]。
    # 直接改写调用方传进来的 dict 是隐蔽的副作用（调用方复用同一个 dict 发
    # 第二次调用时会拿到"被偷偷改成 bool"的值，而它自己以为还是 "true"）。
    params = dict(params)
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
    # 类型校验：schema 的 type 必须强制。旧实现不做校验，把错类型直接传给
    # 函数体，最终漏出 Python 内部错误——例如给 string 参数传 int 会得到
    # "argument should be a str or an os.PathLike object where __fspath__
    # returns a str, not 'int'"，用户看不出是哪个参数错了。
    _PY_TYPES: Dict[str, Any] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": (list, tuple),
        "object": dict,
    }
    for pname, pspec in schema_props.items():
        ptype = pspec.get("type")
        if not ptype or pname not in params:
            continue
        expected = _PY_TYPES.get(ptype)
        if expected is None:
            continue
        val = params[pname]
        if val is None:
            continue  # Optional：显式 None 交给函数处理
        # bool 是 int 子类：integer 参数不接受 True/False
        if ptype in ("integer", "number") and isinstance(val, bool):
            return err_result(
                tool_name,
                f"Invalid type for parameter '{pname}' of tool '{tool_name}': "
                f"expected {ptype}, got boolean.",
            )
        if not isinstance(val, expected):
            # 数字类参数允许字符串：MCP 客户端常把 JSON number 序列化成 str，
            # 且 sim_runner / mutation_runner 在函数边界**故意**做了
            # `int(timeout_sec)` 宽容转换（见其注释，那里也给出可读错误）。
            # 入口若一律拒绝会破坏这个已存在的兼容设计——实测
            # run_sim(timeout_sec="1700") 曾可用，加严后变成 Invalid type。
            # 因此只对「非数字类型」严格，数字类放行给下游自行解析。
            if ptype in ("integer", "number") and isinstance(val, str):
                continue
            # boolean 同理：MCP/JSON 客户端常把 bool 序列化成字符串。
            # 实测 run_sim(waveform="true") 曾被 Invalid type 拒绝，而
            # timeout_sec="300"（数字字符串）是接受的 —— 同一调用方的两种参数
            # 待遇不一致。这里只接受**明确的布尔字面量**（大小写不敏感），
            # 其他字符串仍拒绝（绝不静默转成 False，那会静默关掉波形等功能）。
            if ptype == "boolean" and isinstance(val, str):
                _low = val.strip().lower()
                if _low in ("true", "1", "yes"):
                    params[pname] = True
                    continue
                if _low in ("false", "0", "no"):
                    params[pname] = False
                    continue
                return err_result(
                    tool_name,
                    f"Invalid value for boolean parameter '{pname}' of tool "
                    f"'{tool_name}': got {val!r}. Expected a boolean or one of "
                    "'true'/'false'/'1'/'0'/'yes'/'no'.",
                )
            return err_result(
                tool_name,
                f"Invalid type for parameter '{pname}' of tool '{tool_name}': "
                f"expected {ptype}, got {type(val).__name__}.",
            )

    # enum 校验：schema 声明的可选值必须强制执行，否则调用方拼错协议名时
    # 会得到「Protocol 'BOGUS' not found in any module」——看起来像 RTL 不支持
    # 该协议，实际是参数值本身非法。实测 `protocol='BOGUS'` 与合法但 RTL 不匹配的
    # `protocol='ddr3'` 报错文本完全一致，无法区分。
    for pname, pspec in schema_props.items():
        allowed = pspec.get("enum")
        # Optional 参数：MCP 客户端常把省略的可选字段序列化成 null。
        # 上面类型校验明确写了「显式 None 交给函数处理」，enum 校验必须一致，
        # 否则 {"protocol": null} 会被判成"非法枚举值"。
        if allowed and pname in params and params[pname] is not None \
                and params[pname] not in allowed:
            return err_result(
                tool_name,
                f"Invalid value {params[pname]!r} for parameter '{pname}' of tool '{tool_name}'. "
                f"Allowed values: {allowed}.",
            )

    # minimum / maximum 校验：schema 里声明的数值边界必须强制执行。
    # 旧实现完全没有这条 —— `timeout_sec=-5` 会被直接传进
    # subprocess.run(timeout=-5)，Python 立刻抛 TimeoutExpired 并报
    # 「Simulation timed out after -5s」，语义荒谬；`timeout_sec=0` 同理，
    # 永远不可能跑完任何仿真。这类越界值应在入口拒绝，而不是靠下游偶然报错。
    # 字符串数字（MCP 客户端常见）先尝试转 float 再比，转不动就跳过
    # （交给下游的宽容转换逻辑处理）。
    for pname, pspec in schema_props.items():
        if pname not in params or params[pname] is None:
            continue
        lo, hi = pspec.get("minimum"), pspec.get("maximum")
        if lo is None and hi is None:
            continue
        val = params[pname]
        num: Optional[float] = None
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            num = float(val)
        elif isinstance(val, str):
            try:
                num = float(val.strip())
            except ValueError:
                num = None
        if num is None:
            continue
        if lo is not None and num < lo:
            return err_result(
                tool_name,
                f"Invalid value {val!r} for parameter '{pname}' of tool '{tool_name}': "
                f"must be >= {lo}.",
            )
        if hi is not None and num > hi:
            return err_result(
                tool_name,
                f"Invalid value {val!r} for parameter '{pname}' of tool '{tool_name}': "
                f"must be <= {hi}.",
            )

    # required 校验：schema 声明的必填项若缺失，必须在入口拒绝。
    # 旧实现只查未知参数名，不查 required——于是 `required: ["out_dir"]`
    # 形同虚设：emit_testbench 不传 out_dir 会把产物直接写进调用方 cwd
    # （实测在 /tmp/dircwd 凭空出现 4 个 .sv/.py）并返回 ok=True。
    # 注意：只校验「缺失」，不校验空字符串——空值语义由各工具自己决定。
    schema_required = TOOL_REGISTRY[tool_name].get("inputSchema", {}).get("required") or []
    # 显式 None 不算"提供了"：{"out_dir": None} 过去同时通过类型校验（None 跳过）
    # 与 required 校验（key 存在），然后把 None 漏进函数体，报出与参数无关的
    # 下游错误（实测 "RTL file not found: x.sv" —— 明明问题是 out_dir 是 None）。
    missing_required = [k for k in schema_required
                        if k not in params or params[k] is None]
    if missing_required:
        return err_result(
            tool_name,
            f"Missing required parameter(s) {missing_required} for tool '{tool_name}'.",
        )
    try:
        return fn(**params)
    except Exception as exc:
        return err_result(tool_name, str(exc))