# fpga_toolbox 功能手册

面向 AI Agent 与人类工程师的 FPGA/ASIC 验证工具箱。13 个原子工具，统一信封、
统一错误码、可直接从 MCP / CLI / Python 三处调用。

---

## 1. 它解决什么问题

FPGA 验证里最常见的失败不是"断言失败"，而是**看起来通过了**：

| 症状 | 真实原因 | 本工具箱的对策 |
|---|---|---|
| CI 全绿，上板挂 | 测试台只 `$display("DONE")`，没有任何判定 | `lint_tb` 的 S1/S2/S3 规则检出静默通过 |
| 报告写 "0 errors" | 仿真崩在第 1 条断言，日志残缺 | `normalize_sim_result` 校验产物完整性 |
| "覆盖率 100%" | 覆盖率数据来自旧二进制 | `run_sim` 逐套件身份核对 + 版本守卫 |
| "已清理网表" | 清理器没匹配到实例，原样输出 | `clean_pt_netlist` 产物自检（E051）|
| "依赖完整" | 例化了仓库外的模块，静默丢失 | `index_repo` 的 W102 + `closure_is_complete` |

工具箱的核心主张：**任何"通过"都必须能被反问"你凭什么说它通过了"**。
每个工具除了给出结果，还要给出让结果可被质疑的证据——诊断码、产物校验、
自审计项。

---

## 2. 架构

```
                    ┌─────────────────────────────┐
   MCP / CLI / Python ──▶│  call_tool(name, params)    │
                    │  ① 未知参数名校验            │
                    │  ② required 校验             │
                    │  ③ type 校验                 │
                    │  ④ enum 校验                 │
                    └──────────────┬──────────────┘
                                   ▼
                    ┌─────────────────────────────┐
                    │  13 个原子工具（纯函数）      │
                    └──────────────┬──────────────┘
                                   ▼
                    ┌─────────────────────────────┐
                    │  ToolResult 统一信封         │
                    │  {ok, tool, data,           │
                    │   diagnostics, error}       │
                    └─────────────────────────────┘
```

**统一信封**意味着调用方只需写一次错误处理，13 个工具全部适用：

```python
r = call_tool("index_repo", {"project_root": "."})
if not r["ok"]:
    print(r["error"])                       # 人类可读的一句话
    for d in r["diagnostics"]:              # 结构化诊断
        print(d["code"], d["path"], d["message"])
```

---

## 3. 工具分组

### 3.1 仓库理解（1–3）

#### `index_repo` — 仓库索引

解析 RTL 仓库，得到模块、例化关系、头文件归属。

```bash
python3 -m fpga_toolbox index_repo --project-root examples/multi_bus_ref
```

```json
{
  "ok": true,
  "data": {
    "total_rtl_files": 36,
    "modules": {
      "apb_slave": {
        "file_path": "apb_slave.sv",
        "instantiated_modules": [],
        "unresolved_instantiations": [],
        "includes": []
      }
    },
    "unresolved_instantiations": {}
  }
}
```

**关键能力**：`unresolved_instantiations` 与 `W102`。

当 RTL 例化了一个仓库里**没有定义**的模块（厂商 IP、加密 IP、尚未克隆的子模块），
本工具不会静默忽略：

```json
{
  "unresolved_instantiations": {"soc_top": ["vendor_ddr_phy", "secure_boot_rom"]},
  "diagnostics": [
    {"severity": "WARNING", "code": "W102", "path": "soc_top",
     "message": "Instantiation of module 'vendor_ddr_phy' has no definition in this repo; ..."}
  ]
}
```

> 为什么重要：这类模块在真实项目里极常见。静默丢弃会让依赖闭包"看起来完整"，
> 沙箱缺文件、综合缺模块，而所有中间步骤都报成功。

#### `resolve_deps` — 依赖闭包

BFS 计算目标模块的传递依赖。带环不死循环，并透传上游的未解析例化信息。

```bash
python3 -m fpga_toolbox resolve_deps --target-module axi_apb_bridge --project-root examples/axi_apb_ref
```

```json
{
  "ok": true,
  "data": {
    "dependency_closure": ["axi_apb_bridge", "apb_slave_regs"],
    "dependency_edges": [{"from": "axi_apb_bridge", "to": "apb_slave_regs"}],
    "rtl_files": ["axi/sv/axi_apb_bridge_sv.sv", "..."],
    "header_files": ["inc/defs.svh", "inc/base.svh"],
    "all_files": ["axi/sv/axi_apb_bridge_sv.sv", "...", "inc/defs.svh", "inc/base.svh"],
    "unresolved_instantiations": {},
    "closure_is_complete": true
  }
}
```

`closure_is_complete: false` 是给调用方的**单点信号**：这份文件清单不完整，
别直接拿去建沙箱。

