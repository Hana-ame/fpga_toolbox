# Changelog 索引

按迭代阶段拆分为独立变更记录，避免单文件臃肿：

- `01-v0.5.0-fusion.md`：双仓融合基线（12 原子工具、参考靶场、协议编辑器与对比报告）
- `02-independent-audit-and-third-party.md`：外部独立审计、第二轮实机验证与第三方仓库实测修复
- `03-dual-side-bridge-support.md`：`emit_testbench` 双侧桥 APB 从机模型重构、零 `WIDTHEXPAND` 与 `cocotb + Verilator` 端到端通过
- [04-multi-bus-hbm-pcie-ddr-ahb.md](04-multi-bus-hbm-pcie-ddr-ahb.md) — Native AHB-Lite / DDR3 / DDR4 / HBM / PCIe Gen3·4·5 (×1/×4/×16) classifiers, simulatable SV benchmark, and 7-stage live Verilator + mutation verification
- [05-multi-protocol-emitter-and-pcie-3x3.md](05-multi-protocol-emitter-and-pcie-3x3.md) — Multi-protocol `emit_testbench` (APB/AXI4/AHB/DDR3/DDR4/HBM/PCIe 3×3), native `[CHECK]` parsing, and per-project tabbed interface report pages
- [06-complex-dual-if-repack-ooo-loopback.md](06-complex-dual-if-repack-ooo-loopback.md) — Dual-interface translation (AXI4-64↔APB32), AXI4 OoO/same-ID fabric, PCIe 3DW/4DW/Split-CplD loopback, 256B golden scoreboards, and 3/3 middle-logic mutation kills
- [07-soc-fabric-spi-eth-pm-an-ltssm-mem.md](07-soc-fabric-spi-eth-pm-an-ltssm-mem.md) — SPI/Ethernet classifiers, bidirectional conversions (SPI↔APB, ETH↔AXI4, APB+PCIe, AXI4→DDR), POR/negotiation-fail/sleep handshakes, built-in shared MEM, and 12/12 (100%) RTL mutation kills
- [08-full-conversion-matrix-buf-cycs-syntax-color.md](08-full-conversion-matrix-buf-cycs-syntax-color.md) — Real direction/width pills (zero `-`), SV syntax color highlighting, configurable `BUF_CYCS=0/2/4` bridges (`APB→AXI4`, `PCIe→AXI4`, `AXI4→DDR3`), and full 256B sweeps (97 Verilator + 7 cocotb checks)
- [09-unified-omni-bus-hub-hotplug-buf.md](09-unified-omni-bus-hub-hotplug-buf.md) — Unified 6-IF all-directional hub (`soc_omni_bus_hub.sv`), Ethernet/PCIe hot-plug disconnect/reconnect, tri-domain sleep/wake, `BUF_CYCS=0/2/4`, 115 Verilator + 7 cocotb checks, and 15/15 (100%) RTL mutations

## eda_campaign 集成

- [10-synopsys-vcs-dc-pt-campaign-integration.md](10-synopsys-vcs-dc-pt-campaign-integration.md) — Synopsys VCS/UVM `parse_uvm_log`、DC/PT `parse_dc_pt_qor`、PT 黑盒网表清理与远端 `csh`/`/dev/shm` 启动器。⚠ 诊断码已从 `E040–E045` 平移为 `E046–E050`（`E040` 被 `audit_harness` 占用）

## eda_campaign 审计

- [20-eda-campaign-audit.md](20-eda-campaign-audit.md) — 审计 `clean_netlist_for_pt` 发现 2 个真实缺陷：参数化 `#(...)` blackbox 实例完全不被清理（`removed=0`）、注释中的 `endmodule` 导致成块扫描错位并往注释里注入 assign。附一次自我纠偏：位选择不应被合并成根 net
- [21-eda-campaign-audit-2.md](21-eda-campaign-audit-2.md) — 第二轮审计剩余 5 个函数：`generate_remote_run_script` **命令注入**（实测可执行任意命令）、`aggregate_seed_medians` 传错层级静默返回空、配对基线用不完整数据判「逐位一致」
- [22-eda-campaign-audit-3.md](22-eda-campaign-audit-3.md) — 第三轮审 `parse_uvm_log` 正则层：千分位 `10,000` 被截成 `10`（**可致假通过**）、`delivered=` 依赖字段顺序

