# Synopsys VCS / DC K-2015.06 / PrimeTime K-2015.06 论文复跑与工具箱集成指南

> ✅ **实现状态：`fpga_toolbox.eda_campaign` 已实现**（`fpga_toolbox/eda_campaign.py`，504 行）。
>
> 本文档描述的 API 均已落地并接入 CI（`tests/test_eda_campaign.py`）：
> `generate_remote_run_script` / `parse_uvm_log` / `parse_dc_pt_qor` /
> `aggregate_seed_medians` / `verify_paired_baseline_reproducibility` /
> `clean_netlist_for_pt` / `tool_clean_pt_netlist`。
>
> **诊断码说明**：本文档原先使用 `E040–E045`，但 `E040` 已被本仓库的
> `audit_harness` 占用（含义为「自审计护栏失效」）。为避免同一码两种语义，
> `eda_campaign` 的这批码已整体平移为 **`E046–E050`**：
>
> | 码 | 含义 |
> |---|---|
> | `E046` | DC K-2015.06 `UID-423` / 输出 <700B 且仅含 `No paths.` 的假成功报告 |
> | `E047` | `set_max_fanout` 施加在 net 对象报 `UID-119` |
> | `E048` | K-2015.06 调用 `write_db` / `-format db` 报 `UID-530` |
> | `E049` | `Received Signal 15` 或 `No space left on device` |
> | `E050` | 旧 `simv` / 截断日志（少于 3 行或缺 `UVM_ERROR :` 汇总）|

本指南将 **Paper 1 (DLR-MAC)** 与 **Paper 2 (RS+GBN)** 的远端实机复跑流程直接与 `fpga_toolbox.eda_campaign` 结合，把人工目测、正则解析、K-2015.06 死坑拦截和 PrimeTime 黑盒清理全部固化为可复用的自动化门禁。

## 1. 远端 `csh` 安全与 `/dev/shm` 磁盘纪律

远端服务器（如 `lzc@192.168.108.128`）默认 shell 为 `csh`，且根盘空间有限：
- 所有复杂命令统一通过 `ssh -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa ... "bash -c '...'"` 执行，或本地写 `.sh` 后 `scp` 到 `/tmp/` 后台运行。
- 所有综合/仿真工作目录必须放在 `/dev/shm/<tag>`（tmpfs），完成后拷回 `report/` 与 CSV 并清理 `/dev/shm/<tag>`。
- 并发上限：DC `compile_ultra` 最多同时跑 **2 个**；VCS 扫掠可起 **4 个 worker** 并行。

使用 `fpga_toolbox.eda_campaign.generate_remote_run_script` 一键生成标准启动脚本：

```python
from fpga_toolbox.eda_campaign import generate_remote_run_script

script = generate_remote_run_script(
    host="lzc@192.168.108.128",
    tag="p1_base",
    local_script_name="run_base.sh",
    use_dev_shm=True,
)
```

## 2. Paper 1 VCS / UVM 1.2 日志自动解析与门禁

`parse_uvm_log(log_text, expected_frames=10000)` 内置黄金正则与自动判据：
- 自动提取：`delivered`、`fid_bad`、`NP_LAT`（`min` / `avg` / `max`）、`all_retry`、`send_nums`、`UVM_ERROR`、`win_span` 最大值、以及注入标定 `pcs_words / flips_exp / flips_act`。
- 严格通过判据：`delivered == expected_frames && fid_bad == 0 && UVM_ERROR == 0`。
- **旧 `simv` / 截断日志防呆 (`E050`)**：若日志少于 3 行或缺少 `UVM_ERROR :` 汇总，直接判失败，杜绝拿错旧 `simv` 误判。
- 使用 `aggregate_seed_medians(runs)` 自动输出 Table 3 所需的 **5 种子中位数**与实际翻转率标定（`Σflips_act / Σflips_exp`、`Σflips_act / Σpcs_words`）。

## 3. DC K-2015.06 综合报告解析与四大死坑自动拦截

`parse_dc_pt_qor(qor_text, run_log_text)` 自动提取 WNS / TNS / 违例数 / Leaf Cell Count / 组合与非组合面积，并自动拦截 K-2015.06 已知陷阱：

| 错误码 | 拦截的真实死坑 | 自动建议修复 |
|---|---|---|
| `E046` | DC `report_timing -group async_default` 报 `UID-423` 或输出 `<700B` 且仅含 `No paths.` 的假成功报告 | 改用 PrimeTime 的 `{**async_default**}` 测量 recovery |
| `E047` | `set_max_fanout` 施加在 net 对象报 `UID-119`（或在 pin 上 no-op） | 改用 `enable_recovery_removal_arcs true` + `high_fanout_net_threshold 100000` |
| `E048` | K-2015.06 调用 `write_db` / `-format db` 报 `UID-530` | 仅导出 `.v` / `.sdc` / `.sdf` 给 PrimeTime |
| `E049` | 出现 `Received Signal 15` 或 `No space left on device` | 工作目录迁至 `/dev/shm` 且限制并发 DC ≤ 2 |

配对实验双跑校验：`verify_paired_baseline_reproducibility(run_a, run_b)` 严格核对两次基线综合的 Leaf Cell Count 与总面积逐位一致（如 `74,265` / `50,656.129 µm²`）。

## 4. PrimeTime K-2015.06 参数化黑盒网表自动清理

针对 DC 网表中 `RDBUS` / `CLKCROSSBUS` / `CLKCROSSDFF` 因多实例位宽不一致导致 PT `link_design` 失败的问题，调用 `tool_clean_pt_netlist(in_verilog, out_verilog)`：
1. 按 `module ... endmodule` 切分，移除未解析黑盒实例；
2. 提取 `.o_*` 输出端口连接并展平 `{a, b, c}` 位拼接；
3. 在每个模块 `endmodule` 前逐根插入标量 `assign <net> = 1'b0;`（避免 PT 拒收 `assign {a,b}=0;`）；
4. 返回被删实例数与 tie-off 列表，便于论文如实披露（CSR/CDC 域黑盒 tie-off，不在 `pcs_clk` 复位路径上）。

## 5. Paper 1 & Paper 2 关键基准数字对照

- **Paper 1 清洁延迟**：avg 5 拍 = `6.25 ns`，min 4 拍 = `5.0 ns`（`jitter ≤ 1` 拍）。
- **Paper 1 综合基线**：`50,656.13 µm²` / `74,265` leaf（`pcs_clk` WNS `0.00`、0 违例）；OCV derate (`0.95/1.05`) 仍 `0.00 ns`；arc-aware recovery 面积 `+2.35%`（`51,848.50 µm²`），PT `**async_default**` 由 `−0.6664 ns`（10,303 条）收敛至 **`+0.0042 ns`（0 条）**。
- **Paper 2 RS+GBN 14nm DC**：六码型 500 MHz 全收敛，协议 DFF `3,117–24,742`，面积 `4,616–41,286 µm²`；解析模型偏差 DFF `≤ 2.0%`、面积 `+2.6%~+11.5%`。
