# Changelog — 真实第三方代码验证（Unreleased）

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