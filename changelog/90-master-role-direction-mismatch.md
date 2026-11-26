# 149 · 主设备配从设备模板时，错误信息不指向「角色反了」

## 现象

APB **主设备**（`psel/penable/pwrite/paddr/pwdata/pstrb` 是 output，
`pready/prdata/pslverr` 是 input）经 `emit_testbench(protocol="apb")`：

```
Cannot emit a 'apb' testbench for apb_master_dut: DUT port direction(s) disagree
with the template: psel->psel (expected DUT input, got output); penable->penable ...
```

分类器其实已经正确判定 `role="master"`，但错误信息完全不提这一点。
用户看到「端口方向不符」，第一反应会去改 RTL 端口方向 —— 而那些方向对一个
主设备来说本来就是对的。

## 根因

所有 TB 模板都是从设备视角写的（驱动请求、等响应）。`EXPECTED_TB_DIR` 里的
期望方向也按从设备定义。`_wrong_dir_ports()` 只做逐端口方向比对，没有利用
`role` 这个已经知道的信息。

## 修复

方向校验失败时先看 `role`：

- `role == "master"` → 明确说明「DUT 是 master，而所有模板都以 slave 身份工作，
  因此方向必然相反」，建议手写主设备侧 TB 或对从设备跑 emit_testbench；
- 其他情况保持原错误，但附上检测到的 role（若有）；
- 两种分支都在 `data` 里返回 `role` 与 `direction_mismatches`。

## 验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| APB 主设备 | 只列方向不符 | 指出 master vs slave 模板 + 数据字段 |
| APB/AXI4/AHB-Lite/DDR3/DDR4/HBM/PCIe 从设备 | 正常生成 | 不变（7/7 `ok=True`） |

回归测试：`test_master_role_is_explained_on_direction_mismatch`。
