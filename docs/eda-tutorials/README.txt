EDA Tutorials & Verification Docs Bundle
========================================

离线文档集，供阅读/查阅使用。**注意：本目录是纯文档，不包含可执行代码。**

文件清单
--------
1. eda-tools-dark-guide.html          - Synopsys 六大 EDA 工具指令/Flag/参数范围/Debug/错误码参考（黑底夜间版）
2. paper-repro-guide.html             - Paper 1 (DLR-MAC) + Paper 2 (RS+GBN) 实验复跑教程解读页
3. multi_bus_verification_report.html - soc_omni_bus_hub 等六接口全向核心的验证审计报告
                                        （对应 examples/multi_bus_ref/omni_hub/）
4. EXPERIMENT_REPRO_GUIDE.md          - Synopsys VCS/DC/PT 与 `fpga_toolbox.eda_campaign` 集成指南

解压后直接用任意浏览器双击 .html 文件即可离线查看。
两个 HTML 引用了 Google Fonts（在线字体），断网时会回退到系统字体，正文内容不受影响。

✅ 与当前代码库的状态（已实现）
--------------------------------
`EXPERIMENT_REPRO_GUIDE.md` 描述的 `fpga_toolbox.eda_campaign` 模块**已实现**
于 `fpga_toolbox/eda_campaign.py`，测试见 `tests/test_eda_campaign.py`（已接入 CI）。

已落地 API：
  eda_campaign.generate_remote_run_script
  eda_campaign.parse_uvm_log
  eda_campaign.parse_dc_pt_qor
  eda_campaign.aggregate_seed_medians
  eda_campaign.verify_paired_baseline_reproducibility
  eda_campaign.clean_netlist_for_pt
  eda_campaign.tool_clean_pt_netlist

诊断码说明
----------
本模块诊断码为 E046–E050：
  E046  DC UID-423 / 输出 <700B 且仅含 "No paths." 的假成功报告
  E047  set_max_fanout 施加在 net 对象报 UID-119
  E048  K-2015.06 调用 write_db / -format db 报 UID-530
  E049  Received Signal 15 或 No space left on device
  E050  旧 simv / 截断日志（少于 3 行或缺 UVM_ERROR : 汇总）

注：原设计用 E040–E045，但 E040 在本仓库已被 audit_harness 占用
（含义为「自审计护栏失效」），故整体平移至空闲段 E046–E050。
