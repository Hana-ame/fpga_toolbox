# Changelog — 契约 `module_name` 直通产物文件名与 Makefile（路径穿越 + 命令注入）

## 背景

契约 spec（YAML/JSON，用户或 AI 手写）里 `boundary.module_name` 只是一个"名字"，
但它被原样插进：

- 每个产物文件名：`<mod>_axil_wrap.sv` / `<mod>_axil_sva_checker.sv` /
  `<mod>_axil_bind.sv` / `test_<mod>_axil.py` / `<mod>_apb_slave_model.sv`
- 生成的 cocotb `Makefile`：`MODULE = test_<mod>_axil`、`TOPLEVEL = <mod>_axil_wrap`
- 生成的 SV 源码本身：`module <mod>_axil_wrap`、`bind <mod> ...`

`build_and_validate_ir` 只检查了「非空」，`_normalize_regfile_spec_v2`
（`apb-regfile-spec/v2` 入口）则是 `str(data.get("module", ...))` 直接透传。

## 为什么是真缺陷（两类，均实测复现）

### 1. 路径穿越：产物被写到 `out_dir` 之外

`module_name: ../../EVILMOD`，`out_dir=/tmp/<t>/a/b/out`：

```
ok=True
  unit_isolation_wrapper  /tmp/<t>/a/EVILMOD_axil_wrap.sv          <- 祖父目录
  sva_protocol_checker    /tmp/<t>/a/EVILMOD_axil_sva_checker.sv   <- 祖父目录
  in_situ_bind_file       /tmp/<t>/a/EVILMOD_axil_bind.sv          <- 祖父目录
  cocotb_testbench        /tmp/<t>/a/b/out/EVILMOD_axil.py
```

4 个产物里 3 个落到 `out_dir` **之外**，工具仍报 `ok=True`。
`Path(out_dir) / f"{mod}_axil_wrap.sv"` 对 `..` 不做任何归一化，越界是静默的。
写文件的位置实际由**调用方数据**决定，而 `out_dir` 是调用方唯一以为在控制的边界。

### 2. Makefile 命令注入（更严重）

`module_name: m$(shell touch PWNEDMK)_x` → 生成的 Makefile：

```make
MODULE = test_m$(shell touch PWNEDMK)_x_axil
TOPLEVEL = m$(shell touch PWNEDMK)_x_axil_wrap
```

用户执行 `make` 即执行 `touch PWNEDMK`。实测文件被真实创建（`make` 用假的
`cocotb-config` 跑通，`PWNEDMK` 出现在工作目录里，`MODULE` 展开成 `test_m_x_axil`）。
同理可注入任意命令（`$(shell rm -rf ...)`、反引号等）。这不是"文件名怪一点"，
是**契约里的一个字符串变成了命令执行**。

## 修复

- `fpga_toolbox/core/validator.py`：新增 `_SV_IDENT_RE = ^[A-Za-z_][A-Za-z0-9_$]*$`
  与新诊断 **E016**。`module_name` 非空但不匹配 → ERROR E016，文案直接说明
  它会被插进文件名与 Makefile，因此拒绝路径分隔符 / `..` / 空白 / 点号 /
  shell 与 Make 元字符。合法 SV 标识符的选择口径是"SV 本身允许什么"——
  `$` 在 SV 标识符里合法（`a$b` 可以），因此不误伤。
- 覆盖面：`build_and_validate_ir` 是 v1 与 `apb-regfile-spec/v2` 两个入口的
  共同下游，E016 一处即覆盖两个入口（实测 v2 的 `module: ../../V2EVIL` 同样被拒）。
- `fpga_toolbox/tb_emitter.py`：写**任何产物之前**按同一正则再核一次
  （`_MODULE_NAME_RE`），返回结构化 `err_result`。这是纵深防御：`build_and_validate_ir`
  也可能被直接调用（测试/内部复用），且旧行为在穿越时抛的是裸的
  `FileNotFoundError: .../test_../x_axil.py`（完全没指向真因）。

## 验证

回归 `tests/test_full_coverage.py::test_module_name_not_used_as_unsafe_filename`：

1. 3 个穿越形式（`../../EVILMOD` / `x/../../../../tmp/PWNED109` / `../esc`）
   全部 `ok=False` + `E016`，且 `out_dir` **外**零文件。
2. `m$(shell touch PWNEDMK)_x` 被拒，无 Makefile 生成、无注入标记文件。
3. 4 个合法名字（`reg_slave` / `apb_dut` / `M1_x` / `a$b`）仍正常生成，
   `TOPLEVEL = <mod>_axil_wrap` 正确 —— 确认没有过度收紧。
4. `apb-regfile-spec/v2` 入口的 `module: ../../V2EVIL` 同样被拒。

完整验证：4 个测试套件全过 / ruff 0 错（0.17.6 本地非系统）/
`verify_invariants --fast` 5 层全过 / self-audit 14/14 / docs HTML 同步。
