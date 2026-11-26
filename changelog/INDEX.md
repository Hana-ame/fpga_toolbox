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


## 输入健壮性与误报封堵（沙箱 / shell / CLI / 依赖解析 / lint）

- [49-mutation-target-containment.md](49-mutation-target-containment.md) — `run_mutation` 变异目标无沙箱约束，绝对路径 / `..` / 指向外部的符号链接会被 `write_bytes` 改写工作目录之外的文件（`try/finally` 还原只在正常走到时生效，crash 落在 write 与 restore 之间就永久破坏外部文件）。加 `_resolve_mutation_target`：越界目标跳过不改写，合法目标照常变异并还原
- [50-remote-work-dir-injection.md](50-remote-work-dir-injection.md) — `generate_remote_run_script` 的 `remote_work_dir` 参数被原文插进**远端执行**的脚本，却没有任何白名单（host/tag/local_script_name 都有校验）。实测 `remote_work_dir='x; touch /tmp/PWNED; #'` 让远端 bash 真的执行 touch，使三道白名单形同虚设。加 `_REMOTE_WORK_DIR_RE`（允许目录分隔符与 `~`，拒绝 shell 元字符）
- [51-cli-atfile-structured-error.md](51-cli-atfile-structured-error.md) — CLI 的 `@file` 参数指向不存在的路径时，`read_text` 抛 `FileNotFoundError`（`OSError` 子类）而非 `ValueError`，`main()` 的 `except ValueError` 接不住，堆栈直接冒泡。`_parse_json_or_file` 加 `except OSError` 归一成 `ValueError`，与「非法 UTF-8」「JSON 语法错」同构处理
- [52-resolve-deps-prefixed-include.md](52-resolve-deps-prefixed-include.md) — `resolve_deps` 对带路径前缀的 `` `include "inc/defs.svh"``（headers 按 basename 索引）直接 `hdrs[inc]` 查不到 -> 真实存在的 header 被误报 W101 not-found 并排除在 `all_files` 之外，`copy_sandbox` 拿这份清单就漏拷 header、沙箱 RTL 编译报 Cannot find include file。加 `_resolve_include` 按 basename 两级查找
- [53-validator-bad-offset.md](53-validator-bad-offset.md) — 契约 spec 由用户/AI 手写，`offset` 写成非数字（`"abc"` / `4.5` / `"0xZZ"`）时 `_to_int` 里 `int(str(val), 0)` 直接抛 `ValueError` 冒泡成裸堆栈，而同一字段越界输入走的是 E005 诊断 —— 同一字段非法输入一条给诊断一条给崩溃，不一致且无定位。加 `_to_int_or_none`，解析不出按 E005（register）/ E008（illegal_addr）报可定位诊断并 `continue` 避免级联假 E004
- [54-handshake-while-case-insensitive-ready.md](54-handshake-while-case-insensitive-ready.md) — `lint_tb` while 分支的 READY 映射用 `sig.replace("axi.", "")` 不做大小写归一（wait 分支用 `.split(".")[-1].upper()`）。实测同一份代码 `while (!axi.BVALID)` 报 R1 而 `while (!axi.bvalid)` 报 R3 假错误 —— 纯大小写差异，真实的第三方 TB 大量用小写全中。改用与 wait 分支一致的归一
- [55-handshake-r2-r4-case-insensitive.md](55-handshake-r2-r4-case-insensitive.md) — `lint_tb` 的 R4（等 VALID 后立刻拉低 READY）/ R2（if(VALID) 分支内拉低 READY）正则只认大写 `axi\.([A-Z_]+VALID)`，且 drop 检测 `\b{READY}\b` 大小写敏感。实测同一握手 bug `axi.BVALID` 报 R4/R2 而 `axi.bvalid` **静默漏报** —— 真实第三方 TB 大量用小写，死锁风险被 lint 说「没问题」。与缺陷 104 同根（假阴性方向）。加 `re.IGNORECASE` + `.upper()` 映射
- [56-ready-raised-before-case-insensitive.md](56-ready-raised-before-case-insensitive.md) — `_ready_raised_before` 被 R1/R3/R2 三条规则共用，`ready_sig` 是规范大写但源码可能是小写，`\b{ready_sig}\b` 大小写敏感。实测 `bready = 1; wait(axi.bvalid);` 仍报假 R3（READY 已拉高却判「未拉高」）。缺陷 104/105 改了各自位置漏了这个共用 helper。两处搜索加 `re.IGNORECASE`，并验证拉低/无 READY 仍报 R3（不假通过）
- [57-s1-sv-literal-constant-stub.md](57-s1-sv-literal-constant-stub.md) — `lint_tb` S1（验证 helper 只返回常量 = stub）旧正则 `^return [-]?\d+;` 只认纯整数，漏掉 SV 最地道的常量 stub `return 1'b0 / '0 / 8'hFF / 0.0` —— 语义与 `return 0;` 等价（helper 无任何真实判定，静默通过的根源）。假阴性，与 105 同向。扩成覆盖纯整数/位宽字面量/未位宽 '0/'1/实数，保留无分支守卫，表达式 `return a+1` 与含分支函数不误报

