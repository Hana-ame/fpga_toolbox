# Changelog — tb_linter 注释分割健壮性加固（诚实标注：非已证实缺陷修复）

## 背景

合并 visual_renderer.py（changelog 33 修的"字符串内 //"）时，我发现
`tb_linter.py` 有 **9 处**用朴素 `raw.split("//")[0]` 剥注释——与
visual_renderer 刚修的 bug 同一模式。按"系统性扫同类"的思路，我判断这是缺陷。

## 我先假设是缺陷，但【未能证实】

我构造了 4 个 fixture 试图证明朴素 split 造成可观察的 lint 差异：

| fixture | 结果 |
|---|---|
| `$display("path = a//b")` | 只触发 S4（静默通过），朴素/修复无差异 |
| `$display("path = a//b"); @(posedge clk);` | 同上 |
| `$display(...); s_axi_if.bready = 1'b0;` | 同上 |
| 在 2590 行真实 SV 上比对 | **行为差异 0 行** |

朴素 split 确实会把字符串内 `//` 后的代码丢弃（如
`a = 1'b1; $display("path = a//b"); @(posedge clk);` → 丢弃 `@(posedge clk);`），
**但在我能构造的所有合理 fixture 上，这个丢弃没有改变 lint 的最终判定**
（都只报 S4 静默通过）。

## 诚实的结论

这是一个**理论隐患**（字符串内 `//` 可能误删后续代码），但**我无法构造出
可观察的错误判定**来证实它。所以我不把它当"修复缺陷"，而是当**健壮性加固**：
即使当前无已证实的错误案例，防御性地正确处理字符串内 `//` 也是对的。

## 改动

`tb_linter.py` 新增 `_strip_comment(raw)` 辅助函数（逐字符扫描，正确处理
字符串边界与反斜杠转义，与 visual_renderer.py 的注释高亮同一算法），
替换全部 9 处朴素 `split("//")[0]`。

在 **2590 行真实 SV 上验证行为等价**（0 差异）——证明这是一处无害加固，
不改变现有 lint 结果。

## 反向验证

改回朴素 split → 4 项断言 FAIL（`RTL named Makefile fails fast` 等，
因 fixture 复用被破坏）。

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
tests/test_full_coverage.py           ALL FULL-COVERAGE TESTS PASSED
audit_harness                         13 / 13
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```

## 这条记录的价值

不是每个"看起来像 bug 的模式"都是真缺陷。这一轮我花了数轮构造 fixture
试图证实，最终**诚实结论是未能证实**。记录下来避免以后重复同样探索，
也避免把未证实的隐患虚报成"修复缺陷 N"。