**`header_files` 是传递闭包**：header 之间可以互相 `` `include ``（`defs.svh`
里 include `base.svh` 很常见），工具会深度优先展开到最底层，不会只给模块
直接 include 的那一层——否则拿 `all_files` 去复制沙箱会缺文件，编译报
`Cannot find include file`。

**`rtl_files` 只含真正的编译单元**：被 `` `include "defs.sv" `` 引进来的
`.sv` 文件不会出现在 `rtl_files`（否则其中定义的 module 会被编译两次，
Verilator 默认 warning-as-error 直接失败），但仍保留在 `all_files` 里供沙箱
复制。`index_repo` 的 `included_files` 字段列出这些文件。

#### `copy_sandbox` — 隔离沙箱

带 SHA-256 校验的复制，且拒绝越界。

```bash
python3 -m fpga_toolbox copy_sandbox \
  --project-root examples/axi_apb_ref \
  --relative-files axi/axi_apb_bridge.sv axi/apb_slave_regs.sv \
  --sandbox-dir /tmp/sandbox
```

安全边界（都有测试锁定）：

| 行为 | 结果 |
|---|---|
| 沙箱 = 项目根 | 拒绝 |
| 沙箱是项目根的祖先 | 拒绝 |
| 清理非空目录但无 `.fpga_sandbox` 标记 | 拒绝（除非 `force=true`）|
| 相对路径含 `..` 或绝对路径 | 拒绝 |
| **项目内符号链接指向项目外** | **拒绝**（解析真实路径后校验）|

最后一条是实测发现的：`rtl/link.sv -> /outside/creds.txt` 这样的符号链接
曾能把项目外文件内容搬进沙箱。项目**内部**的符号链接（解析后仍在根内）正常放行。

---

### 3.2 接口与契约（4–6）

#### `extract_interfaces` — 多协议接口识别

从 RTL 识别 11 种接口，支持内置解析器与可选 `slang` AST 后端。

```bash
python3 -m fpga_toolbox extract_interfaces --rtl-file examples/multi_bus_ref/pcie_tlp_ep.sv
```

```json
{
  "ok": true,
  "data": {
    "modules": [{
      "module_name": "pcie_tlp_ep",
      "clock": {"port": "clk", "freq_mhz": 100.0},
      "reset": {"port": "rst_n", "active_low": true, "sync": true},
      "interfaces": [{
        "protocol": "pcie",
        "role": "endpoint",
        "pcie_gen": 4,
        "pcie_lanes": 4,
        "port_map": {"tx_valid": "pcie_tx_tvalid", "...": "..."}
      }]
    }]
  }
}
```

支持的协议：`axi4_lite` · `axi4`（Full）· `axi4_stream` · `apb` · `ahb_lite` ·
`ddr3` · `ddr4` · `ddr5` · `hbm` · `pcie` · `spi` · `ethernet`，外加 SV
`interface`/`modport`。

时钟/复位识别支持常见命名：`clk`/`aclk`/`pclk`/`hclk`（含 `clk_extra`/`clk2`/
`clk_i` 等第二时钟）、`rst_n`/`aresetn`/`hresetn`/`presetn`（含 `rst_extra`/
`rst_async` 等第二复位）。

##### 标准接口 + 绑定层（`canonical_iface`）

识别**不再靠逐信号正则**。命名风格是开放集合（`psel` / `p_sel` / `psel_i` /
`s0_psel` / `s_apb_psel` / `PSel` …），为每种风格维护一条正则必然会漏（历史上
三种不同缺陷都源于此）。现在的两层结构：

1. **标准接口**（`fpga_toolbox/canonical_iface.py`）：

   ```python
   CanonSignal(name="pwdata", direction="in", width_role="data", required=True)
   CanonSignal(name="pstrb",  direction="in", width_role="strb", required=False)
   ```

   每个协议只声明**协议语义**（信号名、从设备视角方向、位宽含义、是否必需），
   不描述拼写。另有 `min_signals`、`role_probes`、`burst_probes`、
   `required_tokens`/`forbidden_tokens`（`ddr3_`/`hbm_`/`pcie_` 这类强制前缀，
   防止 HBM 与 AXI4 互抢）、`group_neutral_tokens`（PCIe 的 `rx`/`tx` 不拆组）。

2. **绑定层**：把任意端口名 token 化后，按 **token 后缀拼接**匹配 canonical 名：

   | DUT 端口 | token | 绑定 |
   |---|---|---|
   | `i_psel` | `[i, psel]` | `psel`（`i` 是方向词，剔除） |
   | `psel_i` | `[psel, i]` | `psel` |
   | `PSlvErr` | `[p, slv, err]` | `pslverr` |
   | `p_sel` / `p_enable` | `[p, sel]` | `psel` / `penable` |
   | `s_apb_psel` | `[s, apb, psel]` | `psel`，组 `s_apb` |
   | `mem_ddr3_dq_out` | `[mem, ddr3, dq, out]` | `dq`，组 `mem_ddr3` |
   | `pcie_rx_tvalid` | `[pcie, rx, tvalid]` | `rx_tvalid`，组 `pcie` |
   | `awlen_o` | `[awlen, o]` | `awlen` → 升级为 `axi4` |

   方向词（`i`/`o`/`in`/`out`）**永不进入通道前缀**，所以一个接口不会被拆成两半。
   多个端口绑到同一 canonical 时，`binding.ambiguous` 记录被拒绝者与原因
   （错误映射**可见**而非静默丢弃）。

