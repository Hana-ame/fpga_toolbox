#!/usr/bin/env bash
# Install local Git pre-commit hook enforcing fpga_toolbox invariants before every commit.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"
if [ ! -d "$REPO_ROOT/.git" ]; then
  echo "Not a git repository (.git not found). Initialize git first, then re-run." >&2
  exit 1
fi
cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "[pre-commit] Running fpga_toolbox invariant & intent-drift gate..."
python3 scripts/verify_invariants.py --fast
EOF
chmod +x "$HOOK"
echo "Installed pre-commit invariant gate at $HOOK"
