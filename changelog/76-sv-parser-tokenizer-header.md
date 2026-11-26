# Changelog — builtin SV 解析器的 5 处缺陷（分词器 / module header / 多维位宽）

review 第四类（SV 解析核心）逐条复现，确认 5 项。共同后果：**builtin 后端
（slang 不可用时的唯一后端）给出的端口位宽/参数系统性错误**，而下游的
`emit_testbench` / `validate_contract` / `index_repo` 全部依赖它。

## 1. 分词器不认 `$` 前缀 → `$clog2(...)` 全失效

```python
(?P<IDENT>[A-Za-z_][A-Za-z0-9_$]*)      # 不允许 $ 开头
(?P<SYM>[#()\[\],;=:\+\-\*/<>\{\}\.\?@!~&|\^])   # $ 也不在 SYM 里
```

`$` 被直接丢弃，`$clog2(DEPTH)` 变成 `clog2(DEPTH)`，`eval_int_expr` 解析失败 →
宽度退化为 1：

| backend | `[$clog2(DEPTH)-1:0]` |
|---|---|
| builtin（修复前）| width=1 |
| slang | width=4 |

修复：`IDENT` 改为 `\$?[A-Za-z_][A-Za-z0-9_$]*`。修复后 builtin 也给 4。

## 2. `<<` / `>>` 被拆成单字符 → 移位参数解析失败

分词器把 `<<` 拆成 `<` `<`，`_parse_params` 用 `" ".join(t.value ...)` 重拼成
`< <`，`eval_int_expr` 无法解析：

```
parameter int W = 1 << 3;  ->  params={}   （期望 {'W': 8}）
input logic [W-1:0] d;     ->  width=1     （期望 8）
```

修复：`SYM` 优先匹配多字符运算符
`<< | >> | <= | >= | == | != | && | \|\| | ** | ++ | --`。

## 3. `module automatic foo` 把修饰符当模块名

自动机标（`automatic` / `static`）是**生命周期修饰符**，旧实现直接取下一个
IDENT 当模块名：

```
module automatic auto_dut (input logic clk, output logic q);
->  mod.name = "automatic",  ports = []
```

修复：读完 `module` 后先跳过 `automatic` / `static`。

## 4. `module foo import pkg::*; #(...) (...)` 丢光参数与端口

SV 允许 package import 出现在 module header 与参数列表之间。旧实现只认紧随
模块名的 `#(` / `(`，遇到 `import` 就跳出：

```
module imp_dut import mypkg::*; #(parameter int W = 8) (input logic [W-1:0] d, ...)
->  params={}, ports=[]      （完全空壳）
```

修复：header 内跳过连续的 `import ... ;` / `export ... ;` 声明。

## 5. 多维 packed 数组位宽未相乘（**两个后端都错**）

SystemVerilog 里多维 packed 声明的总位宽是各维**乘积**：
`logic [1:0][7:0]` = 2×8 = **16**。

| backend | `[1:0][7:0]` | `[3:0][3:0]` |
|---|---|---|
| builtin（修复前）| 8（只解析第一对 `[]`，随后被整体替换）| 4 |
| slang（修复前）| 2（`re.search` 只取第一维）| 4 |
| 修复后（两者）| **16** | **16** |

修复：

- builtin `_parse_range`：循环解析**连续**的 `[...]` 维度，宽度累乘；
- slang 分支：`re.findall` 全部维度后累乘（原先 `re.search` 只取第一个）。

## 验证

- 5 项逐条修复后，builtin 与 slang 在 `$clog2` / 移位参数 / `module automatic` /
  header import / 多维 packed 上给出**一致且正确**的结果
- 4 个测试套件全过（新增 `test_sv_parser_tokenizer_and_header_fixes`）
- ruff 0 错 / `verify_invariants --full` **6 层全过**（18/18 DUT 仍干净解析）
  / docs HTML 同步
