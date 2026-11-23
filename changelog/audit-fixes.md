# Changelog — 独立审计修复（Unreleased）

本轮对 v0.5.0 做外部审计（非作者视角重跑全部工具），发现并修复 3 处「声称能做但实际静默退化」的真实缺陷。这些缺陷的共同点恰好是本项目 README 反复批判的行为——静默通过。

### Fixed
- **`slang` AST 后端此前从未真正生效**（`core/sv_parser.py`）。两个叠加的根因：
  1. `slang --ast-json -` 会把人类可读 banner（`Top level design units:` / `Build succeeded: ...`）与 JSON 混在同一个 stdout，旧代码直接 `json.loads(整段)` 必然失败；
  2. 真实 RTL 普遍 `import xxx_pkg::*` / 引用 SV interface，只把单个文件喂给 slang 会报 `unknown package/interface` 并以非 0 退出（但仍输出完整 AST），旧代码因 `returncode != 0` 整段丢弃。

  修复后：从第一个 `{` 定位 JSON；自动收集同目录 `*_pkg.sv` / `*_if.sv` 作为同批编译文件并加 `-I` include 目录；加 `--timescale 1ns/1ps` 消除缺 timescale 的噪声报错。实测：参考靶场含 module 的 39 个 `.sv` 文件 **100% 走 slang 后端、0 静默回退**（修复前几乎全为 builtin）。

- **`slang` 不导出 `InterfacePort.interfaceDef/modport`**（slang 12.x 的 ast-json 只有 `{name,kind,addr,isGeneric}`）。直接切到 slang 会丢掉 `axi_lite_if.slave` 这类信息。新增 `_enrich_from_builtin`：slang 为主后端，用 builtin 解析补齐它缺失的 interface 类型/modport，两种后端互补而非二选一。

- **`slang` 回退对调用方不可见**。新增 `W013` 诊断（`iface_extractor.py`）：slang 装了却没生效时明确上报原因，而不是静默退回 builtin。

- **`emit_testbench` 报错只有 `'awaddr'` 一个单词**（`tb_emitter.py`）。spec 缺 AXI4-Lite boundary（如纯 APB 寄存器文件）时，IR 会带着空 `port_map` 往下走，最终在生成 wrapper 时抛 `KeyError('awaddr')`，调用方看不出根因。修复后两处 fail-fast：
  - 存在 ERROR 级诊断（如 E001 缺必需 port_map）时提前失败并透传完整诊断；
  - spec 无 AXI4-Lite boundary 时明确提示「需提供 axi4_lite boundary，或用 `extract_interfaces --emit-draft-yaml` 生成」。

### Fixed（续，第二轮实机验证）

第二轮把文档标注为「待实机仿真」的 `run_sim` / `run_mutation` 真正跑通，又发现并修复 2 类缺陷：

- **`run_sim` / `run_mutation` 的数值参数不接受字符串**（`sim_runner.py` / `mutation_runner.py`）。MCP / CLI 调用方常把数字序列化成字符串（JSON number → str），schema 虽声明 `integer`/`float`，但 `call_tool` 不做类型强制。结果 `timeout_sec='1700'` 传进 `subprocess.run(timeout=...)`，其内部 `timeout - (now - start)` 抛 `TypeError: unsupported operand type(s) for +: 'float' and 'str'`。单套件路径不触发，只有全量回归（需显式传大 timeout）才暴露。修复后：入口显式 `int()`/`float()` 转换，非法值给出可读错误而非裸 TypeError。

- **「逐套件身份核对」护栏对真实日志从不生效**（`verify_suite_identities`，最严重）。旧正则 `==\s*sim:\s*(\w+)\s*==` 要求套件名后紧跟 `==`，但 `sim.sh` 实际打印 `== sim: <name> (top=<top>) ==`（套件名后带 `(top=...)` / `(WAIT=...)` 参数）。正则永远匹配不到 → 把**所有**套件误判为 missing，即使日志里套件全 PASS。后果：反静默通过护栏形同虚设——它报的是「全套件 missing」而非「哪个套件真漏了」，调用方拿到的是噪声而非信号。修正则后容忍套件名后的参数；实测真实格式日志：4 PASS 正确识别、漏跑的 `dual` 被精确标为 missing。

  该缺陷长期未被发现的原因：现有测试 fixture 用的是理想化格式 `== sim: counter ==`（套件名后直接 `==`），自己造了能匹配的假日志。已把 fixture 改为真实格式，并新增 `test_suite_identity_real_format`（6 项检查，覆盖 `(top=)` 与 `(WAIT=)` 两种变体、FAIL 识别、漏跑精确报告）。

- **`lint_tb` 违规时丢失结构化汇总**（`tb_linter.py` / `core/types.py`）。有 finding 时返回 `err_result` 且 `data={}`，调用方拿不到 `error_count/warning_count/findings` 计数字段，只能从 `diagnostics` 重新解析——与 `run_mutation`（`ok=False` 也带 `data`）等工具不一致。修复：`err_result` 新增可选 `data` 参数（默认 `{}` 保持向后兼容，不影响现有调用点），`lint_tb` 违规时显式带结构化汇总。

- **`audit_harness` 自审计漏掉最核心的反静默护栏**（`self_auditor.py`）。A1–A4 只覆盖 lint_tb / mutation 残留 / normalizer 拒垃圾 / sandbox 拒根目录四条路径，**唯独不含「逐套件身份核对」**——而它是 `run_sim` / `normalize` 的守门逻辑。这正是本轮套件身份正则失效（护栏形同虚设）却始终显示「自审计 4/4 PASS」的原因：自审计有结构性盲区。新增三项检查，自审计覆盖从 4 项扩至 7 项：
  - **A5** `SUITE_IDENTITY_REAL_FORMAT`：真实格式日志能解析 + 漏跑套件被精确抓出；
  - **A6** `SUITE_IDENTITY_ALL_PASS`：套件齐时 `all_verified=True` 不假报警；
  - **A7** `RUN_SIM_VERSION_GUARD`：伪造 `meets_min_version=False` 环境，确认 `run_sim` 在 Verilator 版本不达标时显式 E020 拒绝而非静默运行。

  连带修掉一处测试脆性：`test_audit_and_registry` 硬编码断言 `passed == 4`，加检查即误报为回归。改为断言「全部通过且 `passed >= 9`」（增检查不破、删检查会报），并给 A7 的 `r_sim`/`e020` 加了预置初值守卫，避免 `tool_run_sim` 抛异常时变成未定义变量 TypeError。

  自审计覆盖最终从 4 项扩至 **9 项**（A1–A9），新增的三项分别锁死：套件身份核对解析真实日志（A5）、套件齐时不假报警（A6）、Verilator 版本守卫 E020（A7）、emit_testbench 缺 boundary 时 fail-fast（A8）、slang 回退对调用方可见（A9）。后两项专门覆盖本轮修的两个静默退化缺陷，确保它们不会再次悄悄回归。