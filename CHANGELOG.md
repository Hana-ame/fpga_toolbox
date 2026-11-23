# Changelog

All notable changes to `fpga_toolbox` and its companion verification workbench are documented in this file.

## [Unreleased] — multi-bus-pass 合并（多总线能力 + 缺陷修复并集）

把外部演进版 `fpga_toolbox-multi-bus-pass`（216 文件，9 个迭代阶段）与本仓 36 个缺陷修复做真正的三方合并，而非覆盖。该版本从同一 v0.5.0 基线分出，**不含**本轮的 W102/CST/符号链接/编码等修复，直接覆盖会全部丢失。

### Added（来自 multi-bus-pass）
- **多总线原生协议分类**（`iface_extractor.py`）：新增 AHB-Lite / DDR3 / DDR4 / HBM / PCIe Gen3·4·5 / SPI / Ethernet 的分类器，`[aps]` 时钟复位正则扩到 `[ahps]`（支持 HCLK）；AXI4-Full 由 `awlen/arlen/wlast/rlast/awburst/arburst` 判定并与 axi4_lite 区分。
- **多协议 `emit_testbench`**（新增 `protocol_tb_emitter.py`，622 行）：`protocol=apb|axi4|ahb_lite|ddr3|ddr4|hbm|pcie|auto` 时不再走契约路径，直接从 RTL AST 生成自检 SystemVerilog testbench + Verilator 运行脚本，PCIe 自动展开 Gen3/4/5 × x1/x4/x16 共 9 个套件。
- **`[CHECK]` 结构化日志解析**（`result_normalizer.py`）：Verilator TB 的逐笔事务 `[CHECK]` 行被解析成 rule 级 check；有明细时不再重复输出套件级 PASS 行，无明细时退回原行为。
- **报告渲染大改**（`visual_renderer.py` 132→504 行）：SV 语法高亮、方向/位宽徽章、多项目分页面板、mutation 矩阵。
- **多总线靶场** `examples/multi_bus_ref/`（82 文件）：7 类协议 RTL、complex_datapath（AXI4-64↔APB32、OoO 重排、PCIe repack loopback）、full_conversion_suite（带 BUF_CYCS）、soc_fabric（SPI/ETH/PM/LTSSM）、omni_hub（6 接口全向 hub）。
- **`web_tools/`**：协议编辑器与仓库对比报告页；**`release.yml`**：tag 驱动的构建/冒烟/Release/PyPI 流程。

### Fixed（合并期发现的新缺陷）
- **`emit_testbench` 的 `protocol` 参数未在 `registry.py` 声明**。`tb_emitter.py` 支持它，但 `inputSchema.additionalProperties=False` 且未列 `protocol`，导致通过 MCP/`call_tool` 调用时被 `Unknown parameter(s) ['protocol']` 拒绝——多协议功能只有 CLI 内部直调可用，对外宣称支持但实际不通。已补 `protocol` 枚举参数并把 `required` 从 `["spec_path","out_dir"]` 放宽为 `["out_dir"]`（多协议路径不需要 spec_path）。
- **测试 `test_multi_bus_classifiers` 是死代码**。它定义在 `if __name__ == "__main__": sys.exit(main())` 之后，且 `main()` 未调用它——永远不会执行，多总线分类器实际零覆盖。已移入 `main()` 正常执行。

### 合并验证（全部实跑）
- 7 种协议 TB 生成全部成功；**Verilator 5.054 实跑**：APB 8/8、AXI4 6/6、AHB-Lite 6/6、DDR3、DDR4、HBM 全 PASS；**PCIe 9/9 套件 PASS**（Gen3/4/5 × x1/x4/x16，DW=64/128/512）。
- 本仓 36 个缺陷修复**全部存活**（脚本化逐项复核 13 个关键锚点 + 行为实测：符号链接逃逸仍被拒、`unresolved_instantiations` 仍在）。
- `tests/test_regression.py` ALL CHECKS PASSED；`tests/test_fusion_and_cross_repo.py` ALL FUSION CHECKS PASSED（含新增 dual-side 与多总线分类器）；`audit_harness` 9/9；`docs_check` D1–D13 全绿；`ruff check fpga_toolbox tests` 通过；Python 3.10 语法门禁通过。
- 合并期回退并重新应用的修复：`iface_extractor` 的 pure-APB draft 修复被包版本回退（纯 APB 设备又写空 `axi4_lite` 段 → 17 条 E001），已按原逻辑重新应用并把 AXI4-Full 一并纳入。