- [58-pcie-tb-parameterization.md](58-pcie-tb-parameterization.md) — `emit_testbench(protocol="pcie")` 模板把参数覆盖与网线位宽全部写死，DUT 稍不同形就产出**编译不过**的 TB 却报 `ok=True` + 9 个 suite。三个同根变体实测（Verilator 5.054 全部非零退出）：① 固定配置 DUT 未声明 `GEN/LANES/DW` → 3 条 `Parameter not found`；② 有 `GEN/LANES` 但无 `DW` → `Parameter not found: 'DW'`；③ cfg 端口 8 位 → TB 网线写死 `[3:0]/[5:0]` 触发 `WIDTHEXPAND`（Verilator 默认警告即错误）。修复：参数覆盖只对 DUT **真实声明**的参数发出，网线位宽取 RTL 真实端口位宽，只有 `GEN`+`LANES` 都声明才算 9 配置矩阵（且仅当 `DW` 真映射 `tdata` 位宽时覆盖它），固定配置退化单 suite 且 run 脚本不再传 `-GGEN/-GLANES`。四种 DUT 形态实测全 `rc=0`

- [59-module-name-unsafe-filename.md](59-module-name-unsafe-filename.md) — 契约 `boundary.module_name` 被原样拼进产物文件名与 cocotb Makefile，validator 只查了「非空」。两类实测后果：① `../../EVILMOD` 让 4 个产物里 **3 个写到 `out_dir` 之外**（`Path/..` 静默越界），仍报 `ok=True`；② `m$(shell touch PWNEDMK)_x` 让生成的 Makefile 出现 `$(shell ...)`，用户一 `make` 就**执行任意命令**（实测文件被真实创建）。新增诊断 **E016** 按 SV 标识符 `[A-Za-z_][A-Za-z0-9_$]*` 收口（v1 与 `apb-regfile-spec/v2` 两入口共用同一 validator，一处覆盖两路），tb_emitter 再在写产物前做一次纵深防御。`$` 在 SV 标识符里合法故不误伤 `a$b`

- [60-ddr-hbm-template-widths.md](60-ddr-hbm-template-widths.md) — DDR3/DDR4/HBM 的 TB 模板把端口位宽、数据常量、`$display` 格式宽度全部写死，而 Verilator **默认警告即错误**，DUT 位宽不符时生成的 TB 编译不过却报 `ok=True`（DUT 一点没验到）。三个协议实测：DDR3 写死 BA=3 位/常量 `8'hBEEF` → 4 条 WIDTHDIGITS + 1 条 WIDTHTRUNC；DDR4 写死 adr=17/dq=64/dm_dbi_n=8 位 → 4 条 WIDTHTRUNC；HBM 写死 data=256/ID=6/addr=28 位 → 64 位与 512 位 DUT 各 2/6 条 WIDTHEXPAND。新增 `_pw()` 从 `port_details` 取 RTL 真实位宽（三级回退），三个模板全部跟随，数据常量按位宽掩码、格式宽度同步。顺带修掉一个按十六进制字符截断数据图案的 off-by-one（dw=256 时低 64 位错成 `0xF123456789ABCDEF`）。6 个参考 DUT 全部 0 宽度告警 rc=0