返回体中的绑定证据：

```json
{
  "port_map": {"psel": "i_psel", "pready": "o_pready"},
  "split_port_map": {"dq": {"in": "mem_ddr3_dq_in", "out": "mem_ddr3_dq_out"}},
  "binding": {
    "matched": {"psel": "psel", "pready": "pready"},
    "ambiguous": [{"canonical": "paddr", "chosen": "paddr",
                   "rejected": ["p_addr"], "reason": "..."}]
  }
}
```

`split_port_map` 表达**双向信号被拆成 in/out 两半**的常见写法（DDR 控制器的
`ddr3_dq_in` / `ddr3_dq_out`）；只有单个 `inout` 端口时为空。

##### 扩展新协议

只需在 `CANONICAL_INTERFACES` 里声明一次，绑定顺序由
`bind_order()` 自动派生，**不需要**改 `classify_module` 或写任何正则：

```python
"mybus": CanonInterface(
    protocol="mybus",
    min_signals=3,
    role_probes=("req",),
    required_tokens=("mybus",),          # 端口名必须含该 token
    signals=(
        _sig("req",  "in"), _sig("gnt", "out"),
        _sig("addr", "in", "addr"), _sig("data", "inout", "data"),
    ),
),
```

若 `emit_testbench` 尚无该协议的模板，会明确报告
「detected but this emitter has no testbench template」，而不是误用其它协议的
模板生成一个错误的 TB。

#### `validate_contract` — 契约校验

校验 YAML/JSON 边界契约与寄存器位域规则。

```bash
python3 -m fpga_toolbox validate_contract --spec-path /tmp/bad_spec.yaml
```

```json
{
  "ok": false,
  "error": "Semantic validation failed.",
  "diagnostics": [
    {"severity": "ERROR", "code": "E001",
     "path": "boundary.axi4_lite.port_map.awvalid",
     "message": "Missing required 'awvalid'."}
  ]
}
```

诊断码（校验类）：

| 码 | 含义 |
|---|---|
| `E000` | 缺 `boundary` / `test_plan` 段 |
| `E001` | 必需端口未映射 |
| `E002` | `data_width` 不是 32/64 |
| `E011` | 契约里的端口在 RTL 中不存在 |
| `E012` | 端口方向与预期不符 |
| `E013` | 位域重叠 |
| `E014` | 位域超宽 |
| `E015` | 位域 `bits` 顺序写反（`hi<lo`）|
| `E016` | `module_name` 不是合法 SV 标识符（会拼进产物文件名与 Makefile，故拒绝路径穿越/命令注入）|

`E015` 值得一提：`bits: "7:0"` 写成 `"0:7"` 时，旧实现报 `E014 越界`，
误导用户去改宽度。现在专门报"顺序反了"。

`E016` 值得一提：`module_name` 不只是名字，它被拼进每个产物文件名
（`<mod>_axil_wrap.sv`、`test_<mod>_axil.py`）和生成 Makefile 的
`MODULE`/`TOPLEVEL`。`../../EVIL` 会把产物写到 `out_dir` 之外，
`m$(shell ...)` 会让用户执行 `make` 时执行任意命令 —— 两者实测可复现。
现在按 SV 标识符 `[A-Za-z_][A-Za-z0-9_$]*` 收口（`$` 在 SV 里合法，不误伤）。

#### `emit_testbench` — 两条路径

**路径 A：契约驱动**（生成 AXI4-Lite wrapper + SVA + bind + cocotb）

```bash
python3 -m fpga_toolbox emit_testbench \
  --spec-path contract.yaml --out-dir /tmp/tb --rtl-file dut.sv
```

产出 4 个工件：`unit_isolation_wrapper` / `sva_protocol_checker` /
`in_situ_bind_file` / `cocotb_testbench`，外加一个**可直接 `make` 的 `Makefile`**
（RTL 会一并复制进 out_dir，产物自包含）。若 DUT 是双侧桥（AXI4-Lite + APB master），
额外生成 `apb_slave_model` 并报 `dual_side: true`。

```bash
python3 -m fpga_toolbox emit_testbench \
  --spec-path contract.yaml --out-dir /tmp/tb --rtl-file dut.sv
cd /tmp/tb
make SIM=verilator VERILATOR_TRACE=1        # VERILATOR_TRACE=1 才产出波形
```