详见 [`changelog/INDEX.md`](changelog/INDEX.md)。

## [Unreleased] — 未知例化不再静默丢弃

### Fixed
- **`index_repo` 静默丢弃「例化了但仓库里没有定义」的模块**（`repo_indexer.py`）。`_find_instantiations` 只记录 `value in known` 的名字，例化厂商 IP / 加密 IP / 未克隆子模块时被完全静默丢掉：`instantiated_modules` 只剩仓库内模块、`diagnostics` 为空，下游 `resolve_deps` 的依赖闭包与 `all_files` 因此少一截却回答 `ok: True`，`copy_sandbox` 的沙箱静默缺文件。

  第一版修复直接去掉 `in known` 守卫，实测在自带靶场产生 **106 条误报**（`int`/`automatic`/`void`/`axi_lite_if` 等），噪声淹没信号，已回滚。正确做法是用 slang CST 的显式节点 `HierarchyInstantiation.type.text` 精确取例化目标（零误报），slang 不可用时返回「无法判断」并退回旧行为（宁漏报不刷噪声）。新增 `unresolved_instantiations` 字段与 `W102` 诊断，并把 SV interface 定义名（`apb_if`/`axi_lite_if`）排除，否则靶场会有 45 条误报。修复后靶场 W102 = 0 条，真实未知例化精确上报。并补上缺陷的后半段：`resolve_deps` 原来不读该字段，仍返回「看起来完整」的文件清单，现透传 `unresolved_instantiations` 并新增 `closure_is_complete` 布尔信号（`False` 表示清单不完整、不要直接拿去建沙箱）。新增 `tests/test_regression.py::test_unknown_instantiations_reported`（9 项检查，含防误报与下游透传回归）。详见 [`changelog/12-unknown-instantiation-w102.md`](changelog/12-unknown-instantiation-w102.md)。

## [Unreleased] — copy_sandbox 符号链接逃逸修复

### Fixed
- **`copy_sandbox` 跟随符号链接，把项目根之外的文件拷进沙箱**（`sandbox_copier.py`）。旧实现只做字符串层面的路径校验（拒绝对路径与 `..`），随后 `src.is_file()` 与 `shutil.copy2(src, dst)` 都会跟随符号链接，因此项目内的一个符号链接就能把项目外任意文件读进沙箱、算进 sha256 与 `file_count`。实测两条逃逸路径均成立：文件级（`rtl/link.sv -> /outside/creds.txt`）与目录级（`rtl/extdir -> /outside`，再取 `rtl/extdir/creds.txt`）。

  这是「声称隔离却泄漏」——沙箱的存在意义就是不把项目外内容带进来，而共享头文件目录、monorepo 软链这类写法很常见，契约会被轻易破坏。

  修复：字符串校验之后补一次真实路径校验（`resolve(strict=True)` 跟随所有链接），确认解析结果仍在 `project_root` 内；项目内部的相对符号链接解析后仍在 root 内，照常放行。新增 `tests/test_regression.py::test_sandbox_symlink_escape`（5 项检查）覆盖文件/目录两种逃逸与内部链接放行。详见 [`changelog/11-sandbox-symlink-escape.md`](changelog/11-sandbox-symlink-escape.md)。

## [Unreleased] — 纯 APB draft 契约修复

### Fixed
- **`extract_interfaces --emit-draft-yaml` 对纯 APB 设备生成自家 validator 拒绝的 draft**（`iface_extractor.py`）。生成 draft 时 `axi4_lite` 段是无条件写出的：RTL 只有一套 APB 端口、根本没有 AXI4-Lite 接口时，也会写出空的 `axi4_lite.port_map: {}`。而 `validator.py` 的 `has_axil = isinstance(axil_raw, dict) or not has_apb` 把空 dict 也当作「存在 AXI 边界」，于是对空 `port_map` 逐条检查 16 个必需信号，产生 **17 条 E001**。

  后果：README 推荐的两步流程（`extract_interfaces --emit-draft-yaml` → `emit_testbench --spec-path`）用在纯 APB RTL 上，第一步产物被第二步以 17 条 E001 拒绝；用户只会以为自己契约写错，而不是工具只生成 AXI4-Lite wrapper。

  修复：仅当 RTL 真有 AXI4-Lite 接口（或两种接口都没有，保持旧行为）时才写 `axi4_lite` 段。纯 APB 设备 draft 现在 0 ERROR、`validate_contract` 返回 `ok: True`；`emit_testbench` 的报错也从 17 条 E001 变为可读的 `Spec has no AXI4-Lite boundary ...`。新增 `tests/test_regression.py::test_pure_apb_draft`（4 项检查）锁死该行为。详见 [`changelog/10-pure-apb-draft.md`](changelog/10-pure-apb-draft.md)。