- [61-axi4-template-id-len-widths.md](61-axi4-template-id-len-widths.md) — AXI4 `ID` 宽度是实现自选（常见 4/8/16 位）、`LEN` 可窄到 4 位，而 TB 模板把 `awid/bid/arid/rid` 写死 `[3:0]`、`awlen/arlen` 写死 `[7:0]`。Verilator 默认警告即错误，8 位 ID DUT → 4 条 WIDTHEXPAND、4 位 len DUT → 2 条 WIDTHTRUNC，构建失败却返回 `ok=True`。修复：ID/len/size/burst/resp 全部按 `_pw()` 取真实端口位宽，**测试向量同步缩放**（ID 掩码、地址/数据掩码、十六进制格式宽度、beats 收紧到 2^len_w），并**保留原有 6 个检查含 SLVERR 语义**（参数化期间一度砍掉用例属覆盖回退，已恢复）

- [62-power-on-reset-names.md](62-power-on-reset-names.md) — 复位名识别只认 `rst/reset` 家族，**完全不含 `por` / `poreset` / `power_on_reset` / `pon`**。而仓库自带的 6 协议集成 DUT `soc_omni_bus_hub.sv` 唯一复位就叫 `por_n`（`if (!por_n)`）—— 后果不是少一条 warning，而是 `emit_testbench` 的**每条协议模板都硬连复位脚**，检测不到就拒绝生成，于是 pcie/ddr4/axi4/apb 四条路径全被挡掉，报错还误导用户「加一个 rst/rst_n/aresetn/hresetn」。修复 `_RST_RE` + `_reset_active_low` 覆盖 POR 家族（含 `n` 前缀与 `_n/n/b` 后缀），并保留原有 `[ahps]?` 分支；锚定+后缀严格可选，`port`/`ponder`/`por_ready`/`por_cnt` 不误伤。16 个极性用例 0 mismatch，7 参考 DUT 回归全绿

- [63-code-review-batch-fixes.md](63-code-review-batch-fixes.md) — v0.5.0 外部代码评审的批量修复（14 项，逐条复现后确认）。**最严重**：`run_mutation` 无 baseline 预检，`test_cmd` 坏掉时每个变异都非零退出 → **假 100% 变异得分**，且 `ERROR` 被 `survived = total - killed` 算成存活。另有 `index_repo` 用展开源码覆写子模块的 `instantiated_modules`/`includes`（实测 datapath 被错标 include 了 datapath.v）+ O(F×M) 次重复 tokenize；`lint_tb` 的 S1 写在 `scan_handshake` 里（规则路由错位）、`s_apb`/`m_apb` 漏检、R2/R4 写死 `axi.` 前缀、S4 被 `"no error(s)"` 字符串绕过；`call_tool` 的 Optional enum 拒绝 None / required 接受 None / number 接受 bool / 原地改写调用方 dict；`sv_parser` 把 int/byte/shortint/longint 当 1 位、`$clog2(1)` 返回 1（应为 0）；`_to_int` 对 17 个字段仍抛裸 ValueError；`eda_campaign` 的 dev_shm 路径不一致 + PT 网表编码 + 转义实例名残留漏检；生成的 cocotb 用 `json.loads(r"""...""")` 会因描述含三引号而 SyntaxError、`WAVE_FILE` 未转义、复位极性靠脆弱 replace（模板一变就静默反相）。新增 4 个回归测试

