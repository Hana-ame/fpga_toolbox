# fpga_toolbox 示例集

每个示例都可直接运行。所有输出均为**实际运行结果**，非示意。

前置条件：

```bash
pip install "pyyaml>=6.0"
# 仿真相关示例另需 Verilator >= 5.040（可选 slang）
```

---

## 示例 1 — 从 RTL 到可运行测试台（契约路径）

**目标**：手里有一个 AXI4-Lite 从机 RTL，想在不手写测试台的情况下跑起仿真。

### 1.1 准备 DUT

`/tmp/ex1/dut.sv`：

```verilog
`timescale 1ns/1ps
module reg_slave #(parameter int ADDR_W = 12, DATA_W = 32) (
  input  logic              aclk,
  input  logic              aresetn,
  input  logic [ADDR_W-1:0] s_axi_awaddr,
  input  logic [2:0]        s_axi_awprot,
  input  logic              s_axi_awvalid,
  output logic              s_axi_awready,
  input  logic [DATA_W-1:0] s_axi_wdata,
  input  logic [DATA_W/8-1:0] s_axi_wstrb,
  input  logic              s_axi_wvalid,
  output logic              s_axi_wready,
  output logic [1:0]        s_axi_bresp,
  output logic              s_axi_bvalid,
  input  logic              s_axi_bready,
  input  logic [ADDR_W-1:0] s_axi_araddr,
  input  logic [2:0]        s_axi_arprot,
  input  logic              s_axi_arvalid,
  output logic              s_axi_arready,
  output logic [DATA_W-1:0] s_axi_rdata,
  output logic [1:0]        s_axi_rresp,
  output logic              s_axi_rvalid,
  input  logic              s_axi_rready
);
  logic [DATA_W-1:0] rw_reg;
  always_ff @(posedge aclk) begin
    if (!aresetn) rw_reg <= '0;
    else if (s_axi_awvalid && s_axi_wvalid) rw_reg <= s_axi_wdata;
  end
  assign s_axi_awready = 1'b1;
  assign s_axi_wready  = 1'b1;
  assign s_axi_bresp   = 2'b00;
  assign s_axi_bvalid  = s_axi_awvalid && s_axi_wvalid;
  assign s_axi_arready = 1'b1;
  assign s_axi_rdata   = rw_reg;
  assign s_axi_rresp   = 2'b00;
  assign s_axi_rvalid  = s_axi_arvalid;
endmodule
```

### 1.2 提取接口，生成契约草稿

```bash
python3 -m fpga_toolbox extract_interfaces \
  --rtl-file /tmp/ex1/dut.sv \
  --module-name reg_slave \
  --emit-draft-yaml /tmp/ex1/contract.yaml
```

真实输出（节选）：

```json
{
  "ok": true,
  "data": {
    "rtl_file": "/tmp/ex1/dut.sv",
    "modules": [{
      "module_name": "reg_slave",
      "parser_backend": "slang",
      "parameters": {"ADDR_W": 12, "DATA_W": 32},
      "total_ports": 21,
      "clock": {"port": "aclk", "freq_mhz": 100.0},
      "reset": {"port": "aresetn", "active_low": true, "sync": true},
      "interfaces": [{
        "protocol": "axi4_lite",
        "group_prefix": "s_axi",
        "role": "slave",
        "addr_width": 12,
        "data_width": 32,
        "port_map": {"awaddr": "s_axi_awaddr", "wdata": "s_axi_wdata", "...": "..."}
      }]
    }],
    "draft_yaml_path": "/tmp/ex1/contract.yaml"
  }
}
```

注意返回的是 `modules` **数组**（一个文件可含多个模块），不是顶层字段。

生成的 `contract.yaml` 已经可以往下用——时钟、复位、19 个 AXI4-Lite 信号、
1 条默认寄存器规则都由工具推断填入：

```yaml
boundary:
  module_name: reg_slave
  clock: {port: aclk, freq_mhz: 100.0}
  reset: {port: aresetn, active_low: true, sync: true}
  axi4_lite: {addr_width: 12, data_width: 32, role: slave, port_map: {...}}
test_plan:
  register_rules:
  - {rule_id: REG_001, name: CTRL_REG, offset: 0, access: RW,
     write_mask: 0xffffffff, read_mask: 0xffffffff}
  illegal_addr_rules:
  - {rule_id: ILL_001, offset: 252, expected_resp: SLVERR}
```

真实项目里通常再手工补充 `register_rules`（每个寄存器的 offset / access / write_mask）。

### 1.3 校验契约（交叉核对 RTL）

```bash
python3 -m fpga_toolbox validate_contract \
  --spec-path /tmp/ex1/contract.yaml --rtl-file /tmp/ex1/dut.sv
```

```json
{"ok": true,
 "data": {"module": "reg_slave", "rtl_cross_checked": true,
          "has_apb": false, "register_count": 1}}