> **跑路径 A 的 cocotb TB** 需要额外依赖（`pip install cocotb cocotbext-axi`），
> 且 cocotb 2.x 的 Makefile 有 4 个反直觉的值（实测踩过，Verilator 5.054 +
> cocotb 2.1.0）—— 生成器已经把这几个坑都填好了，下面是它们各自的由来：
>
> ```makefile
> TOPLEVEL_LANG = verilog     # 不是 "verilator"！否则静默跳过仿真（仍报 TESTS=1/rc=0）
> SIM = verilator             # 默认 simulator 是 icarus，必须显式指定
> MODULE = test_reg_slave_axil
> TOPLEVEL = reg_slave_axil_wrap
> VERILOG_SOURCES = reg_slave_axil_wrap.sv reg_slave_axil_sva_checker.sv \
>                   reg_slave_axil_bind.sv dut.sv
> COCOTB_PLUSARGS = +WAVE     # 不加则 --trace 编了也拿不到波形
> include $(shell cocotb-config --makefiles)/Makefile.sim
> ```
>
> - `cocotb-config` 在 cocotb 2.x 里属于 `cocotb_tools`，不在 `cocotb`；
> - cocotb 要求 Verilator ≥ 5.036，系统自带 5.020 会被 `$(error)` 拦住，
>   需 `VERILATOR_BIN_DIR=/path/to/verilator/bin` 指向自建 5.040+；
> - 波形要用 `VERILATOR_TRACE=1`（**不是** `VERILATOR_EXTRA_ARGS=--trace`，
>   后者传给 verilator 的 `--trace` 会被 cocotb 的参数拼装吞掉，实测无波形）。
>   生成的 Makefile 已内置 `COCOTB_PLUSARGS = +WAVE`，所以
>   `make SIM=verilator VERILATOR_TRACE=1` 一条命令即可拿到 145 信号波形。

**路径 B：多协议直出**（从 RTL 直接生成自检 SV TB）

```bash
python3 -m fpga_toolbox emit_testbench \
  --rtl-file examples/multi_bus_ref/apb_slave.sv \
  --out-dir /tmp/tb_apb --protocol apb
```

```json
{"ok": true, "data": {"protocol": "apb", "suites": ["apb"],
 "artifacts": {"sv_testbench": "...", "sim_script": "..."}}}
```

11 种协议可用：`apb` / `axi4` / `axi4_stream` / `ahb_lite` / `ddr3` / `ddr4` /
`ddr5` / `hbm` / `pcie` / `spi` / `ethernet`（PCIe 自动展开
Gen3·4·5 × x1·x4·x16 共 9 套件）。

`axi4_stream` 是 sink（从端）模板：AXI4-Stream 没有地址、没有读通道，唯一的
流控是 TREADY/TVALID 握手，所以自检围绕「握手是否真的完成」而不是地址/数据
比对 —— 逐 beat 等 TREADY（1000 拍超时即报出卡在第几拍）、beat 计数必须精确
相等、TVALID 期间 payload 必须稳定、TKEEP/TLAST 包边界。可选的
`TLAST`/`TKEEP`/`TID`/`TDEST`/`TUSER` 按 DUT 是否真的暴露来决定是否生成，
所以只有 `TDATA`/`TVALID`/`TREADY` 三个信号的最小实现同样能跑。

`ddr5` / `spi` / `ethernet` 三个模板同样各自独立（都不复用总线时序）：

| 协议 | TB 扮演 | 自检内容 |
|---|---|---|
| `ddr5` | DRAM 器件 | CA 总线命令拍、CS_n/ACT_n 互斥、**差分 CK 必须互补**、读命令后必须出现 `rd_valid` |
| `spi` | SPI 从设备（flash/sensor） | CS_n 成帧、**每帧恰好 8 个 SCLK 沿**、空闲时钟静止（带 2 拍回落窗口）、全双工回环 |
| `ethernet` | 链路对端（peer MAC/PHY） | TX 帧长 **>= 60 字节**（最小帧硬约束）、`tx_en` 流式成帧、数据无 X |

三者的故障注入都已验证会 FAIL 而不是假装通过（短帧、帧截断、TREADY 方向写反）。

生成的 TB 自带 Verilator 运行脚本，跑起来是这样的：

```bash
$ bash /tmp/tb_apb/run_apb_slave_apb.sh
== sim: apb (top=tb_apb_slave_apb) ==
[CHECK] id=APB_001 | proto=APB | cat=reset_read | tx=RD addr=0x0000 | exp=0x12345678 | got=0x12345678 | status=PASS
[CHECK] id=APB_002 | proto=APB | cat=pstrb_write | tx=WR addr=0x0000 wdata=0xaa00bb00 strb=0xa | status=PASS
...
PASS: apb all 8 checks passed
```

**生成前的三道闸门**（每个都是实测缺陷的产物）：