- [64-suite-identity-and-empty-mutation.md](64-suite-identity-and-empty-mutation.md) — 又两处同根静默假通过：「只看某条证据存在，不看它是否属于该对象/是否覆盖完整边界」。① `verify_suite_identities` 对任意含 `\bPASS\b` 的行判该套件 PASS —— 套件打印 `[CHECK] ... status=PASS` 后在 `$finish` 前崩溃（断言挂/段错误/超时被杀）仍被判 PASS；② `result_normalizer` 的完成度只比**汇总行总数** vs 启动套件数，A 重复两条 `PASS:` + B 崩溃一条没有时仍放行（实测 `overall_status=PASS`）。修复：按套件边界归集各自的结束汇总行，启动过却没有自己汇总行的套件判 FAIL，`VERILATOR_NO_SUMMARY` 点名具体套件。真实 apb(1)/pcie(9) 日志不误伤。另修 `old: ""` 被当成「匹配任意位置」：`replace(b"", new, 1)` 把 new 插到文件开头，变异「成功」却无语义 —— 现显式拒绝且不动文件

- [65-normalizer-malformed-results.md](65-normalizer-malformed-results.md) — `normalize_sim_result` 违背自己的 docstring 承诺（「无法识别的输入报错而非静默 NO_RESULTS」）三处：① `checks` 混入非对象 → 裸 `AttributeError: 'str' object has no attribute 'get'`（无字段名无索引）；② `tests` 混入非对象被 `isinstance(t, dict)` **静默过滤** → 零检查 + `overall_status=NO_RESULTS` + **`ok=True`**，调用方看到「成功、零检查」而真相是文件无法解析（本轮最危险）；③ JUnit 里无名 `<testcase/>` 被判 PASS —— 比真正空的 XML（正确判 NO_RESULTS）还宽松，零证据反而成了通过。另把写死的 `engine="Verilator 5.054"` 改成从日志抓真实版本

- [66-visual-report-data-consistency.md](66-visual-report-data-consistency.md) — `render_visual_report` 的自检只查 HTML 标签配平与渲染行数（W050/W051），**完全不看 report 数据是否自相矛盾**：`summary.passed=2` 而 checks 全 FAIL、`overall_status=PASS` 却没有任何检查通过等四种情形，全部 `consistency_ok=True` + 零诊断被原样印进 HTML —— 读者面对「通过率 100%」和满屏 FAIL 行无从判断。新增 **W052**（summary 计数与实际不符）与 **W053**（overall_status 与行级结论矛盾）。另修六种畸形 report 字段抛裸 `AttributeError: 'str' object has no attribute 'get'`（无字段名无索引），改为渲染前统一结构校验 + 可定位错误（与上轮 normalize_sim_result 同口径）；**另暴露流程盲区**：新增诊断码必须在 `docs/FEATURES.md` 登记，但该检查只在 CI 的 `docs_check.py`（D13）里跑，本地 `--fast` 的 Layer 5 不查 —— 本地全绿、CI 四个 test job 全红。已把 D13 前移到本地门禁（与 CI 同口径：字符串字面量声明 + README/docs 范围覆盖）

- [67-self-audit-covers-new-guardrails.md](67-self-audit-covers-new-guardrails.md) — `audit_harness` 是「护栏的护栏」，但最近几轮修的缺陷（变异 baseline / 套件身份 / 报告自洽 / 畸形 results）**都不在矩阵里** —— 这些护栏将来被回退，自审计本身发现不了。新增 **A15–A18**（矩阵 14 → 18 项，18/18 通过）。更关键的是加完 A18 后做回退验证时发现：探针在被测护栏坏掉时会**抛异常把整个 `audit_harness` 打崩**，调用方拿到 traceback 而不是「A18 失败」—— 而自审计的全部价值就是「护栏被回退时必须报 FAIL」。加 `_audit_call` 包装器把任意异常折算成 `(None, "Type: msg")`，A15/A17/A18 全部改用；两次回退验证现在都干净报 FAIL 并指名异常。顺带补全 FEATURES.md 能力表里一直缺失的 A11–A14

