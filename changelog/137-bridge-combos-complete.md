# 功能增强 · 桥接组合全覆盖（SUPPORTED_BRIDGES 声明 = 实现）

## 背景

`SUPPORTED_BRIDGES` 声明了 5 个组合，但只有 3 个有实现（axi4→apb、axi4→axi4、
spi→apb）；`emit_bridge` 对 ethernet→apb 与 axi4_stream→apb 抛「no joint
testbench template yet」。声明与实现不一致会让调用方误以为这 5 个组合都可用。

## 实现

补齐 **ethernet→apb** 与 **axi4_stream→apb**，两者下游都是 APB（与 spi→apb
同为流式转 APB 写）：

| 组合 | 上游 TB 角色 | 下游 | 自检 |
|---|---|---|---|
| `ethernet→apb` | 链路对端：`rx_dv` 拉高 + `rx_data` 逐字节 | APB slave | 帧数据转成 APB 写 |
| `axi4_stream→apb` | 流 master：TVALID + TDATA，服从 TREADY | APB slave | 流 beat 转成 APB 写，TREADY 从不拉高则 FAIL |

两者都用 `@@SENTINEL@@` + `replace()` 填充（SV 模板里大量 `$display` 的
`%0d`/`%08h` 与 Python `%` 占位符冲突）。

## 首次实现踩到的三个真实缺陷

1. **流式桥接 APB 从机的重复计数**：APB 的 access 相位会持续多拍（直到
   `pready` 拉高），旧写法每拍都 `ap_writes++`，**2 个 beat 被计成 3 次
   APB 写** —— 让「几个 beat 转成几次 APB 写」这条断言失效。改用 `ap_counted`
   标志做边沿检测：只在 access 相位第一拍计数，`pready` 拉高时清除。

2. **`ap_counted` 用 `int` 触发构建失败**：`!ap_counted` 对 32 位 int 会触发
   WIDTHTRUNC 警告，而 Verilator 默认**警告即错误** → 构建直接失败。改为
   1 位 `logic`。

3. **位宽哨兵未替换**：新模板的位宽哨兵写成了单 `@` 前缀（`[@ADDR_W-1:0]`），
   而 `replace()` 只匹配双 `@`（`@@ADDR_W@@`），导致网线位宽未被代入 →
   Verilator 隐式建 1 位 → WIDTHTRUNC 构建失败。实测在 ethernet 与
   axi4_stream 两个模板各复现一次。加测试断言「下游 APB 网线必须是有界声明
   而非裸 1 位」防回归。

## 验证

| 场景 | 结果 |
|---|---|
| `ethernet→apb`（探针 DUT，apb 侧恒 0） | FAIL（0 次 APB 写）—— 正确判定 |
| `axi4_stream→apb`（探针 DUT，空壳） | FAIL（0 次 APB 写）—— 正确判定 |
| `axi4_stream→apb`（功能 DUT，真转换） | `PASS: 2 stream beats converted into 3 APB write(s)` |
| 六层门禁（完整 EDA） | **6/6**，Layer 4 `217/217` 函数 / 38 模块 |