```

`rtl_cross_checked: True` 表示契约里的端口名/方向已经在真实 RTL 上核对过
（`E011` 端口不存在、`E012` 方向不符都在这一步拦）。

### 1.4 生成测试台

```bash
python3 -m fpga_toolbox emit_testbench \
  --spec-path /tmp/ex1/contract.yaml \
  --out-dir /tmp/ex1/tb \
  --rtl-file /tmp/ex1/dut.sv
```

```json
{
  "ok": true,
  "data": {
    "module": "reg_slave",
    "dual_side": false,
    "output_dir": "/tmp/ex1/tb",
    "artifacts": {
      "unit_isolation_wrapper": "/tmp/ex1/tb/reg_slave_axil_wrap.sv",
      "sva_protocol_checker":  "/tmp/ex1/tb/reg_slave_axil_sva_checker.sv",
      "in_situ_bind_file":     "/tmp/ex1/tb/reg_slave_axil_bind.sv",
      "cocotb_testbench":      "/tmp/ex1/tb/test_reg_slave_axil.py"
    }
  },
  "diagnostics": []
}
```

4 个工件各司其职：

| 工件 | 作用 |
|---|---|
| `*_axil_wrap.sv` | 单元隔离 wrapper，把 DUT 的 AXI 端口提到顶层 |
| `*_axil_sva_checker.sv` | 协议断言（含心跳探针，证明引擎在跑）|
| `*_axil_bind.sv` | 把 checker bind 进 DUT，无需改 RTL |
| `test_*_axil.py` | cocotb 测试台，含写回读、非法地址、随机反压 |

生成的 SVA checker 里带心跳：

```verilog
int unsigned sva_hb_cnt;
always_ff @(posedge clk) begin
  if (!rst_n) sva_hb_cnt <= 0;
  else begin
    sva_hb_cnt <= sva_hb_cnt + 1;
    if (sva_hb_cnt % 100 == 99)
      $display("[SVA:HBEAT] rule=P_000 monitored_cycles=%0d status=ACTIVE", sva_hb_cnt + 1);
  end
end
```

> 为什么要心跳：断言通过时是**零输出**的。没有心跳，"全部通过"与"checker
> 根本没 bind"在日志上完全一样，`normalize_sim_result` 无法区分。

---

## 示例 2 — 多协议测试台直出（无需写契约）

**目标**：已有 RTL，想知道它的总线协议行为是否符合预期，不想写契约。

```bash
python3 -m fpga_toolbox emit_testbench \
  --rtl-file examples/multi_bus_ref/apb_slave.sv \
  --out-dir /tmp/ex2 --protocol apb
```

```json
{"ok": true, "protocol": "apb", "suites": ["apb"],
 "artifacts": {"sv_testbench": "...", "sim_script": "..."}}
```

直接运行生成的脚本：

```bash
$ bash /tmp/ex2/run_apb_slave_apb.sh
== sim: apb (top=tb_apb_slave_apb) ==
[CHECK] id=APB_001 | proto=APB | cat=reset_read | tx=RD addr=0x0000 wdata=0x00000000 strb=0xf waits=1 | exp=0x12345678(err=0) | got=0x12345678(err=0) | status=PASS
[CHECK] id=APB_002 | proto=APB | cat=pstrb_write | tx=WR addr=0x0000 wdata=0xaa00bb00 strb=0xa waits=1 | exp=0xaa00bb00(err=0) | got=0xaa00bb00(err=0) | status=PASS
[CHECK] id=APB_003 | proto=APB | cat=pstrb_read | tx=RD addr=0x0000 wdata=0x00000000 strb=0xf waits=1 | exp=0xaa34bb78(err=0) | got=0xaa34bb78(err=0) | status=PASS
...
[CHECK] id=APB_008 | proto=APB | cat=pslverr_ill | tx=RD addr=0x0040 wdata=0x00000000 strb=0xf waits=1 | exp=0x00000000(err=1) | got=0x00000000(err=1) | status=PASS
PASS: apb all 8 checks passed
```

每条 `[CHECK]` 都是自证的：`exp` / `got` 成对打印，`err` 位单独列出，
`status` 由比对结果决定。日志被截断时，`normalize_sim_result` 靠汇总行计数
判断是否跑完（见示例 6）。

7 种协议可用，每个都有对应的自检序列：

| 协议 | 检查内容 | 实际运行结果 |
|---|---|---|
| `apb` | reset 读、PSTRB 写、RO、W1C、PSLVERR | `PASS: apb all 8 checks passed` |
| `axi4` | INCR4/INCR8 突发 + ID + RLAST + SLVERR | `PASS: axi4_full all 6 burst/SLVERR checks passed` |
| `ahb_lite` | 流水线 RW/RO + HREADYOUT + HRESP | `PASS: ahb_lite all 6 checks passed` |
| `ddr3` | ACT/WR/RD(CL=2)/PRE + 多 bank | `PASS: ddr3 ACT/WR/RD(CL=2)/PRE all checks passed` |
| `ddr4` | BG/BA + DM_DBI_n 字节掩码 | `PASS: ddr4 BG/BA + DM_DBI_n masked write/readback passed` |
| `hbm` | 256-bit 伪通道 ID 跟踪 + 反压 | `PASS: hbm 256-bit pseudo-channel ID+data+backpressure passed` |
| `pcie` | Gen3/4/5 × x1/x4/x16 共 9 套件 | 见示例 3 |

### 生成前的三道闸门

多协议路径会在写文件**之前**校验，避免产出无法编译或有隐患的 TB：

```bash
# ① 端口缺失：DUT 是 APB3 风格（无 pstrb/pslverr）
$ python3 -m fpga_toolbox emit_testbench --rtl-file apb3_dut.sv --protocol apb --out-dir /tmp/x
ok: False
error: Cannot emit a 'apb' testbench for apb3_dut: DUT is missing port(s)
       pstrb->pstrb, pslverr->pslverr that the generated template drives.
       Detected RTL ports: ['paddr', 'pclk', 'penable', 'prdata', 'pready',
       'presetn', 'psel', 'pwdata', 'pwrite']

