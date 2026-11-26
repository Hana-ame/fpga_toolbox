# 176–178 · 绑定顺序手工维护、DDR5 未声明、新架构缺文档

## 176 · `_BIND_ORDER` 是与 canonical 声明并列的第二份手工清单

`classify_module` 里原本是：

```python
_BIND_ORDER = ("hbm", "pcie", "ddr4", "ddr3", "ahb_lite", "axi4_lite",
               "axi4_stream", "apb", "spi", "ethernet")   # 函数内局部字面量
```

而协议语义又声明在 `CANONICAL_INTERFACES`。**两份手工清单必须一致**：往
canonical 里加协议却忘了加进 `_BIND_ORDER`，协议就永远检测不到（静默失效）。

修复：`canonical_iface.bind_order()` 派生顺序——

```python
known = [p for p in _BIND_PRIORITY if p in CANONICAL_INTERFACES]
rest  = sorted(p for p in CANONICAL_INTERFACES if p not in known)
return tuple(known + rest)
```

优先级仍需人工指定（不能按字母序：HBM/PCIe 的 AXI 风格通道会被 `axi4_lite`
先抢走），但**新增协议不会再被漏掉**：只要声明就会追加到末尾参与绑定。

## 177 · 新架构（标准接口 + 绑定层）完全没有文档

`README.md` 与 `docs/*.md` 里搜不到 `canonical_iface` 的任何说明。用户看不出：

- 为什么不需要为每种命名写正则
- `i_psel` / `psel_i` / `p_sel` / `s_apb_psel` / `PSlvErr` 分别怎么绑定
- `split_port_map`（DDR 的 `dq_in`/`dq_out` 拆分）是什么
- `binding.ambiguous` 怎么读
- **怎么扩展一个新协议**

补了 `docs/FEATURES.md` 的一整节「标准接口 + 绑定层」，含绑定示例表、
返回体证据结构、以及「扩展新协议」的最小声明样例（只需一个
`CanonicalInterface`，不用改 `classify_module`、不写正则）。

## 178 · 文档构建器泄漏占位符（列表项内的围栏代码块）

加了上面那节文档后 `docs/build_docs_html.py` 自检失败：

```
[FAIL] FEATURES.html: 残留占位符（\x00）
```

生成物里出现 `<p><code>\x00CODE0\x00</code></p>` 与真实 NUL 字节。根因是
列表项内的**缩进围栏**显示为：

```markdown
1. **标准接口**（`fpga_toolbox/canonical_iface.py`）：

   ```python
   CanonSignal(...)
   ```
```

两个 bug 叠加：

1. 列表解析只把匹配 `^\s*[-*]` 的行当列表项，项内的缩进续行/围栏交给外层；
2. 围栏正则 `^```(\w*)\s*$` 不认缩进，于是 `   ```python` 被当普通段落，
   其内容又被 `_inline` 的行内代码正则吞掉反引号 → 留下占位符。

修复：

- 列表解析收集**缩进续行**（含空行后仍是缩进内容的情形），续行递归走
  `convert()` 渲染，因此项内围栏/段落/嵌套列表都能正确出块；
- 围栏正则允许前导缩进（`^(\s*)```(\w*)$`），并把该缩进从代码内容里剥掉，
  避免生成物出现悬空缩进。

修复后 `FEATURES.html` 自检通过，`<li>` 内正确嵌套
`<div class="code-block">…</div>`，NUL 计数为 0。

## 附带：DDR5 声明（验证扩展性）

按用户此前提出的协议扩展方向，用新架构加了 `ddr5` 标准接口（`ca` 更窄、
`dqs_t`/`dqs_c` 成对）。**只改了一处声明**：

```
extract_interfaces(ddr5_ctrl)
→ [('ddr5', 11)]  out_of_scope=[]      （11 个信号全绑定，零越界）
```

`emit_testbench` 如实报告：

```
the RTL's bus protocol(s) ['ddr5'] were detected but this emitter has no
testbench template for them. Supported protocols: [...]
```

而不是像旧架构那样把它误认成 DDR4 用错模板生成 TB。DDR3/DDR4/HBM 的
`required_tokens`/`forbidden_tokens` 保证 DDR5 前缀的端口不会被它们抢走。

## 验证

- 7 个参考 DUT 全部不变（apb 9 / axi4 29 / ahb 9 / ddr3 11 / ddr4 11 /
  hbm 18 / pcie 11，越界端口全为 0）
- 动态注册一个临时 `CanonicalInterface` 后 `bind_order()` 自动包含它
  （可扩展性保证的单元测试）
- `verify_invariants.py --full` 六层全过，函数覆盖 **169/169**
- `docs/build_docs_html.py` 自检通过，`FEATURES.html` NUL 计数 0
- 四套测试全绿，ruff 干净

回归测试：`test_bind_order_is_derived_from_canonical_declarations`。
