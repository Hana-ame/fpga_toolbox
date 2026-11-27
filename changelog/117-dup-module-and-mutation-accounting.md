# 194–195 · mutation 会计缺防御断言；重复定义 module 静默取第一个

## 194 · mutation 会计不变式无防御

`run_mutation` 的计数语义：

```
total     = 变异总数
evaluated = total - errors - skipped    （真正测得结果的）
killed    = 测试台杀掉的
survived  = evaluated - killed           （没杀掉的）
```

计算式天然满足两条不变式：

```
killed + survived == evaluated            （每个被评估的变异非杀即活）
evaluated + errors + skipped == total     （每个变异归入且仅归入一类）
```

实测当前实现是对的（mixed spec：`total=3, evaluated=2, killed=0,
survived=2, errors=1, skipped=0`，两条成立）。但**没有任何断言**保护它 ——
历史上就出现过 `survived = total - killed`（把 ERROR 也算成存活、污染分数）的
问题。将来若新增一种 status 却忘了加进分类，会产出自相矛盾的分数而无人察觉。

修复：加防御性断言，不一致时返回结构化错误而不是给出无意义的分数：

```
Internal accounting inconsistency: total=... evaluated=... killed=...
survived=... errors=... skipped=...
```

## 195 · 同一 module 在多个文件定义 → 静默取第一个

```
a.sv:  module dupmod(...) ... endmodule
b.sv:  module dupmod(...) ... endmodule
       module user(...) dupmod u(...); endmodule
```

`index_repo` 按文件顺序取 `a.sv` 作为 `dupmod` 的归属，`b.sv` 的定义**静默
消失**，零诊断。但 `resolve_deps(user)` 返回 `rtl_files=['a.sv','b.sv']` ——
两个文件一起交给编译器：

```
%Warning-MODDUP: Duplicate declaration of module: 'dupmod'
%Error: Exiting due to 1 warning(s)      （Verilator 默认 warning-as-error）
```

真实成因很常见：目录里残留旧版本文件、`_bak`/`_old` 备份、从别处拷来的同名
模块。工具本该在索引阶段就指出，而不是让用户在编译错误里自己找。

修复：`index_repo` 检出重复定义并报 `W058`，列出**所有**定义文件：

```
Module 'dupmod' is defined in 2 files (a.sv, b.sv). The index uses the first
one, but compiling them together fails with 'Duplicate declaration of module'.
Remove the stale/backup copy.
```

要排除「`include 展开」造成的假重名（被 include 的源文件里的模块会同时出现在
自己文件和 includer 的解析结果里 —— 见缺陷 133）：用「文件原文里是否真的
声明了 `module <name>`」判定，并跳过 `included_files` 里的文件。

**在仓库自带示例上抓到了真问题**：`examples/axi_apb_ref` 里
`tb_counter8` 同时定义在 `tb_counter8_sv.sv` 和 `tb_counter8_tb.sv`，W058
正确报出（不是误报）。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| mutation 会计 | 无断言保护 | 不变式不成立时返回结构化错误 |
| 两个文件定义同一 module | 静默取第一个，零诊断 | `W058` 列出两个文件 |
| `include` 展开的模块 | — | 不误报（`examples/multi_bus_ref` 零 W058）|
| 仓库自带 `examples/axi_apb_ref` | — | 正确报出 `tb_counter8` 真重复 |

四套测试全绿；六层门禁 6/6（**48 个诊断码**已登记），函数覆盖 **170/170**，
`audit_harness` 19/19。

回归测试：`test_mutation_accounting_is_self_consistent`、
`test_duplicate_module_definitions_are_reported`。
