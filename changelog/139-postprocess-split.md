# 重构 · 编排层后处理拆分（orchestrator 729 → 518 行）

## 背景

`emitters/orchestrator.py`（729 行）是拆分 `protocol_tb_emitter.py` 后子包最大
的文件。它把「选模块/接口 → 三道闸门 → 派发模板」与「后处理 + 落盘 + 运行脚本」
堆在一个 477 行的 `emit_protocol_testbench` 里。

后处理的三件事（`add_lint_off` / `add_waveform_dump` / `_emit_run_script`）对
**所有协议完全一致**、不依赖具体协议语义 —— 与编排逻辑是不同职责，理应分文件。

## 拆分

`emitters/orchestrator.py` 保留编排层（`emit_protocol_testbench`，选模块 → 三道
闸门 → 派发 → 调用后处理 → 落盘），后处理移到 `emitters/postprocess.py`：

| 函数 | 作用 |
|---|---|
| `add_lint_off` | 给 TB 注入 `lint_off PINMISSING`（DUT 额外端口退化为可抑制警告） |
| `add_waveform_dump` | 注入 `$dumpfile`/`$dumpvars` + plusargs 目标变量声明 |
| `_emit_run_script` | 生成 Verilator 运行脚本（suite 派发、波形开关、二进制重建判定） |

编排层 729 → 518 行；postprocess.py 229 行。

## 验证

| 项目 | 结果 |
|---|---|
| 六层门禁（完整 EDA） | **6/6**，Layer 4 `217/217` 函数 / **46 模块**（+1 postprocess.py） |
| ruff | 0 errors |
| 13 个测试套件 | 全通过 |
| 6 类协议模板 emit | 全部正常（apb/axi4/ahb_lite/ddr3/pcie/axi4_stream） |
| 5 个桥接组合 | 全部正常 |
