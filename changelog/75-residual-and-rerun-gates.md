# Changelog — 残留检测的删除型误报/重复模式漏报 + suite 重跑沿用旧状态

三处都是「判定信号选错」：用「子串是否存在」代替了「状态是否一致」。

## 1. 删除型变异（`new == ""`）必然误报 E030

```python
if m["old"] not in txt and m["new"] in txt:   # new="" 时 "" in txt 恒真
```

删除型变异（把某段代码删掉）的 `new` 是空串，于是**任何**「old 已消失」的文件
都被判成残留。实测：文件已是变异态 `assign y =;`、spec 为
`old=' a & b' / new=''` 时，`check_residual_mutations` 返回 E030 —— 于是合法的
删除型变异永远无法运行（`run_mutation` 在入口就被残留守卫拦下）。

修复：删除型变异**不参与**残留检查（`if _new == "": continue`）。理由：删除后
的状态（old 消失）与合法变异后的状态完全相同，无法用文本区分；还原保证由主
循环的 try/finally 提供，这是 by design 的取舍。

## 2. 重复模式只替换第一处 → 混合态漏报

变异主循环用 `replace(old_b, new_b, 1)` 只替换**第一处**。若 old 在同一文件出现
多次：

```
原始：  assign a = x & y;
        assign b = x & y;
变异后：assign a = x | y;      <- 第一处已改
        assign b = x & y;      <- 第二处仍是 old
```

旧判据 `old not in txt` 为假（第二处还在）→ 认为没有残留，**漏报**真实未恢复
的混合态。实测该文件对 `old='x & y' / new='x | y'` 返回空残留列表。

修复：三种信号都要覆盖

- old 完全消失、new 出现 → 完整未恢复（原有）；
- **old 与 new 并存** → 混合态（只要两者都在，就说明"至少改写了一处、且至少
  还剩一处未改写"，这正是 `replace(...,1)` + 多处匹配的产物，必然是残留）；
- `count(new) > count(old)` → 兜底。

## 3. suite 重跑时 `setdefault` 沿用第一次的 PASS

```python
results.setdefault(current, "STARTED")   # 第二次遇到同名头时不重置
```

同名 suite 重跑（sim 脚本重试、或日志里同一 suite 出现两段）时，状态不重置：

```
== sim: apb (top=t) ==
PASS: apb ok
== sim: apb (top=t) ==          <- 第二次被截断，没有任何输出
```

`observed={'apb': 'PASS'}`、`summaries={'apb': 'PASS'}`、
**`all_verified=True`** —— 一个被截断的重跑算作通过。

修复：每次遇到 suite 头都 `results[current] = "STARTED"` 并
`summaries.pop(current, None)`，重跑必须以它**自己的**汇总行重新证明通过。

验证四种形态：单次 PASS → True；重跑 PASS → True；**重跑截断 → STARTED /
False**；重跑崩溃 → FAIL / False。真实 9-suite pcie 日志仍 `all_verified=True`、
9 条 summaries（不误伤）。

## 验证

- 4 个测试套件全过（新增 `test_residual_and_suite_rerun_gates`，覆盖删除型
  不误报、完整未恢复、混合态、完全干净、suite 重跑三态）
- ruff 0 错 / `verify_invariants --full` **6 层全过** / docs HTML 同步
- 正常变异流程（含重复模式替换 + 字节级还原）复测不变
