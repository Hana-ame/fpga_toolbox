#!/usr/bin/env bash
# Install local Git pre-commit hook enforcing fpga_toolbox invariants before every commit.
set -euo pipefail

# 用 git 自己定位仓库根，而不是假设脚本被放在 <repo>/scripts/ 下。
# 旧实现 REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" 假设脚本
# 总在 scripts/ 里 —— 用户把脚本复制到别处（如仓库根、/tmp）运行时，REPO_ROOT
# 会指到错误位置，误报 "Not a git repository"（实测踩坑）。
# git rev-parse --show-toplevel 基于【当前工作目录】定位真实仓库根，任意位置
# 运行都正确。hook 里的 python3 scripts/... 也用相对仓库根的路径，一致。
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ] || [ ! -d "$REPO_ROOT/.git" ]; then
  echo "Not a git repository (run this from inside the fpga_toolbox repo, or 'git init' first)." >&2
  exit 1
fi
HOOK="$REPO_ROOT/.git/hooks/pre-commit"
cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "[pre-commit] Running fpga_toolbox invariant & intent-drift gate..."
python3 scripts/verify_invariants.py --fast
EOF
chmod +x "$HOOK"
echo "Installed pre-commit invariant gate at $HOOK"
