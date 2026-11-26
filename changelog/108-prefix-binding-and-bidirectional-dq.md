# 172–175 · 绑定层的数据没被模板用上：双向 dq、rd_valid、非标准前缀

标准接口 + 绑定层落地后，又暴露出四类「绑定层算对了、下游没用上」的问题。

## 172 · 双向 `dq` 被当成冲突

DDR 控制器在真实 RTL 里通常把数据总线拆成两半：

```systemverilog
input  logic [15:0] ddr3_dq_in;    // 控制器读到的数据
output logic [15:0] ddr3_dq_out;   // 控制器驱动的数据
```

旧绑定把两个端口都归到 canonical `dq`，然后按「冲突」处理：选一个、把另一个
记进 `ambiguous` 并拒绝。结果 `ddr3_dq_out` 落进 `out_of_scope_ports` 被 tie 到
常量 —— 而 TB 模板正要驱动/观察它。

修复：方向相反、位宽相同的同 canonical 端口配成 **twin（in/out 对）**：

```json
{"split_port_map": {"dq": {"in": "ddr3_dq_in", "out": "ddr3_dq_out"}}}
```

twin 的两半都加入 `claimed`，不再被判越界。

## 173 · DDR3 canonical 漏了 `rd_valid`

DDR4 的 canonical 有 `rd_valid`，DDR3 没有 → `ddr3_rd_valid` 落进
`out_of_scope_ports` 被 tie，而 TB 模板把它当响应握手信号读。补上。

## 174 · 模板名去查 canonical 键的 port_map，永远查不到

`_missing_tb_ports` 用 `REQUIRED_TB_PORTS` 里的**模板侧拼写**（`ddr3_ck_p`）去
`_pm(iface, "ddr3_ck_p", ...)`，而 port_map 以 canonical 名（`ck_p`）为键：

```python
mapped = _pm(iface, canon, canon)   # 查 "ddr3_ck_p"，实际键是 "ck_p"
```

于是任何带非标准前缀的合法 DUT（`mem_ddr3_*`）都被判成「缺 12 个端口」，
尽管绑定层已经完整识别。修复：加 `_TEMPLATE_TO_CANON` 桥接表。

双向 `dq` 的 in/out 两半还要**分别**校验：DUT 只有 `ddr3_dq_out`、没有
`ddr3_dq_in` 时仍必须拒绝（否则生成的 TB 连一个不存在的 pin，PINNOTFOUND ——
缺陷 121 的回归防护）。

## 175 · 模板实例连线仍是硬编码端口名

即使校验放过，DDR3/DDR4/HBM/PCIe 模板里仍是：

```systemverilog
.{mod} dut (.ddr3_ck_p(clk), .ddr3_cke(ddr3_cke), ...);
```

对 `mem_ddr3_*` DUT 就是 PINNOTFOUND，而工具报 `ok=True`。修复：四个模板的
全部端口名改为 `_pm()` / `_split_pm()` 解析的真实名字。

## 验证

新增 6 个「非标准前缀」DUT 变体（`mem_ddr3_*` / `m_ddr4_*` / `m_hbm_*` /
`m_axi_*` / `m_apb_*` / `ep_pcie_*`），全部：

```
apb    rc=0 warn=0 pass=1     axi4   rc=0 warn=0 pass=1
ddr3   rc=0 warn=0 pass=1     ddr4   rc=0 warn=0 pass=1
hbm    rc=0 warn=0 pass=1     pcie   rc=0 warn=0 pass=9
```

7 个原参考 DUT 仍全绿，且 DDR3/DDR4 的 `out_of_scope_ports` 从 2/1 降到 **0**。

`verify_invariants.py --full` 六层全过，函数覆盖 **168/168**。

回归测试：`test_nonstandard_prefix_dut_ports_are_bound_and_wired`。
