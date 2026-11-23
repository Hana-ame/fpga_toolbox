# Changelog — 端口方向不匹配导致仿真永久挂起

前两轮（14、15）给 `emit_testbench --protocol` 加了生成前的**端口存在**校验（缺端口 → fail-fast）。但只校验"端口在不在"还不够：**方向错了照样出坏产物**，而且症状比缺端口更隐蔽。

### Fixed

- **`emit_testbench --protocol` 不校验端口方向，方向反向时生成的 TB 会永久挂起**（`protocol_tb_emitter.py`）。

  APB 模板按协议语义连线——TB 驱动 `psel`/`penable`/`pwrite`/`paddr`/`pwdata`/`pstrb`（DUT 的输入），读取 `pready`/`prdata`/`pslverr`（DUT 的输出）。握手等待是：

  ```verilog
  do begin @(posedge clk); #1; waits++; end while (!pready);
  ```

  若 DUT 把 `pready` 误写成 `input`，TB 这边 `pready` 就没被任何东西驱动，这个循环**永远等不到响应**：

  ```
  $ timeout 30 bash run_apb_dir_apb.sh
  （无输出，被 SIGTERM 杀死）
  ```

  而 `emit_testbench` 仍返回 `ok=True`，产物看起来正常。

  **为什么比编译失败更危险**：方向错**不会**编译失败——Verilator 只报几条通用 warning（`UNDRIVEN`、`TIMESCALEMOD` 之类），很容易被 `-Wno-fatal` 吞掉。到了 CI 里，症状是 **job 超时**而不是失败，排查方向完全被误导。这也是我实测时任务直接卡死、需要手动 `job_kill` 才发现的原因。

### 修复

新增 `EXPECTED_TB_DIR`（按协议列出每个端口在 DUT 侧应有的方向）与 `_wrong_dir_ports()`，与端口存在校验一起在写文件前执行：

```
Cannot emit a 'apb' testbench for apb_dir: DUT port direction(s) disagree with
the template: pready->pready (expected DUT output, got input). A direction
mismatch does not fail to compile but hangs the simulation (the testbench
waits forever for a response).
```

实现要点：

- 方向表按**模板的实际用法**推导：TB 赋初值的是 DUT 输入，TB 无初值只读取的是 DUT 输出；
- `inout` 端口跳过方向校验（由 DUT 侧三态驱动，TB 端按 input/output 都能工作）；
- 顺带把端口名与方向的解析合并成一次 `parse_sv_file`（原实现会解析两次）；
- 方向表只覆盖 `apb`/`ahb_lite`/`axi4` —— `ddr3`/`ddr4`/`hbm`/`pcie` 的模板是 DUT 侧观察式连线，不驱动握手，不存在这类挂起模式。

### Tests

`test_multi_bus_emitter_port_guard` 从 12 项扩到 15 项，新增：`pready` 写成 `input` 的 APB 被拒绝、错误信息解释方向不匹配的风险、**不写出会挂起的 TB**。

### 验证

- 官方靶场 7 协议生成全部成功，方向校验无误伤；实跑 APB 8/8、AXI4 6/6、AHB 6/6、DDR3/DDR4/HBM PASS。
- regression / fusion / audit 9-9 / docs_check D1–D13 / ruff / py3.10 全绿。
