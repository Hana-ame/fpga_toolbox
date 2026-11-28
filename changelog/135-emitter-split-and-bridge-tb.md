# 重构 + 功能增强 · 拆分 `protocol_tb_emitter.py` 并支持桥接 DUT 联合 TB

## 1 · 拆分 2,428 行单文件（v0.7.0 架构主线）

`protocol_tb_emitter.py` 是全仓最大单文件。11 个协议模板彼此独立、只共享一组
常量与端口解析 helper，却堆在一个文件里 —— 任何一处改动都要在 2,400 行里定位，
新增协议（ddr5/spi/ethernet 就是这样加进来的）让体积单调增长。

**拆分到 `fpga_toolbox/emitters/` 子包**：

| 文件 | 行数 | 内容 |
|---|---|---|
| `common.py` | 305 | 常量表（`SUPPORTED_MULTI_PROTOCOLS`/`REQUIRED_TB_PORTS`/`EXPECTED_TB_DIR`）+ 端口解析 helper（`_pm`/`_pw`/`_split_pm`/`_wrong_dir_ports`/`_missing_tb_ports`） |
| `apb.py` | 123 | APB3/4 从机 |
| `axi4.py` | 160 | AXI4-Full 突发从机 |
| `ahb_lite.py` | 118 | AHB-Lite 流水线从机 |
| `axi4_stream.py` | 207 | AXI4-Stream sink |
| `ddr.py` | 360 | DDR3 / DDR4 / DDR5（时序模型相近，共用 helper） |
| `hbm.py` | 128 | HBM 伪通道 |
| `pcie.py` | 262 | PCIe TLP 端点（Gen3/4/5 × x1/x4/x16 矩阵） |
| `spi.py` | 194 | SPI 从设备 |
| `ethernet.py` | 165 | 以太网 MAC |
| `orchestrator.py` | 694 | 编排：模块/接口选择、生成前三道闸门、后处理（复位极性/波形/`.*` 展开）、落盘 + 运行脚本 |
| `bridge.py` | 621 | **新增**：双接口桥接联合 TB（见下） |

**向后兼容**：`protocol_tb_emitter.py` 保留为门面（75 行），re-export 全部公开
符号与既有私有 helper（`_emit_run_script` / `_emit_*_tb` / `_pm` 等）。
测试与 `audit_harness` 的 `import ... as _pte` 形式无需改动。

**一个教训**：门面的每个导入都必须列进 `__all__`。否则 ruff 把「导入了但本文件
没用」的兼容导出当成 F401 死代码删掉，导致 `cannot import name '_emit_run_script'`
—— 实测 `ruff --fix` 就发生过一次。已在门面 docstring 里注明原因。

## 2 · 桥接 DUT 联合 TB（v0.7.0 功能主线）

**缺口**：`emit_protocol_testbench()` 命中第一个支持协议后即 break，其余总线端口
只能 tie-off。对**桥接 DUT**（AXI4→APB、AXI4→AXI4）这等于什么都没验证 —— 桥接的
全部价值就在于「上游事务被正确转换成下游事务」，只驱动一侧永远发现不了转换错误。

**实现** `emitters/bridge.py`：

- **桥接识别按 role 组合**（`classify_bridge`）：桥接 DUT 必然有一个 `slave` 侧
  + 一个 `master` 侧。role 由分类器按信号方向推断，比端口名前缀可靠
  （`s_axi_*` / `m_apb_*` 只是惯例）。
- **双边联合 TB**：上游侧（DUT 是 slave）TB 充当 master 发事务；下游侧（DUT 是
  master）TB 充当 slave 收事务并应答，然后校验**跨总线一致性**。
- 首批支持：`axi4→apb`（位宽转换桥）、`axi4→axi4`（位宽转换/重排器），以及
  `spi→apb` / `ethernet→apb` / `axi4_stream→apb` 的识别与如实拒绝（见下）。

