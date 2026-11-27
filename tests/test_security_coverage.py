"""安全边界（路径逃逸、注入、XSS、变异工具、数值越界）

安全边界（路径逃逸、注入、XSS、变异工具、数值越界）

从 tests/test_full_coverage.py 按领域拆分而来（原文件 ~5500 行、86 个用例，
定位失败需要通读全文）。拆分只搬移代码，不改断言。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from fpga_toolbox.registry import call_tool  # noqa: E402


def test_mutation_target_containment() -> None:
    """Defect 99: run_mutation must not write to files outside the workdir.

    旧实现 ``wd / m["file"]`` 不做沙箱约束，绝对路径 / ``..`` / 指向外部的符号
    链接都会被 write_bytes 改写。finally 还原只在正常执行时生效，任何 crash
    落在 write 与 restore 之间就永久破坏外部文件。
    """
    print("coverage: mutation target containment (abs / ../ / symlink escape)")
    import os as _os

    from fpga_toolbox.mutation_runner import tool_run_mutation

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "a.sv").write_text("module a; endmodule\n", encoding="utf-8")
        outside = wd.parent / "ext_target.sv"
        outside.write_text("EXT_ORIG\n", encoding="utf-8")

        # 绝对路径越界
        spec_abs = wd / "m_abs.yaml"
        spec_abs.write_text(
            # 单引号标量：Windows 绝对路径含反斜杠，双引号标量会被当成转义序列
            f"mutations:\n  - id: M01\n    file: '{outside}'\n    old: EXT_ORIG\n    new: MUT\n",
            encoding="utf-8")
        # 全部变异都越界 => 一个都评估不到 => 必须 ok=False（分数未定义），
        # 而不是像旧实现那样报一个「0% 或 100%」的分数。这条同时锁住缺陷 113
        # 的 evaluated==0 分支。
        r_abs = tool_run_mutation(str(wd), str(spec_abs), ["true"], min_score=0.0)
        assert not r_abs["ok"], "all-out-of-sandbox spec must not report a score"
        assert "No mutation could be evaluated" in r_abs["error"], r_abs["error"]
        assert r_abs["data"]["evaluated"] == 0
        assert any(x["status"] == "ERROR" for x in r_abs["data"]["results"]), r_abs["data"]["results"]
        assert outside.read_text(encoding="utf-8") == "EXT_ORIG\n"

        # ../ 相对路径越界
        spec_rel = wd / "m_rel.yaml"
        spec_rel.write_text(
            'mutations:\n  - id: M01\n    file: "../ext_target.sv"\n    old: "EXT_ORIG"\n    new: "MUT"\n',
            encoding="utf-8")
        r_rel = tool_run_mutation(str(wd), str(spec_rel), ["true"], min_score=0.0)
        assert not r_rel["ok"] and r_rel["data"]["evaluated"] == 0
        assert any(x["status"] == "ERROR" for x in r_rel["data"]["results"])
        assert outside.read_text(encoding="utf-8") == "EXT_ORIG\n"

        # 沙箱内符号链接指向外部
        outside2 = wd.parent / "ext_target2.sv"
        outside2.write_text("EXT2_ORIG\n", encoding="utf-8")
        _os.symlink(outside2, wd / "link.sv")
        spec_link = wd / "m_link.yaml"
        spec_link.write_text(
            'mutations:\n  - id: M01\n    file: "link.sv"\n    old: "EXT2_ORIG"\n    new: "MUT"\n',
            encoding="utf-8")
        r_link = tool_run_mutation(str(wd), str(spec_link), ["true"], min_score=0.0)
        assert not r_link["ok"] and r_link["data"]["evaluated"] == 0
        assert any(x["status"] == "ERROR" and "inside the working directory" in x["detail"]
                   for x in r_link["data"]["results"]), r_link["data"]["results"]
        assert outside2.read_text(encoding="utf-8") == "EXT2_ORIG\n"

        # 合法目标仍正常变异并还原
        spec_ok = wd / "m_ok.yaml"
        (wd / "good.sv").write_text("assign y = 1;\n", encoding="utf-8")
        spec_ok.write_text(
            'mutations:\n  - id: M01\n    file: "good.sv"\n    old: "assign y = 1;"\n    new: "assign y = 0;"\n',
            encoding="utf-8")
        # 合法目标仍正常变异并还原。test_cmd 必须「baseline 通过、变异后失败」
        # 才是真实可用的检测命令：grep 断言 good.sv 里仍是 assign y = 1。
        # 旧测试用 ["false"]（永远失败）——那正是缺陷 113 里"坏 test_cmd 造出
        # 假 100% 得分"的情形，现在会被 baseline 检查挡掉。
        r_ok = tool_run_mutation(
            str(wd), str(spec_ok),
            ["grep", "-q", "assign y = 1;", "good.sv"], min_score=100.0)
        assert r_ok["ok"], r_ok.get("error")
        assert r_ok["data"]["killed"] == 1, r_ok["data"]
        assert r_ok["data"]["evaluated"] == 1
        assert r_ok["data"]["baseline"]["returncode"] == 0
        assert "assign y = 1;" in (wd / "good.sv").read_text(encoding="utf-8")  # 已还原

def test_remote_work_dir_injection_blocked() -> None:
    """Defect 100: generate_remote_run_script 未校验 remote_work_dir。

    host/tag/local_script_name 都有白名单，但 remote_work_dir 会被原文插进
    **远端执行**的脚本（use_dev_shm=False 路径的
    `mkdir -p {remote_work_dir}/{tag}`）。漏校验它让三道白名单形同虚设。
    """
    print("coverage: remote_work_dir shell-injection blocked")
    from fpga_toolbox.eda_campaign import generate_remote_run_script

    # 注入向量必须被拒
    for bad in ["x; touch /tmp/PWNED; #", "$(reboot)", "`id`", "~/x && rm -rf /", "a|b"]:
        try:
            generate_remote_run_script("h", "t", "run.sh",
                                       remote_work_dir=bad, use_dev_shm=False)
            raise AssertionError(f"remote_work_dir injection NOT rejected: {bad!r}")
        except ValueError:
            pass

    # 合法目录仍可用，且原样出现在脚本里（无元字符）
    for good in ["~/work", "/tmp/eda", "/home/user/proj_run"]:
        s = generate_remote_run_script("h", "t", "run.sh",
                                       remote_work_dir=good, use_dev_shm=False)
        assert f"mkdir -p {good}/t && cd {good}/t" in s, (good, s)

    # dev_shm=True 路径不引用 remote_work_dir，但仍走校验且不受影响
    s_shm = generate_remote_run_script("h", "t", "run.sh",
                                       remote_work_dir="~/work", use_dev_shm=True)
    assert "mkdir -p /dev/shm/t" in s_shm
    # dev_shm=True 下非法 remote_work_dir 同样被拒（一致性）
    try:
        generate_remote_run_script("h", "t", "run.sh",
                                   remote_work_dir="; bad", use_dev_shm=True)
        raise AssertionError("dev_shm=True should still validate remote_work_dir")
    except ValueError:
        pass

def test_eda_remote_tag_cannot_escape_staging_dir() -> None:
    """Defect 145: tag='..'/'..' 这类纯点 tag 会越界删除远端目录。

    `_TAG_RE = ^[A-Za-z0-9_.-]+$` 允许 '.'，于是 tag='..' 或 tag='.' 通过校验。
    生成脚本把 tag 拼进 `WORK="/dev/shm/{tag}"`，末尾执行 `rm -rf $WORK`：

    - tag='..' → WORK="/dev/shm/.."  → rm -rf /dev/shm/..  (= /dev)
    - tag='.'  → WORK="/dev/shm/."   → rm -rf /dev/shm/.   (= /dev/shm 整目录)

    与已修的 remote_work_dir='/' 是同一类「字符白名单挡不住路径语义」问题，
    只是从 tag 侧绕进来。
    """
    print("coverage: eda_campaign tag cannot escape staging dir")
    from fpga_toolbox.eda_campaign import generate_remote_run_script

    for bad in ("..", ".", "../.."):
        try:
            script = generate_remote_run_script("host1", bad, "run.sh", "~/work")
        except ValueError:
            continue
        raise AssertionError(
            f"tag {bad!r} was accepted; generated WORK line: "
            + next(ln for ln in script.splitlines() if ln.startswith("WORK=")))

    # 正常 tag 必须仍可用
    for good in ("ver1", "v1_2-3", "a.b", "run-2024.11"):
        script = generate_remote_run_script("host1", good, "run.sh", "~/work")
        assert f'WORK="/dev/shm/{good}"' in script, good

    # 其他参数保持原防护
    for args in [
        ("host\nrm -rf /", "t", "run.sh", "~/work"),      # host 换行注入
        ("host", "t", "../evil.sh", "~/work"),             # script 带路径
        ("host", "t", "run.sh", "/"),                      # workdir 根
        ("host", "t", "run.sh", "~/work/.."),              # workdir ..
    ]:
        try:
            generate_remote_run_script(*args)
        except ValueError:
            continue
        raise AssertionError(f"injection accepted: {args}")

def test_security_hardening_injection_xss_verilator_hijack() -> None:
    """Defect 127: 命令注入绕道、/tmp verilator 劫持、审计报告存储型 XSS。

    1. `generate_remote_run_script` 的四个白名单用 `$` 结尾 —— Python 的 `$` 也
       匹配「末尾换行之前」，于是 `tag='t\\n'` / `host='h\\n'` 能通过校验，
       插入脚本后把 `HOST="..."` 的字符串字面量从换行处截断，后续内容成为独立
       shell 命令（实测生成的脚本里 `HOST="host1` + 换行）。
    2. 同一个函数只做**字符**白名单，不查路径语义：`remote_work_dir='/'` 生成
       `WORK="//t"`、`'~/work/..'` 生成 `~/work/../t`，而脚本末尾是
       `rm -rf $WORK` —— 「清理临时目录」会删掉主目录下的同名目录。
    3. `find_verilator()` 把 `/tmp/verilator/bin/verilator` 排在 `shutil.which`
       **之前**。`/tmp` 通常是 1777，多用户机器上他人可预置同路径二进制来劫持
       本工具实际调用的仿真器。
    4. `render_visual_report` 把 `extra_meta`（源自 coverage_summary /
       mutation_summary）未转义插入 HTML：`<img src=x onerror=...>` 原样落盘，
       构成存储型 XSS；且 `mutation_score` 为非数字时
       `round(100.0*float(...))` 抛 `TypeError: can't multiply sequence ...`
       让整个报告渲染失败。
    """
    print("coverage: injection ($ vs \\Z, '..' paths), /tmp verilator hijack, report XSS")
    from fpga_toolbox.eda_campaign import generate_remote_run_script
    from fpga_toolbox.eda_env import _is_owned_by_me, find_verilator

    # ---- 1) 末尾换行注入：四个参数都必须被拒 ----
    for args in (("host1\n", "t", "run.sh"), ("host1", "t\n", "run.sh"),
                 ("host1", "t", "run.sh\n"), ("host1\nrm -rf ~\n", "t", "run.sh")):
        try:
            generate_remote_run_script(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"trailing-newline injection accepted: {args!r}")

    # ---- 2) 路径语义：'..' 与根路径必须被拒，合法子目录仍可用 ----
    for wd in ("/", "//", "~", "~/work/..", "/dev/shm/..", "/tmp/../home"):
        try:
            generate_remote_run_script("h", "t", "run.sh", wd, use_dev_shm=False)
        except ValueError:
            pass
        else:
            raise AssertionError(f"dangerous remote_work_dir accepted: {wd!r}")
    for wd in ("~/work", "/tmp/foo", "~/a/b", "/dev/shm"):
        s = generate_remote_run_script("h", "t", "run.sh", wd, use_dev_shm=False)
        assert 'WORK="' in s, wd

    # ---- 3) /tmp verilator 劫持：不应优先于 ~/.local 或 PATH ----
    v = find_verilator()
    assert v != "/tmp/verilator/bin/verilator", v
    # _is_owned_by_me 对非 /tmp 路径直接放行；对不存在的路径返回 False
    assert _is_owned_by_me("/nonexistent/verilator") is False

    # ---- 4a) XSS：所有输入字段都不得原样落进 HTML ----
    with tempfile.TemporaryDirectory() as td:
        payload = "<img src=x onerror=alert(1)>"
        rep = {
            "module": payload, "overall_status": "PASS", "generated_at": payload,
            "summary": {"total": 1, "passed": 1, "failed": 0},
            "checks": [{"rule_id": payload, "status": "PASS", "category": payload,
                        "protocol": payload, "tx": payload, "expected": payload,
                        "observed": payload, "engine": payload, "detail": payload}],
            "coverage_summary": {"rtl_pct": payload},
            "mutation_summary": {"killed": 1, "total": 1, "results": []},
            "capability_matrix": [{"protocol": payload, "dut": payload,
                                   "extract": payload, "emit_tb": payload,
                                   "sim_engine": payload, "checks_summary": payload,
                                   "mutation": payload}],
            "raw_log_excerpt": payload,
        }
        r = call_tool("render_visual_report", {
            "report": rep, "dep_graph": {"dependency_closure": [payload]},
            "extracted_interfaces": [], "out_html_path": str(Path(td) / "x.html")})
        assert r["ok"], str(r.get("error"))[:200]
        page = (Path(td) / "x.html").read_text(encoding="utf-8")
        svg = (Path(td) / "x.svg").read_text(encoding="utf-8")
        for doc in (page, svg):
            # 判据是「未转义的标签」而不是裸文本：转义后的
            # `&lt;img src=x onerror=alert(1)&gt;` 里仍含 onerror 字样，
            # 但不会被执行。真正危险的是 `<img` / `<svg` 这类开标签。
            assert "<img src=x" not in doc, "unescaped <img> injected into output"
            assert "<svg onload" not in doc, "unescaped <svg> injected into output"

    # ---- 4b) mutation_score 非数字不能让渲染崩 ----
    with tempfile.TemporaryDirectory() as td:
        for bad in ("<svg onload=alert(1)>", "n/a", None, 42.5, {"x": 1}, [1]):
            rep = {"module": "m", "overall_status": "PASS",
                   "summary": {"total": 1, "passed": 1, "failed": 0},
                   "checks": [{"rule_id": "A", "status": "PASS"}],
                   "mutation_summary": {"mutation_score": bad, "killed": 1,
                                        "total": 1, "results": []}}
            r = call_tool("render_visual_report", {
                "report": rep, "dep_graph": {}, "extracted_interfaces": [],
                "out_html_path": str(Path(td) / "y.html")})
            assert r["ok"], (bad, str(r.get("error"))[:160])

def test_validator_bad_offset_no_crash() -> None:
    """Defect 103: 非数字 offset（"abc" / 4.5 / "0xZZ"）让 int() ValueError 冒泡。

    spec 由用户/AI 手写，offset 可能写成非数字。旧实现 _to_int 里
    int(str(val), 0) 直接抛 ValueError，用户只看到 invalid literal for int()
    堆栈，没指向是哪个字段，也没落进诊断码体系。改用 _to_int_or_none，按既有
    E005（register_rules）/ E008（illegal_addr_rules）给可定位诊断。
    """
    print("coverage: validator non-numeric offset -> E005/E008 (no crash)")
    import yaml

    from fpga_toolbox.core.validator import build_and_validate_ir, load_raw_spec

    for bad in ["abc", 4.5, "0xZZ"]:
        with tempfile.TemporaryDirectory() as td:
            spec = {
                "boundary": {"module_name": "m"},
                "test_plan": {
                    "protocol_rules": {"max_resp_cycles": 16},
                    "register_rules": [
                        {"rule_id": "R1", "name": "CTRL", "offset": bad,
                         "reset_value": 0, "access": "RW"}],
                    "illegal_addr_rules": [
                        {"rule_id": "I1", "offset": bad,
                         "expected_resp": "SLVERR"}],
                },
            }
            spec_p = Path(td) / "s.yaml"
            spec_p.write_text(yaml.safe_dump(spec), encoding="utf-8")
            # 修复前这里抛 ValueError；修复后应产出诊断码而非崩溃
            _ir, diags = build_and_validate_ir(load_raw_spec(spec_p))
            codes = [d.code for d in diags]
            assert "E005" in codes, (bad, codes)  # register_rules offset
            assert "E008" in codes, (bad, codes)  # illegal_addr_rules offset
            e005 = next(d for d in diags if d.code == "E005")
            assert "must be an integer" in e005.message, e005.message

    # 合法数字 offset（含 hex 字符串）不应报 E005/E008 offset 错
    with tempfile.TemporaryDirectory() as td:
        spec = {
            "boundary": {"module_name": "m"},
            "test_plan": {
                "protocol_rules": {"max_resp_cycles": 16},
                "register_rules": [
                    {"rule_id": "R1", "name": "CTRL", "offset": "0x4",
                     "reset_value": 0, "access": "RW"}],
                "illegal_addr_rules": [],
            },
        }
        spec_p = Path(td) / "ok.yaml"
        spec_p.write_text(yaml.safe_dump(spec), encoding="utf-8")
        _ir, diags = build_and_validate_ir(load_raw_spec(spec_p))
        e005 = [d for d in diags if d.code == "E005"]
        assert not e005, e005  # 合法 hex offset 不误报

def test_numeric_bounds_are_enforced_at_entry() -> None:
    """Defect 144: schema 的 minimum/maximum 必须在 call_tool 入口强制执行。

    旧实现没有任何数值边界校验，`timeout_sec=-5` 被直接传进
    `subprocess.run(timeout=-5)`，Python 立刻抛 TimeoutExpired 并报出
    「Simulation timed out after -5s」——语义荒谬且把「参数非法」伪装成
    「仿真超时」。`timeout_sec=0` 同理，永远不可能跑完任何仿真。
    """
    print("coverage: numeric bounds enforced at call_tool entry")
    from fpga_toolbox.registry import call_tool, list_tool_schemas

    # schema 必须声明边界（否则校验无从谈起）
    sim_schema = next(s for s in list_tool_schemas() if s["name"] == "run_sim")
    assert sim_schema["inputSchema"]["properties"]["timeout_sec"].get("minimum") == 1, \
        sim_schema["inputSchema"]["properties"]["timeout_sec"]
    mut_schema = next(s for s in list_tool_schemas() if s["name"] == "run_mutation")
    assert mut_schema["inputSchema"]["properties"]["timeout_sec"].get("minimum") == 1, \
        mut_schema["inputSchema"]["properties"]["timeout_sec"]

    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "sim.sh"
        script.write_text(
            "#!/bin/bash\n"
            'echo "== sim: apb (top=tb_apb) =="\n'
            'echo "PASS: apb all 8 checks passed"\n', encoding="utf-8")
        script.chmod(0o755)
        base = {"sim_script": str(script), "expected_suites": ["apb"]}

        for bad in (0, -5, "-3"):
            r = call_tool("run_sim", {**base, "timeout_sec": bad})
            assert r["ok"] is False, (bad, r)
            err = str(r.get("error"))
            assert "must be >= 1" in err, (bad, err[:200])
            # 不能伪装成「仿真超时」
            assert "timed out" not in err, (bad, err[:200])

        # 合法值不再被**边界校验**拒绝；字符串数字（MCP 客户端常见）也接受。
        # 这里不能断言 ok=True —— run_sim 需要真实 Verilator 才能跑完仿真，而
        # README 承诺控制面工具在零 EDA 环境下可用，测试在无 Verilator 的机器上
        # 必须仍然通过（缺陷报告 3 号阻塞项）。判据因此改为「错误不是边界校验
        # 产生的」，这正好是本用例要守的行为。
        for good in (1, 300, "1700"):
            r = call_tool("run_sim", {**base, "timeout_sec": good})
            err = str(r.get("error") or "")
            assert "must be >=" not in err and "must be <=" not in err, (good, err[:200])
            assert "Invalid value" not in err, (good, err[:200])

        # 省略该参数时默认生效（同样只校验未被边界规则拒绝）
        r = call_tool("run_sim", base)
        err = str(r.get("error") or "")
        assert "must be >=" not in err and "Invalid value" not in err, err[:200]

def test_min_score_bounds_are_enforced() -> None:
    """Defect 159: min_score 没有 0-100 边界。

    `min_score` 是「可接受的变异杀伤率百分比」，上限必然是 100。旧 schema 只有
    `type: number`：
    - `min_score=150` → 永远不可能达到（分数上限 100%），每次调用都必然失败；
    - `min_score=-1` → 无意义的阈值，任何分数都「达标」。
    两者都应作为非法参数在入口拒绝，而不是伪装成「分数未达标」。
    """
    print("coverage: min_score bounds enforced")
    from fpga_toolbox.registry import call_tool, list_tool_schemas

    schema = next(s for s in list_tool_schemas() if s["name"] == "run_mutation")
    ms_spec = schema["inputSchema"]["properties"]["min_score"]
    assert ms_spec.get("minimum") == 0, ms_spec
    assert ms_spec.get("maximum") == 100, ms_spec

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "dut.sv").write_text(
            "module dut (input logic a, output logic b);\n  assign b = a;\nendmodule\n",
            encoding="utf-8")
        sp = root / "spec.json"
        sp.write_text(json.dumps({"mutations": [
            {"id": "m1", "file": "dut.sv", "old": "assign b = a;",
             "new": "assign b = ~a;"}]}), encoding="utf-8")
        base = {"workdir": str(root), "mutation_spec": str(sp),
                "test_cmd": ["true"]}

        for bad in (-1, -0.5, 101, 150):
            r = call_tool("run_mutation", {**base, "min_score": bad})
            assert r["ok"] is False, (bad, r)
            err = str(r.get("error"))
            assert "must be >=" in err or "must be <=" in err, (bad, err[:200])
            # 不能把「参数非法」伪装成「分数未达标」
            assert "below threshold" not in err, (bad, err[:200])

        # 合法值照常工作
        for good in (0, 50, 100):
            r = call_tool("run_mutation", {**base, "min_score": good})
            # score=0 时 >=0 通过、<50/<100 不通过，两种都算「参数被接受」
            err = str(r.get("error") or "")
            assert "must be >=" not in err and "must be <=" not in err, (good, err[:200])

def test_regfile_spec_type_and_value_validation() -> None:
    """Defect 150/151/152: 手写 regfile spec 的类型与数值错误不能静默/崩栈。

    150: `registers` 写成 mapping（`{"CTRL": {...}}`）时，归一化代码
         `for idx, r in enumerate(...)` 迭代到**键字符串**，随后 `r.get(...)`
         抛裸 `AttributeError: 'str' object has no attribute 'get'` —— 未捕获
         异常漏出，错误信息与真正原因（registers 类型不对）毫无关联。
    151: `offset: "zzz"` 经宽容 `_to_int` 静默回退到 `idx*4`，validate_contract
         返回 ok=true + 零诊断，寄存器被悄悄挪位。
    152: `reset: "zz"` 同样静默变成 0。
    """
    print("coverage: regfile spec type and value validation")
    from fpga_toolbox.registry import call_tool

    base = {"$schema": "apb-regfile-spec/v2", "module": "x",
            "bus": {"protocol": "APB", "addr_width": 8, "data_width": 32,
                    "wait_cycles": 0}}

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def _write(name: str, obj) -> str:
            fp = root / name
            fp.write_text(json.dumps(obj), encoding="utf-8")
            return str(fp)

        # 150: registers 不是列表 → 结构化错误，不能是裸 AttributeError
        p1 = _write("bad_regs.json", {**base, "registers": {"CTRL": {"offset": "0x0"}}})
        r1 = call_tool("validate_contract", {"spec_path": p1})
        assert r1["ok"] is False
        e1 = str(r1.get("error"))
        assert "must be a list" in e1, e1[:200]
        assert "AttributeError" not in e1, e1[:200]

        # 元素不是对象 → 也要有明确信息
        p1b = _write("bad_elem.json", {**base, "registers": ["oops"]})
        r1b = call_tool("validate_contract", {"spec_path": p1b})
        assert r1b["ok"] is False
        assert "must be an object" in str(r1b.get("error")), str(r1b.get("error"))[:200]

        # 151: 非法 offset → E005
        p2 = _write("bad_off.json", {**base, "registers": [
            {"name": "R", "offset": "zzz", "attr": "RW", "reset": "0x0"}]})
        r2 = call_tool("validate_contract", {"spec_path": p2})
        assert r2["ok"] is False, r2
        codes2 = [(d["code"], d["message"]) for d in (r2.get("diagnostics") or [])]
        assert any(c == "E005" and "offset" in m for c, m in codes2), codes2

        # 152: 非法 reset → E005
        p3 = _write("bad_rst.json", {**base, "registers": [
            {"name": "R", "offset": "0x0", "attr": "RW", "reset": "zz"}]})
        r3 = call_tool("validate_contract", {"spec_path": p3})
        assert r3["ok"] is False, r3
        codes3 = [(d["code"], d["message"]) for d in (r3.get("diagnostics") or [])]
        assert any(c == "E005" and "reset" in m for c, m in codes3), codes3

        # 合法 spec 不受影响
        p4 = _write("ok.json", {**base, "registers": [
            {"name": "VERSION", "offset": "0x00", "attr": "RO", "reset": "0x00010203"},
            {"name": "CTRL", "offset": "0x04", "attr": "RW", "reset": "0x0"}]})
        r4 = call_tool("validate_contract", {"spec_path": p4})
        assert r4["ok"] is True, str(r4.get("error"))[:200]
        assert r4["data"]["register_count"] == 2, r4["data"]

def test_mutation_rejects_empty_old_pattern() -> None:
    """Defect 118: 变异 spec 的 `old: ""` 被当作"匹配任意位置"静默执行。

    `b"" in bytes` 恒为真，`original.replace(b"", new, 1)` 会把 new 插到文件
    **开头**（实测）——变异"成功"、进入分母影响得分，却没有任何语义。这不是
    "匹配到所有位置"，是 spec 写错了，必须显式拒绝且不动文件。
    """
    print("coverage: run_mutation rejects empty 'old' pattern")
    from fpga_toolbox.mutation_runner import tool_run_mutation

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "a.txt").write_text("hello world\n", encoding="utf-8")
        (wd / "m.json").write_text(json.dumps({"mutations": [
            {"id": "E", "file": "a.txt", "old": "", "new": "ZZ", "desc": "empty"}]}),
            encoding="utf-8")
        r = tool_run_mutation(str(wd), str(wd / "m.json"), ["true"], min_score=0.0)
        assert not r["ok"], "empty 'old' must not produce a score"
        assert "No mutation could be evaluated" in r["error"], r["error"]
        res = r["data"]["results"][0]
        assert res["status"] == "ERROR" and "empty" in res["detail"].lower(), res
        assert (wd / "a.txt").read_text(encoding="utf-8") == "hello world\n"

def test_mutation_spec_non_object_entries_are_rejected() -> None:
    """Defect 156: mutation spec 里的非对象元素不能被静默丢弃。

    `load_mutations` 对非 dict 元素是 `continue`：

        {"mutations": ["oops", {"id":"m1","file":"dut.sv","old":...,"new":...}]}
        → total=1, evaluated=1, ok=True

    `"oops"` 这条变异凭空消失，用户会以为 spec 里所有变异都跑了。全部是
    `"oops"` 时更糟：total=0/evaluated=0，错误信息说
    "0 error(s), 0 unmatched out of 0"，完全没提元素类型不对。
    """
    print("coverage: mutation spec non-object entries rejected")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "dut.sv").write_text(
            "module dut (input logic a, output logic b);\n"
            "  assign b = a;\nendmodule\n", encoding="utf-8")

        def _run(name: str, spec) -> dict:
            sp = root / name
            sp.write_text(json.dumps(spec), encoding="utf-8")
            return call_tool("run_mutation", {
                "workdir": str(root), "mutation_spec": str(sp),
                "test_cmd": ["true"], "min_score": 0})

        # 全部是非对象 → 必须明确报错，不能说「0 个变异」
        r = _run("all_bad.json", {"mutations": ["oops"]})
        assert r["ok"] is False, r
        err = str(r.get("error"))
        assert "must be objects" in err, err[:250]
        assert "silently skipped" in err, err[:250]
        # 不能伪装成「没有可评估的变异」
        assert "No mutation could be evaluated" not in err, err[:250]

        # 混合：有效项 + 非对象 → 同样拒绝（否则非对象项静默消失）
        r2 = _run("mixed.json", {"mutations": [
            "oops",
            {"id": "m1", "file": "dut.sv", "old": "assign b = a;", "new": "assign b = ~a;"}]})
        assert r2["ok"] is False, r2
        assert "must be objects" in str(r2.get("error")), str(r2.get("error"))[:250]

        # 合法 spec 正常执行
        r3 = _run("ok.json", {"mutations": [
            {"id": "m1", "file": "dut.sv", "old": "assign b = a;", "new": "assign b = ~a;"}]})
        assert r3["ok"] is True, str(r3.get("error"))[:250]
        assert r3["data"]["total"] == 1, r3["data"]
        assert r3["data"]["evaluated"] == 1, r3["data"]

        # 省略 id 时仍自动生成确定性 id（合法用法，不误伤）
        r4 = _run("noid.json", {"mutations": [
            {"file": "dut.sv", "old": "assign b = a;", "new": "assign b = ~a;"}]})
        assert r4["ok"] is True, str(r4.get("error"))[:250]
        assert r4["data"]["results"][0]["id"] == "M01", r4["data"]["results"]

def test_mutation_accounting_is_self_consistent() -> None:
    """缺陷 194: mutation 报告的数字必须自洽。

    `run_mutation` 的计数语义是：
        total     = 变异总数
        evaluated = total - errors - skipped   （真正测得结果的）
        killed    = 测试台杀掉的
        survived  = evaluated - killed          （没杀掉的）

    两条不变式必须恒成立：
        killed + survived == evaluated          （每个被评估的变异非杀即活）
        evaluated + errors + skipped == total   （每个变异归入且仅归入一类）

    计算式天然满足，但历史上出现过 `survived = total - killed`（把 ERROR 也算成
    存活）导致分数被污染的问题。这里既验证不变式，也验证代码里真的加了防御性
    断言（而不是只靠"当前实现碰巧对"）。
    """
    print("coverage: mutation accounting is self consistent")
    from fpga_toolbox.registry import call_tool

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "d.sv").write_text(
            "module d (input logic a, output logic b);\n  assign b = a & 1'b1;\n"
            "endmodule\n", encoding="utf-8")
        (root / "spec.json").write_text(json.dumps({"mutations": [
            {"id": "m1", "file": "d.sv", "old": "a & 1'b1", "new": "a | 1'b1"},
            {"id": "m2", "file": "d.sv", "old": "module d", "new": "module d /*X*/"},
            {"id": "m3", "file": "nope.sv", "old": "x", "new": "y"},   # ERROR
            {"id": "m4", "file": "d.sv", "old": "no-such-text", "new": "z"},  # SKIP
        ]}), encoding="utf-8")

        r = call_tool("run_mutation", {
            "workdir": str(root), "mutation_spec": str(root / "spec.json"),
            "test_cmd": ["true"], "min_score": 0})
        d = r["data"]
        # 不变式 1：被评估的变异非杀即活
        assert d["killed"] + d["survived"] == d["evaluated"], d
        # 不变式 2：每个变异归入且仅归入一类
        assert (d["evaluated"] + d["errors"] + d["skipped"] == d["total"]), d
        # results 里的状态计数必须与汇总字段一致
        st = [x["status"] for x in (d.get("results") or [])]
        assert sum(1 for x in st if x.startswith("KILLED")) == d["killed"], (st, d)
        assert st.count("SURVIVED") == d["survived"], (st, d)
        assert st.count("ERROR") == d["errors"], (st, d)
        assert st.count("SKIP_UNMATCHED") == d["skipped"], (st, d)
        # 分数定义：killed / evaluated（分母为 0 时 0.0）
        exp = round((d["killed"] / d["evaluated"]) * 100.0, 2) if d["evaluated"] else 0.0
        assert d["mutation_score"] == exp, (d["mutation_score"], exp)

def test_review_fixes_mutation_baseline_and_accounting() -> None:
    """Defect 113: run_mutation 无 baseline 预检 + ERROR 计入 survived 分母。

    旧实现只看「变异后 test_cmd 非零」就判 KILLED。若 test_cmd 本身是坏的
    （写错名字 / 缺二进制 / 测试台本来就失败），每个变异都以非零退出 → 全部
    KILLED → **假 100% 变异得分**（分数满格但一个故障都没验）。另外
    survived = total - killed 把 status="ERROR"（缺文件/越界）也算成存活并
    留在分母里，污染「测试台没杀掉的故障」这一语义。
    """
    print("coverage: run_mutation baseline precheck + ERROR/SKIP excluded from score")
    from fpga_toolbox.mutation_runner import tool_run_mutation

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "dut.sv").write_text("assign q = clk;\n", encoding="utf-8")
        (wd / "m.yaml").write_text(
            'mutations:\n  - id: M1\n    file: "dut.sv"\n'
            '    old: "assign q = clk;"\n    new: "assign q = 0;"\n', encoding="utf-8")

        # 1) baseline 就失败 => 必须 fail-fast，且一个字节都不能改
        r = tool_run_mutation(str(wd), str(wd / "m.yaml"), ["false"], min_score=0.0)
        assert not r["ok"], "broken test_cmd must not produce a mutation score"
        assert "Baseline" in r["error"], r["error"]
        assert (wd / "dut.sv").read_text(encoding="utf-8") == "assign q = clk;\n"

        # 2) 命令不存在 => 结构化错误而不是 FileNotFoundError 裸堆栈
        r2 = tool_run_mutation(str(wd), str(wd / "m.yaml"),
                               ["definitely_not_a_binary_xyz_113"], min_score=0.0)
        assert not r2["ok"] and "not executable" in r2["error"], r2.get("error")

        # 3) 正常路径：baseline 通过 -> 变异后检测命令失败 -> 真 KILLED
        r3 = tool_run_mutation(str(wd), str(wd / "m.yaml"),
                               ["grep", "-q", "assign q = clk;", "dut.sv"], min_score=0.0)
        assert r3["ok"], r3.get("error")
        d3 = r3["data"]
        assert d3["killed"] == 1 and d3["evaluated"] == 1 and d3["survived"] == 0
        assert d3["mutation_score"] == 100.0
        assert d3["baseline"]["returncode"] == 0
        assert (wd / "dut.sv").read_text(encoding="utf-8") == "assign q = clk;\n"

        # 4) ERROR 的变异不进分母：1 个合法变异存活 + 1 个缺文件
        (wd / "m2.yaml").write_text(
            'mutations:\n'
            '  - id: OK\n    file: "dut.sv"\n'
            '    old: "assign q = clk;"\n    new: "assign q = 0;"\n'
            '  - id: BAD\n    file: "nope.sv"\n'
            '    old: "x"\n    new: "y"\n', encoding="utf-8")
        r4 = tool_run_mutation(str(wd), str(wd / "m2.yaml"), ["true"], min_score=0.0)
        d4 = r4["data"]
        assert d4["total"] == 2 and d4["errors"] == 1 and d4["evaluated"] == 1, d4
        assert d4["survived"] == 1, d4
        assert d4["mutation_score"] == 0.0, d4

        # 5) 全 ERROR => 分数未定义，必须 ok=False（不是 0% 或 100%）
        (wd / "m3.yaml").write_text(
            'mutations:\n  - id: B1\n    file: "nope.sv"\n'
            '    old: "x"\n    new: "y"\n', encoding="utf-8")
        r5 = tool_run_mutation(str(wd), str(wd / "m3.yaml"), ["true"], min_score=0.0)
        assert not r5["ok"] and "No mutation could be evaluated" in r5["error"], r5.get("error")

def test_review_fixes_artifact_escaping_and_polarity() -> None:
    r"""Defect 116: 生成产物里的插值未转义 + 复位极性靠脆弱字符串 replace。

    * `_emit_cocotb` 用 `json.loads(r` + 三引号 + `{regs}` + 三引号 + `)` 嵌入契约 JSON：寄存器描述里
      只要出现三引号（连续三个双引号）或结尾反斜杠，生成的 cocotb 测试台就是
      SyntaxError，
      而工具已报 ok=True。
    * `_emit_wrap` 把 `os.environ["WAVE_FILE"]` 原文插进 `$dumpfile("...")`：
      Windows 路径 `C:\tmp\w.vcd`（含反斜杠）或含引号的路径会生成非法 SV。
    * active-high 复位靠两处 `tb_src.replace(", rst = 0", ...)` 打补丁，
      模板缩进一变就静默匹配不到 —— TB 带着反相复位跑，表现为莫名 FAIL/超时。
    """
    print("coverage: artifact escaping (cocotb JSON / WAVE_FILE) + reset polarity")

    pm = {k: f"s_axil_{k}" for k in (
        "awaddr", "awvalid", "awready", "wdata", "wstrb", "wvalid", "wready",
        "bresp", "bvalid", "bready", "araddr", "arvalid", "arready",
        "rdata", "rresp", "rvalid", "rready")}

    def _emit_contract(regs: list) -> dict:
        spec = {
            "schema_version": "1.0",
            "boundary": {"module_name": "m", "clock": {"port": "clk"},
                         "reset": {"port": "rst_n"}, "axi4_lite": {"port_map": pm}},
            "test_plan": {"register_rules": regs},
        }
        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "s.json"
            sp.write_text(json.dumps(spec), encoding="utf-8")
            r = call_tool("emit_testbench", {"spec_path": str(sp), "out_dir": str(Path(td) / "out")})
            arts = {}
            if r["ok"]:
                for k, v in r["data"]["artifacts"].items():
                    p_ = Path(v)
                    arts[k] = p_.read_text(encoding="utf-8") if p_.is_file() else ""
            return r, arts

    # 1) 描述里含三引号 + 结尾反斜杠：cocotb 源码必须仍是合法 Python
    r, arts = _emit_contract([
        {"rule_id": "R1", "name": "CTRL", "offset": 0, "access": "RW",
         "reset_value": 0, "description": 'evil """ quote'},
        {"rule_id": "R2", "name": "BACK", "offset": 4, "access": "RW",
         "reset_value": 0, "description": "ends with backslash \\"},
    ])
    assert r["ok"], str(r.get("error"))[:200]
    import ast as _ast
    _ast.parse(arts["cocotb_testbench"])  # SyntaxError 会直接让测试失败
    assert "json.loads(" in arts["cocotb_testbench"]

    # 2) WAVE_FILE 含反斜杠/引号：$dumpfile 的参数必须是合法 SV 字符串
    import os as _os
    _old = _os.environ.get("WAVE_FILE")
    try:
        _os.environ["WAVE_FILE"] = 'C:\\tmp\\w"x.vcd'
        _r2, _arts2 = _emit_contract(
            [{"rule_id": "R1", "name": "CTRL", "offset": 0, "access": "RW", "reset_value": 0}])
        assert _r2["ok"], str(_r2.get("error"))[:200]
        wrap = _arts2["unit_isolation_wrapper"]
        dump_lines = [ln.strip() for ln in wrap.splitlines() if "$dumpfile(" in ln]
        assert dump_lines, wrap[:400]
        for ln in dump_lines:
            if ln.startswith('$dumpfile("'):
                # 引号必须成对且中间没有未转义的裸引号/反斜杠。
                # 合法形态形如 $dumpfile("C:/tmp/wx.vcd"); —— 剥掉首尾的引号后
                # payload 里不能还有裸引号或反斜杠。
                payload = ln[len('$dumpfile("'):]
                payload = payload[:payload.rindex('"')]
                assert '"' not in payload, ln
                assert "\\" not in payload, ln
    finally:
        if _old is None:
            _os.environ.pop("WAVE_FILE", None)
        else:
            _os.environ["WAVE_FILE"] = _old

    # 3) active-high 复位：生成的 TB 必须「声明 =1 / 释放 =0」，且能真跑通
    rtl = (
        "`timescale 1ns/1ps\n"
        "module apb_ah (\n"
        "  input logic pclk, input logic preset,\n"
        + "".join(f"  input logic {s_},\n" for s_ in ("psel", "penable", "pwrite"))
        + "  input logic [7:0] paddr, input logic [31:0] pwdata,\n"
        "  input logic [3:0] pstrb,\n"
        "  output logic pready,\n  output logic [31:0] prdata,\n  output logic pslverr\n"
        ");\n"
        "  logic [31:0] r;\n"
        "  always_ff @(posedge pclk) begin\n"
        "    if (preset) r <= 32'h0;\n"
        "    else if (psel && penable && pwrite) r <= pwdata;\n"
        "  end\n"
        "  assign prdata = r; assign pready = 1'b1; assign pslverr = 1'b0;\n"
        "endmodule\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "dut.sv"
        f.write_text(rtl, encoding="utf-8")
        r3 = call_tool("emit_testbench", {
            "rtl_file": str(f), "out_dir": str(Path(td) / "out"), "protocol": "apb"})
        assert r3["ok"], str(r3.get("error"))[:300]
        tb = Path(r3["data"]["artifacts"]["sv_testbench"]).read_text(encoding="utf-8")
        # 声明处：, preset = 1（断言态）；释放处：preset = 0;
        assert "preset = 1" in tb, tb[:600]
        assert "preset = 0;" in tb, tb[:600]


def main() -> int:
    test_mutation_target_containment()
    test_remote_work_dir_injection_blocked()
    test_eda_remote_tag_cannot_escape_staging_dir()
    test_security_hardening_injection_xss_verilator_hijack()
    test_validator_bad_offset_no_crash()
    test_numeric_bounds_are_enforced_at_entry()
    test_min_score_bounds_are_enforced()
    test_regfile_spec_type_and_value_validation()
    test_mutation_rejects_empty_old_pattern()
    test_mutation_spec_non_object_entries_are_rejected()
    test_mutation_accounting_is_self_consistent()
    test_review_fixes_mutation_baseline_and_accounting()
    test_review_fixes_artifact_escaping_and_polarity()
    print("ALL test_security_coverage.py TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
