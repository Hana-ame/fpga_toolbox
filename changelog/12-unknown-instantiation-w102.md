# Changelog — 未知例化被静默丢弃

### Fixed

- **`index_repo` 静默丢弃「例化了但仓库里没有定义」的模块**（`repo_indexer.py`）。

  `_find_instantiations` 的判定条件是 `t.kind == "IDENT" and t.value in known`——只有**仓库内已定义**的模块名才会被记录。例化一个仓库里没有定义的模块时（厂商 IP、加密 IP、尚未克隆的子模块，这在真实项目里极常见），它被**完全静默地丢掉**：

  ```verilog
  module top(input logic clk);
    sub_ok           u_ok(.clk(clk));    // 仓库内有定义 → 记录
    vendor_ip_missing u_vip(.clk(clk));  // 仓库内无定义 → 静默丢弃
  endmodule
  ```

  实测修复前：`instantiated_modules == ['sub_ok']`，`diagnostics == []`。下游 `resolve_deps` 因此给出 `dependency_closure == ['top','sub_ok']`、`all_files` 只有 2 个文件，**回答 `ok: True` 且零告警**——用户会以为依赖完整，实际少了被例化的模块，后续 `copy_sandbox` 产出的沙箱静默缺文件。这正是本项目反复批判的「静默通过」。

### 为什么不能简单放宽启发式

第一版尝试直接去掉 `t.value in known` 守卫、把所有 `IDENT IDENT (` 形态都当例化，实测在自带参考靶场上产生 **106 条误报**，全是噪声：

```
W102 ... Instantiation of module 'int' has no definition ...
W102 ... Instantiation of module 'automatic' has no definition ...
W102 ... Instantiation of module 'void' has no definition ...
W102 ... Instantiation of module 'axi_lite_if' has no definition ...   (interface 声明，不是例化)
```

`function automatic int foo(...)`、`function automatic void bar()`、`axi_lite_if slave;`、`return x;` 的 token 形态与 `vendor_ip u_v(` 完全无法用词法区分。106 条噪声会淹没真实告警——噪声换静默，同样不合格。该版本已回滚。

### 正确修复：用 slang CST 的显式例化节点

slang 的 CST 里例化是**显式节点** `HierarchyInstantiation`，其 `type.text` 就是被例化的模块名，零误报。新增 `core/sv_parser.py::slang_instantiation_targets()`：

- `slang --cst-json -` 在 JSON 之后还会追加解释器汇总文本，整段 `json.loads` 会报 `Extra data`，改用 `JSONDecoder().raw_decode()` 只取第一个 JSON 对象；
- 返回 `None` 表示「无法判断」（slang 缺失/解析失败），返回 `[]` 表示「确实没有例化」——两者语义不可混用，前者让调用方退回旧行为且不报未知，宁可漏报不刷噪声。

`repo_indexer` 侧据此新增 `unresolved_instantiations` 字段与 `W102` 诊断。两个必要的排除项：

- **SV interface 类型名**（`apb_if` / `axi_lite_if`）在 CST 里同样是 `HierarchyInstantiation`，若不过滤会在参考靶场产生 45 条误报。用 `interface\s+(\w+)` 收集 interface 定义名，与 module 名同等视为「已定义」。
- `_NON_INST` 关键字集照旧排除。

### 结果

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 例化仓库外模块 | 静默丢弃，0 告警 | `unresolved_instantiations: ['vendor_ip_missing', ...]` + 每条一个 `W102` |
| 参考靶场（含 3 个 interface） | — | **W102 = 0 条**（interface 声明不误报） |
| `function automatic int foo()` 等 | — | 不误报 |
| 参数化例化 / `generate` 内例化 | 已知模块正常 | 未知模块同样精确检出 |

### 下游透传（同一缺陷的后半段）

只在上游 `index_repo` 报 `W102` 不够：调用方常把 `repo_index` 直接喂给 `resolve_deps` 拿文件清单去 `copy_sandbox`，而 `resolve_deps` 原来根本不读 `unresolved_instantiations`，仍会返回一份「看起来完整」的文件列表与 `ok: True`。已补：

- `resolve_deps` 现在只对**依赖闭包内**的模块汇总未解析例化（闭包外的不报，避免噪声）；
- 新增 `unresolved_instantiations` 字段与 `closure_is_complete` 布尔量——后者是给调用方的单点信号：`False` 意味着这份文件清单不完整，别直接拿去建沙箱；
- 同时为每条缺失模块补一条 `W102`。

实测：`/tmp/unk`（例化 `vendor_ip_missing`/`another_unknown`）→ `closure_is_complete: False` + 2 条 W102；自带靶场的 `axi_apb_bridge` → `closure_is_complete: True` 且 0 告警。

### Tests

新增 `tests/test_regression.py::test_unknown_instantiations_reported`（6 项检查）：已知例化仍记录、未知例化上报、`W102` 恰好 1 条、真叶子模块无误报、**函数返回类型与 interface 声明不误报**（锁死第一版 106 条噪声的回归）。

### 验证

- `tests/test_regression.py` ALL CHECKS PASSED；`tests/test_fusion_and_cross_repo.py` ALL FUSION CHECKS PASSED
- `audit_harness` ok；`docs_check` D1–D13 通过 —— 其中 **D13 自己先抓到了这次漏改**：新增 `W102` 后未同步文档，D13 报「1 个诊断码文档未提及：W102」，补进 `docs/TEST_REPORT_AND_PLAN.md` 后才转绿
- `ruff check fpga_toolbox tests`（CI 同款命令）`All checks passed!`；Python 3.10 语法门禁通过
