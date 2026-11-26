# Changelog — DDR3/DDR4 必需端口表漏 `*_dq_in`（产出 PINNOTFOUND 坏 TB 却报 ok）

## 背景

`emit_testbench` 在生成 TB **之前**会用 `REQUIRED_TB_PORTS` 校验「模板要硬连的
端口在 DUT 上是否存在」，避免产出编译不过的 TB 却报 `ok=True`（缺陷 14/15 建立
的护栏）。

但 `ddr3` / `ddr4` 两张表只列了 `dq_out`，**漏了 `dq_in`**。

## 为什么是真缺陷

模板对 DUT 是**双向**语义：

- `ddr3_dq_in` —— TB 驱动，写入数据（DUT 的 input）
- `ddr3_dq_out` —— DUT 驱动，读回数据（DUT 的 output）

两张表都只有 `dq_out`。于是一个**没有 `dq_in` 端口**的 DDR3 DUT（例如把 DQ 建模
成单端 `inout`，或只读的存根）通过了生成前校验，产出的 TB 带着

```systemverilog
.ddr3_dq_in(ddr3_dq_in),
```

连线，Verilator 直接：

```
%Error-PINNOTFOUND: tb_ddr3_no_dqin_ddr3.sv:17:29: Pin not found: 'ddr3_dq_in'
%Error: Exiting due to 1 error(s)
rc=1
```

而 `emit_testbench` 返回 `ok=True` —— 与缺陷 14/15 修掉的是**同一类**问题：
生成前校验不完整，护栏漏了一个端口。

（`ddr4` 同理，只是我构造的第一个 ddr4 fixture 还缺别的端口，报错文本里
被更早的缺失项盖住了；表里确实同样漏 `ddr4_dq_in`。）

## 修复

1. `REQUIRED_TB_PORTS["ddr3"]` / `["ddr4"]` 补上 `*_dq_in`，并在表上方写明
   「dq_in 必须一起校验，因为模板对 DUT 是双向语义」。
2. 修复后实测：缺 `dq_in` 的 DUT 被拒，错误点名
   `ddr3_dq_in->ddr3_dq_in`；两个参考 DUT（`ddr3_mem_ctrl.sv` /
   `ddr4_mem_ctrl.sv`）仍 `rc=0` / 0 告警 / 各 1 条 PASS。

## 防回归：新增自审计 A19

这类缺陷的本质是「模板与表格两份清单手工同步」。加一条**结构自检**比再加一条
回归测试更根本：从每个 `_emit_*_tb` 的源码里抽出**同名成对**的 `.foo(foo)`
连线（模板里端口连接一律是这个形态），与 `REQUIRED_TB_PORTS` 做差集。

```python
_connected = {n for n, arg in re.findall(
    r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\{?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}?\s*\)", _src)
    if n == arg}
_connected -= {"GEN", "LANES", "DW"}          # PCIe 的参数覆盖不是端口
_gap = sorted(_connected - set(REQUIRED_TB_PORTS.get(proto, ())))
```

这样「模板加了连线、忘了同步表格」以后会被 A19 当场抓到。已验证：临时把
`ddr3_dq_in` 从表里删掉 →

```
ok=False failed=['A19_REQUIRED_PORTS_COVER_TEMPLATE']
gaps=["ddr3: template connects ['ddr3_dq_in'] not in REQUIRED_TB_PORTS"]
```

> 实现 A19 时自己也踩了一次误报：第一版只匹配 `.name(` 前缀，把 docstring 里
> `.protocol_tb_emitter(` 也算成端口，七个协议全部报假 gap。改成「名字与实参
> 同名」后干净通过 —— **自审计探针本身也要防误报**，否则它会变成噪声源。

## 验证

- 缺陷复现 → 修复后拒绝（`ok=False` 且点名 `ddr3_dq_in`）
- 参考 DUT 回归：ddr3 / ddr4 各 `rc=0`、0 告警、1 条 PASS
- `audit_harness` **19/19**（新增 A19）
- 4 个测试套件全过 / ruff 0 错 / `verify_invariants --full` **6 层全过** /
  本地 `docs_check.py`（D1–D13）全绿 / docs HTML 同步
- 新增回归 `test_ddr_required_ports_cover_dq_in`
