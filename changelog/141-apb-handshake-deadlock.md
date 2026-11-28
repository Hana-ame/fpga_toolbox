# 功能增强 · 波形诊断新增 APB 握手死锁检测

## 背景

`diagnose_waveform` 此前只覆盖 AXI/AXI4-Stream 的 VALID/READY 两信号握手。
**APB 是三信号协议**：PSEL+PENABLE 构成一次传输请求，PREADY 是从机应答 ——
没有 VALID/READY 对，走不到 AXI 的检测分支。

死锁模式：PSEL 拉高后 PREADY 一直为低 —— 主设备等从机应答，从机永不应答，
仿真挂死。日志只会说「APB 事务超时」，波形能直接指出「PSEL 从 t=X 拉高，
PREADY 到仿真结束从未拉高」，指向 DUT 流控实现。

## 实现

`_find_apb_deadlocks(scan)`：按同前缀配对 PSEL→PREADY（如 `dut.psel` →
`dut.pready`），找出「PSEL 拉高后 PREADY 从未拉高」的窗口。与 AXI 检测分开
实现，因为 APB 的配对规则不同（`_ready_of` 对 psel 返回 None）。

## 首次实现踩到的两个真实缺陷

1. **APB 信号变迁未被收集**：`_scan` 的过滤器 `endswith(("VALID","READY"))`
   把 `psel`/`penable`/`pready` 全排除 → transitions 里只有 t=0 的初始值，
   APB 死锁检测返回空（假阴性）。扩展过滤器加入 `PSEL`/`PENABLE`/`PREADY`。

2. **`_find_apb_deadlocks` 缺 `max_t` 变量**（NameError，重构时遗漏）。

## 验证

| 场景 | 结果 |
|---|---|
| APB 死锁（psel@30 拉高、pready 从未拉高） | `deadlock count=1` |
| APB 健康（pready 会拉高） | `deadlock count=0`（无假阳性） |
| 六层门禁（完整 EDA） | **6/6**，Layer 4 `218/218` 函数 / 46 模块 |

测试：`tests/test_waveform_diag.py` 新增 2 用例（APB 死锁 + 健康无假阳性），
合计 8 用例。