1. **端口存在性** — DUT 缺模板要连的端口时 fail-fast（否则生成 `.pstrb(pstrb)`
   连到不存在的端口，Verilator 报 `PINNOTFOUND`）；
2. **端口方向** — DUT 把 `pready` 写成 `input` 时 fail-fast。这个比编译失败更危险：
   方向错**不会**编译失败，但 TB 的 `while (!pready)` 会**永久挂起**，CI 表现为超时；
3. **端口位宽** — 窄总线上测试向量按 `dw` 缩放，避免 `8'h12345678` 被静默截断。

---

### 3.3 结果归一化与可视化（7–8）

#### `normalize_sim_result` — 三源合一

把 cocotb XML/JSON、SVA 日志、Verilator 多套件日志归一化成统一报告。

```bash
python3 -m fpga_toolbox normalize_sim_result \
  --module-name apb_slave --verilator-log-path /tmp/sim.log --expected-suites apb dual
```

```json
{
  "ok": true,
  "data": {
    "overall_status": "PASS",
    "checks": [{"rule_id": "APB_001", "category": "reset_read", "status": "PASS", "detail": "..."}],
    "summary": {"total": 8, "passed": 8, "failed": 0, "skipped": 0, "pass_rate": 1.0}
  }
}
```

**三个输入源都有完整性信号**——这是本工具最重要的设计：

| 输入源 | 完整性信号 | 缺信号时的行为 |
|---|---|---|
| cocotb JSON | `completed: true` | `completed=false` → `COCOTB_INCOMPLETE` FAIL |
| SVA 日志 | `[SVA:HBEAT]` 心跳 | 有心跳无违例 → PASS；无心跳无违例 → NO_RESULTS |
| Verilator 日志 | `PASS:`/`FAIL:` 汇总行计数 | 启动 N 套件需 ≥N 条汇总，不足 → `VERILATOR_NO_SUMMARY` FAIL |

> 为什么必须有这层：仿真日志天然容易被截断（被杀、超时、崩溃）。只统计
> "我看到了什么"而不判断"我是否看完"，就会把残缺产物当成完整结论。
>
> SVA 的特殊之处：断言通过时**零输出**，所以加了周期性心跳
> `[SVA:HBEAT] rule=P_000 monitored_cycles=100 status=ACTIVE`，
> 让"全通过"与"引擎根本没跑"可区分。

#### `render_visual_report` — SVG + HTML 看板

```bash
python3 -m fpga_toolbox render_visual_report \
  --report-json /tmp/report.json --dep-graph-json /tmp/graph.json \
  --ifaces-json /tmp/ifaces.json --out-html-path /tmp/report.html
```

产出 SVG 依赖拓扑 + HTML 规则审计看板，内置 HTML 标签平衡与行数一致性自检
（`consistency_ok`）。看板包含 SV 语法高亮、方向/位宽徽章、mutation 矩阵。

---

### 3.4 反踩坑与执行（9–11）

#### `lint_tb` — 静态反踩坑

```bash
python3 -m fpga_toolbox lint_tb --target-path /tmp/bad_tb.sv
```

```json
{
  "ok": false,
  "data": {"error_count": 2, "warning_count": 0,
    "findings": [
      {"severity": "ERROR", "code": "C1",
       "message": "Blocking interface drive immediately after @(posedge) ..."}
    ]}
}
```

三组规则（S 组对 `.sh`/`.py` 与 `.sv` 都生效）：

| 组 | 码 | 抓什么 |
|---|---|---|
| 时钟竞争 | `C1` `C2` | `@(posedge)` 后立即用阻塞赋值驱动 interface 信号 |
| 握手死锁 | `R1`–`R4` | valid 撤回过早、ready 无限等待、响应超时 |
| 静默通过 | `S1` `S2` `S3` `S4` | 常量返回的 stub 函数、SKIP 后 exit 0、只数 PASS 行数、**SV 测试台只 `$display` 无任何判定** |

`S4` 是 SV 测试台的静默通过检测：文件里有 `$display` 却没有任何
`assert` / `$error` / `$fatal` / 自研 `check_*` 任务，说明它只在汇报、
从不表达失败——这样的仿真必然"通过"。判定前会剥掉注释，并对带真实判定的
TB 保持零误报（`examples/multi_bus_ref` 的 18 个 TB 全部干净）。

`C1`/`C2` 覆盖常见接口实例命名（`*_if` / `*_bus` / `axi` / `apb` / `s_axi` /
`m_axi` / `u_axi`），且只在 `@(posedge)` 上下文触发以控制误报。

#### `run_sim` — 仿真封装（含波形与完整日志）

```bash
python3 -m fpga_toolbox run_sim \
  --sim-script examples/multi_bus_ref/sim.sh --suite apb \
  --log-dir /tmp/out --waveform --require-waveform
```

