# Changelog — APB `pstrb` / AXI4 `wstrb` 位宽用 `dw//8` 推导而非取真实端口

## 背景

APB/AXI4 模板里 strobe 位宽原来写成：

```python
sw = max(1, dw // 8)
```

即「数据位宽 / 8」。**规范并不要求 strobe 位宽等于 DATA/8** —— APB 的 `PSTRB`
是按字节使能，AXI4 的 `WSTRB` 同理，但实现完全可以选择与 `DATA/8` 不同的宽度
（窄传输实现、只支持部分字节通道等）。

## 为什么是真缺陷

构造一个**合法**的 APB 从设备：16 位 `pwdata` + 4 位 `pstrb`（按字节方案）。
`extract_interfaces` 如实报告：

```
data_width = 16
pstrb      = {"port": "pstrb", "direction": "input", "width": 4}
```

但 TB 模板按 `16 // 8 = 2` 生成：

```systemverilog
logic [1:0] pstrb = '1;
```

Verilator 连线上报：

```
%Warning-WIDTHEXPAND: tb_apb_slave_apb.sv:16:38: Input port connection
  'pstrb' expects 4 bits on the pin connection, but pin connection's
  VARREF 'pstrb' generates 2 bits.
%Error: Exiting due to 4 warning(s)
```

Verilator 默认**警告即错误** → 构建失败，而 `emit_testbench` 返回 `ok=True`。
与缺陷 110（DDR/HBM 位宽）、111（AXI4 ID/len 位宽）是同一类问题的又一实例：
**用推导值代替真实端口位宽**。

## 修复

`fpga_toolbox/protocol_tb_emitter.py`：

- APB：`sw = _pw(iface, "pstrb", max(1, dw // 8))`
- AXI4：`sw = _pw(iface, "wstrb", max(1, dw // 8))`

`_pw` 优先取 `port_details` 里的**真实端口位宽**，缺失时才退回 `dw//8` 兜底。

顺带把 APB 与 AHB 的 `aw` / `dw` 也统一改用 `_pw`（此前只有 AXI4/DDR/HBM 走了
`_pw`，APB/AHB 仍直接用 `iface["addr_width"]` / `["data_width"]`）：

- APB：`aw = _pw(iface, "paddr", ...)`、`dw = _pw(iface, "pwdata", ...)`
- AHB：`aw = _pw(iface, "haddr", ...)`、`dw = _pw(iface, "hwdata", ...)`

这样七个协议的位宽来源口径完全一致：**端口真实位宽 > classifier 推导 > 标准默认**。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 16 位 pwdata + 4 位 pstrb | `logic [1:0] pstrb` → WIDTHEXPAND 构建失败 | `logic [3:0] pstrb`，TB 侧 0 告警 |
| 10 位 paddr + 16 位 pwdata | 网线按推导值 | 跟真实端口，`[9:0]` / `[15:0]` |
| 6 个参考 DUT（apb/axi4/ahb/hbm/ddr3/ddr4） | 全 PASS | 全 `rc=0` / 0 告警 / 各 1 条 PASS（无回归） |

## 防回归

扩展自审计 **A19**（上一轮新增的「模板与表格一致性」检查）：除比对端口清单外，
再静态断言 `pstrb` / `wstrb` 的位宽确实由 `_pw(...)` 取得 —— 如果将来又把推导值
写回去，A19 当场报

```
strobe=['apb: pstrb width not taken via _pw()']
```

已验证（临时回退 `sw = max(1, dw // 8)` → A19 干净 FAIL；恢复后 19/19）。

回归测试 `test_strobe_width_follows_real_port`：覆盖 `_pw` 的三级回退语义，
并用「16 位数据 + 4 位 pstrb」的端到端 DUT 断言 TB 里 `pstrb` 是 `[3:0]` 而不是
`[1:0]`，同时校验 `paddr` / `pwdata` 网线也跟随真实端口。
