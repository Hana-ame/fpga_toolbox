# 115 · 同步 `USER_MANUAL.html` 派生校验并补全 `ddr5` 协议文档（184）

> 对应验收项：`changelog/111-acceptance-review.md` #184（P2）

## 现象

1. `docs/build_docs_html.py` 仅管理 `FEATURES.md` 与 `EXAMPLES.md`，`docs/USER_MANUAL.md` 与 `docs/USER_MANUAL.html` 脱离 `--check` 同步校验。
2. `canonical_iface.py` 与 `docs/FEATURES.md` 已支持 `ddr5`（共 12 类总线协议），但 `README.md` 与 `docs/USER_MANUAL.md` 仍写“11 类总线协议”或遗漏 `DDR5`。

## 修复

1. **`README.md`**：在 `extract_interfaces` 职责说明中补全 `DDR5`。
2. **`docs/USER_MANUAL.md`**：更新总线协议数量为 12 类，并在协议列表中补全 `ddr5`。
3. **`docs/build_docs_html.py`**：将 `USER_MANUAL.md` → `USER_MANUAL.html` 加入 `DOCS` 映射，并重新生成 `docs/USER_MANUAL.html` 纳入 `--check` 与结构/链接/锚点自检。

## 验证

- `python3 docs/build_docs_html.py --check`：`FEATURES.html`、`EXAMPLES.html`、`USER_MANUAL.html` 全部同步且结构/链接/锚点自检通过。
- `python3 examples/axi_apb_ref/axi/docs_check.py`：D1–D13 文档一致性检查通过。
