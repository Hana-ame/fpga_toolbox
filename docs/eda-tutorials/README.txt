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

⚠ 重要：与当前代码库的状态差异
--------------------------------
`EXPERIMENT_REPRO_GUIDE.md` 描述的 `fpga_toolbox.eda_campaign` 模块**尚未在本仓库实现**。
该文档引用的以下 API 目前都不存在：

  eda_campaign.generate_remote_run_script
  eda_campaign.parse_uvm_log
  eda_campaign.parse_dc_pt_qor
  eda_campaign.aggregate_seed_medians
  eda_campaign.verify_paired_baseline_reproducibility
  eda_campaign.tool_clean_pt_netlist

因此该文档应视为**设计说明 / 待实现方案**，其中的示例代码不能直接运行。
若将来实现该模块，请按文档中的函数签名与错误码语义落地。

诊断码冲突提醒
--------------
文档里 E040–E045 是 `eda_campaign` 计划使用的码，其中：

  E040  文档: DC report_timing 假成功报告（UID-423 / 输出仅 "No paths."）
        **本仓库现状: E040 已由 audit_harness 使用 = 自审计护栏失效**

同一码在本仓库已有其它含义。若将来实现 `eda_campaign`，必须先重新分配这一批
诊断码，不要复用 E040，否则调用方无法区分是自审计失败还是 DC 假报告。
