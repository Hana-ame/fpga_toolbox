# 缺陷修复 · slang 后端对**多维 unpacked** 端口的位宽错误（两后端不一致）

## 现象

```systemverilog
input  logic [7:0] arr [2][3],      // packed [7:0]=8 位 × unpacked 2×3
input  logic [3:0] mat [0:1][0:2],  // packed [3:0]=4 位 × unpacked 2×3
```

| 后端 | `arr` | `mat` |
|---|---|---|
| builtin | 8 ✓ | 4 ✓ |
| slang（修复前） | **24**（8×3） | **12**（4×3） |

两个后端**不一致**，且 slang 的值是错的。

## 根因

slang 用 `$` 明确分隔 packed 与 unpacked：`logic[7:0]$[0:1][0:2]` 表示 packed
只有 `[7:0]`=8 位，`$` 之后全是 unpacked（两个维度）。

旧实现用 `re.sub(r"\$\s*\[..\]", "", tstr)` 逐个剥 `$[...]`。但多维 unpacked
**只有一个 `$`**：剥掉 `$[0:1]` 后，剩下的 `[0:2]` 没有前缀、不被正则匹配 →
被误算成 packed → `8*3=24`。

缺陷 186 只修了**单维** unpacked 的 case（`logic[7:0]$[0:3]` → 剥掉后无残留），
多维是它的漏网之鱼 —— 这正是「验证一致性≠验证正确性」的实例：此前只验证了
builtin/slang 对某组端口**一致**，没验证是否**正确**。

## 修复

改为**截断到第一个 `$`**：`$` 之后（无论几维 unpacked）一律不计入端口连线宽度。

```python
_dollar = tstr.find("$")
if _dollar >= 0:
    tstr = tstr[:_dollar]   # $ 之后全是 unpacked
```

语义依据：SystemVerilog 中端口连线宽度只算 **packed** 维度；unpacked 维度定义
「多少个元素」，不改变单个连线宽度（下游 TB 按元素数展开连线，但端口本身的
位宽是 packed 部分）。

## 验证

| 端口 | builtin | slang（修复后） |
|---|---|---|
| `logic [7:0] a [0:3]` | 8 | 8 ✓ |
| `logic g [2]` | 1 | 1 ✓ |
| `logic [3:0][7:0] packed_only` | 32 | 32 ✓ |
| `logic [1:0][1:0][1:0] triple` | 8 | 8 ✓ |
| `logic [7:0] arr [2][3]` | 8 | **8** ✓（修复前 24） |
| `logic [3:0] mat [0:1][0:2]` | 4 | **4** ✓（修复前 12） |

两后端**一致且正确**。既有测试 `test_fz_unp_widths` 断言的 6 个 case 全部保持
通过（未改断言），并新增多维 unpacked 的覆盖。

门禁：完整 EDA **6/6**，Layer 4 `217/217` 函数 / 46 模块。