## [Unreleased] — 独立审计修复

本轮对 v0.5.0 做外部审计（非作者视角重跑全部工具），发现并修复 3 处「声称能做但实际静默退化」的真实缺陷。这些缺陷的共同点恰好是本项目 README 反复批判的行为——静默通过。

### Fixed
- **`slang` AST 后端此前从未真正生效**（`core/sv_parser.py`）。两个叠加的根因：
  1. `slang --ast-json -` 会把人类可读 banner（`Top level design units:` / `Build succeeded: ...`）与 JSON 混在同一个 stdout，旧代码直接 `json.loads(整段)` 必然失败；
  2. 真实 RTL 普遍 `import xxx_pkg::*` / 引用 SV interface，只把单个文件喂给 slang 会报 `unknown package/interface` 并以非 0 退出（但仍输出完整 AST），旧代码因 `returncode != 0` 整段丢弃。

  修复后：从第一个 `{` 定位 JSON；自动收集同目录 `*_pkg.sv` / `*_if.sv` 作为同批编译文件并加 `-I` include 目录；加 `--timescale 1ns/1ps` 消除缺 timescale 的噪声报错。实测：参考靶场含 module 的 39 个 `.sv` 文件 **100% 走 slang 后端、0 静默回退**（修复前几乎全为 builtin）。

- **`slang` 不导出 `InterfacePort.interfaceDef/modport`**（slang 12.x 的 ast-json 只有 `{name,kind,addr,isGeneric}`）。直接切到 slang 会丢掉 `axi_lite_if.slave` 这类信息。新增 `_enrich_from_builtin`：slang 为主后端，用 builtin 解析补齐它缺失的 interface 类型/modport，两种后端互补而非二选一。

- **`slang` 回退对调用方不可见**。新增 `W013` 诊断（`iface_extractor.py`）：slang 装了却没生效时明确上报原因，而不是静默退回 builtin。相关 W 码：`W001`（可选 axi4_lite 信号未映射）、`W010`/`W011`（无 clock/reset 时默认值提示）、`W101`（`include` 头文件未找到）。

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

## [Unreleased] — 真实第三方代码验证

用 GH 上 3 个真实 AXI↔APB 桥仓库（`axi_to_apb`、`AXI4-Lite-to-APB-Bridge`、`AXI4_Lite_APB4_Bridge`，共 11 个 RTL/TB 文件）实测工具箱，验证其在非自带靶场上的表现。

### 结果
- `index_repo`：3/3 正确解析（模块/端口/参数/例化）。
- `extract_interfaces`：3/3 正确识别 AXI4-Lite(slave) + APB(master) 协议、时钟/复位、端口映射。
- `lint_tb`：**发现 1 个真实漏报**（见下）。

### Fixed
- **`index_repo` 的 `file_path` 归属在 `include` 拼接仓库上错乱**（`repo_indexer.py`）。做跨工具集成测试（index_repo → resolve_deps → extract_interfaces）时发现：`datapath` 实际定义在 `datapath.v`，但 index_repo 标成 `top_proc.v`——因为 `top_proc.v` include 了 `datapath.v`，解析器展开后 `datapath` 出现在两个文件的解析结果里，modules 字典按名去重时「排序靠后的文件覆盖前面的」，`top_proc.v` 覆盖了正确的 `datapath.v`。下游 `extract_interfaces(file_path)` 因此返回的第一个模块是 `top_proc` 而非 `datapath`（跨工具不一致）。

  修复：新增 owner 归属表——模块应归属到**原始文本里真正声明 `module <name>`** 的文件（`_module_body` 在该文件能定位到自身定义体），而非 include 展开后恰好覆盖的文件。实测 riscv-cpu：`datapath→datapath.v`、`regfile→regfile.v`、`DATA_MEMORY→ram.v` 全部修正。

  这是 #12（例化归属）的姊妹缺陷：#12 修的是「例化归属错」，本条修的是「file_path 归属错」，两者同源于 `include` 展开，但需分别处理。

