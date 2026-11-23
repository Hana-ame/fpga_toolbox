# Changelog — APB 测试向量写死 32 位，窄总线上被截断

### Fixed

- **`emit_testbench --protocol apb` 的测试向量写死 32 位字面量，在窄总线上被截断**（`protocol_tb_emitter.py`）。

  模板里每个事务都直接写死 32 位数：

  ```python
  apb_tx("APB_001", "reset_read", 1'b0, {aw}'h0000, {dw}'h0, {sw}'hF, {dw}'h12345678, 1'b0);
  ```

  `{dw}` / `{sw}` 会随接口位宽替换，但**值本身不缩放**。对一个 `data_width=8` 的 APB 设备（宽度推断是正确的，`addr_width=4`/`data_width=8`），生成出：

  ```verilog
  apb_tx("APB_001", ..., 8'h12345678, ...)   // 8 位宽标注 + 8 个十六进制数字
  ```

  Verilator 报 8 条 `WIDTHDIGITS` + 1 条 `WIDTHTRUNC`——数据被截断，断言值失真。测试仍显示"跑通"，但验证的是截断后的错误值，比直接失败更糟。

### 修复

按实际位宽计算图案与字面量位数：

```python
dw_mask = (1 << dw) - 1
pat_a = 0x12345678 & dw_mask          # 值按 dw 掩码
dhex = max(1, (dw + 3) // 4)          # 十六进制位数按 dw 计算
ahex = max(1, (aw + 3) // 4)          # 地址位数按 aw 计算
```

几个非平凡的点：

1. **只改值不够**。第一版只把值掩码成 `8'h78`，但格式串仍是 `{val:08X}`，写出 `8'h00000078`——8 个十六进制数字塞进 8 bit，`WIDTHDIGITS` 照旧。位数必须一起缩放。
2. **地址字面量也要缩放**：`4'h0000` / `4'h0040` 同样超宽（4 位只需 1 个十六进制数字）。
3. **必须保留部分写语义**。原模板 `APB_002` 用 `4'b1010`（隔字节使能）验证"部分写只影响选中字节"，`APB_003` 的期望值 `0xAA34BB78` 是基于此手算的。第一版我把它改成 `${sw}'h{sw_mask}`（全字节使能），直接导致 32 位标准靶场 `APB_003` 失败（`exp=0xaa34bb78 got=0xaa00bb00`）。改为按 `sw` 缩放的交替掩码 `strb_alt` 后恢复通过。
4. **窄总线的非法地址用例无意义**。默认非法地址 `0x40` 在 `aw=4` 时根本不在地址空间内（`4'h40` 本身超宽）。此时退化为该空间最大地址并把"期望 slverr"放宽为不检查——在物理上不存在的越界地址上断言是自欺欺人。

### 一次差点造成跨协议回归的失误

批量字符串替换 `"0040"` 时，误伤了 `_emit_axi4_tb` 与 `_emit_ahb_tb` 里**恰好也含 `4'h0040`** 的地址字面量，把它们变成 `{aw}'h{64:0{ahex}X}`，导致这两个协议报 `NameError: name 'ahex' is not defined`（`ahex` 只在 APB 函数内定义）。

发现方式是 7 协议全矩阵生成：`axi4`/`ahb_lite` 报错而其余 5 个正常。修复用 `git show HEAD:` 取原版函数体整段还原，而不是再猜一次替换规则。

### 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 标准 32 位（`examples/multi_bus_ref/apb_slave.sv`） | PASS | PASS（向量与原模板逐字等价）|
| 窄总线 `dw=8, aw=4` | 9 条位宽警告、数据截断 | **0 条位宽警告** |
| axi4 / ahb_lite / ddr3 / ddr4 / hbm / pcie | PASS | PASS（无误伤）|

7 协议生成全部成功；实跑 APB 8/8、AXI4 6/6、AHB 6/6、DDR3/DDR4/HBM PASS；regression / fusion / audit 9-9 / docs_check D1–D13 / ruff / py3.10 全绿。