```json
{
  "ok": true,
  "data": {
    "returncode": 0,
    "elapsed_sec": 7.871,
    "log": {
      "path": "/tmp/out/sim_apb.log",
      "size_bytes": 1619,
      "lines": 16,
      "truncated_in_response": false
    },
    "waveform": {
      "requested": true,
      "format": "vcd",
      "total_bytes": 2581,
      "files": [{"kind": "VCD", "size_bytes": 2581, "empty": false, "path": ".../wave_apb.vcd"}]
    }
  }
}
```

**抓 log**：`log_dir` 把**完整** stdout+stderr 落盘为 `sim_<suite>.log`。
不传时返回值只有 4000/2000 字符的尾部——长日志的开头会被丢弃，事后无法
回溯第一条断言失败附近的上下文（实测 27923 字符的日志，返回值里只有 4000）。

**抓波形**：`waveform=true` 会置 `WAVE=1`，让生成的 sim 脚本给 verilator 加
`--trace`（`waveform_format=fst` 时用 `--trace-fst`）。TB 里的
`$dumpfile`/`$dumpvars` 由 `+WAVE` plusarg 控制，产物落在脚本目录或 `log_dir`。

波形与日志的护栏：

| 码 | 触发条件 | 为什么需要 |
|---|---|---|
| `E020` | Verilator 版本 < 5.040 | 旧版本跨文件 virtual interface 有缺陷 |
| `E021` | `require_slang=true` 但 slang 缺失 | 显式要求就要显式失败 |
| `E022` | 仿真退出码非 0 | — |
| `E023` | 套件静默跳过 | 逐套件身份核对 |
| `E024` | 要求波形但没产出非空波形 / `require_waveform` 与 `waveform` 矛盾 | **Verilator 缺 `--trace` 时静默忽略 `$dumpvars`**，仿真 PASS 但波形不存在 |
| `E025` | `waveform_format=fst` 但环境缺 lz4 开发头文件 | 否则会产出「后缀 `.fst`、内容是 VCD」的假文件 |

> `require_waveform` 的判定不只看文件是否存在：它会与**运行前的波形快照**
> 比对 `(size, mtime)`，只认本次新增或变化的文件。否则上一次运行遗留的
> `.vcd` 会被当成本次证据，让「其实没抓到」也能通过。

#### `run_mutation` — 变异测试

```bash
python3 -m fpga_toolbox run_mutation \
  --workdir /tmp/mut --mutation-spec /tmp/muts.json --test-cmd "python3 -m pytest -q"
```

在 `try/finally` 里保证原文件还原，并拦截残留变异（`E030`）。

```json
{"ok": true, "data": {"mutation_score": 100.0, "killed": 4, "survived": 0}}
```

---

### 3.5 工程辅助（12–13）

#### `clean_pt_netlist` — PrimeTime 网表清理

DC 导出的网表里常有未解析的参数化黑盒（`RDBUS`/`CLKCROSSBUS`/`CLKCROSSDFF`），
导致 PrimeTime `link_design` 失败。

```bash
python3 -m fpga_toolbox clean_pt_netlist --in-verilog nl.v --out-verilog nl_clean.v
```

```json
{"ok": true, "data": {"removed_instances": 2, "tied_off_nets": ["dout[15:0]", "dout[16]"]}}
```

输出示例：

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

**产物自检**：清理后若输出里仍有黑盒实例（实例语法没被匹配到，如缺分号），
返回 `E051` 而不是静默报成功。

#### `audit_harness` — 故障注入自审计

给护栏本身做故障注入，证明它们出错时不会静默通过。

```bash
python3 -m fpga_toolbox audit_harness
```

```json
{
  "ok": true,
  "data": {
    "total": 19, "passed": 19, "failed": 0,
    "checks": [
      {"id": "A1_LINT_FAULT_CATCH", "passed": true, "detail": "ok=False codes=['C1', 'R2']"},
      {"id": "A2_MUTATION_RESIDUAL_GUARD", "passed": true, "detail": "ok=False codes=['E030']"},
      ...
      {"id": "A10_PT_NETLIST_RESIDUAL_GUARD", "passed": true, "detail": "ok=False codes=['E051']"}
    ]
  }
}
```

