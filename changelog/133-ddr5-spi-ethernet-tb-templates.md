# 功能增强 · 补齐 ddr5 / spi / ethernet 原生 TB 模板（8 → 11 协议）

`canonical_iface.py` 一直支持 12 类总线协议，但 `protocol_tb_emitter.py` 只有
8 种模板，`ddr5` / `spi` / `ethernet` 停留在「分类器能认、emitter 报 no
template」。本轮补齐，**每个都是真实 Verilator 跑绿**。

三者都不能复用既有总线模板，各有独立语义：

## `ddr5` —— TB 扮演 DRAM 器件

DDR5 与 DDR4 的结构差异决定了不能照抄：

- **CA 总线取代分立引脚**：`ACT_n`/`CS_n`/地址都编码进 `CA[6:0]`（2 拍一条
  命令），不再是 DDR4 的 `adr[16:14]` 命令编码；
- **DQS 是差分对**，无独立 `dm_dbi_n` 引脚；
- `rd_valid` 是控制器给 TB 的读数据有效指示。

自检：CA 命令拍计数、`CS_n` 低时必须发命令、`ACT_n` 与命令语义互斥、
**差分 CK 必须互补**（接反 = 采样边沿错位，只表现为偶发数据错，极难定位）、
读命令后必须出现 `rd_valid`。

## `spi` —— TB 扮演 SPI 从设备

- 无地址、无 ready，唯一流控是 `CS_n` 片选 + `SCLK` 位时钟；
- **模式（CPOL/CPHA）决定采样沿**，假设错就整体错 1 位（典型现场故障）；
- 全双工：master 在 MOSI 移出的同时从 MISO 移入。

自检：`CS_n` 成帧、**每帧恰好 8 个 SCLK 沿**（帧截断检测）、空闲时钟静止、
MISO 回环图案。

## `ethernet` —— TB 扮演链路对端

- 收发双通道，无 valid/ready 握手，靠 `tx_en`/`rx_dv` 流式成帧；
- **最小帧 60 字节**是协议硬约束（不含 FCS），短于它真实 MAC 会丢弃。

自检：TX 帧长 >= 60 字节、`tx_en` 成帧统计、`tx_data` 无 X。

## 首次实现踩到的三个真实问题

1. **SPI 空闲时钟检查误报**：Mode0 master 收尾时要把 SCLK 从 1 拉回空闲 0，
   这发生在 `CS_n` 拉高的同一拍或紧后一拍，是**合法**的。旧检查在 3 帧上
   各报一次错。修复：`CS_n` 拉高后给 2 拍回落窗口，窗口之后再变才判违规。
2. **三个协议的方向表缺失**：`MISO`/`TREADY` 之类写反的 DUT 不会被拦，而是
   生成一个永远等不到响应的 TB（挂死而不报错）。已补齐 `EXPECTED_TB_DIR`
   与 `REQUIRED_TB_PORTS`。
3. **错误文案把 TB 角色写死成 "bus slave"**：对 SPI/Ethernet 会误导用户去找
   一个不存在的「从设备」。改为按协议给准确角色（SPI slave / link partner /
   DRAM device / HBM stack），并在 `data.testbench_role` 里透出。

## 验证

| 协议 | 合法 DUT | 故障 DUT |
|---|---|---|
| ddr5 | `PASS: ddr5 CA/CS_n/ACT_n protocol, differential clock, read path all verified` | — |
| spi | `PASS: spi 3 frames, CS_n framing / SCLK bit count / idle clock verified` | 帧截断 + 空闲乱跳 → `FAIL: spi 504 errors` rc=1 |
| ethernet | `PASS: ethernet rx frame accepted, tx frame length >= 60 bytes, no X on tx_data` | 5 字节超短帧 → `FAIL` rc=1 |

三个方向写反的 DUT 均在**生成前**被拒绝。参考靶场新增
`ddr5_mem_ctrl.sv` / `spi_master.sv` / `eth_mac.sv` + 对应 TB，接入
`examples/multi_bus_ref/sim.sh`。

## 顺带修掉的覆盖率门禁退化

零 EDA 环境下 Layer 4 曾报 176/178（`_emit_spi_tb`/`_emit_ethernet_tb`
未覆盖）—— 因为这两个纯字符串生成函数只被「需要 verilator」的测试调用，
无 verilator 时整体 return。修复：把结构断言（模块名、`errs` 必须为 int）
移到不依赖 EDA 的套件里无条件执行，与 `axi4_stream` 的处理方式一致。

同时修正 `test_regression.py` 的 `_HAVE_VLT` 判定：`find_verilator()` 按产品
设计会回退到 `~/.local/...` 绝对路径，但 runner 脚本内部走 PATH 查找，两者
口径不一致会让测试「不跳过、却跑不起来」。现在要求「>= 5.040」与「PATH 可见」
同时成立（评审 182 同类问题的第二次修正）。
