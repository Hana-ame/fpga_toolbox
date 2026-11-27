# 功能增强 · CI 显式 slang 矩阵维度 + changelog 索引自动化

## 1. CI 增设「无 slang 降级」节点（评审方向一）

**问题**：`test` job 矩阵的每个节点都尝试安装 slang，且安装步骤是
`continue-on-error: true`。一旦下载失败，节点会**静默退化成降级节点** ——
名义上测了 slang 路径，实际只测了 builtin 回退，把内置解析器的回归掩盖掉。
这正是评审 181（typedef 端口误判 1-bit）长期漏网的机制。

**修复**：

- slang 提升为矩阵的**一等维度**：4 个节点 `slang: true`、1 个
  `ubuntu-latest py3.11 slang: false`，job 名带 `, no slang` 后缀。
- 新增双向断言步骤：声称有 slang 的节点 `command -v slang` 必须成功，
  降级节点必须**确实没有** slang。矩阵配置与实际环境从此可验证，不再靠
  「安装步骤是否碰巧成功」。
- 与既有 `test-no-eda` job 分工明确：
  - 矩阵 `slang: false` → 无 slang，但 verilator/ruff 可用（正常开发机形态）
  - `test-no-eda` → slang/verilator/ruff **全都没有**（README 承诺的最低口径）

## 2. changelog 索引自动化（评审方向四）

**问题**：

- `10-` 编号下有两个文件（`10-pure-apb-draft.md` 与
  `10-synopsys-vcs-dc-pt-campaign-integration.md`，来自不同分支各自独立编号）；
- `changelog/INDEX.md` 靠手工维护，新增条目忘记登记就静默漂移；
- `01`–`03` 长期是反引号而非链接（点不开）；
- 顶层 `CHANGELOG.md` 的发布摘要同样手工维护，与实际内容脱节。

**修复**：

- `10-pure-apb-draft.md` 迁移为 **`130-pure-apb-draft.md`**，编号自此唯一。
  原路径**保留转发文件**（标题以「已迁移」开头）以兼容已发布的外部链接，
  该文件不参与编号唯一性检查。
- 新增 `scripts/sync_changelog_index.py`，提供 `--check` 与 `--write`：
  - 校验编号唯一（转发文件豁免）
  - 校验每个 `changelog/*.md` 都在 INDEX 里有链接
  - 校验 INDEX 无死链
  - 校验 `CHANGELOG.md` 自动摘要段与 `changelog/` 实际内容一致
- 自动摘要段由标记 `<!-- BEGIN/END AUTO-GENERATED CHANGELOG SUMMARY -->`
  包裹，`--write` 只重写该段，**人工内容不受影响**。
- 接入 Layer 5 门禁：漂移会让六层门禁直接失败（实测负例：制造重号后
  `--check` 返回 1 并报出两个具体文件名）。

## 3. 验证

| 环境 | Layer 3 | Layer 4 | 门禁 |
|---|---|---|---|
| 严格零 EDA（仅 PyYAML） | 降级 compileall | 178/178（排除 5 个 slang 门控） | **6/6** |
| slang-only（CI 中间态） | 降级 | 183/183 | **6/6** |
| 完整 EDA | ruff 0 errors | 183/183 | **6/6** |

changelog 自检：`131 个文件、编号唯一、INDEX 完整、CHANGELOG 摘要同步`；
负例（人为制造 `130-dup-probe.md`）被正确拒绝。
