# 140 · interface 打包的总线端口被误报成「DUT 缺端口」

## 现象

现代 SV 写法把整组总线信号放进 `interface`，DUT 顶层只有一个端口：

```systemverilog
module iface_dut (input logic clk, input logic rst_n, apb_if.slave apb);
```

`extract_interfaces` 返回：

```json
{"interfaces": [{"protocol": "apb", "port_map": {}, "missing_signals": [],
                 "sv_interface": {"port": "apb", "interface_type": "apb_if",
                                  "modport": "slave"}}]}
```

`port_map` 是空字典、`missing_signals` 是空列表、**没有任何诊断**。调用方看到
`protocol: "apb"` 会以为抽到了可用接口。

下游 `emit_testbench` 的报错更是把根因带偏：

```
Cannot emit a 'apb' testbench for iface_dut: DUT is missing port(s)
psel->psel, penable->penable, ... Detected RTL ports: ['apb', 'clk', 'rst_n']
```

「总线用 interface 打包了」被说成「DUT 漏了端口」，用户会去翻 RTL 找缺失端口，
而实际上 RTL 一个都没漏。

## 根因

`iface_extractor` 只判断「协议识别成功」，没有区分「信号来自扁平端口」与
「信号在 interface 内部」。`protocol_tb_emitter` 的 `_missing_tb_ports()` 也只
按端口名做差集，看到 `port_map` 为空就退回 canon 名去比，自然全缺。

## 修复

1. `extract_interfaces` 新增两个如实字段：
   - `interfaces_expandable`：是否存在任何 `port_map` 非空的接口；
   - `unexpanded_sv_interfaces`：`[{port, interface_type, modport, protocol}]`，
     仅列 `sv_interface` 存在但 `port_map` 为空的那几个。
2. `emit_testbench` 在报缺失端口前先看 `sv_interface`：若是 interface 打包，
   错误信息改为

   ```
   the 'apb' bus is declared as an SV interface port 'apb' of type 'apb_if'
   (modport slave). The interface's signals live inside the interface, so they
   cannot be driven as flat DUT ports; ... Either flatten the bus into plain
   ports in the RTL, or instantiate the interface in your own testbench and
   bind it to the DUT.
   ```

   并在 `data` 里返回 `sv_interface` + `unexpanded_signal_count`。

## 验证

- interface 打包的 APB DUT：`interfaces_expandable == false`、
  `unexpanded_sv_interfaces == [{port: apb, interface_type: apb_if,
  modport: slave}]`；`emit_testbench` 的错误信息包含 `SV interface port` /
  `apb_if`，**不再**出现 `DUT is missing port(s)`。
- 扁平端口的 DUT：`interfaces_expandable == true`、
  `unexpanded_sv_interfaces == []`；6 个参考 DUT 仍全部正常生成 TB。

回归测试：`test_unexpanded_sv_interface_ports_are_reported`。
