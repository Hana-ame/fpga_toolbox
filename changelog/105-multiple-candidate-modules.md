# 168 · 多模块文件里静默只测第一个匹配模块

## 现象

一个 `.sv` 文件里有两个 APB 从机（地址宽度不同）：

```systemverilog
module helper_mod (...); endmodule
module apb_a (... input logic [7:0] paddr ...); endmodule
module apb_b (... input logic [15:0] paddr ...); endmodule
```

`emit_testbench(protocol="apb")` 返回 `ok=true`、`module_name="apb_a"`，
但**没有任何信号提示 apb_b 的存在**。用户如果不细看返回体的单个字段，就无从
察觉「生成的 TB 测的不是我以为的那个模块」。

## 根因

模块选择逻辑取文件顺序里第一个协议匹配的模块就 `break`：

```python
for cand in mods:
    for i in cand.get("interfaces") or []:
        if i.get("protocol") == want:
            m, chosen = cand, i
            break
    if m is not None:
        break
```

选择本身是合理的（必须选一个），但**没有披露其它候选**。

## 修复

收集同文件中其余带目标协议的模块，放进 `other_candidate_modules`：

```json
{"module_name": "apb_a",
 "other_candidate_modules": [
   {"module_name": "apb_b", "protocol": "apb", "group_prefix": "default"}]}
```

并对每个发 `W057`：

```
multimod.sv contains another module 'apb_b' with a 'apb' interface too, but
this testbench targets 'apb_a' (the first match in file order). Generate a
separate testbench per module if you meant that one.
```

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 双 APB 模块文件 | `ok=true`，只提 `apb_a` | + `other_candidate_modules=[apb_b]`、`W057` |
| 单模块文件（7 个参考 DUT） | — | `other_candidate_modules=[]`，无 W057 假报 |

新增诊断码 `W057`，已登记到 README 诊断码范围与 `docs/FEATURES.md` 表格。

回归测试：`test_multiple_candidate_modules_are_disclosed`。
