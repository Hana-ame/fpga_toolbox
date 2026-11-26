# Changelog — AXI4 TB 模板 ID/len/size/burst/resp 位宽跟随 DUT

## 背景

AXI4-Full 的 `ID` 宽度是**实现自选**（常见 4 / 8 / 16 位），`LEN` 也可窄到 4 位
（≤16 beats）。`emit_testbench(protocol="axi4")` 的模板却把它们写死：

```python
logic [3:0]    s_axi_awid = 0, s_axi_bid, ...
logic [7:0]    s_axi_awlen = 0, s_axi_arlen = 0;
logic [2:0]    s_axi_awsize = 3'd2;
logic [1:0]    s_axi_awburst = 2'b01, ...
```

## 为什么是真缺陷

Verilator **默认警告即错误**，所以位宽不一致会让构建直接失败，而工具返回
`ok=True` —— DUT 一点没验到。两类实测：

### 8 位 ID

同一份 `axi4_full_slave.sv`，只把 `IDW` 从 4 改成 8：

```
%Warning-WIDTHEXPAND: tb.sv:21: Input port connection 's_axi_awid' expects 8 bits,
                              but pin connection's VARREF 's_axi_awid' generates 4 bits.
%Warning-WIDTHEXPAND: tb.sv:24: ... 's_axi_bid' expects 8 bits ... generates 4 bits.
%Warning-WIDTHEXPAND: tb.sv:25: ... 's_axi_arid' expects 8 bits ... generates 4 bits.
%Warning-WIDTHEXPAND: tb.sv:27: ... 's_axi_rid' expects 8 bits ... generates 4 bits.
%Error: Exiting due to 4 warning(s)
```

### 4 位 LEN

```
%Warning-WIDTHTRUNC: tb.sv:21: 's_axi_awlen' expects 4 bits, ... generates 8 bits.
%Warning-WIDTHTRUNC: tb.sv:25: 's_axi_arlen' expects 4 bits, ... generates 8 bits.
```

（该 fixture 还有 3 条告警来自 DUT *内部*把 4 位 len 接到 8 位寄存器 ——
那是 fixture 自身的问题，不是 TB 的问题；TB 侧 0 告警。）

## 修复

`fpga_toolbox/protocol_tb_emitter.py::_emit_axi4_tb`：

- `aw/dw/id_w/len_w/size_w/burst_w/resp_w` 全部由 `_pw()` 从
  `port_details`（扫描 DUT 端口所得）取真实位宽，缺失时退回
  `addr_width`/`data_width` 再退回标准默认。
- 端口声明、task 形参（ID / resp）、`beats-1` 的 `len_w'()` 转换、
  burst 编码的 `burst_w'b01` 全部按参数化位宽生成。
- **测试向量按位宽缩放**（这一步同样必要，否则只是把 WIDTHTRUNC 换成
  WIDTHDIGITS）：
  - ID 值 `4'd3/7/9` → `{id_w}'d{3|7|9 & id_mask}`
  - 地址 `16'h0010/0040/0200` → 按 `aw` 掩码 + 十六进制位数 `ahex`
  - 数据 `32'hA000_0000/0xBEEF_1000/0xDEADBEEF` → 按 `dw` 掩码
  - `beats` 收紧到 `min(目标, 2**len_w)`
  - `%04h`/`%08h` 等格式宽度按 `ahex`/`dhex`/`idhex` 同步
- **保留原有 6 个检查的语义**（4 burst + 2 SLVERR 用例）。宽度参数化期间一度
  把测试序列改写成 4 个检查、丢掉 SLVERR 用例 —— 那是覆盖回退，已恢复原序列，
  只把常量改成按位宽缩放；SLVERR 期望值在窄 `resp_w` 上退化为 `resp_ok`，
  并新增 `chk_resp` 形参让「非法地址不校验数据」的原语义保留。

## 验证

- 参考 DUT `axi4_full_slave.sv`：`rc=0`、0 条告警、`PASS: axi4_full all 6
  burst/SLVERR checks passed` —— 6 个检查全部保留且通过。
- 8 位 ID 变体：`rc=0`、0 条告警、同样 `PASS`（修复前 4 条 WIDTHEXPAND + Exiting）。
- 4 位 LEN 变体：TB 侧 0 告警（仅剩 fixture 自身的 3 条）。

回归 `tests/test_full_coverage.py::test_axi4_template_follows_id_len_widths`：
覆盖 `_pw` 取值、8 位 ID/4 位 len/12 位地址/64 位数据的网线位宽、写死值不残留、
测试向量已缩放（`16'h0200` / `4'd9` 不得出现）、以及**测试序列不得被削减**
（检查数 ≥6 且 resp 校验仍在）。

完整验证：4 个测试套件全过 / ruff 0 错（0.17.6 本地非系统）/
`verify_invariants --full` 6 层全过（含 100% 函数覆盖）/ docs HTML 同步。
