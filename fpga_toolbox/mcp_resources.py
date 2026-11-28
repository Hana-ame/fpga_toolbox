"""MCP resources 与 prompts 的内容提供者。

Agent 接入 MCP 后最常见的两类浪费：
  * 客户端把「诊断码含义」「协议规范」整段塞进提示词，每个 session 重来一遍；
  * 同一个「从 RTL 到变异审计」标准流程被反复手写。

MCP 的 resources / prompts 正是为这类**可复用上下文**设计的：客户端按需拉取，
不占提示词预算。本模块从仓库内的**唯一事实源**（docs/FEATURES.md 的诊断码表、
canonical_iface 的协议声明）动态构建，避免再维护一份副本而漂移。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from .canonical_iface import CANONICAL_INTERFACES
from .protocol_tb_emitter import SUPPORTED_MULTI_PROTOCOLS
from .registry import TOOL_REGISTRY

_ROOT = Path(__file__).resolve().parents[1]
_FEATURES = _ROOT / "docs" / "FEATURES.md"

#: 诊断码表格行：`| `E001` | 必需端口未映射 |`
_CODE_ROW = re.compile(r"^\|\s*`([EW]\d{3})`\s*\|\s*(.+?)\s*\|\s*$")


def diagnostic_codes() -> List[Dict[str, str]]:
    """从 docs/FEATURES.md 提取诊断码速查表（文档是唯一事实源）。"""
    if not _FEATURES.is_file():
        return []
    out: List[Dict[str, str]] = []
    seen: set = set()
    for line in _FEATURES.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _CODE_ROW.match(line.strip())
        if not m:
            continue
        code, desc = m.group(1), m.group(2).strip()
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "description": desc})
    return sorted(out, key=lambda x: x["code"])


def _render_code_table(codes: List[Dict[str, str]]) -> str:
    lines = [
        "# fpga_toolbox 诊断码速查表",
        "",
        f"共 {len(codes)} 个诊断码，来源：docs/FEATURES.md（唯一事实源）。",
        "",
        "| 码 | 含义 |",
        "|---|---|",
    ]
    lines += [f"| `{c['code']}` | {c['description']} |" for c in codes]
    lines += [
        "",
        "约定：`E*` 为错误（阻断），`W*` 为警告（可继续但需关注）。",
        "工具返回的 `diagnostics[]` 每项含 `severity` / `code` / `path` / `message`。",
    ]
    return "\n".join(lines) + "\n"


def protocols_doc() -> str:
    """11 类可生成 TB 的协议 + canonical 接口声明。"""
    lines = [
        "# fpga_toolbox 协议规范速查",
        "",
        f"## 可生成原生自检 TB 的协议（{len(SUPPORTED_MULTI_PROTOCOLS)} 类）",
        "",
        "| 协议 | 生成器状态 |",
        "|---|---|",
    ]
    for p in SUPPORTED_MULTI_PROTOCOLS:
        lines.append(f"| `{p}` | 原生自检 TB + Verilator 运行脚本 |")
    lines += [
        "",
        f"## canonical 接口声明（{len(CANONICAL_INTERFACES)} 类，分类器可识别）",
        "",
        "分类器识别范围大于 TB 生成器：能识别但无模板时，`emit_testbench` 会如实",
        "报「已识别但无模板」而不是谎称没找到。",
        "",
        "| 协议 | 最少信号数 | 关键 probe | 必需信号 |",
        "|---|---|---|---|",
    ]
    for name, ci in sorted(CANONICAL_INTERFACES.items()):
        req = ", ".join(s.name for s in ci.signals if s.required) or "—"
        lines.append(
            f"| `{name}` | {ci.min_signals} | {', '.join(ci.role_probes)} | {req} |")
    lines += [
        "",
        "## 命名风格绑定",
        "",
        "命名风格由「标准接口 + 绑定层」统一处理，无需改 RTL：",
        "`i_psel` / `psel_i` / `p_sel` / `s0_psel` / `mem_ddr3_dq_out` 均可绑定。",
        "双向信号（DDR dq）的拆分见 `split_port_map`。",
    ]
    return "\n".join(lines) + "\n"


def tools_doc() -> str:
    """当前注册的全部原子工具（含参数与说明）。"""
    lines = [
        "# fpga_toolbox 原子工具清单",
        "",
        f"共 {len(TOOL_REGISTRY)} 个工具，全部通过 `ToolResult(ok/tool/data/diagnostics/error)` 信封返回。",
        "",
        "| 工具 | 说明 | 必需参数 |",
        "|---|---|---|",
    ]
    for name, meta in TOOL_REGISTRY.items():
        desc = meta["description"].replace("|", "\\|")
        req = ", ".join(meta["inputSchema"].get("required", [])) or "—"
        lines.append(f"| `{name}` | {desc} | {req} |")
    return "\n".join(lines) + "\n"


def list_resources() -> List[Dict[str, Any]]:
    codes = diagnostic_codes()
    return [
        {
            "uri": "fpga-toolbox://diagnostics/codes",
            "name": "诊断码速查表",
            "description": f"全部 {len(codes)} 个 E*/W* 诊断码的含义（来自 docs/FEATURES.md）",
            "mimeType": "text/markdown",
        },
        {
            "uri": "fpga-toolbox://protocols",
            "name": "协议规范速查",
            "description": f"{len(SUPPORTED_MULTI_PROTOCOLS)} 类可生成 TB 的协议 + "
                           f"{len(CANONICAL_INTERFACES)} 类 canonical 接口声明与命名绑定规则",
            "mimeType": "text/markdown",
        },
        {
            "uri": "fpga-toolbox://tools",
            "name": "原子工具清单",
            "description": f"当前注册的 {len(TOOL_REGISTRY)} 个工具及其参数",
            "mimeType": "text/markdown",
        },
    ]


def read_resource(uri: str) -> Dict[str, Any]:
    """按 URI 返回 resource 内容；未知 URI 抛 KeyError 由调用方转 -32602。"""
    codes = diagnostic_codes()
    if uri == "fpga-toolbox://diagnostics/codes":
        return {"uri": uri, "mimeType": "text/markdown",
                "text": _render_code_table(codes)}
    if uri == "fpga-toolbox://protocols":
        return {"uri": uri, "mimeType": "text/markdown", "text": protocols_doc()}
    if uri == "fpga-toolbox://tools":
        return {"uri": uri, "mimeType": "text/markdown", "text": tools_doc()}
    raise KeyError(uri)


# ---------------------------------------------------------------------------
# prompts：把高频标准流程沉淀下来，减少 Agent 侧提示词冗余
# ---------------------------------------------------------------------------

_PROMPT_DEFS = [
    {
        "name": "rtl-to-mutation-audit",
        "title": "从 RTL 到变异审计（标准流程）",
        "description": "对一份 RTL 依次做：接口提取 → TB 生成 → 仿真 → 结果归一化 → 变异杀伤率审计。",
        "arguments": [
            {"name": "rtl_file", "description": "RTL 文件路径", "required": True},
            {"name": "protocol", "description": "可选：指定协议（不填则 auto 检测）",
             "required": False},
            {"name": "min_score", "description": "变异杀伤率下限（默认 80）", "required": False},
        ],
    },
    {
        "name": "debug-failing-simulation",
        "title": "排查仿真失败",
        "description": "给定仿真日志与 TB，定位失败根因（伪 PASS / 套件未跑完 / 波形缺失）。",
        "arguments": [
            {"name": "log_path", "description": "仿真日志路径", "required": True},
            {"name": "module_name", "description": "被测模块名（用于报告归一化）",
             "required": False},
        ],
    },
    {
        "name": "analyze-eda-campaign",
        "title": "分析商业 EDA 跑批结果",
        "description": "解析 VCS/UVM 与 DC/PT 日志，做多 seed 中位数聚合与基线可复现性核对。",
        "arguments": [
            {"name": "uvm_log_path", "description": "VCS/UVM 日志路径", "required": False},
            {"name": "qor_report_path", "description": "DC/PT QoR 报告路径", "required": False},
        ],
    },
]


def list_prompts() -> List[Dict[str, Any]]:
    return [dict(p) for p in _PROMPT_DEFS]


def _prompt_rtl_to_mutation_audit(args: Dict[str, Any]) -> str:
    rtl = args.get("rtl_file") or "<rtl_file>"
    proto = args.get("protocol") or "auto"
    score = args.get("min_score") or "80"
    return f"""请对 `{rtl}` 执行完整的验证闭环，每一步都必须用工具实际执行并检查返回：