| 项 | 验证什么 |
|---|---|
| A1 | `lint_tb` 能抓到时钟竞争与握手缺陷 |
| A2 | `run_mutation` 拒绝残留变异（E030）|
| A3 | `normalize_sim_result` 拒绝垃圾 JSON，不静默 PASS |
| A4 | `copy_sandbox` 拒绝以项目根为沙箱 |
| A5/A6 | 套件身份核对能解析真实日志、套件齐时不假报警 |
| A7 | Verilator 版本守卫（E020）生效 |
| A8 | `emit_testbench` 缺 boundary 时 fail-fast |
| A9 | slang 回退对调用方可见（W013）|
| A10 | `clean_pt_netlist` 清理残留时报 E051 |
| A11 | SV 静默通过（S4）能检出，有真实判定时不误报 |
| A12 | `run_sim` 波形/日志产物守卫（E024、陈旧波形不冒充本次产物）|
| A13 | 契约路径 TB 的波形开关链路完整（$dumpfile + plusarg + 变量声明 + 无死 `ifdef`）|
| A14 | 生成的 shell 脚本语法 + `set -u` 运行期无未声明变量 |
| A15 | `run_mutation` 有 baseline 预检（坏 test_cmd 不能报假 100%）|
| A16 | 套件打印 PASS 后崩溃不算通过 |
| A17 | 审计报告 `summary`/`overall_status` 与 `checks` 矛盾必须可见（W052/W053）|
| A18 | 畸形 results（非对象元素、无名 testcase）必须报错/判 FAIL，不静默 NO_RESULTS |
| A19 | `REQUIRED_TB_PORTS` 覆盖各协议模板**实际硬连**的每个端口（防模板加连线而漏同步表格 → PINNOTFOUND 却报 ok）|

---

## 4. 诊断码总表

### 校验类（`validate_contract`）

| 码 | 含义 |
|---|---|
| `E000` | 缺必需段 |
| `E001` | 必需端口未映射 |
| `E002` | 数据宽度非法（非 32/64）|
| `E010` | `out_of_scope_ports` 的输入端口缺 `tie_off` |
| `E011` | 契约端口在 RTL 中不存在 |
| `E012` | 端口方向不符 |
| `E013` | 位域重叠 |
| `E014` | 位域超宽 |
| `E015` | 位域 `bits` 顺序写反（`hi<lo`）|

### 运行时类

| 码 | 工具 | 含义 |
|---|---|---|
| `E020` | `run_sim` | Verilator 版本 < 5.040 |
| `E021` | `run_sim` | 要求 slang 但缺失 |
| `E022` | `run_sim` | 仿真退出码非 0 |
| `E023` | `run_sim` | 套件静默跳过 |
| `E024` | `run_sim` | 要求波形却无有效产物（缺 `--trace` 时 verilator 静默忽略 `$dumpvars`）；或 `require_waveform` 与 `waveform` 矛盾 |
| `E025` | `run_sim` | `waveform_format=fst` 但环境缺 lz4 开发头文件，无法构建 `--trace-fst` |
| `E030` | `run_mutation` | 残留变异未还原 |
| `E031` | `run_mutation` | 变异得分低于门槛 |
| `E040` | `audit_harness` | 自审计护栏失效 |
| `E051` | `clean_pt_netlist` | 清理后仍残留黑盒实例 |

### 警告类

| 码 | 工具 | 含义 |
|---|---|---|
| `W001` | `validate_contract` | 可选 axi4_lite 信号未映射 |
| `W010` | `extract_interfaces` | 未检测到时钟，用默认值 |
| `W011` | `extract_interfaces` | 未检测到复位，用默认值 |
| `W012` | `extract_interfaces` | builtin 解析到 SV interface 端口，建议装 slang |
| `W013` | `extract_interfaces` | slang 回退对调用方可见 |
| `W014` | `run_sim` | `waveform=true` 却没产出波形文件（脚本可能不响应 `WAVE=1` / 缺 `--trace`）；`require_waveform=true` 会把它升级成 `E024` 硬错误 |
| `W050` | `render_visual_report` | HTML 标签不平衡 |
| `W051` | `render_visual_report` | 规则行数不一致 |
| `W052` | `render_visual_report` | `summary.total/passed/failed` 与 `checks` 实际统计不符（报告会印出自相矛盾的结论）|
| `W053` | `render_visual_report` | `overall_status` 与行级结论矛盾（PASS 却无一行通过 / FAIL 却无一行失败）|
| `W054` | `emit_testbench` | 输出端口位宽无法解析（typedef/interface 类型），wrapper 未连接该观察端口 |
| `W055` | `lint_tb` | 目录模式下没有匹配到任何候选文件（`tb_` 前缀 / `vip` / `driver`），"没检查"≠"检查通过" |
| `W056` | `emit_testbench` | DUT 还有其它总线接口未被该 TB 驱动/检查，其端口保持悬空 |
| `W057` | `emit_testbench` | 同一 RTL 文件里还有别的模块带相同协议接口，但 TB 只针对选中的那个 |
| `W058` | `index_repo` | 同一 module 在多个文件中定义（旧版本/备份残留），一起编译会 `Duplicate declaration` |
| `W101` | `copy_sandbox` | 被 include 的文件未进沙箱 |
| `W102` | `index_repo` | 例化了仓库内无定义的模块 |

### `eda_campaign` 专用（Synopsys 论文复跑）

| 码 | 含义 |
|---|---|
| `E046` | DC `UID-423` / 假成功报告（`<700B` 且仅 `No paths.`）|
| `E047` | `set_max_fanout` 施加在 net 对象报 `UID-119` |
| `E048` | K-2015.06 调用 `write_db` 报 `UID-530` |
| `E049` | `Received Signal 15` 或磁盘满 |
| `E050` | 旧 `simv` / 截断日志 |

