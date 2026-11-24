# Changelog — `install_git_hook.sh` 从非 scripts/ 目录运行时误报

## 背景

合并进来的 `scripts/install_git_hook.sh`（37 轮引入的六层门禁配套脚本）在
真实 git 仓库里运行时报错。我按文档从子目录运行测试它时立即触发。

## 缺陷 92 — REPO_ROOT 依赖脚本自身位置，误报"Not a git repository"

旧实现：

```bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"
if [ ! -d "$REPO_ROOT/.git" ]; then
  echo "Not a git repository (.git not found). ..." >&2; exit 1
fi
```

`dirname "${BASH_SOURCE[0]}")/..` 假设脚本**总在 `<repo>/scripts/` 下**。
用户把脚本复制到仓库根、或 `deep/nested/` 子目录运行时，`REPO_ROOT` 指向
错误位置（如 `/tmp`），那里没有 `.git` → 误报：

```
$ cd deep/nested && bash install_git_hook.sh
Not a git repository (.git not found). Initialize git first, then re-run.
```

而 `cwd` 明明在真实 git 仓库里。

## 修复

用 `git rev-parse --show-toplevel` 基于【当前工作目录】定位真实仓库根，
任意位置运行都正确：

```bash
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ] || [ ! -d "$REPO_ROOT/.git" ]; then
  echo "Not a git repository (run this from inside the fpga_toolbox repo, ...)" >&2
  exit 1
fi
```

## 验证

| 场景 | 结果 |
|---|---|
| 临时 git 仓库的 `deep/nested/` 子目录运行 | hook 装到 `<root>/.git/hooks/pre-commit` ✅ |
| 非 git 目录运行 | 退出码 1 + `Not a git repository (run this from inside ...)` ✅ |
| `bash -n` 语法检查 | 通过 ✅ |

反向验证：改回 `dirname/..` 旧实现 → 测试断言 `rc=1 Not a git repository`
立即 FAIL。

## 回归测试

`test_full_coverage.py` 新增 `test_install_git_hook_uses_git_toplevel`：
- `bash -n` 语法检查
- 断言脚本用 `git rev-parse --show-toplevel` 定位
- 端到端：临时 git 仓库子目录运行 → hook 装上且内容含 `verify_invariants.py --fast`
- 非 git 目录 → 退出码 1 + 可读提示

## 验证

```
tests/test_regression.py              ALL CHECKS PASSED
tests/test_fusion_and_cross_repo.py   ALL FUSION CHECKS PASSED
tests/test_eda_campaign.py            ALL EDA CAMPAIGN TESTS PASSED
tests/test_full_coverage.py           ALL FULL-COVERAGE TESTS PASSED（新增 1 个测试函数，5 项断言）
audit_harness                         13 / 13
docs_check                            D1–D13 全过
build_docs_html.py --check            HTML 与 md 同步
```