1. `extract_interfaces` — 先确认协议与端口绑定。
   参数：`{{"rtl_file": "{rtl}"}}`
   若返回的 interface 为空，先查 W010（无时钟）/ W011（无复位），不要猜端口名。

2. `emit_testbench` — 生成自检 TB 与运行脚本。
   参数：`{{"rtl_file": "{rtl}", "out_dir": "/tmp/tb_out", "protocol": "{proto}"}}`
   若报「已识别但无模板」，说明该协议分类器认得出但生成器还不支持，如实上报，
   不要退回手写 TB 掩盖缺口。

3. `run_sim` — 真实运行。
   参数：`{{"sim_script": "<上一步 artifacts.sim_script>", "log_dir": "/tmp/logs"}}`
   要求 `ok=true` 且日志里有 `PASS:` 汇总行。若 `ok=false`，把 diagnostics 原样报出。

4. `normalize_sim_result` — 归一化为严格报告。
   只有 `overall_status == "PASS"` 才算通过；注意它会拦下「打印 PASS 但套件
   未跑完」「没有逐笔 [CHECK]」这类伪通过。

5. `run_mutation` — 变异审计。
   参数：`{{"workdir": "<DUT 所在目录>", "mutation_spec": "<变异清单>",
   "test_cmd": ["bash", "<运行脚本>"], "min_score": {score}}}`
   要求 `mutation_score >= {score}`。baseline 必须先在**未变异**代码上通过，
   否则整个审计无意义（工具会预检并报 E023）。

