# 181–184 · builtin 解析器 P0：typedef 位宽谎报、localparam 不可见；CI 门禁缺口

一次 clean-room 验收（纯 Python 3.11、无 slang / Verilator / ruff）暴露四处问题，
其中前两个是**生产缺陷**，后两个让它们长期没被 CI 拦住。

## 181 · typedef 端口静默赋位宽 1（P0）

```systemverilog
input  dbg_e        dbg_mode_i,   // enum logic [2:0] → 实际 3 位
input  my_pkg::cfg_t cfg_i,
output dbg_e        dbg_state_o
```

builtin 后端对三者都报 `width=1`，而 slang 后端报 `0`：

```
builtin: [('dbg_mode_i', 1), ('cfg_i', 1), ('dbg_state_o', 1)]
slang:   [('dbg_mode_i', 0), ('cfg_i', 0), ('dbg_state_o', 0)]
```

后果连锁：

1. 下游 TB 按 1 位连线 → Verilator `WIDTHTRUNC`（默认 warning-as-error）→ 构建失败；
2. **Defect 138/139 的防护只在 `width==0` 时生效**，谎报成 1 就直接绕过
   （`tie_off` 用 `1'b0` 而不是 `'0`，输出端不会走空连接 + W054）；
3. 两个后端行为不一致，同一份 RTL 在不同机器上结果不同。

根因：`_parse_ansi` 的类型前缀循环遇到非内建类型标识符时 `else: break`，
`cur_w` 保持默认 1；`pkg::type` 连 `::` 都没处理。

修复：显式建模「类型前缀」——`::` 之后与「后面还有 token 的 IDENT」都判为
用户自定义类型，位宽记为 **0（未解析）**，与 slang 后端对齐；内建类型
（`int`=32 / `byte`=8 / `longint`=64）与可求值维度不变。

## 182 · 端口位宽引用的 localparam 在模块体内，求值不到（P0）

```systemverilog
module m (
  input  logic [DW-1:0] data_i,
  output logic [W-1:0]  data_o
);
  localparam W = 8;
  localparam DW = (W > 4) ? 16 : 8;
```

实测 `data_i` **整条端口丢失**、`data_o` 位宽报成 1、`parameters={}`。两个原因：

1. **解析时机**：`localparam` 声明在端口列表之后，解析端口时参数表为空，
   `[DW-1:0]` 求值失败；
2. **表达式能力**：`eval_int_expr` 不支持 SV 三元 `cond ? a : b`
   （`ast.parse` 直接 `SyntaxError`），也不支持比较/位运算。

修复：

- 解析端口**之前**先扫描模块体收集 `localparam`/`parameter`（迭代求解，允许
  后一个引用前一个）；
- 端口列表内联的 `localparam` 先按 `;` 整段切出单独求值——旧的 `_split_top`
  按逗号切，会把后面的端口吸附进参数条目一起丢掉；
- `eval_int_expr` 增加：SV 三元（翻译成 Python `a if c else b`，只处理单个
  三元，嵌套的返回 None 保持"未知"）、比较运算（`< <= > >= == !=`）、
  位运算（`& | ^`）与一元 `~` / `not` / `+`。

修复后：

```
params= {'W': 8, 'DW': 16}
ports: [('clk', 1), ('rst_n', 1), ('data_i', 16), ('data_o', 8)]
```

## 183 · 测试对 slang / Verilator 硬依赖（零 EDA 下硬失败）

- `test_review_fixes_registry_parser_and_types`（L1544）与
  `test_sv_parser_tokenizer_and_header_fixes`（L2625）无条件
  `for backend in ("builtin", "slang")`，无 slang 时 `parse_sv_file` 抛
  `RuntimeError`（这是文档化的契约），`if not mods: continue` 兜不住。
  修复：新增 `_available_backends()`，只在 `shutil.which("slang")` 存在时才测
  slang 分支。
- `test_numeric_bounds_are_enforced_at_entry` 对合法 `timeout_sec` 断言
  `run_sim(...)["ok"] is True`，而 `run_sim` 需要真实 Verilator 才能跑完仿真 ——
  与 README「控制面工具零 EDA 强依赖」直接冲突。修复：断言改为「错误不是
  边界校验产生的」，这正是该用例要守的行为。

## 184 · CI 没有接入最大的回归集与六层门禁（P1）

`.github/workflows/ci.yml` 只跑了 `test_regression.py` /
`test_fusion_and_cross_repo.py` / `test_eda_campaign.py`：

```
grep -nE "test_|verify_invariants" ci.yml
110: run: python tests/test_regression.py
113: run: python tests/test_fusion_and_cross_repo.py
116: run: python tests/test_eda_campaign.py
```

**`test_full_coverage.py` 和 `scripts/verify_invariants.py` 完全没接**，所以
上面两个 P0 在 clean-room 下必现却一直没被拦截。

修复：CI 增加两步

```yaml
- name: Full-coverage regression suite
  run: python tests/test_full_coverage.py
- name: Six-layer invariant gate
  run: python scripts/verify_invariants.py --fast
```

## 附带：文档滞后

| 位置 | 旧 | 新 |
|---|---|---|
| `docs/USER_MANUAL.html`（手绘图内嵌，2 处） | audit_harness「13 项」 | 「19 项」 |
| `scripts/verify_invariants.py` docstring | `13-check audit_harness` | `19-check` |
| `docs/USER_MANUAL.md` 顶部与工具表 | 「11 类总线协议」 | 「12 类」（含 ddr5） |
| `README.md` 工具表 | 缺 DDR5 | 补 DDR5 + 绑定层说明 |
| `docs/build_docs_html.py` deck | 「37 个诊断码」 | 「47 个」 |

并把手工维护的 `USER_MANUAL.html` 纳入 `build_docs_html.py --check` 的
结构/死链/锚点自检（它含内嵌 SVG、不由 markdown 派生，之前完全没人校验）。

## 验证

- 参考 DUT 端口数与参数完全不变（apb 11 / axi4 31 / ddr3 14 / ddr4 14 /
  hbm 20 / pcie 13，参数 AW/DW/IDW/GEN/LANES 一致）
- 无 slang 时 `backend="auto"` 回退 builtin，typedef 端口仍是 `width=0`
- 四套测试全绿；六层门禁 6/6，函数覆盖 **170/170**
- `docs/build_docs_html.py --check`：FEATURES / EXAMPLES / USER_MANUAL 全部通过
- `audit_harness` 19/19

回归测试：`test_builtin_parser_unknown_typedef_width`、
`test_localparam_ternary_and_body_declared_params`。
