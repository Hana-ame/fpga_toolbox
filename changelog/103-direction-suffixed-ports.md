# 166 · 方向后缀（`psel_i` / `pready_o`）让协议识别彻底失败

## 现象

一个**完全标准**的 APB 从机，只是用了 `_i`/`_o` 方向后缀：

```systemverilog
module apb_suffix_dut (
  input  logic        psel_i,
  input  logic        penable_i,
  input  logic        pwrite_i,
  input  logic [7:0]  paddr_i,
  input  logic [31:0] pwdata_i,
  input  logic [3:0]  pstrb_i,
  output logic        pready_o,
  output logic [31:0] prdata_o,
  output logic        pslverr_o
);
```

`extract_interfaces` 输出：

```json
{"interfaces": [],
 "out_of_scope_ports": ["psel_i","penable_i","pwrite_i","paddr_i",
                        "pwdata_i","pstrb_i","pready_o","prdata_o","pslverr_o"]}
```

**0 个接口，9 个信号全部落空**。AXI4-Lite 用 `awaddr_o`/`awready_i` 同样：
17 个信号全落空。这是真实 RTL 里极常见的命名风格。

## 根因

所有 `_*_PATS` 的形态都是 `(?:^|_)sig$` —— 信号名必须在**末尾**。

- 前缀问题（`i_psel`）在缺陷 131 修过，因为 `(?:^|_)` 天然能跳过前导下划线
- **后缀没有任何保底**：`psel_i` 的末尾是 `_i`，`psel$` 匹配不到

## 修复

匹配前先剥掉方向后缀再跑模式，但 `port_map` 里存的仍是**原始端口对象**：

```python
_DIR_SUFFIX_RE = re.compile(r"_(?:i|o|in|out|ib|ob)$", re.I)
_mname = p.name
_suf = _DIR_SUFFIX_RE.search(_mname)
if _suf:
    _mname = _mname[:_suf.start()]
for canon, pat in pats:
    m = pat.search(_mname)
    ...
```

`_i`/`_o`/`_in`/`_out`/`_ib`/`_ob` 六种形态都覆盖。

## 验证

| DUT | 修复前 | 修复后 |
|---|---|---|
| APB（`psel_i` … `pslverr_o`） | `interfaces=[]`，9 个越界 | `apb`，9 个信号，越界 0 |
| AXI4-Lite（`awaddr_o` … `rready_o`） | `interfaces=[]`，17 个越界 | `axi4_lite`，17 个信号，越界 0 |
| TB 生成 | — | `ok=true`，TB 连 `.psel_i(psel_i)` 等真实端口名 |
| 7 个参考 DUT | 正常 | **完全不变**（apb 9 / axi4 29 / ahb 9 / ddr3 10 / ddr4 10 / hbm 18 / pcie 11） |

四套测试全绿。

回归测试：`test_direction_suffixed_bus_ports_are_classified`。
