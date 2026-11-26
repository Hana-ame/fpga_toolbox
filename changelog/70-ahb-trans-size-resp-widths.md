# Changelog — AHB `htrans` / `hsize` / `hresp` 位宽写死，不跟真实端口

## 背景

AHB-Lite 模板里三个控制/响应信号的位宽是写死的：

```systemverilog
logic HREADYOUT, HRESP;        <- HRESP 当成 1 位标量
logic [1:0] HTRANS = 0;
logic [2:0] HSIZE = 3'd2;
```

规范给的是 HTRANS=2 / HSIZE=3 / HRESP=1 位，但**真实 IP 常扩展它们** ——
例如用 2 位 HRESP 表达 SPLIT/RETRY（某些 SoC 互联的实现）。

## 为什么是真缺陷

DUT 用 2 位 HRESP 时，TB 声明的却是 1 位标量：

```
%Warning-WIDTHEXPAND: tb_ahb_lite_slave_ahb_lite.sv:17:46: Output port
  connection 'HRESP' expects 2 bits on the pin connection, but pin
  connection's VARREF 'HRESP' generates 1 bits.
%Error: Exiting due to N warning(s)
```

Verilator 默认**警告即错误** → 构建失败，而 `emit_testbench` 返回 `ok=True`。
与缺陷 110/111/122 同一模式：**用规范默认值代替真实端口位宽**。

## 修复

`fpga_toolbox/protocol_tb_emitter.py::_emit_ahb_tb`：

1. `htrans_w = _pw(iface, "htrans", 2)` / `hsize_w = _pw(iface, "hsize", 3)` /
   `hresp_w = _pw(iface, "hresp", 1)` —— 真实端口位宽优先，规范值兜底。
2. 声明按真实位宽：`logic [{hresp_w-1}:0] HRESP;` 等。
3. **HSIZE 常量按位宽收敛**：`2**log2(dw/8)` 需要装进 `hsize_w` 位，超窄时钳到
   `(1<<hsize_w)-1`；HTRANS 的 NONSEQ 编码同理。
4. **所有调用点字面量同步**：`1'b0` / `1'b1` 在 2 位形参上同样是 WIDTHEXPAND，
   全部改成 `{hresp_w}'d0` / `{hresp_w}'d1`。
5. **`!exp_err` 改成 `exp_err == 0`**：Verilog 的 LOGNOT 只接受 1 位操作数，
   多位上报 `LOGNOT expects 1 bit on the LHS`。

### 修复过程记录（我第一版只做了一半）

第一版只改了声明和 task 形参，跑出来 TB 侧仍有 **7 条**告警：

```
%Warning-WIDTHTRUNC: LOGNOT expects 1 bit on the LHS, but 'exp_err' generates 2 bits
%Warning-WIDTHEXPAND: TASKREF 'ahb_read' expects 2 bits ... CONST '1'h0' generates 1 bits  (×4)
```

—— 因为调用点还在传 `1'b0`，且 `!exp_err` 没改。第二次修补时又漏了 `AHB_003`
（它的 `1'b0` 与 `AHB_001` 文本相同，被 `replace(..., 1)` 的 first-match 吃掉）。
**这类"改声明不改调用点"的漏改，只有实际编译才能发现** —— 也说明"生成成功"不等于
"生成正确"。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 2 位 HRESP DUT | TB 侧 7 条宽度告警（含 LOGNOT + 4×TASKREF）| **TB 侧 0 告警**（剩 6 条全来自 fixture 内部仍写 1 位赋值）|
| 1 位 HRESP 参考 DUT | `rc=0` / 0 告警 / 6 checks PASS | 无变化 |
| 6 个参考 DUT（apb/axi4/ahb/hbm/ddr3/ddr4）| — | 全 `rc=0` / 0 告警 / 各 1 条 PASS |

回归 `test_ahb_trans_size_resp_widths_follow_port`：覆盖 `_pw` 取值、HRESP 声明
位宽、**所有 AHB task 调用点不得含 `1'b0`/`1'b1`**、不得出现 `!exp_err`、以及参考
DUT（1 位 HRESP）仍生成 `[0:0]`。