# ② 方向错误：pready 被写成 input
ok: False
error: Cannot emit a 'apb' testbench for apb_dir: DUT port direction(s) disagree
       with the template: pready->pready (expected DUT output, got input).
       A direction mismatch does not fail to compile but hangs the simulation
       (the testbench waits forever for a response).

# ③ 位宽：窄总线自动缩放测试向量
$ # data_width=8 时生成的是 8'h78 而不是 8'h12345678
```

> 第 ② 条尤其重要：方向错**不会编译失败**，Verilator 只报几条 `UNDRIVEN`
> 警告。但 TB 的 `do ... end while (!pready)` 永远等不到响应——CI 表现为**超时**
> 而不是失败，排查方向会被完全误导。

---

## 示例 3 — PCIe 9 套件矩阵

```bash
python3 -m fpga_toolbox emit_testbench \
  --rtl-file examples/multi_bus_ref/pcie_tlp_ep.sv \
  --out-dir /tmp/ex3 --protocol pcie
```

```
ok: True | protocol: pcie
suites: ['pcie_g3x1', 'pcie_g3x4', 'pcie_g3x16',
         'pcie_g4x1', 'pcie_g4x4', 'pcie_g4x16',
         'pcie_g5x1', 'pcie_g5x4', 'pcie_g5x16']
```

跑单个套件或全部：

```bash
$ bash /tmp/ex3/run_pcie_tlp_ep_pcie.sh pcie_g3x1
== sim: pcie_g3x1 (top=tb_pcie_tlp_ep_pcie) ==
PASS: pcie Gen3 x1 (DW=64) MWr/MRd/CplD passed

$ bash /tmp/ex3/run_pcie_tlp_ep_pcie.sh pcie_g5x16
== sim: pcie_g5x16 (top=tb_pcie_tlp_ep_pcie) ==
PASS: pcie Gen5 x16 (DW=512) MWr/MRd/CplD passed

$ bash /tmp/ex3/run_pcie_tlp_ep_pcie.sh all     # 跑全部 9 个
```

每个套件用不同的 `GEN`/`LANES` 覆盖参数，数据宽度自动推导：
`x1 → DW=64`、`x4 → DW=128`、`x16 → DW=512`。

---

## 示例 4 — 反踩坑：静态 Lint 抓静默通过

**目标**：测试台看起来跑完了，但事实上什么也没验证。

`/tmp/ex4/bad_tb.sv`：

```verilog
`timescale 1ns/1ps
module bad_tb;
  logic clk = 0;
  axi_lite_if s_axi_if();
  always #5 clk = ~clk;

  initial begin
    // C1: 在 @(posedge) 上下文用阻塞赋值驱动 interface（时钟竞争）
    @(posedge clk);
    s_axi_if.awvalid = 1'b1;
    s_axi_if.awaddr  = 12'h100;

    repeat(10) @(posedge clk);
    // S1: 静默通过 —— 没有任何 PASS/FAIL 判定就结束
    $display("TEST DONE");
  end
endmodule
```

```bash
$ python3 -m fpga_toolbox lint_tb --target-path /tmp/ex4/bad_tb.sv
ok: False
findings:
  C1  /tmp/ex4/bad_tb.sv:10  Blocking interface drive immediately after @(posedge) without delay.
  S4  /tmp/ex4/bad_tb.sv:15  SV testbench emits $display but contains no pass/fail
                             decision (no assert/$error/$fatal/check task).
                             The simulation can only pass; it never expresses failure.
error: Lint failed with 2 error(s) and 0 warning(s).
```

两个发现对应 TB 的两个独立问题：

- `C1` — 时钟竞争（行为可能不稳）
- `S4` — **静默通过**：这个 TB 打印了 `TEST DONE` 就 `$finish`，从头到尾没有
  任何判定。它跑一百次都是"通过"，因为它从未表达过失败。

