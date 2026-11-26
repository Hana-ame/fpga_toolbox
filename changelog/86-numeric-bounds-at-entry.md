# 144 · schema 没有数值边界，负/零超时被当成「仿真超时」

## 现象

```
run_sim(timeout_sec=-5)
→ ok=false, error="Simulation timed out after -5s: Command [...] timed out after -5 seconds"
```

`-5` 被原样传进 `subprocess.run(timeout=-5)`，Python 立即抛 `TimeoutExpired`。
工具把「参数非法」报成「仿真超时」，语义荒谬且把排查方向带偏。
`timeout_sec=0` 同样：任何仿真都不可能跑完。

`run_mutation` 的 `timeout_sec` 有完全一样的问题。

## 根因

`registry.call_tool` 做了「未知参数名」「类型」「enum」「required」四类校验，
**唯独没有数值边界**。schema 里也从没写过 `minimum` —— 即使写了也不会有人检查。

## 修复

1. `registry.call_tool` 新增 `minimum` / `maximum` 校验：值越界返回结构化错误
   `Invalid value -5 for parameter 'timeout_sec' of tool 'run_sim': must be >= 1.`。
   字符串数字（MCP 客户端常把 JSON number 序列化成 str）先尝试 `float()` 再比较，
   转不动则跳过、交给下游既有的宽容转换逻辑。
2. `run_sim.timeout_sec` 与 `run_mutation.timeout_sec` 的 schema 加 `"minimum": 1`。

## 验证

| `timeout_sec` | 修复前 | 修复后 |
|---|---|---|
| `0` | `timed out after 0s` | `must be >= 1` |
| `-5` | `timed out after -5s` | `must be >= 1` |
| `"-3"` | `timed out after -3s` | `must be >= 1` |
| `1` / `300` | 正常 | 正常 |
| `"1700"`（字符串） | 正常 | 正常（兼容保留） |
| 省略 | 默认 300 | 默认 300 |

- 错误文本里**不再**出现 `timed out`（不把参数错误伪装成超时）。
- L1 schema↔signature 双射仍 13/13；四套测试全绿。

回归测试：`test_numeric_bounds_are_enforced_at_entry`。