最后汇报：协议、生成/运行结果、归一化状态、变异杀伤率，以及任何 diagnostic 码。
禁止声称「应该能通过」——每一条结论都必须来自上述工具的返回体。"""


def _prompt_debug_failing_simulation(args: Dict[str, Any]) -> str:
    log = args.get("log_path") or "<log_path>"
    mod = args.get("module_name") or "<module_name>"
    return f"""请定位 `{log}` 的仿真失败根因：

1. `normalize_sim_result` — 先归一化。
   参数：`{{"module_name": "{mod}", "verilator_log_path": "{log}"}}`
   重点看：`overall_status`、`failed_suites`、`checks` 里 status=FAIL 的条目。

2. 按症状分流：
   - **整体 PASS 但某个套件没跑完** → 查 E050（旧 simv / 截断日志），
     通常是仿真被 kill 而汇总行来自上一次运行。
   - **有 FAIL 但 exp/got 相同** → 可能是 X/Z 传播，exp 与 got 都是 x，
     `!==` 才能暴露；检查 TB 是否用 `==` 比较。
   - **日志为空但 rc=0** → 伪 PASS，检查是否 `$display` 后直接 `$finish`。
   - **超时无输出** → 检查时钟/复位是否真的被驱动（W010/W011）。

3. 若怀疑波形，用 `run_sim` 带 `waveform=true, require_waveform=true` 重跑，
   它会断言波形文件**真的**产生（Verilator 无 --trace 时会静默忽略 $dumpvars）。

4. 给出结论时必须引用具体证据行（日志原文或 diagnostic 码），不要只给推测。"""


def _prompt_analyze_eda_campaign(args: Dict[str, Any]) -> str:
    uvm = args.get("uvm_log_path") or "<uvm_log_path>"
    qor = args.get("qor_report_path") or "<qor_report_path>"
    return f"""请分析这批商业 EDA 跑批结果（无需任何 EDA 许可证，工具只解析日志）：

1. `parse_uvm_log` — VCS/UVM 日志。
   参数：`{{"log_path": "{uvm}"}}`
   ⚠ 注意 `fid_bad` 缺失会解析为 None 并导致门禁失败：这是有意的，
   「日志不完整」不能当成「没有问题」。

2. `parse_dc_pt_qor` — DC/PT QoR 报告。
   参数：`{{"report_path": "{qor}"}}`
   重点拦 E046：`UID-423`（async_default 组在 DC 不支持）与
   `No paths.` 迷你报告（假成功）。

3. 多 seed 时用 `aggregate_seed_medians`：
   参数：`{{"runs": [<每 seed 的 parse_uvm_log data 内层对象>]}}`
   ⚠ 必须传**内层 data**，不是整个 ToolResult 信封；传错会被显式拒绝。

4. 需要远端复跑时用 `generate_remote_run_script` 生成启动脚本，
   它会拒绝 shell 注入字符与危险 tag（`.` / `..`）。

汇总时明确区分「未通过」与「无数据」——两者含义完全不同。"""


_PROMPT_RENDERERS = {
    "rtl-to-mutation-audit": _prompt_rtl_to_mutation_audit,
    "debug-failing-simulation": _prompt_debug_failing_simulation,
    "analyze-eda-campaign": _prompt_analyze_eda_campaign,
}


def get_prompt(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """渲染 prompt；未知 name 抛 KeyError 由调用方转 -32602。"""
    renderer = _PROMPT_RENDERERS[name]
    text = renderer(arguments or {})
    return {
        "description": next(p["description"] for p in _PROMPT_DEFS if p["name"] == name),
        "messages": [
            {"role": "user", "content": {"type": "text", "text": text}},
        ],
    }
