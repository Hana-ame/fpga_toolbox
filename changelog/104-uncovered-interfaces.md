# 167 · 多接口 DUT 只测第一个接口，其余静默悬空

## 现象

一个模块同时暴露两个 APB 从机端口：

```systemverilog
module twoapb_dut (
  input  logic s0_psel, ... output logic s0_pready, ...,
  input  logic s1_psel, ... output logic s1_pready, ...
);
```

`emit_testbench(protocol="apb")` 返回 `ok=true`，但生成的 TB 里：

```
grep -c "s1_" tb_twoapb_dut_apb.sv
→ 0
```

`s1` 的 9 个端口**一个都没出现** —— 既没连接，也没 tie-off。这意味着：

- `s1` 侧的输入引脚悬空（Verilator 的 PINMISSING 又被模板注入的 lint_off 掩盖）
- 该 DUT 的 s1 部分**完全没有被验证**
- 而工具报 `ok=true`，用户看到「TB 生成成功」会以为是整颗 DUT 的测试台

分类器其实是**正确**的：它把端口分成 `s0`（9 个信号）与 `s1`（9 个信号）两组
（多通道分组工作正常，见早期验证）。问题在 emitter 只取第一组就收工。

## 根因

```python
for i in cand_ifaces:
    if i.get("protocol") == want:
        m, chosen = cand, i
        break          # 取到第一个就 break，其余接口不记录、不提示
```

## 修复

选出要驱动的接口后，把同模块的其余接口收集成 `uncovered_interfaces`：

```json
{"uncovered_interfaces": [
   {"protocol": "apb", "group_prefix": "s1", "signal_count": 9}]}
```

并对每个发 `W056`：

```
the DUT also exposes a 'apb' interface (group 's1', 9 signal(s)) that this
testbench does not drive or check. Its ports are left unconnected, so that
part of the DUT is not verified. Generate a separate testbench for it (split
the DUT or use a per-interface RTL wrapper).
```

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 双 APB DUT | `s1_*` 完全不出现，零提示 | `uncovered_interfaces=[{apb,s1,9}]` + `W056` |
| 单接口 DUT（apb/axi4/ddr3…） | — | `uncovered_interfaces=[]`，无 W056 假报 |
| `ok` 语义 | `true`（误导） | `true` + 明确的未覆盖面披露 |

新增诊断码 `W056`，已登记到 README 诊断码范围与 `docs/FEATURES.md` 表格。

回归测试：`test_multi_interface_dut_reports_uncovered_bus`。