反过来，带真实判定的 TB 不会被误报。以下 4 种形态都返回 `ok=True`：

```verilog
assert (dout == 8'h12) else $error("bad");        // 内联断言
if (dout !== 8'h12) $error("mismatch");           // if + $error
if (dout !== 8'h12) $fatal(1, "mismatch");        // if + $fatal
check_eq(dout, 8'h12);  // 自研 task，内部 if + $error
```

三组规则：

| 组 | 码 | 抓什么 | 作用于 |
|---|---|---|---|
| 时钟竞争 | `C1` `C2` | `@(posedge)` 后用阻塞赋值驱动 interface 信号 | `.sv` |
| 握手死锁 | `R1`–`R4` | valid 撤回过早、无限等 ready、响应超时 | `.sv` |
| 静默通过 | `S1` `S2` `S3` | 常量返回的 stub 函数、SKIP 后 `exit 0`、只数 PASS 行数 | `.sh`/`.py` |
| 静默通过 | `S4` | SV 测试台只 `$display` 无任何判定 | `.sv` |

> `lint_tb` 返回 `ok=False` + 结构化 `findings`。调用方可以直接拿
> `data.error_count` / `data.warning_count` 做门禁，不用解析文本。

---

## 示例 5 — 参数化变异测试

**目标**：验证测试台真的能抓住 RTL 缺陷（不是"跑完就算过"）。

`/tmp/ex5/dut.sv`：

```verilog
`timescale 1ns/1ps
module dut(input logic a, b, output logic y);
  assign y = a & b;
endmodule
```

`/tmp/ex5/muts.json`：

```json
[
  {"id": "M01", "file": "dut.sv", "old": "a & b", "new": "a | b", "desc": "and->or"},
  {"id": "M02", "file": "dut.sv", "old": "a & b", "new": "a",      "desc": "drop b"}
]
```

`/tmp/ex5/check_dut.sh`（真值表自检，任一不符即非零退出）：

```bash
#!/bin/bash
set -e
verilator --binary -j 0 -Wno-fatal --top-module tb_dut tb_dut.sv dut.sv -o sim > /dev/null 2>&1
./obj_dir/sim | grep -q "PASS: dut AND truth table"
```

```bash
$ python3 -m fpga_toolbox run_mutation \
    --workdir /tmp/ex5 \
    --mutation-spec /tmp/ex5/muts.json \
    --test-cmd '["bash","check_dut.sh"]'
```

```
ok: True | total: 2 | killed: 2 | survived: 0 | mutation_score: 100.0
  M01  KILLED  returncode=1  and->or
  M02  KILLED  returncode=1  drop b
```

> `--test-cmd` 收的是 **argv JSON 数组**，不走 shell。直接写
> `--test-cmd "test -f dut.sv"` 会被当成单个可执行文件名并报
> `[Errno 2] No such file or directory: 'test -f dut.sv'`。

两个关键保证：

1. **`try/finally` 强还原** — 无论测试命令成功、失败还是抛异常，`dut.sv` 都会
   被恢复。实测验证过：运行前后 `sha256sum /tmp/ex5/dut.sv` 完全一致。
2. **残留变异拦截（`E030`）** — 如果进程被 kill 导致 finally 没跑完，
   下次进入时会检测到工作区残留变异并直接报错，不会在脏状态上继续。

变异分数低于阈值时返回 `ok=false` + `E031`：

```
$ # 若 test_cmd 不真正校验（如 '["test","-f","dut.sv"]' 恒为 0）
ok: False | killed: 0 | survived: 2 | mutation_score: 0.0
E031: 0/2 killed (0.0% < 80.0%).
```

> 这正是变异测试的价值：台阶恒绿的命令会暴露出来——它杀不死任何变异，
> 说明它根本没在检查行为。

---

## 示例 6 — 归一化三源日志 + 完整性检查

**目标**：把 cocotb / SVA / Verilator 三种日志合成一份统一报告，并且**能识别
报告是否完整**。

### 6.1 完整链：跑仿真 → 归一化

```python
from fpga_toolbox import call_tool

# 1) 跑仿真
r = call_tool("run_sim", {"sim_script": "examples/multi_bus_ref/sim.sh", "suite": "apb"})
print(r["ok"], r["data"]["returncode"])        # True 0
tail = r["data"]["stdout_tail"]
# == sim: apb (top=tb_apb) ==
# [CHECK] id=APB_001 | ... | status=PASS
# ...
# PASS: apb all 8 checks passed