- [68-ddr-required-ports-dq-in.md](68-ddr-required-ports-dq-in.md) — `emit_testbench` 的生成前端口校验表 `REQUIRED_TB_PORTS` 里 ddr3/ddr4 **只列了 `dq_out`、漏了 `dq_in`**，而模板对 DUT 是双向语义（dq_in 由 TB 驱动写数据、dq_out 由 DUT 驱动读数据）。于是一个没有 `dq_in` 端口的 DDR3 DUT 通过了校验，产出的 TB 硬连 `.ddr3_dq_in(...)` → `%Error-PINNOTFOUND: Pin not found: 'ddr3_dq_in'` 而工具报 `ok=True`（与缺陷 14/15 同类）。补表后拒绝并点名端口，参考 DUT 仍全绿。**另加自审计 A19**：从模板源码抽「同名成对」的 `.foo(foo)` 连线与必需表做差集，以后模板加连线忘同步表格会被当场抓到（已验证生效；实现时第一版只匹配 `.name(` 前缀导致七协议全假 gap，改成同名匹配才正确 —— 探针自身也要防误报）

- [69-strobe-width-follows-port.md](69-strobe-width-follows-port.md) — APB `pstrb` / AXI4 `wstrb` 的位宽被写成 `dw // 8` **推导值**，而规范并不要求 strobe 位宽 == DATA/8。实测一个合法 APB 从设备（16 位 `pwdata` + 4 位 `pstrb`，`extract_interfaces` 也如实报告 `pstrb.width=4`）生成的 TB 是 `logic [1:0] pstrb`，连线 `%Warning-WIDTHEXPAND: 'pstrb' expects 4 bits ... generates 2 bits` → Verilator 警告即错误、构建失败，而工具报 `ok=True`。修复：strobe 位宽改走 `_pw()` 取真实端口（`dw//8` 仅兜底），并把 APB/AHB 的 `aw`/`dw` 也统一到 `_pw`（此前只有 AXI4/DDR/HBM 走），七协议位宽来源口径一致：**端口真实位宽 > classifier 推导 > 标准默认**。A19 扩展为同时断言 strobe 必须经 `_pw` 取得

- [70-ahb-trans-size-resp-widths.md](70-ahb-trans-size-resp-widths.md) — AHB 模板把 `htrans`/`hsize`/`hresp` 位宽写死成规范默认值（2/3/1 位），而真实 IP 常扩展它们（如 2 位 HRESP 表达 SPLIT/RETRY）。DUT 用 2 位 HRESP 时 TB 声明的是 1 位标量 → `WIDTHEXPAND` → Verilator 警告即错误、构建失败而工具报 `ok=True`。修复 `_pw` 取真实位宽 + 声明/常量/task 形参**调用点字面量**全部同步（`1'b0` 在 2 位形参上同样 EXPAND）+ `!exp_err` 改 `exp_err == 0`（LOGNOT 只接受 1 位）。记录一个教训：第一版只改声明和形参，TB 侧仍有 7 条告警（调用点没改），第二版又漏了文本重复的 `AHB_003` —— **改声明不改调用点的漏改只有实际编译才能发现**

- [71-ddr-control-widths.md](71-ddr-control-widths.md) — DDR3/DDR4 模板把整组控制信号塞进同一个 `logic` 标量声明（`ddr3_cke = 1, ddr3_cs_n = 1, ...`），但**多 rank 控制器用多位 CKE/CS_n**（每 rank 一位，JEDEC 标准做法）。DUT 用 2 位 `ddr3_cke` 时 TB 侧 2 条 WIDTHEXPAND → Verilator 警告即错误、构建失败而工具报 `ok=True`。修复：DDR3 的 cke/cs_n/ras_n/cas_n/we_n/rd_valid 与 DDR4 的 act_n/cs_n/odt/rd_valid 全部 `_pw()` 取真实位宽并独立声明；驱动改成按位赋值（多位信号无法用 `{ras,cas,we} = cmd` 拼接整体赋值，否则 WIDTHTRUNC）与 `'0`/`'1` 全位赋值。两台变体 TB 侧告警清零，6 参考 DUT 全绿

