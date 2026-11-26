# 159 · `min_score` 没有 0–100 边界

## 现象

`run_mutation` 的 `min_score` 是「可接受的变异杀伤率百分比」，上限必然是 100。
旧 schema 只声明了 `type: number`：

```
min_score=150 → error="Mutation score 0.0% is below threshold 150.0%."
min_score=-1  → ok=true（任何分数都达标）
```

- `150` 是**永远不可能达到**的阈值：每次调用都必然失败，而且错误信息把它
  伪装成「分数未达标」，让人以为是测试质量问题，不是参数问题。
- `-1` 让阈值形同虚设。

## 根因

缺陷 144 给 `timeout_sec` 加了 `minimum: 1` 时，只处理了「谁需要下界」，
没有系统检查其余数值参数。`min_score` 是最典型的一个（上下界都由语义决定）。

## 修复

```python
"min_score": {"type": "number", "minimum": 0, "maximum": 100,
              "description": "... 0-100 (default 80). Values above 100 are
              unachievable and would always fail."}
```

复用缺陷 144 已实现的 `minimum`/`maximum` 入口校验。

## 验证

| `min_score` | 修复前 | 修复后 |
|---|---|---|
| `-1` | `ok=true` | `must be >= 0` |
| `-0.5` | `ok=true` | `must be >= 0` |
| `0` | 正常 | 正常 |
| `100` | 正常（可达上限） | 正常 |
| `101` | `below threshold 101.0%`（伪装成分数问题） | `must be <= 100` |
| `150` | `below threshold 150.0%` | `must be <= 100` |

回归测试：`test_min_score_bounds_are_enforced`。