**设计取舍**：不复用单协议模板的 `initial` 块。单协议模板自己发起固定序列并
`$finish`，两份模板并排放会变成两个互不相关的仿真，DUT 的桥接逻辑根本没被激励。

## 3 · 首次实现踩到的真实问题（全部修复并固化）

1. **AXI4 下游网线宽度退化**：桥接 TB 漏声明 `arsize`/`arburst`/`awsize`/
   `awburst`，它们被 Verilator 隐式建成 1 位，与 DUT 的 3/2 位端口不匹配 →
   WIDTHTRUNC/WIDTHEXPAND，默认警告即错误，构建直接失败。修复：宽度字典覆盖
   模板里的**每一条**网线，并加测试逐条断言位宽非退化。

2. **APB 采样相位错误**：在 `PSEL` 刚拉高的 setup 相位采样 `pwdata`，得到恒 0，
   会误报成「桥接没有搬数据」，让人去改**正确的 RTL**。APB 协议里只有 access
   相位（PSEL && PENABLE 同时高）paddr/pwdata 才稳定。修复为 access 相位采样。

3. **APB 访问重复计数**：不看 `pready` 就处理，会把同一次传输统计两次
   （`dn_writes` 翻倍），让「桥接拆成几次下游访问」这条断言失去意义。

4. **`while (!(s_axi_bvalid && m_axi_bready))` 被误判 R3**：`tb_linter` 的
   READY 映射只认裸名 `BVALID`，而 `s_axi_bvalid` 是单个标识符里的下划线前缀，
   `split(".")` 归一后仍是 `S_AXI_BVALID`，查不到 → 明明同时查了 READY 仍判 R3。
   修复 `_ready_of()`：剥掉 `s_axi_` / `m_axi_` / `s_axis_` 前缀后再查表。

5. **f-string 四层花括号转义**：SV 拼接 `{{{n{1'b0}}}}, 32'hX` 在 f-string 里
   需要四层转义，实测一次写错就编译失败。改为**预计算字面量变量**，模板里只留
   `{beat0_lit}`。

6. **测试向量只覆盖一半位**：`0xC0FFEE00` 高 32 位为 0，数据透传断言退化成只
   验证了一半位。改用 0xA5/0x5A 交替字节填充，每个 8 位边界都有跳变。

7. **`top_module` 与实际 module 名不一致**：桥接 TB 的 module 是
   `tb_<mod>_bridge`，而编排层用 `tb_<mod>_<proto>` 拼 `--top-module`，
   Verilator 直接报 "not found in design"。修复：`top` 直接取 `tb_abs.stem`。

## 4 · 覆盖

**两个 examples 桥接 DUT 现可由工具自动生成 TB 并 Verilator 实跑通过**：

| DUT | 桥接组合 | 结果 |
|---|---|---|
| `complex_datapath/axi4_64_to_apb_32_bridge.sv` | axi4(64) → apb(32) | `PASS: bridge axi4->apb ... split into 4 downstream accesses`，rc=0 |
| `complex_datapath/axi4_ooo_reorder_fabric.sv` | axi4 → axi4 | `PASS: bridge axi4->axi4 ... data intact`，rc=0 |

两者均已接入 `examples/multi_bus_ref/complex_datapath/sim_complex.sh`。

## 验证

| 项目 | 结果 |
|---|---|
| 单文件复杂度 | 2,428 行 → 最大 694 行（编排层），门面 75 行 |
| 门禁（完整 EDA） | **6/6**，Layer 4 `207/207` 函数 / 37 模块 |
| lint_tb（Layer 6） | 22/22 DUT、5/5 TB 目录、0 错误 0 警告 |
| 测试套件 | 12 个原有套件 + 新增 `tests/test_bridge_coverage.py`（6 用例）全通过 |
| 桥接识别负例 | 单协议 DUT（只有 slave 或只有 master）不被判成桥接 |