## 集成层修复

- [23-clean-pt-netlist-tool-registration.md](23-clean-pt-netlist-tool-registration.md) — `tool_clean_pt_netlist` 是 ToolResult 信封却不注册（CLI/MCP 都调不到）；README 仍称 `eda_campaign`「尚未实现」；顺带把硬编码 `tools==12` 断言改为下限
- [24-pt-netlist-residual-guard.md](24-pt-netlist-residual-guard.md) — `clean_pt_netlist` 只检查「有没有清理动作」不检查「清理是否生效」，缺分号网表静默产出仍含黑盒的坏网表；新增产物自检 + `E051` + A10 自审计项

## 跨工具一致性

- [25-call-tool-required-and-outdir-cwd.md](25-call-tool-required-and-outdir-cwd.md) — 跨工具 schema↔签名扫描发现：`out_dir` 为空时 4 个产物静默写进调用方 cwd；根因是 `call_tool` 从不校验 schema 的 `required`
- [26-call-tool-enum-and-type-validation.md](26-call-tool-enum-and-type-validation.md) — `call_tool` 也不校验 enum 与 type：非法协议名报错指向 RTL，「string 传 int」漏出 CPython 内部错误；同时保留数字字符串兼容（MCP 序列化）

## 输出侧一致性

- [27-sva-heartbeat-anti-silent-pass.md](27-sva-heartbeat-anti-silent-pass.md) — SVA checker 通过时零输出，导致「全部通过」与「checker 没跑」都归一化成 `NO_RESULTS`；加心跳探针使三态可区分
- [28-cocotb-incomplete-report.md](28-cocotb-incomplete-report.md) — cocotb 每记录一条 check 就落盘却无「已跑完」信号，测试崩在第 1 条断言时残缺报告仍判 PASS；加 `completed` 标记 + `COCOTB_INCOMPLETE`
- [29-verilator-missing-summary.md](29-verilator-missing-summary.md) — Verilator 套件在 CHECK 之后崩、无 `PASS:`/`FAIL:` 汇总行时仍被判 PASS；改用「启动套件数 vs 汇总行数」计数校验（逐名匹配会误伤 `PASS: all 20 ...` 这类日志）

## 输入侧一致性

- [30-lint-tb-sv-silent-pass.md](30-lint-tb-sv-silent-pass.md) — `scan_silent_pass` 只被喂 `.sh`/`.py`，SV 测试台从不进入该扫描器，于是 README 头号宣传场景（TB 只 `$display("DONE")` 就结束、无任何判定）返回 `ok=True`；新增 `S4` 规则 + 自审计 `A11`

## 观测能力（波形 / 日志）

- [32-waveform-and-log-capture.md](32-waveform-and-log-capture.md) — 工具箱原先没有波形与日志持久化（只返回 4000 字符 tail）；新增 `run_sim --log-dir/--waveform/--require-waveform` 与 TB 内 `$dumpfile/$dumpvars`。修 4 个「说抓到了其实没有」的缺陷：`wave_path` 未声明编译失败、矛盾组合静默通过、**旧波形冒充本次证据**、**FST 缺 lz4 头时产出后缀 .fst 内容是 VCD 的假文件**；新增诊断码 `E024`/`E025` 与自审计 `A12`
- [33-contract-path-waveform.md](33-contract-path-waveform.md) — **契约路径（文档示例 1）完全没有波形支持**：`--spec-path` 走 tb_emitter 而非 protocol_tb_emitter，产出的 wrapper 一个 `$dumpfile` 都没有。修时连踩 3 个坑：兜底文件名裸输出导致契约 TB **编译不过**、编译期 `` `ifdef WAVE_DUMP `` 是没人 define 的**死宏**（静默失效）、`waveform=True` 脚本不响应 `WAVE=1` 时无声返回空。新增 `W014` 与自审计 `A13`
- [34-blockquote-code-block.md](34-blockquote-code-block.md) — `convert()` 的 blockquote 分支不认 ```` ``` ```` 围栏，引用块内的代码被平铺成一段、整段内容丢失并泄漏 `\x00` 占位符（自检当场报）。围栏解析抽成 `_render_fence`，blockquote 递归调 `convert()`；附确认契约路径 cocotb TB 能真跑通 + cocotb 2.x 的 4 个反直觉配置点

