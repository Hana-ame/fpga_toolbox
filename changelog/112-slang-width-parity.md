# 185–186 · slang 后端位宽两处退化：模块体参数、unpacked 维度

上一轮修好 builtin 的 typedef/localparam 后，用「两个后端交叉比对」做系统性
找茬（同一份 RTL 分别跑 `backend="builtin"` 与 `backend="slang"`，逐端口比
方向与位宽），发现 slang 侧还有两处静默位宽错误。这是最有效的找缺陷方式：
两个独立实现只要不一致，必有一方错。

## 185 · 引用「模块体声明的 parameter」的端口位宽退化为 0

```systemverilog
module fz_body (
  input  logic [W-1:0] p_i,
  output logic [D-1:0] p_o
);
  parameter W = 12;
  localparam D = 24;
endmodule
```

| 后端 | `p_i` | `p_o` |
|---|---|---|
| builtin | 12 ✓ | 24 ✓ |
| slang | **0** | **0** |

增量探测：slang 的 AST `members` 里**能**读到参数
（`Parameter name=W value=12`，`params={'W':12,'D':24}` 也确实解析出来了），
但端口节点的 `type` 字段是 **`<error>`** —— 参数声明在端口之后，slang 对
端口类型求值时还看不到它，于是退化成 `<error>`，进而 width=0。

参数声明在模块头 `#(...)` 里时两个后端都正确，只有「模块体参数 + 端口引用」
这条路径不一致。

修复：`_enrich_from_builtin` 增加第二类补齐——slang 给 `width<=0` 的**普通
端口**，若 builtin 对同名端口能解析出 `width>0`，就用 builtin 的位宽与表达式
替换（builtin 也是 0 时保持 0 = 未知，不用 0 覆盖 0）。
这复用了已有机制（原本只用于补齐 interface 类型的 modport）。

## 186 · unpacked 数组维度被算进端口位宽

```systemverilog
input  logic [7:0]  a [0:3]    // packed 8 位 + unpacked 4 元素
input  logic        g [2]      // 无 packed 维度 → 标量
```

| 端口 | builtin | slang（旧） |
|---|---|---|
| `a` | 8 ✓ | **32**（8×4）|
| `b` = `[3:0] b [0:1]` | 4 ✓ | **8** |
| `g` = `g [2]` | 1 ✓ | **2** |

slang 的 `type` 字段其实**明确标记**了 unpacked 维度，用 `$` 前缀：

```
logic[7:0]$[0:3]     → packed [7:0], unpacked [0:3]
logic$[0:1]          → 无 packed 维度
logic[1:0][1:0]      → 两个 packed 维度
```

旧实现 `re.findall(r"\[(\d+):(\d+)\]", tstr)` 把两者都匹配到并相乘。代码注释
写的是「只取 packed 部分（保守）」，但**实现与注释不符**。

后果：下游 TB 按 32 位网线接 8 位端口 → Verilator `WIDTHTRUNC` /
`WIDTHEXPAND`（默认 warning-as-error → 构建失败），而工具报 `ok=True`。

修复：先用 `re.sub(r"\$\s*\[(?:\d+):(?:\d+)\]", "", tstr)` 剥掉 unpacked 维度，
再对剩余 packed 维度求乘积。

## 验证

- 交叉比对 fuzz：16 个声明形态文件、**47 个端口、0 差异**
  （覆盖 logic/wire/signed/内建原子类型/多维 packed/unpacked/反序维度/
  inout/多名称声明/隐式类型/模块体参数/模块头参数）
- 参考 DUT 端口数与位宽不变
- 四套测试全绿；六层门禁 6/6，函数覆盖 **170/170**

回归测试：`test_parser_backends_agree_on_widths`、
`test_unpacked_array_ports_use_packed_width_only`。