- **`audit_harness` strict 失败时丢失逐项结果**（`self_auditor.py`）。排查全部 12 工具的错误路径后，又发现 `audit_harness` 在 `strict` 且有失败时返回 `err_result` 丢 `data`——调用方只看到「自审计 N 项失败」的汇总，看不到**哪几项失效**（而那正是自审计的核心价值：定位哪个护栏坏了）。修复后 E040 返回时显式带 `data`（含逐项 `passed/failed` 的 `checks`）。至此 `lint_tb`(#6)、`run_mutation`(#10)、`audit_harness` 三处同类缺陷全部修完，根因都是 `err_result` 默认丢 `data`。

- **`run_mutation` 分数低于门槛时丢失逐变异结果**（`mutation_runner.py`）。`score < min_score` 返回 `err_result` 且 `data={}`，调用方只看到一句「50% < 100%」，看不到**哪些变异存活**——而那正是变异测试的核心价值（存活变异 = 测试盲区）。与上轮 `lint_tb` 同类修复：E031 返回时显式带 `data`（含 killed/survived/results）。

  实测：include 拼接仓库（`leaf.v` + `top.v`）上，M01（加法改减法）被杀、M02（改实例名）存活——修复后能看清 M02 是等价变异，而非只有一句分数不足。

- **`index_repo` 例化归属在 `include` 拼接仓库上全错**（`repo_indexer.py`）。用 2 个真实 RISC-V 核仓库实测时发现：这些仓库用 `` `include`` 把多个子模块定义拼进一个翻译单元（如 `Single_Cycle_Top.v` include 了 PC/ALU/寄存器堆/存储器全部子模块）。旧实现把**整个文件源**交给例化扫描，导致文件里**每一个**模块都继承该文件的全部例化——12 个模块里有 11 个被误标「例化了 PC/寄存器堆/存储器」，而那些其实是顶层 `Single_Cycle_Top` 的例化。叶子模块 ALU/Register_File 呈现 8 条错误例化。

  修复：新增 `_module_body(name, src)`，用 token 行号定位 `module <name>` 到配对 `endmodule`，只把该模块**自身定义体**交给例化扫描。`include` 展开进来的子模块在本文件原始文本里没有自身 `module` 声明时不归属任何例化（返回空串，而非回退到整个文件源）。

  实测两个真实 RISC-V 仓库：32-bit Single-Cycle（include 拼接）修复前 11 个模块误带 8–9 条例化 → 修复后仅 `Single_Cycle_Top` 带 9 条、叶子全 0；riscv-cpu 同样修正。新增 `test_index_repo_include_boundary`（3 项检查）锁死该行为。

- **`lint_tb` 漏报 `wait()` 形式的握手风险**（`tb_linter.py`）。R1/R3 旧实现只匹配 `while (!BVALID) @(posedge clk);`，不识别语义等价的 `wait (BVALID);`。而真实第三方 testbench 广泛使用 `wait()`——实测 GH 的 `AXI4_Lite_APB4_Bridge`，其 write task 在 `bready=1'b0` 下 `wait(bvalid)`，与 R1 抓的 `while(!bvalid)` 是同一风险模式，却完全漏报。修复后新增 `wait(expr)` 检查（兼容裸信号名 `bvalid` 与带前缀 `axi.BVALID`，大小写不敏感），并在 `wait` 同时检查 READY 时正确判定为干净。实测该真实 TB：旧实现 0 报告 → 修后抓出 3 处 R3（bvalid×2、rvalid×1）。

### Tests
- 新增 `test_lint_wait_form`（3 项检查）：`wait(bvalid)` 无 READY → R3；`wait(bvalid && bready)` → 干净。
- 新增 `test_index_repo_include_boundary`（3 项检查）：include 拼接项目的例化归属正确。

- **参考靶场 `mutate.py` 的路径与靶场布局不一致**。`MUTATIONS` 里 33 条的 `file` 是相对 `mutate.py`（原 `axi/` 平铺布局）的裸文件名，但靶场把这些 RTL 放进了 `axi/sv/` 子目录，导致 `run_mutation` 用 `<workdir>/<file>` 解析时 33 条全部 `Missing file`。这是靶场拷贝时目录布局漂移，属已知限制：使用 `run_mutation` 时需让 spec 的 `file` 路径与实际布局一致（靶场为 `axi/sv/` 前缀）。

- **`emit_testbench` 生成的测试台对 AXI↔APB 桥无法运行**（`tb_emitter.py`，核心用途）。用真实 AXI↔APB 桥实测时发现：生成的 cocotb 测试台只驱动 AXI4-Lite 侧，完全不驱动 APB 侧——而桥的 APB 侧是 master，需要连 APB slave 才能完成事务。APB 侧 input（pready/prdata/pslverr）被 tie 0，桥发起读后等不到 pready，触发 P_003 RVALID timeout（240ns 失败）。

  修复：`_emit_cocotb` 生成 APB slave 响应器（`cocotb.start_soon`），响应桥的 APB 读写（write 存 mem、read 返回 mem、拉高 pready）。APB 端口经 wrapper 连到 obs_ 探针，cocotb 从 `dut.obs_*` 驱动。实测：AXI↔APB 桥的 cocotb 测试台 `test_axil_rules` **PASS**（4470ns，TESTS=1 PASS=1 FAIL=0）—— emit_testbench 生成的测试台首次对真正的 AXI↔APB 桥运行通过。

  另：APB obs_ 探针/tie_off 位宽改用该端口实际宽度（paddr/pwdata/prdata=32，pstrb=4），修 WIDTHEXPAND。

### Verified（实机，第二轮续）
- `render_visual_report` 实际渲染验证：生成 1036 字节 SVG（含 DUT 框 + 2 个依赖模块 + `axi4_lite(2), apb(2)` 接口端口数标注）与 1799 字节 HTML，`consistency_ok: True`，无静默退化。注意 CLI 的 `extracted_interfaces` 需传 JSON **数组**（传两个独立 dict 会被当成字符串报 `'str' object has no attribute 'get'`）。
- `lint_tb` 对真实 testbench（非故障注入脚本）跑通：在 `axi/sv/` 扫出 2 ERROR（R3）+ 22 WARNING（R1）。经代码核对，R1 全是「READY 在循环外预先置高」的合法模式（静态规则无法做数据流追踪，归为 WARNING 合理）；R3 抓到的是 testbench **故意**保持 READY=0 等 VALID 的边界场景（测「响应未取走时另一通道可完成」），spec 上确有死锁风险，报 ERROR 是有价值的真实信号而非误报。

### Verified（实机）
- Verilator 5.054 完整 `make install`（补装 `help2man` 后）——此前手工安装把源码树误拷进 `share/verilator/include`，导致包装脚本 `verilator` 推导的 `VERILATOR_ROOT` 找不到 `verilated_std.sv`，所有依赖内置 std 的构建报 `%Error: Cannot find verilated_std_waiver.vlt`。重装后 include 干净、`verilated_std.sv` / `verilated_std_waiver.vlt` / `verilated.mk` 齐全。
- `run_sim` 单套件（counter，20 checks）首次跑（清空产物）端到端 PASS；`run_mutation` 单变异（M01 地址译码错位）被正确 KILLED（`mutation_score: 100.0`），`try/finally` 强还原验证通过（跑完无残留），E030 残留拦截验证通过（预置未还原变异时拒绝运行）。
- 说明：25 套件全量回归在 `run_sim` 默认 300s 超时内跑不完（实测需更久），属超时配置问题而非缺陷，需显式传更大的 `--timeout-sec`。

### Tests
- `tests/test_fusion_and_cross_repo.py` 新增两组回归：
  - `test_slang_backend_real_rtl`（7 项检查）：锁死 slang 后端在真实 RTL 上的生效、无静默回退、W013 可见性；
  - `test_emit_testbench_error_paths`（5 项检查）：锁死两条错误路径都给出清晰信息而非 cryptic KeyError。

## [0.5.0] - 2026-11-22

### Added
- **4 个新原子工具（总数由 8 个扩展至 12 个）**：
  - `lint_tb`（`fpga_toolbox/tb_linter.py`）：整合原 `agent/verilog` 的时钟沿竞争（C1–C2）、AXI 握手死锁（R1–R4）与静默通过（S1–S3）静态检查规则。
  - `run_sim`（`fpga_toolbox/sim_runner.py`）：封装 Verilator ≥ 5.040 + slang 仿真、`stdbuf` 日志保活与逐套件身份校验（`verify_suite_identities`），缺工具时显式报 `E020/E021` 拒绝静默通过。
  - `run_mutation`（`fpga_toolbox/mutation_runner.py`）：参数化 RTL 变异测试引擎，内置 `try/finally` 强还原、残留变异拦截（`E030`）与杀死率门槛（`E031`）。
  - `audit_harness`（`fpga_toolbox/self_auditor.py`）：A1–A4 故障注入式自审计，验证护栏出错时必非零/非 ok 退出。
- **环境探测模块**：新增 `fpga_toolbox/eda_env.py`，移植 `vlt_env.sh` 的 Verilator ≥ 5.040 最低版本拦截逻辑。
- **现代 SV 解析与 APB 协议支持**：
  - `sv_parser.py` 新增可选 `slang --ast-json` 后端，内置解析器原生识别 SV `interface/modport` 端口并输出 `W012` 告警，消除 1-bit `tie_off` 误判。
  - `iface_extractor.py` 新增 APB3/APB4 分类器与主从角色判定。
  - `types.py` / `validator.py` 新增 `ApbBoundaryIR`、`BitFieldIR`、`QualityGatesIR`，兼容 `apb-regfile-spec/v2` JSON 规格，新增位域重叠（`E013`）与位宽越界（`E014`）校验，扩展寄存器属性至 `RO/RW/W1C/W1S/RC/RSVD/WO`。
- **交互式 Web 工具与报告（`web_tools/`）**：
  - `web_tools/protocol-editor.html`：单文件自定义协议编辑器（全 Pills/按钮组、`TB ➔ DUT` ⇄ `TB ⬅ DUT` 单击切换、`control/state/data` 分类、Verilog 复位字面量、可隐藏位宽列、彩色单胶囊通道组枚举与管理弹窗、每通道对称 `TX/RX` 与一键镜像补齐、交互式波形沙箱、内置 Mini-TLC BFS 模型检测器，支持导出 YAML / SV Monitor / TLA+ `.tla` / JSON）。
  - `web_tools/repo-comparison.html`：双仓库架构与跨库实测对比报告。
- **参考靶场与测试文档**：
  - `examples/axi_apb_ref/`：完整纳入 `agent/verilog` 的 AXI4-Lite ↔ APB 桥 DUT、VIP、25 个 Verilator 测试套件与 `regfile_spec.json`。
  - `tests/test_fusion_and_cross_repo.py`：跨库解析、位域校验、静态 Lint、变异测试、Verilator 身份核对与自审计回归套件。
  - `docs/TEST_REPORT_AND_PLAN.md`：全工具实测结果与外部 EDA/CI 环境测试方案。
  - `docs/verification-schemes-report.html`：FPGA 仿真与验证全景方案说明报告（5 类仿真引擎 + 6 大验证方法论 + 3 套项目落地配方与缺陷拦截地图）。

### Changed
- `tb_emitter.py`：补全 cocotb 生成器对 `write_mask`、RO/RSVD 不可变检查、`random_backpressure` 与 `iterations` 压测的消费，采用 `startup → 阶段测试 → drain → finalize` 生命周期。
- `result_normalizer.py`：支持 Verilator 多套件日志身份核对及 `coverage_summary` / `mutation_summary` 透传（schema 升至 `1.1`）。
- `visual_renderer.py`：内置 HTML 标签平衡与规则行数一致性自检（`W050/W051`），并渲染覆盖率与变异摘要。
- `.github/workflows/ci.yml` 与 `pyproject.toml`：升级至 `0.5.0`，新增跨库回归测试与 `audit_harness` CI 门槛，并在 ruff 配置中排除 `examples/` 参考靶场。

## [0.4.0] - Prior Baseline
- 原始 `hana-ame/fpga_toolbox` 8 个原子工具、MCP Schema 导出、CLI 三层入口与沙箱防误删契约。
