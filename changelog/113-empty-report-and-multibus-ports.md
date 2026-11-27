# 187–188 · 空报告宣称 PASS；多接口 TB 未连接端口的风险未披露

## 187 · 多接口 DUT 只驱动一个接口时，未连接端口的后果没讲清

`emit_testbench` 对多接口 DUT 只驱动第一个接口（缺陷 167 已加 `W056` 披露
未覆盖接口），但原措辞只说「那部分未被验证」。实测一个双通道 DDR3 控制器：

```
emit_testbench(dual_ddr3, "ddr3")   → ok=true, W056 (ch1_ddr3 未覆盖)
verilator --lint-only ...           → 通过
verilator --binary  ...             → C++ 编译失败：
    Vtb_dual_ddr3_ddr3___024root__0__Slow.cpp:55:22:
    error: lvalue required as left operand of assignment
```

即：**`ok=true` 的产物可能根本构建不出来**。根因在 Verilator 对「部分连接的
多总线 DUT + 表达式驱动端口」的 C++ 生成（属 Verilator 侧缺陷，工具无法修），
但工具侧的 W056 应当提醒这个可能性 —— 生成的 TB 本身就为此注入了
`lint_off PINMISSING`，说明「未连接端口」是已知风险面。

修复：W056 措辞补充「未连接端口在某些工具里连 elaboration 都会失败（生成的
TB 正因此才设置 lint_off PINMISSING）」，并建议拆分 DUT 或按接口包 wrapper。

## 188 · 0 条检查 + `overall_status=PASS` 被判「一致」

`render_visual_report` 的 W053 规则原本是：

```python
if overall == "PASS" and n > 0 and counted["PASS"] == 0:
```

`n > 0` 把**最严重的情形整段跳过**：

```
{"checks": [], "overall_status": "PASS", "summary": {全部 0}}
→ consistency_ok=True, diagnostics=[]
```

一份没有任何证据行的报告宣称通过，却判定「一致」。而同性质的「全部 SKIP
却 PASS」因为 `n > 0` 成立反倒会被 W053 抓到 —— 两种情形处理不一致。

修复：`n == 0` 且 `overall == PASS` 时同样报 W053：

```
overall_status=PASS but the report contains no checks at all. A pass with zero
evidence rows is not a verification result.
```

`0 条检查 + FAIL` 不报（什么都不做本就该失败，不矛盾）。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 双通道 DDR3 TB | `ok=true`，W056 只说"未验证" | W056 额外提示可能 elaboration 失败 |
| `checks=[] + PASS` | `consistency_ok=True`，无诊断 | `consistency_ok=False` + W053 |
| `checks=[] + FAIL` | 一致 | 一致（不假报） |
| 正常 `1 PASS + PASS` | 一致 | 一致（不假报） |

四套测试全绿；六层门禁 6/6，函数覆盖 **170/170**。

回归测试：`test_empty_report_cannot_claim_pass`。
