# 107 · 架构重构：标准接口定义 + 绑定层（取代逐协议正则补丁）

## 为什么重构

缺陷 131（`i_`/`o_` 前缀把 APB 拆成两组）、166（`psel_i`/`pready_o` 方向后缀
让协议识别**完全失效**）、170（带后缀的 full-AXI4 burst 信号漏绑定）是**同一个
病根的三次发作**：

```python
_AXIL_PATS = (
    ("awaddr", re.compile(r"(?:^|_)aw_?addr$", re.I)),
    ("wvalid", re.compile(r"(?:^|_)w_?(?:valid|vld)$", re.I)),
    ...
)
for canon, pat in pats:
    m = pat.search(port.name)          # 名字必须恰好以 canonical 形态结尾
```

命名风格的空间是**无界的**：`psel` / `p_sel` / `psel_i` / `psel_in` /
`psel_ib` / `s0_psel` / `s_apb_psel` / `PSel` / `sAwAddr` / `p_sel_o` …
每遇到一种就得新增/修改一条正则，而且正则之间互相抢匹配（缺 `$` 边界时会
误吞）。这是**架构问题**，不是清单不够长。

## 新架构

### 1. 标准接口（`fpga_toolbox/canonical_iface.py`）

每个协议**声明一次**，只描述协议语义，不描述拼写：

```python
CanonSignal(name="pwdata", direction="in", width_role="data", required=True)
CanonSignal(name="pstrb",  direction="in", width_role="strb", required=False)
```

`CanonInterface` 还记录：`min_signals`（最低特征信号数）、`role_probes`
（判定 master/slave 的探针）、`burst_probes`（axi4_lite → axi4 的升级判据）、
`required_tokens` / `forbidden_tokens`（`ddr3_` / `hbm_` / `pcie_` 这类强制
前缀，防止 HBM 与 AXI4 互相抢端口）、`group_neutral_tokens`（PCIe 的
`rx`/`tx` 方向词不能拆组）。

覆盖 10 个协议：apb / axi4_lite / ahb_lite / ddr3 / ddr4 / hbm / pcie /
spi / ethernet / axi4_stream。

### 2. 绑定层

把任意 DUT 端口名映射到标准信号，**匹配的是 token 后缀拼接**而非字面正则：

```
s0_p_sel_i  -> ["s0","p","sel","i"]  -> 后缀 "p"+"sel" == "psel" -> 绑定 psel
                                        剩余前缀 ["s0"]（"i" 是方向词，剔除）
PSlvErr     -> ["p","slv","err"]     -> 拼接 "pslverr" == "pslverr"
s_apb_psel  -> ["s","apb","psel"]    -> 前缀 ["s","apb"] -> 组 "s_apb"
pcie_rx_tvalid -> ["pcie","rx","tvalid"] -> 绑定 rx_tvalid，"rx" 是方向词
```

- `tokenize_port_name()`：snake_case / camelCase / PascalCase / 数字通道/
  单位数前缀全部覆盖
- `_match_suffix()`：按 **token 拼接**比较，因此 `p_sel` / `psel` / `PSel`
  等价，`aw_addr` / `awaddr` 等价
- `_filter_group_tokens()`：方向词（`i`/`o`/`in`/`out`）与协议内部方向词
  （PCIe `rx`/`tx`）**永不进入通道前缀**，避免把一个接口拆成两半
- `_preference()`：`paddr` 精确名优先于 `p_addr_o` 这类别名，防止野端口
  劫持 canonical
- `bound.ambiguous`：多个端口绑到同一 canonical 时，记录被拒绝者与原因
  （错误映射**可见**而非静默丢弃）

### 3. 接线（`classify_module`）

TB 仍只面向 canonical 信号生成，通过 `port_map` 连到真实端口名。返回体新增
`binding.matched`（每个 canonical 被什么拼写匹配到）与
`binding.ambiguous`。

## 效果

| DUT 命名风格 | 旧实现 | 新实现 |
|---|---|---|
| `i_psel` / `o_pready` | 拆成 "i"/"o" 两组（131） | 单组 9 信号 |
| `psel_i` / `pready_o` | **0 个接口**（166） | 单组 9 信号 |
| `PSel` / `PSlvErr` | 部分失配 | 9 信号 |
| `s_apb_psel` | 组名 `s_apb` ✓ | 组名 `s_apb` ✓ |
| `awlen_o` / `rlast_i`（full AXI4） | 降级成 axi4_lite（170） | 正确 `axi4` + 29 信号 |
| `pcie_rx_tvalid` / `pcie_tx_tdata` | 分成两半 | 单组 11 信号 |

7 个参考 DUT 全部保持正确识别（apb 9 / axi4 29 / ahb_lite 9 / ddr3 10 /
ddr4 11 / hbm 18 / pcie 11），且 PCIe 的越界端口从 3 降到 **0**。

omni_hub（6 协议综合 DUT）正确抽出 pcie/ddr4/axi4/apb/spi/ethernet。

## 端到端验证

7 个协议的 TB 全部重新生成并跑 Verilator：

```
apb rc=0 warn=0 pass=1    axi4 rc=0 warn=0 pass=1
ahb_lite rc=0 warn=0 pass=1    ddr3 rc=0 warn=0 pass=1
ddr4 rc=0 warn=0 pass=1    hbm rc=0 warn=0 pass=1
pcie rc=0 warn=0 pass=9   （9 个 Gen×Lane suite）
```

`verify_invariants.py --full` 六层全过，函数覆盖 **166/166**（新模块 100%）；
`audit_harness` 19/19；四套测试全绿。
