"""Tool 10: Verilator + slang simulation runner with stdbuf log retention and suite identity checks."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result
from .eda_env import probe_eda_env


def verify_suite_identities(log_text: str, expected_suites: List[str]) -> Dict[str, Any]:
    """Ported from agent/verilog/suite_verify.py: verify each named suite passed, not just PASS line count."""
    results: Dict[str, str] = {}
    current: Optional[str] = None
    # 真实 sim.sh 打印的 header 是 `== sim: <name> (top=<top>) ==`，套件名后可能
    # 带 (top=...) / (WAIT=...) 等参数。旧正则要求套件名后紧跟 `==`，对真实日志
    # 永远匹配不到 → 把所有套件误判为 missing，「逐套件身份核对」这个反静默
    # 通过护栏对真实日志从不生效（实测踩过）。
    hdr_re = re.compile(r"==\s*sim:\s*([A-Za-z0-9_-]+)")
    for line in log_text.splitlines():
        m_hdr = hdr_re.search(line)
        if m_hdr:
            current = m_hdr.group(1)
            results.setdefault(current, "STARTED")
        if current:
            if re.search(r"\bFAIL\b|\$fatal|ERROR:", line):
                results[current] = "FAIL"
            elif re.search(r"\bPASS\b", line) and results.get(current) != "FAIL":
                results[current] = "PASS"
    missing = [s for s in expected_suites if s not in results]
    failed = [s for s in expected_suites if results.get(s) != "PASS" and s not in missing]
    return {
        "expected": expected_suites,
        "observed": results,
        "missing": missing,
        "failed": failed,
        "all_verified": not missing and not failed,
    }


def _collect_waveforms(dirs: List[Path]) -> List[Dict[str, Any]]:
    """扫描给定目录下的波形文件（VCD / FST / LXT2 / WLF / SHM）。

    波形是仿真最直接的证据：断言挂了但没人知道周期号时，只能靠波形回溯。
    但"有波形"不能只凭 TB 里写了 `$dumpvars` —— Verilator 在没有 `--trace`
    时只打一条 `-Info: $dumpvar ignored, as Verilated without --trace`，
    仿真照常 exit 0 并报 PASS，波形文件根本不存在（实测复现）。
    所以必须**在文件系统上确认产物存在且非空**。
    """
    pats = {".vcd": "VCD", ".fst": "FST", ".lxt2": "LXT2", ".wlf": "WLF", ".shm": "SHM"}
    found: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            kind = pats.get(p.suffix.lower())
            if not kind:
                continue
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            found.append({
                "path": str(p),
                "kind": kind,
                "size_bytes": size,
                "empty": size == 0,
            })
    return found


def _collect_logs(dirs: List[Path], exclude: set[str]) -> List[Dict[str, Any]]:
    """收集完整日志文件（.log / .txt / .rpt），排除已作为波形处理的文件。"""
    found: List[Dict[str, Any]] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file() or str(p.resolve()) in exclude:
                continue
            if p.suffix.lower() not in (".log", ".txt", ".rpt"):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            found.append({"path": str(p), "size_bytes": size, "empty": size == 0})
    return found


def _fst_available(verilator_bin: str) -> tuple[bool, str]:
    """探测 --trace-fst 是否真的能构建（FST 需要 lz4 的**开发头文件**）。

    真实踩坑：本机 Verilator 5.054 自建时装了 liblz4 运行时（liblz4.so.1），
    但没有 lz4.h，于是 `--trace-fst` 在链接阶段 fatal error: lz4.h: No such
    file。更糟的是如果 obj_* 里已有 VCD 二进制，脚本会**复用它**，产出一个
    名为 .fst、内容其实是 VCD 的假文件——只检查扩展名完全看不出问题。

    这里做一个极小规模的编译探测，把「不支持」变成显式错误，而不是静默
    降级或产出假格式文件。
    """
    import subprocess as _sp

    probe_src = (
        'module probe; initial begin $dumpfile("a.fst"); $dumpvars(0, probe); $finish; end\n'
        "endmodule\n"
    )
    with tempfile.TemporaryDirectory(prefix="fst_probe_") as td:
        sv = Path(td) / "probe.sv"
        sv.write_text(probe_src, encoding="utf-8")
        try:
            proc = _sp.run(
                [verilator_bin, "--binary", "--trace-fst", "-j", "1", "-Wno-fatal",
                 "--top-module", "probe", str(sv), "-o", "sim", "-Mdir", str(Path(td) / "obj")],
                capture_output=True, text=True, timeout=180, check=False,
            )
        except (OSError, _sp.TimeoutExpired) as exc:
            return False, f"could not probe --trace-fst: {exc}"
        if proc.returncode != 0:
            blob = (proc.stdout or "") + (proc.stderr or "")
            if "lz4" in blob.lower():
                return False, (
                    "verilator --trace-fst needs the lz4 development headers (lz4.h) "
                    "at build time. Install liblz4-dev / lz4-devel, or use waveform_format='vcd'."
                )
            first = next((ln.strip() for ln in blob.splitlines() if ln.strip()), "unknown error")
            return False, f"verilator --trace-fst build failed: {first[:200]}"
    return True, ""


def tool_run_sim(
    sim_script: str,
    suite: str = "all",
    expected_suites: Optional[List[str]] = None,
    timeout_sec: int = 300,
    seed: Optional[int] = None,
    wait_cycles: Optional[int] = None,
    require_slang: bool = False,
    log_dir: Optional[str] = None,
    waveform: bool = False,
    waveform_format: str = "vcd",
    require_waveform: bool = False,
) -> Dict[str, Any]:
    # MCP / CLI 调用方常把数字参数序列化成字符串（JSON number → str），
    # schema 虽声明 integer，但 call_tool 不做类型强制。若直接把 str 传给
    # subprocess.run(timeout=...)，其内部 `timeout - (now - start)` 会报
    # `unsupported operand type(s) for +: 'float' and 'str'`（实测踩过）。
    # 这里在边界显式转换并给出可读错误，而不是让 TypeError 冒泡。
    try:
        timeout_sec = int(timeout_sec)
    except (TypeError, ValueError):
        return err_result("run_sim", f"timeout_sec must be an integer, got {timeout_sec!r}")
    for _name, _val in (("seed", seed), ("wait_cycles", wait_cycles)):
        if _val is not None:
            try:
                int(_val)
            except (TypeError, ValueError):
                return err_result("run_sim", f"{_name} must be an integer, got {_val!r}")
    seed = int(seed) if seed is not None else None
    wait_cycles = int(wait_cycles) if wait_cycles is not None else None

    # 波形格式白名单（避免把非法值透传到 verilator CLI 而得到晦涩报错）
    waveform_format = (waveform_format or "vcd").lower().strip()
    if waveform_format not in ("vcd", "fst"):
        return err_result(
            "run_sim",
            f"waveform_format must be 'vcd' or 'fst', got {waveform_format!r}",
            diagnostics=[{"severity": "ERROR", "code": "E024", "path": "waveform_format",
                          "message": f"Unsupported waveform format {waveform_format!r}."}],
        )
    # require_waveform 而没有 waveform 是自相矛盾的组合：要求「必须有波形」
    # 却不让脚本开启 --trace，必然什么也抓不到。旧实现直接放行、返回 ok=True
    # （还会把上一次运行遗留的波形文件误当成这次的证据）——典型的静默通过。
    if require_waveform and not waveform:
        return err_result(
            "run_sim",
            "require_waveform=true needs waveform=true: without --trace verilator "
            "silently ignores $dumpvars, so no waveform can ever be produced.",
            diagnostics=[{"severity": "ERROR", "code": "E024", "path": "require_waveform",
                          "message": "require_waveform=true requires waveform=true."}],
        )

    script_p = Path(sim_script)
    if not script_p.is_file():
        return err_result("run_sim", f"Simulation entry script not found: {sim_script}")

    env_info = probe_eda_env()
    if not env_info["verilator"]["meets_min_version"]:
        return err_result(
            "run_sim",
            f"Verilator requirement unmet: {env_info['verilator']['message']}",
            diagnostics=[{"severity": "ERROR", "code": "E020", "path": "eda_env.verilator", "message": env_info["verilator"]["message"]}],
        )
    if require_slang and not env_info["slang"]["available"]:
        return err_result(
            "run_sim",
            "slang is required (require_slang=true) but not found on PATH.",
            diagnostics=[{"severity": "ERROR", "code": "E021", "path": "eda_env.slang", "message": "slang binary not found."}],
        )

    # FST 需要 lz4 开发头文件；不支持时必须显式报错，不能静默降级成 VCD
    # （那会产出「后缀 .fst、内容是 VCD」的假文件）。
    if waveform and waveform_format == "fst":
        fst_ok, fst_msg = _fst_available(str(env_info["verilator"]["path"]))
        if not fst_ok:
            return err_result(
                "run_sim",
                f"waveform_format='fst' is not usable in this environment: {fst_msg}",
                diagnostics=[{"severity": "ERROR", "code": "E025", "path": "waveform_format",
                              "message": fst_msg}],
            )

    cmd: List[str] = []
    if env_info["stdbuf"]["available"]:
        cmd.extend(["stdbuf", "-oL", "-eL"])
    cmd.extend(["bash", str(script_p.resolve()), suite])

    import os
    import time

    run_env = os.environ.copy()
    run_env["VERILATOR"] = str(env_info["verilator"]["path"])
    if seed is not None:
        run_env["SEED"] = str(seed)
    if wait_cycles is not None:
        run_env["WAIT"] = str(wait_cycles)

    # 波形 / 日志抓取：
    #   WAVE=1 让生成的 sim 脚本给 verilator 加 --trace / --trace-fst
    #   WAVE_FORMAT 选择 vcd / fst
    #   WAVE_DIR 存在时 TB 的 $dumpfile 会优先写到该目录（脚本侧使用）
    # 关键：Verilator 在没有 --trace 时**静默忽略** $dumpvars，
    # 只打一条 -Info，仿真仍 PASS、波形文件不存在。所以下面必须做产物断言。
    wave_dir_p: Optional[Path] = None
    if waveform:
        run_env["WAVE"] = "1"
        run_env["WAVE_FORMAT"] = waveform_format
        if log_dir:
            wave_dir_p = Path(log_dir).resolve()
            wave_dir_p.mkdir(parents=True, exist_ok=True)
            run_env["WAVE_DIR"] = str(wave_dir_p)
    elif log_dir:
        wave_dir_p = Path(log_dir).resolve()
        wave_dir_p.mkdir(parents=True, exist_ok=True)

    # 运行前对已有波形做快照。只看 mtime 不可靠：上一次运行刚产生、或
    # 文件系统时间精度较粗时，旧波形会被误认成「本次产出」——于是
    # require_waveform 在「这次其实没抓到」的情况下也能通过（静默通过）。
    # 用 (path -> (size, mtime)) 比对，只认新增或发生变化的文件。
    pre_wave_snapshot: Dict[str, tuple] = {}
    for d in [script_p.parent.resolve()] + ([Path(log_dir).resolve()] if log_dir else []):
        for w in _collect_waveforms([d]):
            try:
                st = Path(w["path"]).stat()
                pre_wave_snapshot[w["path"]] = (st.st_size, st.st_mtime)
            except OSError:
                continue

    started_at = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(script_p.parent),
            env=run_env,
            capture_output=True,
            text=True,
            # Windows 上 text=True 用 locale 编码（cp1252）解码，Verilator/sim.sh
            # 输出含非 ASCII 字节时抛 UnicodeDecodeError；显式 UTF-8 + replace
            # 保证日志总能被解析，而不是让编码问题打断仿真结果归一化。
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return err_result("run_sim", f"Simulation timed out after {timeout_sec}s: {exc}")
    elapsed = time.time() - started_at

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    identity = verify_suite_identities(combined, expected_suites) if expected_suites else None

    # ---- 完整日志持久化 ----
    # 旧实现只在返回值里放 stdout_tail/stderr_tail（各 4000/2000 字符），
    # 超长日志的开头被丢弃，事后无法回溯第一条断言失败附近的上下文。
    log_info: Optional[Dict[str, Any]] = None
    if log_dir:
        log_dir_p = Path(log_dir).resolve()
        log_dir_p.mkdir(parents=True, exist_ok=True)
        suite_tag = re.sub(r"[^\w.-]+", "_", suite or "all")
        log_path = log_dir_p / f"sim_{suite_tag}.log"
        log_path.write_text(combined, encoding="utf-8")
        log_info = {
            "path": str(log_path),
            "size_bytes": log_path.stat().st_size,
            "lines": combined.count("\n") + (1 if combined else 0),
            "truncated_in_response": len(combined) > 4000,
        }

    # ---- 波形产物收集与断言 ----
    # 搜索范围：脚本所在目录 + log_dir（sim 脚本把 obj_* 建在自己目录下，
    # 波形一般也落在 cwd，即脚本目录）。
    search_dirs: List[Path] = [script_p.parent.resolve()]
    if log_dir:
        search_dirs.append(Path(log_dir).resolve())
    waves = _collect_waveforms(search_dirs)
    # 只认「本次运行新增或发生变化」的波形：与运行前快照比对 (size, mtime)。
    # 单纯用 mtime >= started_at 会把上一次刚生成的文件算进来。
    # 注意：筛选必须**无条件**执行，不能只在 waveform=True 时做 —— 否则
    # waveform=False 时磁盘上的旧波形仍会出现在返回结果里，被调用方误读成
    # 「这次产出了波形」（实测：waveform=false + 旧 .vcd 时 files 非空）。
    fresh = []
    for w in waves:
        prev = pre_wave_snapshot.get(w["path"])
        try:
            st = Path(w["path"]).stat()
        except OSError:
            continue
        cur = (st.st_size, st.st_mtime)
        if prev is None or prev != cur:
            fresh.append(w)
    waves = fresh

    logs = _collect_logs(search_dirs, {w["path"] for w in waves})
    waste_evidence = {
        "requested": waveform,
        "format": waveform_format if waveform else None,
        "files": waves,
        "total_bytes": sum(w["size_bytes"] for w in waves),
        "empty_files": [w["path"] for w in waves if w["empty"]],
    }

    data = {
        "script": str(script_p.resolve()),
        "suite": suite,
        "returncode": proc.returncode,
        "elapsed_sec": round(elapsed, 3),
        "eda_env": env_info,
        "suite_identity": identity,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "log": log_info,
        "waveform": waste_evidence,
        "log_files": logs,
    }
    if proc.returncode != 0:
        return err_result("run_sim", f"Simulation exited with code {proc.returncode}.", data=data, diagnostics=[{"severity": "ERROR", "code": "E022", "path": suite, "message": data["stderr_tail"][:300] or "non-zero exit"}])
    if identity and not identity["all_verified"]:
        return err_result(
            "run_sim",
            f"Suite identity check failed (missing={identity['missing']}, failed={identity['failed']}).",
            data=data,
            diagnostics=[{"severity": "ERROR", "code": "E023", "path": suite, "message": "Silent skip or failed suite detected."}],
        )

    # 波形要求下的产物断言：请求了波形就必须真的有非空波形文件。
    # 最典型的成因是 Verilator 缺 --trace —— 它只打一条 -Info 就静默跳过
    # $dumpvars，仿真 PASS、exit 0，而波形不存在。若不在这里拦，调用方会
    # 以为"波形抓到了"，等调试时才发现是空的。
    if require_waveform:
        if not waves:
            hint = (
                " Verilator silently ignores $dumpvars without --trace; "
                "make sure the sim script passes --trace/--trace-fst (run_sim sets WAVE=1)."
                if waveform else ""
            )
            return err_result(
                "run_sim",
                f"waveform required but no waveform file was produced (searched {[str(d) for d in search_dirs]}).{hint}",
                data=data,
                diagnostics=[{
                    "severity": "ERROR", "code": "E024", "path": "waveform",
                    "message": f"No non-empty waveform file found.{hint}",
                }],
            )
        if all(w["empty"] for w in waves):
            return err_result(
                "run_sim",
                "waveform required but every waveform file is empty (0 bytes).",
                data=data,
                diagnostics=[{"severity": "ERROR", "code": "E024", "path": "waveform",
                              "message": "Waveform file(s) exist but are all zero-length."}],
            )
    # waveform=True 但 require_waveform=False 时也要给信号：最常见成因是脚本
    # 不响应 WAVE=1（用户手写的 sim.sh 不知道 WAVE 这个变量）。波形静默没开，
    # 仿真 PASS、files 为空，调用方容易误以为"波形开了只是没人看"。降级为
    # WARNING（W014）不阻塞 ok=True —— 毕竟没要求硬性保证 —— 但必须进
    # diagnostics（调用方读的就是这个字段，埋在 data 里等于没说）。
    w014: List[Dict[str, Any]] = []
    if waveform and not waves:
        w014 = [{"severity": "WARNING", "code": "W014", "path": "waveform",
                 "message": (
                     "waveform=true but no waveform file was produced. The sim script "
                     "may not honour WAVE=1 / --trace (hand-written scripts need to add "
                     "--trace themselves). Use require_waveform=true to make this a hard "
                     "error."
                 )}]
        data["waveform_warnings"] = w014
    return ok_result("run_sim", data, diagnostics=w014)


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("run_sim"))