## 契约路径 Makefile 与运行入口

- [35-contract-path-makefile.md](35-contract-path-makefile.md) — 契约路径 cocotb TB **没有运行入口**（只产 4 个源文件，多协议路径却有 run_*.sh）。实测踩坑 cocotb 2.x 的 4 个反直觉配置：`TOPLEVEL_LANG=verilog`（写成 verilator 会**静默跳过**仍报 TESTS=1）、默认 simulator=icarus、`cocotb-config` 属 cocotb_tools、波形须 `VERILATOR_TRACE=1`。生成可直接 `make` 的 Makefile
- [40-run-sim-makefile.md](40-run-sim-makefile.md) — `run_sim` 把契约路径的 Makefile 当 shell 脚本执行（code 127）。加 Makefile 类型检测，返回可操作指引（用 `make -C ...`、波形加 `VERILATOR_TRACE=1`）
- [44-makefile-cocotb-missing.md](44-makefile-cocotb-missing.md) — 契约路径 Makefile **无 cocotb 时报晦涩的路径错误**（`$(shell cocotb-config ...)` 展开成空串 → `include /Makefile.sim`）。加 cocotb 前置检查报明确安装指引
- [45-contract-makefile-incdir.md](45-contract-makefile-incdir.md) — 契约路径 Makefile **不支持 RTL 的 `include 依赖**（make_project 生成的 RTL 首行就是 `include "defs.svh"`）。复制 inc 目录 + Makefile 加条件 `-I$(INCDIR)`

## 文件名与命令注入面

- [39-makefile-name-collision.md](39-makefile-name-collision.md) — RTL 与生成产物**同名时覆盖 wrapper、产物销毁**（`--rtl-file .../reg_slave_axil_wrap.sv`）。修两层：加冲突检测 + 把检测提到写产物之前（旧实现只拦 RTL 复制，拦不住产物已写入）
- [41-makefile-name-collision-rtl.md](41-makefile-name-collision-rtl.md) — RTL 文件名就叫 **`Makefile`**：模板覆盖 RTL，产物损坏（`grep "module dut"` 0 命中）。冲突检测的 `_reserved` 列表漏了 Makefile 本身，补上
- [42-rtl-filename-makefile-injection.md](42-rtl-filename-makefile-injection.md) — `--rtl-file` 文件名含 make 元字符（`$`/空格/引号/分号）**损坏 Makefile 语法 / 命令注入面**（与 eda_campaign 的 SSH/SCP 注入同类）。加文件名白名单 `^[A-Za-z0-9_.-]+$`

## 文档与门禁健壮性

- [31-docs-html-derivation.md](31-docs-html-derivation.md) — 文档 HTML 版定位为派生产物（markdown 唯一事实源）：写派生器时踩到 4 个真实缺陷——`<strong>` 被转义成可见文本、把 `.md` 无脑改 `.html` 造成 3 个死链、页面出现两个 `<h1>`、**f-string 表达式含反斜杠（仅 py3.12+ 允许，本地 3.13 测不出、CI 3.10/3.11 直接 SyntaxError）**；后者暴露语法门只扫 `fpga_toolbox`+`tests` 的覆盖漏洞，一并修好并加本地回归
- [36-non-utf8-rtl.md](36-non-utf8-rtl.md) — **非 UTF-8 RTL 报误导性诊断**：latin-1 注释让 `read_text(utf-8)` 抛 UnicodeDecodeError → 整份 RTL 变空 → 误报 **19 个 E011 端口缺失**（真错因是编码）。新增 `_read_rtl` 容错解码
- [37-mutation-non-utf8-rtl.md](37-mutation-non-utf8-rtl.md) — **变异测试的非 UTF-8 RTL：崩溃 + try/finally 永久破坏风险**。`errors="replace"` 会让 finally 把含 U+FFFD 的文本写回（实测 2 个 0xe9 全丢）—— 变异测试测完把被测 RTL 改坏。改按字节读写
- [38-cli-atfile-non-utf8.md](38-cli-atfile-non-utf8.md) — CLI `@file.json` 非 UTF-8：未捕获 `UnicodeDecodeError` 带完整堆栈直接冒泡给用户。捕获转 `ValueError` + `main()` 输出结构化错误
- [43-git-hook-repo-root.md](43-git-hook-repo-root.md) — `install_git_hook.sh` 从非 scripts/ 目录运行时**误报 Not a git repository**（REPO_ROOT 依赖脚本自身位置）。改用 `git rev-parse --show-toplevel`
- [46-tb-linter-comment-strip-hardening.md](46-tb-linter-comment-strip-hardening.md) — tb_linter 9 处朴素 `split("//")[0]` 加固为 `_strip_comment`（跳过字符串内 `//`）。**诚实标注**：2590 行真实 SV 验证行为等价 0 差异，未证实造成实际错误判定，故非缺陷修复
- [47-boolean-string-coercion.md](47-boolean-string-coercion.md) — **boolean 参数不接受字符串**（`waveform="true"` 被 Invalid type 拒，而 `timeout_sec="300"` 接受）。加字符串兼容（只接受明确布尔字面量，**绝不静默转 False**）
- [48-generated-shell-syntax-audit.md](48-generated-shell-syntax-audit.md) — 自审计新增 **A14**：生成的 shell 脚本语法/`set -u` 运行期校验（真跑 stub 脚本抓 unbound variable）。含 runtime 检查的「假验证」陷阱教训（BIN echo 0 次 = 没跑到 run_one）