---

## 5. 三种调用方式

### Python

```python
from fpga_toolbox import call_tool, list_tool_schemas

r = call_tool("extract_interfaces", {"rtl_file": "dut.sv"})
if r["ok"]:
    print(r["data"]["modules"][0]["interfaces"])
```

### CLI

```bash
python3 -m fpga_toolbox list                          # 列出所有工具
python3 -m fpga_toolbox schemas                       # 导出 MCP JSON Schema
python3 -m fpga_toolbox extract_interfaces --rtl-file dut.sv
python3 -m fpga_toolbox @params.json                  # 从 JSON 文件传参
```

### MCP（stdio 服务 + 客户端接入）

内置零依赖 MCP server，不需要安装任何 MCP SDK：

```bash
python3 -m fpga_toolbox serve-mcp          # 在 stdio 上服务
```

Claude Desktop / Cursor 的 `mcpServers` 配置：

```json
{
  "mcpServers": {
    "fpga-toolbox": {
      "command": "python3",
      "args": ["-m", "fpga_toolbox", "serve-mcp"]
    }
  }
}
```

协议能力：

| 方法 | 说明 |
|---|---|
| `initialize` | 版本协商：`2025-06-18` / `2025-03-26` / `2024-11-05` / `2024-10-07`，未知版本回落最新 |
| `tools/list` / `tools/call` | 17 个工具；响应同时含 `content[0].text` 与 **`structuredContent`** |
| `resources/list` / `resources/read` | 只读上下文：诊断码速查表、11 类协议规范、工具清单 |
| `prompts/list` / `prompts/get` | `rtl-to-mutation-audit` / `debug-failing-simulation` / `analyze-eda-campaign` |

`structuredContent` 与 `content[0].text` 内容**完全一致**：强类型客户端直接读前者
（不用再解析文本里的 JSON），只认文本的客户端读后者。两个出口都填，才不会让其中
一半客户端拿到空结构。

resources 的内容来自仓库内的**唯一事实源**（`docs/FEATURES.md` 的诊断码表、
`canonical_iface.py` 的协议声明），不额外维护副本，因此不会与代码漂移。

三条硬约定（都是实现时踩过的坑）：

1. **stdout 只能有协议消息** —— 工具执行期间 `sys.stdout` 临时重定向到 stderr。
   否则工具里任何 `print` 都会污染协议流，客户端只表现为「工具超时」。
2. **工具失败 ≠ 协议失败** —— `ok: false` 返回正常响应 + `isError: true`，
   完整 `ToolResult` 在 `content` / `structuredContent` 里。走 JSON-RPC error
   会被客户端当成传输故障并重试。
3. **一条坏消息不能杀死 server** —— `-32700` / `-32601` / `-32602` 之后继续服务。

---

## 6. 边界与已知限制

诚实记录，避免误用：

- **`emit_testbench` 契约路径**只生成 AXI4-Lite wrapper。纯 APB 设备会明确拒绝
  （`Spec has no AXI4-Lite boundary`），不会生成坏产物。
- **多协议 TB 是定宽模型**：`ddr4` 模板固定 64 位数据总线（地址高 3 位编码命令，
  与位宽强耦合）；`ddr3` 的地址/数据位宽可随接口推断。DUT 位宽严重不符时会
  被端口校验拦下，但不会自动适配全部协议变体。
- **`eda_campaign`** 是库模块（不注册为工具），面向 Synopsys VCS/DC/PT 的论文
  复跑流程，需要远端环境，本地只做日志解析与网表处理。
- **`copy_sandbox` 不解析 include 依赖**，只复制调用方显式列出的文件；
  缺依赖时通过 `include_warnings`（`W101`）提醒，不自动补全。
- **Verilator < 5.040** 的跨文件 virtual interface 有缺陷，`run_sim` 会拒绝。

---

## 7. 相关文档

- [`docs/EXAMPLES.md`](EXAMPLES.md) — 端到端可运行示例（含真实输出）
- [`docs/EXAMPLES.html`](EXAMPLES.html) — 示例集 HTML 版（侧栏目录 + 页内搜索，可离线打开）
- [`docs/TEST_REPORT_AND_PLAN.md`](TEST_REPORT_AND_PLAN.md) — 全工具实测结果
- [`docs/eda-tutorials/`](eda-tutorials/) — Synopsys 工具手册与论文复跑指南
- [`changelog/INDEX.md`](../changelog/INDEX.md) — 缺陷修复记录（每篇含现象 / 根因 / 修复 / 验证）
- [`README.md`](../README.md) — 项目概览与快速开始

> 本手册的 HTML 版：[`FEATURES.html`](FEATURES.html)（由本文件生成，勿直接编辑）
