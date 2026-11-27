# 118 · 修正 `FEATURES.html` 副标题中过期诊断码计数（37 → 47）

## 现象

`docs_check.py` D13 实测已覆盖 47 个诊断码，但 `docs/build_docs_html.py` 中 `FEATURES.md` 的页面副标题 `deck` 仍写着“37 个诊断码”，导致派生的 `docs/FEATURES.html` 首屏数字与实际文档内容不一致。

## 改进

1. **`docs/build_docs_html.py`**：将 `FEATURES.md` 的 `deck` 更新为 `13 个原子工具、统一信封、47 个诊断码`。
2. 重新执行 `python3 docs/build_docs_html.py` 同步重建 `docs/FEATURES.html`。

## 验证

- `python3 docs/build_docs_html.py --check`：`FEATURES.html`、`EXAMPLES.html`、`USER_MANUAL.html` 全部同步且结构/链接/锚点自检通过。
