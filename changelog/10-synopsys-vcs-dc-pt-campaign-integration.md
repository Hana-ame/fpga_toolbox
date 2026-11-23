> **合并说明（本仓库）**：本模块原始设计使用 `E040–E045`。但 `E040` 在本仓库
> 已被 `audit_harness` 占用（含义为「自审计护栏失效」），同一码两种语义会让调用方
> 无法区分。故本仓库合并时将这批码整体平移为 **`E046–E050`**：
> `E046`(UID-423/假成功) · `E047`(UID-119) · `E048`(UID-530) · `E049`(Signal 15/磁盘满) · `E050`(旧 simv/截断日志)。
> 代码、文档、测试三处已同步。

# 10 — Synopsys VCS/UVM, DC K-2015.06 & PrimeTime K-2015.06 Campaign Integration

## Summary
Integrated the Paper 1 (DLR-MAC) & Paper 2 (RS+GBN) Synopsys reproduction workflow directly into `fpga_toolbox/eda_campaign.py`, accompanied by `tests/test_eda_campaign.py` and `docs/EXPERIMENT_REPRO_GUIDE.md`.

## Additions
- **UVM Log Parser & Aggregator (`parse_uvm_log`, `aggregate_seed_medians`)**: Golden regex parsing for `delivered`, `fid_bad`, `NP_LAT`, `all_retry`, `win_span`, and injection calibration (`pcs_words / flips_exp / flips_act`), plus stale-`simv` detection (`E050`) and 5-seed median computation.
- **DC / PrimeTime QoR Parser & K-2015.06 Pitfall Guards (`parse_dc_pt_qor`, `verify_paired_baseline_reproducibility`)**: Automatic interception of `E046` (`UID-423` / fake-success `<700B` `No paths.`), `E047` (`UID-119` `set_max_fanout` on net), `E048` (`UID-530` `write_db`), `E049` (Signal 15 / disk full), and paired-baseline bit-exact reproducibility verification.
- **PrimeTime Netlist Blackbox Cleaner (`clean_netlist_for_pt`, `tool_clean_pt_netlist`)**: Strips unresolved parameterized `RDBUS`/`CLKCROSSBUS`/`CLKCROSSDFF` instances per module and emits scalar `assign <net> = 1'b0;` tie-offs before `endmodule`.
- **Remote `csh`-Safe `/dev/shm` Launcher (`generate_remote_run_script`)**: Wraps remote commands in `ssh -oHostKeyAlgorithms=+ssh-rsa ... "bash -c '...'"` with `/dev/shm` staging and report collection.
