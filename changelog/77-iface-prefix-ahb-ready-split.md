# 131 · 方向前缀拆组与 AHB HREADY/HREADYOUT 键冲突

## 现象一：带 `i_`/`o_` 前缀的总线被拆成两组

一个标准 APB 从机（`i_psel`/`i_penable`/`i_pwrite`/`i_paddr`/`i_pwdata`/
`i_pstrb` + `o_pready`/`o_prdata`/`o_pslverr`）经 `extract_interfaces` 后只认出
**一组** `group_prefix="i"` 的 6 个信号，三个输出口全部落进
`out_of_scope_ports`，后续 TB 生成把它们 tie 成常量。

根因在 `_norm_prefix`：

```python
re.sub(r"^(?:i_|o_|in_|out_)", "", raw)     # 旧
```

它要求方向词后面**必须跟下划线**。但各 `_*_PATS` 的模式形如 `(?:^|_)psel$`，
`m.start()` 落在前导下划线**之后**，传给 `_norm_prefix` 的前缀是裸 `"i"` /
`"o"`，正则匹配不到 → 前缀保留 → `i` 组和 `o` 组各自成组。

修复：

```python
norm = re.sub(r"^(?:in|out|i|o)(?:_|$)", "", raw.strip(), flags=re.I)
return norm.strip("_").lower() or "default"
```

`i` / `o` / `i_` / `o_` / `in` / `out` / `in_` / `out_` 八种形态统一归一成
`default`，`s_axi` / `m_axi` / `ch0` 等真实通道前缀不受影响。

## 现象二：AHB `HREADY` 被 tie 到 `1'b0`，仿真永久挂起

`_AHB_PATS` 里 `hready` 的模式是 `h_?ready(?:out|in)?$`，同时匹配 `HREADY`
和 `HREADYOUT`，后遍历到的覆盖先前结果。标准 AHB-Lite 从机**同时**暴露这两个
端口（`HREADY` 是输入、`HREADYOUT` 是输出），实测：

```
port_map["hready"] = "HREADYOUT"
out_of_scope_ports = [{'port': 'HREADY', 'dir': 'input', 'tie_off': "1'b0"}]
```

TB 端永远等不到 ready（`while (!HREADY)`），仿真挂死。这不是编译错误，是
静默挂起。

修复分两处：

1. `_AHB_PATS` 拆成两个 canon：`hreadyout`（`h_?ready_?out$`）与
   `hready`（`h_?ready(?:in)?$`），各归各位。
2. `protocol_tb_emitter` 里模板取端口改为优先 `hreadyout`、回退 `hready`；
   方向表相应改成 `hreadyout: output` / `hready: input`，否则同时有两者时
   会误报 “expected DUT output, got input”。

顺带把 `REQUIRED_TB_PORTS["ahb_lite"]` 里的 `hready` 去掉（模板现在连的是
`hreadyout`，且 `hready` 是可选输入）。

## 验证

- `/tmp/ifx/i_psel.sv`：修复前 `[('apb','i',6)]` + 3 个 out_of_scope；修复后
  单组 `apb/default` 9 个信号、out_of_scope 为空。
- `/tmp/ifx/ahb_both.sv`：修复前 `hready->HREADYOUT` + `HREADY` tie `1'b0`；
  修复后两个 canon 各对端口、out_of_scope 为空、TB 正常生成并连
  `.HREADYOUT(HREADYOUT)`。
- 六个参考 DUT（apb/axi4/ahb_lite/ddr3/ddr4/hbm）重新生成并跑 Verilator：
  全部 `rc=0`、0 告警、`PASS: 1`。
- 回归测试：`test_iface_direction_prefix_and_ahb_ready_split`。