- [72-data-destruction-and-cocotb-json.md](72-data-destruction-and-cocotb-json.md) — 三处**工具报 `ok=True` 的同时毁掉用户东西**的缺陷，优先级最高。① 生成的 cocotb 用 `json.loads(repr(...))`：repr 是 Python 字面量（单引号）不是合法 JSON，**只要 spec 里有任何寄存器，测试一导入就 JSONDecodeError 必崩**（这是我上一轮修「描述含三引号」时引入的回归 —— 修 A 引入 B；正解是 `repr(json.dumps(...))`，repr 只负责安全嵌入、JSON 合法性由 json.dumps 保证）；② `emit_testbench` 无条件 `rmtree(od/"inc")`：`out_dir` 恰为 RTL 目录时把**用户源码 inc/ 目录永久删除**（实测 `inc/defs.svh` 消失、工具仍报 ok）；③ `emit_protocol_testbench` 不检查产物与输入同名：用户把上次的 `tb_*.sv` 当 RTL 传回时**静默覆盖用户 RTL**。均已修复并加断言

- [73-security-hardening.md](73-security-hardening.md) — review 第二类（安全漏洞）逐条复现确认 4 项。① `generate_remote_run_script` 四个白名单用 `$` 结尾，Python 的 `$` 也匹配末尾换行**之前**，于是 `host='host1\\n'` 能通过校验、生成的 `HOST="host1` + 换行把字符串字面量截断成命令注入（实测纯末尾换行才是真入口，带内容的换行被字符类挡下容易误判为安全）；② `remote_work_dir` 只查字符不查语义，`'/'`→`WORK="//t"`、`'~/work/..'`→`~/work/../t`，而脚本末尾是 `rm -rf $WORK`，会删预期范围外的目录；③ `find_verilator()` 把 `/tmp/verilator/bin/verilator`（`/tmp` 是 1777）排在 `shutil.which` **之前**，共享服务器上可被劫持；④ `extra_meta` 未转义插入 HTML（`<img src=x onerror=...>` 原样落盘）构成存储型 XSS，且 `mutation_score` 非数字时渲染器抛 TypeError 整个失败。全部修复并加断言

- [74-silent-pass-gates.md](74-silent-pass-gates.md) — review 第三类（静默误判）确认 4 组共 9 种绕过。① `parse_uvm_log` 只读 `UVM_ERROR` **完全忽略 `UVM_FATAL`**（`UVM_FATAL : 3` 仍判 passed）；② 未传 `expected_frames` 时 `target_frames=frames_obs`，`delivered=0`+`frames=0` 满足 `0==0` → **什么都没跑的仿真通过**；③ `COCOTB_INCOMPLETE` 门禁用 `is False`，`"false"` / `0` / `tests` 分支（整条丢弃 completed）三种形态全绕过；④ `_from_xml` 不校验根标签、不读 suite 级 failures/errors、无名检查用 `if not name`（纯空白是 truthy）—— 非 JUnit XML 得到 `ok=True+NO_RESULTS`、suite 级 `<error>`/`failures="3"`/`name="   "` 全判 PASS。另记一个方法论教训：修 ④ 时第一版只查 suite 内部，而 fixture 把 `<errors>` 放在 `<testsuites>` 下，测出仍 PASS —— 用例构造位置决定能否发现漏洞