# 2) 写日志并归一化
open("/tmp/sim.log", "w").write(tail)
n = call_tool("normalize_sim_result", {
    "module_name": "apb_slave",
    "verilator_log_path": "/tmp/sim.log",
    "expected_suites": ["apb"],
})
```

```json
{
  "ok": true,
  "data": {
    "overall_status": "PASS",
    "summary": {"total": 8, "passed": 8, "failed": 0, "skipped": 0, "pass_rate": 1.0},
    "failed_rules": [],
    "checks": [
      {"rule_id": "APB_001", "protocol": "APB", "category": "reset_read",
       "engine": "Verilator 5.054",
       "tx": "RD addr=0x0000 wdata=0x00000000 strb=0xf waits=1",
       "expected": "0x12345678(err=0)", "observed": "0x12345678(err=0)",
       "status": "PASS"},
      ...
    ],
    "verilator_suite_identity": {
      "expected": ["apb"],
      "observed": {"apb": "PASS"},
      "missing": [],
      "failed": [],
      "all_verified": true
    }
  },
  "diagnostics": []
}
```

注意字段名：是 `overall_status`（不是 `overall`）、`rule_id`（不是 `id`）。
这份 JSON 是稳定的 `schema_version: "1.1"`，可以直接进下游报告生成器。

### 6.2 完整性检查：残缺日志不会被当通过

这是本工具的核心价值。三种输入源各有自己的完整性信号。
注意 `run_sim`/`normalize_sim_result` 的**诊断码写在 `failed_rules`**，不是
`diagnostics`——它们描述的是"这条运行完整性规则失败了"。

**Verilator — 汇总行计数**

```bash
# 完整：启动 2 套件，2 条 PASS/FAIL 汇总
== sim: apb (top=t) ==
PASS: apb passed
== sim: axi (top=t) ==
PASS: axi passed
→ overall_status: PASS

# 崩在第二个套件：只有 1 条汇总
== sim: apb (top=t) ==
PASS: apb passed
== sim: axi (top=t) ==
[CHECK] id=A1 | status=PASS
→ overall_status: FAIL
   failed_rules:
     VERILATOR_NO_SUMMARY: "2 suite(s) started (['apb', 'axi']) but only 1
     'PASS:'/'FAIL:' summary line(s) found (1 suite(s) aborted before $finish).
     Treat as failure, not partial pass."
```

**cocotb — `completed` 字段**

| 输入 | 结果 |
|---|---|
| `{"completed": true, "checks":[...]}` | `PASS` |
| `{"completed": false, "checks":[...]}` | `FAIL` + `COCOTB_INCOMPLETE` |
| `{"checks":[...]}`（旧格式，无字段） | `PASS`（向后兼容） |

参数名是 `cocotb_results_json`（路径），不是 `cocotb_json_path`。

**SVA — 心跳**

| 输入 | 结果 |
|---|---|
| `[SVA:HBEAT] ... status=ACTIVE`（无违例） | `PASS` |
| `[SVA:HBEAT] ...` + `[RULE:P_001] ...` | `FAIL`，`failed_rules: [P_001]` |
| 空文件 | `NO_RESULTS` |

> 三者的共同点：**产物必须自证完整性**。只统计"我看到了什么"而不判断
> "我是否看完"，就会把崩溃、超时、静默跳过都当成通过。

---

## 示例 7 — 仓库理解：未知例化不会被静默丢弃

**目标**：真实仓库里常例化厂商 IP / 加密 IP，这些模块在仓库里没有定义。

`/tmp/ex7/rtl/top.sv`：

```verilog
module soc_top(input logic clk);
  sub_ok           u_ok  (.clk(clk));   // 仓库里有定义
  vendor_ddr_phy   u_phy (.clk(clk));   // 仓库里没有定义
  secure_boot_rom  u_rom (.clk(clk));   // 仓库里没有定义
endmodule
```

`/tmp/ex7/rtl/sub_ok.sv`：

```verilog
module sub_ok(input logic clk); endmodule
```

```bash
$ python3 -m fpga_toolbox index_repo --project-root /tmp/ex7
```

```json
{
  "ok": true,
  "data": {
    "modules": {
      "soc_top": {
        "instantiated_modules": ["sub_ok"],
        "unresolved_instantiations": ["vendor_ddr_phy", "secure_boot_rom"]
      }
    },
    "unresolved_instantiations": {"soc_top": ["vendor_ddr_phy", "secure_boot_rom"]}
  },
  "diagnostics": [
    {"severity": "WARNING", "code": "W102", "path": "soc_top",
     "message": "Instantiation of module 'vendor_ddr_phy' has no definition in this repo; ..."}
  ]
}
```

再查依赖闭包：

```bash
$ python3 -m fpga_toolbox resolve_deps --target-module soc_top --project-root /tmp/ex7
```

```json
{
  "data": {
    "dependency_closure": ["soc_top", "sub_ok"],
    "unresolved_instantiations": {"soc_top": ["vendor_ddr_phy", "secure_boot_rom"]},
    "closure_is_complete": false
  }
}
```

`closure_is_complete: false` 是给调用方的**单点信号**：这份文件清单不完整，
不要直接拿去建沙箱或做综合。

> 没有这个信号时，工具会返回一份"看起来完整"的 2 文件清单并报成功。
> 用户拿去建沙箱，编译时才发现在缺模块——而中间所有步骤都是绿的。

---

## 示例 8 — PrimeTime 网表清理（含产物自检）

**目标**：DC 导出的网表里有未解析的参数化黑盒，导致 PrimeTime `link_design` 失败。

`/tmp/ex8/nl.v`：

```verilog
module soc(clk, dout);
  input clk;
  output [31:0] dout;
  RDBUS #(.WIDTH(32)) u0 (.clk(clk), .o_data(dout[15:0]));
  CLKCROSSDFF u1 (.clk(clk), .o_q(dout[16]));
