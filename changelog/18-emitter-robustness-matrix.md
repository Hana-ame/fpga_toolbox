# Changelog — 用自动畸形矩阵扫出 3 个 TB 生成器缺陷

前四轮（39/41/42/43）都是「测一个点、修一个点」。本轮改成写一个自动扫描器：拿 8 种畸形 RTL × APB 协议跑一遍，对每个 `emit ok=True` 的产物再跑 `verilator --lint-only -Wall`，把「工具报成功但产物坏」的用例一下全抓出来。

扫描器本身先踩了两个坑（值得记，否则结论全错）：

1. **产物文件名猜错**。生成的 TB 叫 `tb_<module>_<proto>.sv`（用模块名，不是用例名），我按用例名去找文件，所有用例都报 `Cannot find file containing module`——8/8 "BAD" 的假象。
2. **把 warning 当 error**。`-Wall` 下 Verilator 只要有任何 warning 就 `Exiting due to N warning(s)` 且 rc=1。我一开始把 rc!=0 当"硬错误"，连 baseline 都被判 BAD。必须只数以 `%Error` 开头、且不是 `Exiting due to` 的行。

修好扫描器后，8 个用例里 5 个本就正常、3 个是真缺陷：

## 缺陷 44 — 缺 reset 端口时硬连不存在的 `rst_n`

`extract_interfaces` 已经正确检测到 `reset: None` 并报 `W011`（"No reset port detected; defaulting to aresetn..."），但 emitter 无视它：

```python
rst = str(((m.get("reset") or {}).get("port")) or "rst_n")   # 回退到猜的名字
```

于是 DUT 没有 reset 端口时生成 `.rst_n(rst_n)`：

```
%Error-PINNOTFOUND: tb_m_apb.sv:12:17: Pin not found: 'rst_n'
```

工具仍报 `ok=True`。修复：clock/reset 任一缺失即 fail-fast，并说明模板必须驱动时钟、硬连 reset 会 PINNOTFOUND。

顺带把 clock 也纳入（同样的回退问题）。

## 缺陷 45 — DUT 带额外端口时 `PINMISSING` 阻断构建

模板用 `.*` 通配连接，只覆盖 TB 里声明了同名变量的端口。DUT 若带有 TB 不关心的额外端口（`dbg` / `test_mode` / `scan_en`，真实设计里极常见），Verilator 报：

```
%Warning-PINMISSING: Instance has missing pin: 'dbg'
%Error: Exiting due to 1 warning(s)
```

注意这是 **warning 但阻断构建**——生成的 `run_*.sh` 没加 `-Wno-PINMISSING`，Verilator 默认把 warning 当错误。修复：TB 里插 `/* verilator lint_off PINMISSING */`，`run_*.sh` 再补 `-Wno-PINMISSING` 双保险。

## 缺陷 46 — 多模块文件盲取 `mods[0]`

一个 `.sv` 文件里有多个 module 时（helper / package wrapper / 顶层），旧实现固定 `m = mods[0]`。若第一个恰好是 helper，就报：

```
Protocol 'apb' not found in helper
```

而文件里明明有带 APB 接口的模块。修复：遍历所有模块，挑**含目标协议接口**的那个；`auto` 时挑第一个含任一支持协议的。报错信息也改为列出全部模块及其协议，便于定位。

## 修复后扫描结果

| 用例 | 修复前 | 修复后 |
|---|---|---|
| baseline | — | OK |
| package + typedef | — | OK |
| 多模块文件 | REJ（选错模块）| OK（正确选中 apb_dut）|
| `#(parameter AW,DW)` 参数化端口 | — | OK |
| pready 常量驱动 | — | OK |
| 64 位地址总线 | — | OK |
| **缺 reset 端口** | ok=True + 产物 `PINNOTFOUND` | **正确拒绝并说明原因** |
| **额外 inout 端口** | ok=True + 产物 `PINMISSING` 阻断 | **OK（编译通过、可实跑）** |

## 验证

- 官方靶场 7 协议生成全 OK，实跑 APB 8/8、AXI4 6/6、AHB 6/6、DDR3/DDR4/HBM PASS——三个修复均无误伤。
- 新增 6 项回归检查（缺 reset 拒绝、多模块选对、PINMISSING 抑制、run 脚本 flag）。
- regression / fusion / audit 9-9 / docs_check D1–D13 / ruff / py3.10 全绿。