## 输入健壮性与注入面封堵（沙箱 / shell / CLI / 依赖解析）

- [49-mutation-target-containment.md](49-mutation-target-containment.md) — `run_mutation` 变异目标无沙箱约束，绝对路径 / `..` / 指向外部的符号链接会被 `write_bytes` 改写工作目录之外的文件（`try/finally` 还原只在正常走到时生效，crash 落在 write 与 restore 之间就永久破坏外部文件）。加 `_resolve_mutation_target`：越界目标跳过不改写，合法目标照常变异并还原
- [50-remote-work-dir-injection.md](50-remote-work-dir-injection.md) — `generate_remote_run_script` 的 `remote_work_dir` 参数被原文插进**远端执行**的脚本，却没有任何白名单（host/tag/local_script_name 都有校验）。实测 `remote_work_dir='x; touch /tmp/PWNED; #'` 让远端 bash 真的执行 touch，使三道白名单形同虚设。加 `_REMOTE_WORK_DIR_RE`（允许目录分隔符与 `~`，拒绝 shell 元字符）
- [51-cli-atfile-structured-error.md](51-cli-atfile-structured-error.md) — CLI 的 `@file` 参数指向不存在的路径时，`read_text` 抛 `FileNotFoundError`（`OSError` 子类）而非 `ValueError`，`main()` 的 `except ValueError` 接不住，堆栈直接冒泡。`_parse_json_or_file` 加 `except OSError` 归一成 `ValueError`，与「非法 UTF-8」「JSON 语法错」同构处理
- [52-resolve-deps-prefixed-include.md](52-resolve-deps-prefixed-include.md) — `resolve_deps` 对带路径前缀的 `` `include "inc/defs.svh"``（headers 按 basename 索引）直接 `hdrs[inc]` 查不到 -> 真实存在的 header 被误报 W101 not-found 并排除在 `all_files` 之外，`copy_sandbox` 拿这份清单就漏拷 header、沙箱 RTL 编译报 Cannot find include file。加 `_resolve_include` 按 basename 两级查找

## 文档交付

- [31-docs-html-derivation.md](31-docs-html-derivation.md) — 文档 HTML 版定位为派生产物（markdown 唯一事实源）：写派生器时踩到 4 个真实缺陷——`<strong>` 被转义成可见文本、把 `.md` 无脑改 `.html` 造成 3 个死链、页面出现两个 `<h1>`、**f-string 表达式含反斜杠（仅 py3.12+ 允许，本地 3.13 测不出、CI 3.10/3.11 直接 SyntaxError）**；后者暴露语法门只扫 `fpga_toolbox`+`tests` 的覆盖漏洞，一并修好并加本地回归

## 合并期缺陷修复（multi-bus-pass 合并审查）

- [10-pure-apb-draft.md](10-pure-apb-draft.md) — `extract_interfaces --emit-draft-yaml` 曾对纯 APB 设备写入空 `axi4_lite` 段，导致自家 validator 报 17 条 E001；仅在有 AXI 接口（或两种都没有）时写出
- [11-sandbox-symlink-escape.md](11-sandbox-symlink-escape.md) — `copy_sandbox` 曾跟随项目内符号链接把项目外文件拷进沙箱（隔离泄漏）；补 `resolve(strict=True)` 真实路径校验
- [12-unknown-instantiation-w102.md](12-unknown-instantiation-w102.md) — `index_repo` 曾静默丢弃仓库外模块的例化；改用 slang CST 的 `HierarchyInstantiation` 精确检出并报 `W102`，`resolve_deps` 透传 `closure_is_complete`
- [13-windows-decoding-and-process-lessons.md](13-windows-decoding-and-process-lessons.md) — Windows cp1252 解码崩溃（`subprocess text=True` → `UnicodeDecodeError` → `AttributeError`），以及三个开发过程错误（stash 致代码漏提交、脆断言、测试依赖 CI 缺失环境）
- [14-multi-bus-tb-port-guard.md](14-multi-bus-tb-port-guard.md) — 多总线 TB 生成器未校验必需端口，对 APB3 风格 DUT 产出 `PINNOTFOUND` 坏 TB 却报 `ok=True`；新增 `REQUIRED_TB_PORTS` 生成前校验
- [15-ddr-hbm-pcie-port-guard.md](15-ddr-hbm-pcie-port-guard.md) — DDR3/DDR4/HBM/PCIe 模板用 `.*` 兜底，DUT 端口名不匹配（如三态 `inout ddr3_dq`）时生成 `Can't find definition of variable` 坏 TB 却报 `ok=True`；补齐 4 协议端口校验
- [16-port-direction-guard.md](16-port-direction-guard.md) — 端口方向反向（如 `pready` 写成 input）时 TB 的握手等待永久挂起，工具却报 `ok=True`；新增 `EXPECTED_TB_DIR` 方向校验
- [17-tb-vectors-scale-with-width.md](17-tb-vectors-scale-with-width.md) — APB 测试向量写死 32 位字面量，窄总线上 `WIDTHDIGITS` 截断、断言值失真；按 `dw`/`aw` 缩放值与十六进制位数，并保留部分写语义
- [18-emitter-robustness-matrix.md](18-emitter-robustness-matrix.md) — 用自动畸形矩阵扫出 3 个缺陷：缺 reset 时硬连 `rst_n`（PINNOTFOUND）、额外端口触发 PINMISSING 阻断构建、多模块文件盲取 `mods[0]`
- [19-wildcard-to-explicit-connections.md](19-wildcard-to-explicit-connections.md) — 6 协议模板的 `.*` 通配在 DUT 有额外端口时报硬错误（`Can't find definition of variable`），上轮的 `lint_off PINMISSING` 无效；改为显式连接，并修 `lint_off` 对多行 module 头不生效