endmodule
```

```bash
$ python3 -m fpga_toolbox clean_pt_netlist --in-verilog /tmp/ex8/nl.v --out-verilog /tmp/ex8/out.v
ok: True | removed: 2 | tied: ['dout[15:0]', 'dout[16]']
```

输出 `/tmp/ex8/out.v`：

```verilog
module soc(clk, dout);
  input clk;
  output [31:0] dout;
// [fpga_toolbox:pt_clean] removed unresolved parameterized blackbox instance
// [fpga_toolbox:pt_clean] removed unresolved parameterized blackbox instance
  assign dout[15:0] = 1'b0;
  assign dout[16] = 1'b0;
endmodule
```

关键点：

- **参数化实例也能匹配** — `RDBUS #(.WIDTH(32)) u0 (...)` 的 `#(...)` 嵌套括号
  被正确处理。旧实现用非贪婪 `\((.*?)\)`，遇到 `#(` 里第一个 `)` 就截断，
  `removed_instances` 恒为 0。
- **标量 tie-off** — 输出 `assign dout[15:0] = 1'b0;` 而非
  `assign {a,b} = 0;`（PT 拒收后者）。
- **产物自检** — 清理后若输出里仍有黑盒实例（如实例缺分号导致没匹配到），
  返回 `E051` 而不是静默报成功：

```bash
$ python3 -m fpga_toolbox clean_pt_netlist --in-verilog bad.v --out-verilog o.v
ok: False
diagnostics: E051  pt.netlist  Unremoved blackbox instance(s) ['RDBUS']; netlist is not PT-clean.
error: Blackbox instance(s) still present in the cleaned netlist: ['RDBUS']. Their
       instantiation syntax was not matched (missing semicolon / truncated statement /
       unsupported construct). PrimeTime would still fail link_design.
```

---

## 示例 9 — 自审计：证明护栏本身有效

```bash
$ python3 -m fpga_toolbox audit_harness
ok: True | 11 / 11
```

逐项输出：

```
A1_LINT_FAULT_CATCH              ok=False codes=['C1', 'R2']
A2_MUTATION_RESIDUAL_GUARD       ok=False codes=['E030']
A3_NORMALIZER_REJECT_JUNK        ok=False
A4_SANDBOX_ROOT_GUARD            ok=False
A5_SUITE_IDENTITY_REAL_FORMAT    observed={'counter': 'PASS', 'sv': 'PASS'} missing=['gaps']
A6_SUITE_IDENTITY_ALL_PASS       all_verified=True missing=[]
A7_RUN_SIM_VERSION_GUARD         ok=False e020=True
A8_EMIT_FAIL_FAST                ok=False error=Spec has no AXI4-Lite boundary ...
A9_SLANG_FALLBACK_VISIBLE        slang-missing diagnostics=1 visible=True
A10_PT_NETLIST_RESIDUAL_GUARD    ok=False codes=['E051']
A11_SV_SILENT_PASS_DETECT        silent ok=False codes=['S4'] judged_ok=True
```

工作机制：每一项都**故意构造一个应该失败输入**，然后断言对应工具确实报错了。
例如 A1 喂一个有时钟竞争和握手缺陷的 TB，要求 `lint_tb` 必须返回
`ok=False` 且诊断码含 `C1`/`R2`。

A11 还额外要求**反向**成立：带 `assert` 的正常 TB 必须返回 `ok=True`。
一个只会说"坏"、不会说"好"的检查器同样不能用。

> 这个自审计不是"测试覆盖率"装饰。实际开发中我临时禁用过 `E051` 检查，
> A10 立刻 FAIL、整体降为 10/11；把 `scan_sv_silent_pass` 换成空函数，
> A11 立刻 FAIL、同样降为 10/11——证明它们确实在监视，不是摆设。

---

## 示例 10 — 安全边界：符号链接不越界

**目标**：`copy_sandbox` 声称"隔离复制"，必须真的隔离。

构造一个项目，里面有一个指向项目外的符号链接：

```bash
mkdir -p /tmp/ex10/proj/rtl /tmp/ex10/outside
echo "module ok; endmodule" > /tmp/ex10/proj/rtl/a.sv
echo "TOP_SECRET" > /tmp/ex10/outside/creds.txt
ln -s /tmp/ex10/outside/creds.txt /tmp/ex10/proj/rtl/link.sv
```

