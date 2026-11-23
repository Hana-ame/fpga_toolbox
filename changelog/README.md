# Changelog

`fpga_toolbox` 的变更记录，每个 changelog 一个文件。

| 文件 | 内容 |
|---|---|
| [`apb-bridge-testbench.md`](apb-bridge-testbench.md) | emit_testbench 支持 AXI↔APB 桥（APB slave 响应器、obs_ 探针、位宽修复）|
| [`pure-apb-draft.md`](pure-apb-draft.md) | 纯 APB 设备的 draft 契约曾写入空 axi4_lite 段，导致自家 validator 报 17 条 E001 |
| [`sandbox-symlink-escape.md`](sandbox-symlink-escape.md) | copy_sandbox 曾跟随符号链接把项目外文件拷进沙箱（隔离泄漏）|
| [`unknown-instantiation-w102.md`](unknown-instantiation-w102.md) | index_repo 曾静默丢弃仓库外模块的例化；改用 slang CST 精确检出并报 W102 |
| [`windows-decoding-and-process-lessons.md`](windows-decoding-and-process-lessons.md) | Windows cp1252 解码崩溃（真实缺陷）+ stash 致代码漏提交/脆断言/测试依赖 CI 缺失环境（过程错误）|
| [`audit-fixes.md`](audit-fixes.md) | 独立审计修复（slang 后端、emit_testbench、run_sim/run_mutation 类型、套件身份核对、自审计 A5–A9）|
| [`third-party-validation.md`](third-party-validation.md) | 真实第三方代码验证（GH 桥仓库、RISC-V 核仓库实测发现的缺陷）|
| [`v0.5.0.md`](v0.5.0.md) | v0.5.0 融合版（工具从 8 个扩展到 12 个）|
| [`v0.4.0.md`](v0.4.0.md) | v0.4.0 基线（原始 8 个原子工具）|

> 完整历史见仓库根的 [`CHANGELOG.md`](../CHANGELOG.md)。
