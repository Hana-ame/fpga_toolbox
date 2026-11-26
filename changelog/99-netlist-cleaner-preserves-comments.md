# 162 · PT 网表清理会把注释里提到的黑盒实例一并删掉

## 现象

```
module top (input clk, output q);
  wire n1, n2;
  // BUFX2 u_comment (.A(clk), .Y(n1));      <- 用户注释
  BUFX2 u1 (.A(clk), .Y(n1));               <- 真实实例
  BUFX2 #(.DRIVE(2)) u2 (.A(n1), .Y(n2));   <- 参数化实例
  DFFX1 u3 (.D(n2), .CK(clk), .Q(q));
endmodule
```

`clean_pt_netlist(blackbox_modules=["BUFX2"])` 输出：

```
  // // [fpga_toolbox:pt_clean] removed unresolved parameterized blackbox instance
  // [fpga_toolbox:pt_clean] removed unresolved parameterized blackbox instance
  // [fpga_toolbox:pt_clean] removed unresolved parameterized blackbox instance
  DFFX1 u3 (.D(n2), .CK(clk), .Q(q));
```

两个真实实例 → **三条**替换注释，用户的注释行内容彻底丢失，第一条还叠加成
`// //`。

## 根因

`inst_re` 直接在模块原文上运行，没有排除注释/字符串区间。注释里的
`// BUFX2 u_comment (...)` 完全符合实例正则的形态，于是被当成真实实例删除并
替换。

（同一模块此前修过「`endmodule` 出现在注释里导致块截断」的问题，当时给
`endmodule` 定位加了注释跳过，但**实例匹配**这条路漏了。）

## 修复

匹配前先掩掉注释与字符串，替换后按顺序还原：

```python
_mask_store = []
_masked = re.sub(_SKIP, lambda m: (store(m.group(0)), "\\x00"*len(m.group(0)))[1],
                 block, flags=re.S)
_stripped = inst_re.sub(_strip_inst, _masked)
new_block = re.sub(r"\\x00+", unmask_next, _stripped)
```

占位与原文**等长**，所以不影响后续 `endmodule` 定位的偏移计算；`\x00` 不可能
出现在实例正则的字符类里，不会误匹配。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 行注释里的实例 | 被删除、注释丢失 | 注释原样保留 |
| 块注释（`/* */`）里的实例 | 被删除 | 保留，块结构完整 |
| 2 个真实实例 | 3 条替换注释、`// //` 叠加 | 恰好 2 条、无叠加 |
| 注释里出现 `endmodule` | assign 位置错乱（旧缺陷） | 块正常结束 |
| 清理后网表 | — | `verilator --lint-only` 0 error |

四套测试全绿。

回归测试：`test_netlist_cleaner_preserves_comments`。