```bash
$ python3 -m fpga_toolbox copy_sandbox \
    --project-root /tmp/ex10/proj \
    --relative-files rtl/link.sv \
    --sandbox-dir /tmp/ex10/sb
ok: False
error: Refusing to stage 'rtl/link.sv': it resolves outside the project root
       ('/tmp/ex10/outside/creds.txt' is not within '/tmp/ex10/proj').
```

项目**内部**的相对符号链接（解析后仍在根内）正常放行：

```bash
ln -s ../y/real.sv /tmp/ex10/proj/x/alias.sv
$ python3 -m fpga_toolbox copy_sandbox --project-root /tmp/ex10/proj \
    --relative-files x/alias.sv --sandbox-dir /tmp/ex10/sb2
ok: True
```

> 旧实现只校验相对路径字符串不含 `..`，但 `is_file()` / `shutil.copy2()` 都会
> 跟随符号链接。实测能把项目外文件内容搬进沙箱并算进 SHA-256——声称隔离却泄漏。

---

## 示例 11 — 抓波形与完整日志

**目标**：仿真过了，但要留证据——完整日志（而不是被截断的尾部）和可以
用 GTKWave 打开的波形。

### 11.1 一条命令同时抓两者

```bash
python3 -m fpga_toolbox run_sim \
  --sim-script /tmp/ex2/run_apb_slave_apb.sh \
  --suite apb \
  --log-dir /tmp/ex11 \
  --waveform --require-waveform
```

```json
{
  "ok": true,
  "data": {
    "returncode": 0,
    "elapsed_sec": 7.871,
    "log": {
      "path": "/tmp/ex11/sim_apb.log",
      "size_bytes": 1619,
      "lines": 16,
      "truncated_in_response": false
    },
    "waveform": {
      "requested": true,
      "format": "vcd",
      "total_bytes": 2581,
      "files": [{"kind": "VCD", "size_bytes": 2581, "empty": false,
                 "path": "/tmp/ex11/wave_apb.vcd"}]
    }
  }
}
```

**日志**：`sim_apb.log` 是**完整** stdout+stderr。对比一下不带 `--log-dir`
时的返回：实测一份 27923 字符的日志，`stdout_tail` 只有 4000 字符——
开头被丢掉，第一条断言失败附近的行往往正好在被丢掉的那部分里。

**波形**：`wave_apb.vcd` 可直接 `gtkwave wave_apb.vcd` 打开：

```
$ head -6 /tmp/ex11/wave_apb.vcd
$version Generated by VerilatedVcd $end
$timescale 1ps $end
 $scope module tb_apb_slave_apb $end
  $var wire 1 2 pclk $end
  $var wire 1 " presetn $end
  $var wire 1 # psel $end
```

实测这份 VCD 有 **30 个信号声明、78 个时间点**，包含 DUT 与 TB 两层 scope。

### 11.2 生成 TB 时已内置波形支持

```bash
python3 -m fpga_toolbox emit_testbench \
  --rtl-file examples/multi_bus_ref/apb_slave.sv \
  --out-dir /tmp/ex11/tb --protocol apb
```

生成的 TB 里有：

```verilog
    // ---- waveform dump (optional; enable with +WAVE on the sim command line) ----
    if ($test$plusargs("WAVE")) begin
      if ($value$plusargs("WAVE_FILE=%s", wave_path)) begin
        $dumpfile(wave_path);
      end else begin
        $dumpfile("wave.vcd");
      end
      $dumpvars(0, tb_apb_slave_apb);
    end
```

以及模块级的 `string wave_path;` 声明。**这个声明是必需的**——少了它
Verilator 会直接报 `Can't find definition of variable: 'wave_path'` 拒绝编译
（实际编译一次才发现，纯看代码看不出来）。

生成的 sim 脚本响应三个环境变量：

```bash
$ bash /tmp/ex11/tb/run_apb_slave_apb.sh apb            # 不抓波形
$ WAVE=1 bash /tmp/ex11/tb/run_apb_slave_apb.sh apb     # 抓 wave_apb.vcd
$ WAVE=1 WAVE_FORMAT=fst bash .../run_apb_slave_apb.sh apb   # FST（需 lz4 头）
$ WAVE_DIR=/tmp/waves bash .../run_apb_slave_apb.sh apb # 指定落盘目录
```

脚本会检查 `obj_*/.wave_enabled` 标记：带 `--trace` 与不带是两套二进制，
开关变化时强制重建，避免复用旧二进制导致「设了 WAVE=1 却没有波形」。

### 11.3 两个真实陷阱（工具已经把它们变成显式错误）

**陷阱 A —— Verilator 静默忽略 `$dumpvars`**

`$dumpvars` 写在 TB 里只是必要条件。没有 `--trace` 时 Verilator 只打一条：

```
-Info: tb.sv:8: $dumpvar ignored, as Verilated without --trace
```

仿真照常 `PASS`、`exit 0`，**波形文件根本不存在**。只检查「代码里有
`$dumpvars`」或「日志里有 PASS」都会误判成功。所以 `--require-waveform`
对产物做了断言：