- [75-residual-and-rerun-gates.md](75-residual-and-rerun-gates.md) — 三处「判定信号选错」（用子串存在代替状态一致）。① `check_residual_mutations` 用 `m["new"] in txt` 判残留，**删除型变异（`new=""`）时 `"" in txt` 恒真** → 任何 old 已消失的文件都误报 E030，合法的删除型变异永远无法运行；② 变异主循环 `replace(...,1)` 只改第一处，old 多次出现时「第一处已改、第二处仍是 old」→ `old in txt` 为真 → **漏报真实混合态**（实测返回空残留列表）；③ `verify_suite_identities` 用 `setdefault`，同名 suite **重跑**不重置状态 → 第一次 PASS、第二次被截断时沿用旧 PASS 与 summaries，判 `all_verified=True`。修复：删除型跳过残留检查（与合法变异态不可区分，还原靠 try/finally）、增加「old 与 new 并存」混合态判据、suite 头每次重置并清空该 suite 汇总记录。真实 9-suite pcie 日志不误伤

- [76-sv-parser-tokenizer-header.md](76-sv-parser-tokenizer-header.md) — review 第四类（SV 解析核心）确认 5 项，共同后果是 **builtin 后端（slang 不可用时的唯一后端）系统性给出错误位宽/参数**。① 分词器 `IDENT` 不允许 `$` 开头且 `$` 不在 `SYM` 里 → **`$clog2(DEPTH)` 被丢成 `clog2(DEPTH)`、宽度退化 1**（slang 正确给 4）；② `<<`/`>>` 被拆成单字符，`_parse_params` 用空格重拼成 `< <` → `parameter int W = 1 << 3` 解析失败、params 空、端口退化 1；③ `module automatic foo` 把生命周期修饰符当模块名（模块名变成 `automatic`、端口全空）；④ `module foo import pkg::*; #(...)` 遇 import 即跳出，**参数与端口全丢**；⑤ 多维 packed `[1:0][7:0]` 总位宽应为乘积 16，builtin 给 8、slang 给 2（**两个后端都错**）。全部修复后两后端结果一致

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
- [77 · 方向前缀拆组与 AHB HREADY/HREADYOUT 键冲突](77-iface-prefix-ahb-ready-split.md)
- [78 · resolve_deps 丢失 header 之间的传递 include](78-resolve-deps-transitive-headers.md)
- [79 · 被 include 的 .sv 被当成独立编译单元](79-included-source-not-compilation-unit.md)
- [80 · 剔除被 include 的源文件时保留了独立可编译的目标](80-rtl-files-keep-independent-modules.md)
- [81 · 不带 timescale 的 DUT 会让 TB 构建失败](81-tb-timescale-for-user-rtl.md)
- [82 · typedef 端口位宽未解析时生成非法或致败代码](82-typedef-port-width-placeholders.md)
- [83 · interface 打包的总线端口被误报成 DUT 缺端口](83-unexpanded-sv-interface-ports.md)
- [84 · lint_tb 目录模式扫到 0 个文件却报 clean](84-lint-tb-zero-match-not-clean.md)
- [85 · 归一化报告忽略 %Error 且零核对被当作通过](85-normalizer-verilator-errors-and-empty-verification.md)
- [86 · schema 数值边界缺失，负/零超时被当成仿真超时](86-numeric-bounds-at-entry.md)
- [87 · eda_campaign 的 tag=.. 让清理临时目录变成删除 /dev](87-eda-tag-dot-escape.md)
- [88 · import pkg::* 依赖的 package 文件不在依赖闭包里](88-package-imports-in-closure.md)
- [89 · 协议没识别到 vs 识别到了但没有 TB 模板](89-detected-but-unsupported-protocol.md)
- [90 · 主设备配从设备模板时错误信息不指向角色反了](90-master-role-direction-mismatch.md)
- [91 · regfile spec 类型错误崩栈、数值错误被静默吞掉](91-regfile-spec-type-value-validation.md)
- [92 · cocotb JSON 里无名结果项被当成通过证据](92-json-unnamed-testcase.md)
- [93 · CHECK 明细行被静默丢弃又会篡改套件结论](93-check-lines-and-suite-summary.md)
- [94 · mutation spec 里的非对象元素被静默丢弃](94-mutation-spec-non-object-entries.md)
