# 缺陷修复 · 波形诊断的跨通道误配与 READY 前缀漏配

## 1 · VALID/READY 跨通道误配

**现象**：`_find_handshake_deadlocks` 旧配对逻辑只按「READY 名以 ready_name
结尾」匹配，会把 `wvalid` 误配到 `awready`（后缀都是 valid/ready 但通道不同：
w 通道 vs aw 通道）。

**修复**：同 scope + **通道前缀一致**才配对。去掉 ready_name 后，`awvalid` 与
`awready` 的余部都是 `aw`，`wvalid` 与 `wready` 的余部都是 `w` —— 通道一致才
配对，不会跨通道误配。

## 2 · `_ready_of` 多段前缀漏配

**现象**：真实 RTL 里 VALID 名常带通道前缀（`axi_awvalid` / `axi_bvalid` /
`u_tvalid`）。旧 `_ready_of` 只剥 `s_`/`m_` + axis 一段前缀，`axi_awvalid`
这类裸 `axi_` 前缀查不到 → 返回 None → 明明同时查了 READY 仍漏报死锁
（实测 `axi_awvalid`@10 拉高、`axi_awready` 从未拉高却检不出）。

**修复**：改为逐段剥前缀直到命中表项（`axi_awvalid` → 剥 `axi_` → `awvalid`
→ 命中 AWREADY）。

## 验证

| 场景 | 结果 |
|---|---|
| `axi_awvalid`@10 拉高、`axi_awready` 从未拉高 | 检出 1 个死锁，配对到 awready |
| `wvalid` 不跨通道误配到 awready | 正确配对到 wready |
| `_ready_of` 11 个前缀变体 | 全部正确 |
| 六层门禁（完整 EDA） | **6/6**，Layer 4 `218/218` 函数 / 46 模块 |