```
$ python3 -m fpga_toolbox run_sim --sim-script ... --suite apb \
    --waveform --require-waveform
ok: False
diagnostics: E024  waveform  No non-empty waveform file found.
error: waveform required but no waveform file was produced (searched [...]).
       Verilator silently ignores $dumpvars without --trace; make sure the sim
       script passes --trace/--trace-fst (run_sim sets WAVE=1).
```

**陷阱 B —— 旧波形冒充本次结果**

如果只判断「这个目录里有没有 `.vcd`」, 那么上一次运行留下的文件会让
「这次其实没抓到波形」也通过。工具的判定是与**运行前快照**比对
`(size, mtime)`，只认本次新增或变化的文件：

```
$ # 磁盘上已有上次的 wave_apb.vcd，但本次脚本没产生波形
ok: False
diagnostics: E024  waveform  No non-empty waveform file found.
waveform.files: []          ← 旧文件不计入
```

**陷阱 C —— FST 缺 lz4 头文件时产出假格式文件**

本机 Verilator 5.054 自建时只装了 `liblz4.so.1`（运行时），没有 `lz4.h`：

```
fatal error: lz4.h: No such file or directory
```

若此时 `obj_*` 里已有 VCD 二进制，脚本会复用并产出一个**后缀 `.fst`、
内容其实是 VCD** 的文件——只看扩展名完全看不出问题（实测文件头是
`$version Generated by VerilatedVcd`）。现在会用一次极小规模的编译探测，
把「不支持」变成显式错误：

```
$ python3 -m fpga_toolbox run_sim ... --waveform --waveform-format fst
ok: False
diagnostics: E025  waveform_format  verilator --trace-fst needs the lz4
             development headers (lz4.h) at build time.
             Install liblz4-dev / lz4-devel, or use waveform_format='vcd'.
```

### 11.4 自审计 A12

这三条护栏被纳入自审计，防止回退：

```
A12_WAVEFORM_LOG_ARTIFACT_GUARD
  contradictory ok=False codes=['E024']
  stale ok=False files=0 codes=['E024']
  log_saved=True
```

反向验证过：把 `run_sim` 里两道波形断言都换成 `if False`，A12 立刻 FAIL，
整体 12/12 → 11/12。

---

## 完整流水线串联

把前几个示例串成一条可执行的验证链：

```bash
set -e
PROJ=examples/axi_apb_ref

# 1) 理解仓库
python3 -m fpga_toolbox index_repo --project-root $PROJ > /tmp/idx.json
python3 -m fpga_toolbox resolve_deps --target-module axi_apb_bridge \
  --project-root $PROJ > /tmp/deps.json

# 2) 建沙箱
python3 -m fpga_toolbox copy_sandbox --project-root $PROJ \
  --relative-files axi/axi_apb_bridge.sv axi/apb_slave_regs.sv \
  --sandbox-dir /tmp/pipe_sb

# 3) 接口 → 契约 → 校验 → 测试台
python3 -m fpga_toolbox extract_interfaces \
  --rtl-file $PROJ/axi/axi_apb_bridge.sv \
  --emit-draft-yaml /tmp/pipe_contract.yaml
python3 -m fpga_toolbox validate_contract \
  --spec-path /tmp/pipe_contract.yaml --rtl-file $PROJ/axi/axi_apb_bridge.sv
python3 -m fpga_toolbox emit_testbench \
  --spec-path /tmp/pipe_contract.yaml --out-dir /tmp/pipe_tb \
  --rtl-file $PROJ/axi/axi_apb_bridge.sv

# 4) 跑仿真 + 抓日志/波形 + 归一化
python3 -m fpga_toolbox run_sim --sim-script $PROJ/sim.sh --suite all \
  --log-dir /tmp/pipe_out --waveform --require-waveform
python3 -m fpga_toolbox normalize_sim_result \
  --module-name axi_apb_bridge \
  --verilator-log-path /tmp/pipe_out/sim_all.log

# 5) Lint + 变异 + 自审计
python3 -m fpga_toolbox lint_tb --target-path $PROJ/axi/
python3 -m fpga_toolbox audit_harness
```

任一步 `ok=false` 就应该停下——这是"反静默通过"的实际用法：**每一环都要
明确表态，不能靠下游兜底**。

---

## 相关文档

- [`docs/FEATURES.md`](FEATURES.md) — 功能手册（13 工具详解 + 诊断码总表）
- [`docs/FEATURES.html`](FEATURES.html) — 功能手册 HTML 版（侧栏目录 + 页内搜索，可离线打开）
- [`docs/TEST_REPORT_AND_PLAN.md`](TEST_REPORT_AND_PLAN.md) — 全工具实测结果
- [`README.md`](../README.md) — 项目概览
- [`changelog/INDEX.md`](../changelog/INDEX.md) — 缺陷修复记录（每个都含现象/根因/修复/验证）

> 本示例集的 HTML 版：[`EXAMPLES.html`](EXAMPLES.html)（由本文件生成，勿直接编辑）
