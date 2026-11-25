"""Tool 9: Static anti-pitfall linter for SV testbenches and verification scripts.

Consolidates three battle-tested linters from agent/verilog:
  * Clocking race checks (C1, C2): blocking assignments on interface signals at posedge
  * AXI handshake deadlock checks (R1-R4): checking VALID without READY or dropping READY early
  * Silent-pass checks (S1-S3): constant-return stubs, SKIP returning 0, count-only PASS checks
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    __package__ = "fpga_toolbox"

from .core.types import err_result, ok_result

# interface 端口的 blocking drive：`instance.signal = value`（点号层次赋值）。
# 旧实现只认 `axi.|apb.` 前缀，漏报任意其他 interface 实例名（mst_if/slv_if/
# u_if0/bus 等）——真实 TB 的 interface 实例名千变万化。放宽为「点号连接的
# 层次赋值」，C1/C2 只在 @(posedge) 上下文触发，误报风险可控。
# interface 端口的 blocking drive：`instance.signal = value`（点号层次赋值）。
# 旧实现只认 `axi.|apb.` 前缀，漏报任意其他 interface 实例名。但放宽到「任意
# instance.」会误报非 interface 的层次引用（如 u_dut.internal_reg = x）。
# 折中：识别 SV interface 实例的常见命名 —— *_if / *_bus / axi / apb，
# 这覆盖了绝大多数真实写法，且不会把 u_dut 这类模块实例误当 interface。
# interface 端口的 blocking drive：`instance.signal = value`（点号层次赋值）。
# 覆盖常见 AXI/APB interface 实例命名约定：*_if / *_bus / axi / apb / s_axi /
# m_axi / s_apb / u_axi / axi_m 等。C1/C2 只在 @(posedge) 上下文触发，误报风险可控。
_IFACE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*_(?:if|bus)|[sm]_[ap]x[i]|axi|apb)\.[A-Za-z_][A-Za-z0-9_]*\s*=(?!=)", re.IGNORECASE)
_WAIT_RE = re.compile(r"@\s*\(\s*posedge|@\s*\(\s*negedge|repeat\s*\(|#\s*\d")
_READY_OF = {
    "AWVALID": "AWREADY",
    "WVALID": "WREADY",
    "ARVALID": "ARREADY",
    "BVALID": "BREADY",
    "RVALID": "RREADY",
}


def _strip_comment(raw: str) -> str:
    """剥掉行注释，但跳过字符串内的 //。

    旧实现用 `_strip_comment(raw)`：字符串里的 // 被误判为注释起点，
    导致该行后续的真实代码被丢弃。实测：
        a = 1'b1; $display("path = a//b"); @(posedge clk);
    朴素分割 → 'a = 1\'b1; $display("path = a'，@(posedge clk); 丢失
    —— 后续代码里的握手/时钟检查被漏掉，lint 判定不准。

    这里逐字符扫描，正确处理字符串边界与反斜杠转义（与
    visual_renderer.py 的注释高亮同一算法）。
    """
    in_str = False
    esc_ch = False
    for i, ch in enumerate(raw):
        if esc_ch:
            esc_ch = False
            continue
        if ch == "\\" and in_str:
            esc_ch = True
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch == "/" and i + 1 < len(raw) and raw[i + 1] == "/":
            return raw[:i]
    return raw


def scan_clocking(path: Path) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, raw in enumerate(lines):
        code = _strip_comment(raw)
        if not code.strip():
            continue
        if re.search(r"@\s*\(\s*posedge", code):
            if re.search(r"#\s*\d", code):
                continue
            if _IFACE_RE.search(code):
                issues.append({
                    "severity": "ERROR",
                    "code": "C1",
                    "path": f"{path}:{i + 1}",
                    "message": "Blocking interface drive on same line as @(posedge).",
                    "context": raw.strip(),
                })
                continue
            for k in range(i + 1, min(i + 4, len(lines))):
                nxt = _strip_comment(lines[k])
                if not nxt.strip():
                    continue
                if _WAIT_RE.search(nxt) or re.search(r"\b(axi|apb)\.[A-Za-z_]+\s*<=", nxt):
                    break
                if _IFACE_RE.search(nxt):
                    issues.append({
                        "severity": "ERROR",
                        "code": "C1",
                        "path": f"{path}:{k + 1}",
                        "message": "Blocking interface drive immediately after @(posedge) without delay.",
                        "context": lines[k].strip(),
                    })
                    break
                if re.search(r"[;$]|begin|if\s*\(", nxt):
                    break
        if re.search(r"always\s*@\s*\(\s*posedge", code):
            depth = 0
            for k in range(i, min(i + 60, len(lines))):
                nxt = _strip_comment(lines[k])
                if k > i and re.search(r"always\s*@|endmodule", nxt):
                    break
                if re.search(r"\bbegin\b", nxt):
                    depth += 1
                if re.search(r"\bend\b", nxt) and not re.search(r"\bend\w+", nxt):
                    depth -= 1
                    if depth <= 0 and k > i:
                        break
                if k > i and _IFACE_RE.search(nxt) and "<=" not in nxt:
                    issues.append({
                        "severity": "ERROR",
                        "code": "C2",
                        "path": f"{path}:{k + 1}",
                        "message": "Blocking interface drive inside always @(posedge) block.",
                        "context": lines[k].strip(),
                    })
    return issues


def scan_handshake(path: Path) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    def _ready_raised_before(idx: int, ready_sig: str, window: int = 40) -> bool:
        # ready_sig 是规范大写（BREADY），但源码里可能是小写（bready）。加
        # re.IGNORECASE 才能识别小写 TB 里「READY 已拉高」—— 否则 R1/R3/R2 全部
        # 误判成「READY 未拉高」而报假错误（缺陷 106，与 104/105 同根）。
        for j in range(idx - 1, max(0, idx - window) - 1, -1):
            ln = _strip_comment(lines[j])
            if re.search(rf"\b{ready_sig}\b\s*(<=|=)\s*1'b0", ln, re.IGNORECASE):
                return False
            if re.search(rf"\b{ready_sig}\b\s*(<=|=)\s*1", ln, re.IGNORECASE):
                return True
        return False

    for i, raw in enumerate(lines):
        ln = _strip_comment(raw)
        if not ln.strip():
            continue
        # 检测「等 VALID 但未同时检查 READY」的握手风险。两种等价写法都要覆盖：
        #   while (!BVALID) @(posedge clk);   ← 旧实现只抓这种
        #   wait (BVALID);                    ← 第三方 TB 广泛使用，旧实现漏报
        # 两者语义相同：等待 VALID 期间未同时检查对应 READY。
        # 兼容裸信号名（bvalid）与带前缀（axi.BVALID），大小写不敏感。
        mwait = re.search(r"\bwait\s*\(([^)]*)\)", ln, re.IGNORECASE)
        if mwait:
            cond = mwait.group(1)
            has_v = re.search(r"([A-Za-z_][A-Za-z0-9_.]*VALID)", cond, re.IGNORECASE)
            has_r = re.search(r"[A-Za-z_][A-Za-z0-9_.]*READY", cond, re.IGNORECASE)
            if has_v and not has_r:
                sig = has_v.group(1)
                # 去掉可选前缀后查 READY 映射（_READY_OF 键为全大写）
                ready = _READY_OF.get(sig.split(".")[-1].upper())
                safe = bool(ready and _ready_raised_before(i, ready))
                code = "R1" if safe else "R3"
                sev = "WARNING" if safe else "ERROR"
                issues.append({
                    "severity": sev,
                    "code": code,
                    "path": f"{path}:{i + 1}",
                    "message": f"wait() waits only on {sig} without checking {ready or 'READY'}.",
                    "context": raw.strip(),
                })
        mwhile = re.search(r"while\s*\(([^)]*)\)", ln, re.IGNORECASE)
        if mwhile:
            cond = mwhile.group(1)
            # 兼容裸信号名（bvalid）与带前缀（axi.BVALID），大小写不敏感
            # （旧实现用 [A-Z_]+VALID 大写敏感，漏报小写 bvalid/rvalid）。
            has_v = re.search(r"([A-Za-z_][A-Za-z0-9_.]*VALID)", cond, re.IGNORECASE)
            has_r = re.search(r"[A-Za-z_][A-Za-z0-9_.]*READY", cond, re.IGNORECASE)
            if has_v and not has_r:
                sig = has_v.group(1)
                # 与上方 wait() 分支同一套归一：_READY_OF 键为全大写，信号可能带前缀
                # （axi.bvalid / axi.BVALID / 裸 bvalid）。旧实现用
                # sig.replace("axi.", "") 不做大小写归一 —— 实测
                # `while (!axi.bvalid)` 在 BREADY 已拉高时仍报 R3（假错误），而同一份
                # 代码写成 `while (!axi.BVALID)` 正确报 R1，纯大小写差异导致判定不同。
                # 这里改用 .split(".")[-1].upper()（与 wait() 分支一致）。
                ready = _READY_OF.get(sig.split(".")[-1].upper())
                safe = bool(ready and _ready_raised_before(i, ready))
                code = "R1" if safe else "R3"
                sev = "WARNING" if safe else "ERROR"
                issues.append({
                    "severity": sev,
                    "code": code,
                    "path": f"{path}:{i + 1}",
                    "message": f"while loop waits only on {sig} without checking READY.",
                    "context": raw.strip(),
                })
        # R4：等 VALID 后立刻拉低 READY。大小写不敏感（与 R1/R3 分支一致，缺陷 104）：
        # 真实第三方 TB 大量用小写（axi.bvalid / bready），旧正则 [A-Z_]+ 只认大写，
        # 小写 TB 静默漏报 R4。
        m4 = re.search(r"while\s*\(\s*!\s*(?:\()?\s*axi\.([A-Za-z_]+VALID)", ln, re.IGNORECASE)
        if m4:
            sig4 = m4.group(1)
            ready4 = _READY_OF.get(sig4.upper())
            if ready4:
                k = i
                if "begin" in ln:
                    depth = 1
                    for k in range(i + 1, min(i + 30, len(lines))):
                        body = _strip_comment(lines[k])
                        if re.search(r"\bbegin\b", body):
                            depth += 1
                        if re.search(r"\bend\b", body):
                            depth -= 1
                            if depth <= 0:
                                break
                for j in range(k + 1, min(k + 4, len(lines))):
                    nxt = _strip_comment(lines[j])
                    if re.search(r"@\s*\(|#\s*\d", nxt):
                        break
                    # 源码里 READY 可能是小写（bready），而 ready4 是规范大写 BREADY
                    # —— 加 re.IGNORECASE 才能匹配到小写拉低。
                    if re.search(rf"\b{ready4}\b\s*(<=|=)\s*1'b0", nxt, re.IGNORECASE):
                        issues.append({
                            "severity": "ERROR",
                            "code": "R4",
                            "path": f"{path}:{i + 1}",
                            "message": f"Drops {ready4} immediately after waiting on {sig4}.",
                            "context": raw.strip(),
                        })
                        break
        # R2：if(VALID) 分支内立刻拉低 READY。大小写不敏感（与 R1/R3/R4 一致，
        # 缺陷 104 同根）：真实第三方 TB 大量用小写，旧正则只认大写，静默漏报 R2。
        mif = re.search(r"if\s*\(\s*axi\.([A-Za-z_]+VALID)\s*\)", ln, re.IGNORECASE)
        if mif:
            sig = mif.group(1)
            ready = _READY_OF.get(sig.upper())
            if ready:
                danger = False
                for k in range(i, min(i + 6, len(lines))):
                    nxt = _strip_comment(lines[k])
                    if k > i and re.search(r"@\s*\(|repeat\s*\(|#\s*\d", nxt):
                        break
                    if re.search(rf"\b{ready}\b\s*(<=|=)\s*1'b0", nxt, re.IGNORECASE):
                        danger = True
                        break
                if danger:
                    issues.append({
                        "severity": "ERROR",
                        "code": "R2",
                        "path": f"{path}:{i + 1}",
                        "message": f"Drops {ready} immediately inside if({sig}) branch.",
                        "context": raw.strip(),
                    })
                elif not _ready_raised_before(i, ready):
                    issues.append({
                        "severity": "WARNING",
                        "code": "R1",
                        "path": f"{path}:{i + 1}",
                        "message": f"Checks {sig} without visible prior {ready}=1.",
                        "context": raw.strip(),
                    })
        # S1 扩展（SV function stub）：SV 的 function 是多行的，旧 S1 正则只匹配
        # shell 单行 stub（func() { return 0; }）。这里识别 SV function 内只有
        # return <常量>; 的 stub —— 真实踩坑：SV 验证 helper 函数返回常量 0/1
        # 时旧正则漏报。
        if re.search(r"\bfunction\b", ln, re.IGNORECASE):
            body = []
            for k in range(i, min(i + 25, len(lines))):
                bk = _strip_comment(lines[k]).strip()
                if k > i:
                    body.append(bk)
                if re.search(r"\bendfunction\b", bk):
                    break
            # return 一个**常量字面量**（无 if/else/case/for/while）→ stub。
            # 常量形态：纯整数（0 / -1）、SV 位宽字面量（1'b0 / 8'hFF）、
            # 未位宽 '0/'1、实数（0.0）。旧正则只认 `^return [-]?\d+;`，漏掉
            # SV 里最地道的常量 stub 写法（return 1'b0 / '0 / 8'hFF）——
            # 这些语义上同样是「helper 永远返回固定值、不含任何真实判定」。
            # 表达式（return a + 1; / return a == 0;）不匹配：字面量必须紧跟
            # return 且后面直接是分号，不能含标识符/运算符。
            _const_re = re.compile(
                r"""^return\s+(?:
                      [-+]?\d+                            # 纯整数
                    | [-+]?\d*'[sS]?[bBdDhHoO][0-9a-fA-FxXzZ_]+  # 位宽字面量 1'b0 / 8'hFF
                    | '[01xXzZ]                          # 未位宽 '0 / '1
                    | [-+]?\d+\.\d+                      # 实数 0.0
                    )\s*;$""",
                re.VERBOSE,
            )
            rets = [b for b in body if _const_re.match(b.strip())]
            # function 体只有 return 常量语句（无 if/else/case/for/while）→ stub
            if rets and len(body) <= 4 and not any(
                    re.search(r"\b(if|else|case|for|while)\b", b) for b in body):
                issues.append({
                    "severity": "ERROR",
                    "code": "S1",
                    "path": f"{path}:{i + 1}",
                    "message": "Verification helper function returns only a constant (stub).",
                    "context": lines[i].strip(),
                })
    return issues


def scan_sv_silent_pass(path: Path) -> List[Dict[str, Any]]:
    """SV testbench silent-pass check (S4).

    旧实现只把 .sh/.py 送进 silent_pass 扫描器，SV 测试台根本没有对应的检查。
    结果是文档/README 反复宣传的头号症状——「测试台只 $display("DONE") 就结束，
    没有任何判定」——lint_tb 完全看不见，返回 ok=True。

    真实形态：initial/always 块里跑完整激励，打印若干 $display，然后 $finish，
    全程没有 if/assert/$error/$fatal/check 任务去做判定。仿真必然「通过」，
    因为它从未表达过失败。

    判定口径（保守，避免误报真实 TB）：
      * 文件里存在 $display（说明它打算汇报结果）
      * 且不存在任何判定构造：assert / $error / $fatal / $assert / else $error
        以及自研 check 任务调用（check/expect/verify/fail/error）
      * 且不存在 if(...) 与 $error/$fatal 的邻近组合
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    # 去掉行注释与块注释，避免注释里的关键字造成漏报/误报
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    if not re.search(r"\$display\b", code):
        return []
    judges = [
        r"\bassert\b", r"\$error\b", r"\$fatal\b", r"\$assert\b", r"\$assertoff\b",
        r"\$\w*assert\w*\b",
        r"\b(?:check|expect|verify|compare|scoreboard|fail|error)(?:ing|_)?\w*\s*\(",
        r"\bif\s*\([^)]*\)[^;\n]*\$(?:error|fatal)\b",
    ]
    for pat in judges:
        if re.search(pat, code, re.IGNORECASE):
            return []
    # 定位第一个 $display，用它作为证据行号
    line_no = 1
    for i, ln in enumerate(text.splitlines(), 1):
        if re.search(r"\$display\b", ln):
            line_no = i
            break
    return [{
        "severity": "ERROR",
        "code": "S4",
        "path": f"{path}:{line_no}",
        "message": (
            "SV testbench emits $display but contains no pass/fail decision "
            "(no assert/$error/$fatal/check task). The simulation can only pass; "
            "it never expresses failure."
        ),
        "context": text.splitlines()[line_no - 1].strip(),
    }]


def scan_silent_pass(path: Path) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # S2: SKIP followed closely by return 0 / exit 0
        if re.search(r"\bSKIP\b", raw) and not stripped.startswith(("echo", "print")):
            for k in range(i, min(i + 4, len(lines))):
                if re.search(r"\b(return|exit)\s+0\b", lines[k]):
                    issues.append({
                        "severity": "WARNING",
                        "code": "S2",
                        "path": f"{path}:{k + 1}",
                        "message": "SKIP branch exits with status 0 (silent pass risk).",
                        "context": lines[k].strip(),
                    })
                    break
        # S3: Count-only PASS threshold (-ge N alongside PASS grep)
        if re.search(r"grep\s+-c.*PASS", raw) or re.search(r"passes.*-ge\s+\$?", raw):
            issues.append({
                "severity": "WARNING",
                "code": "S3",
                "path": f"{path}:{i + 1}",
                "message": "Count-only PASS check without verifying individual suite identities.",
                "context": stripped,
            })
        # S1: Shell/SV stub function immediately returning constant 0/1
        if re.match(r"^(?:function\s+)?[A-Za-z_]\w*\s*\(\)\s*\{\s*return\s+[01]\s*;?\s*\}$", stripped):
            issues.append({
                "severity": "ERROR",
                "code": "S1",
                "path": f"{path}:{i + 1}",
                "message": "Verification helper stub returns a constant value.",
                "context": stripped,
            })
    return issues


def _collect_targets(target_path: Path) -> Tuple[List[Path], List[Path]]:
    sv_files: List[Path] = []
    script_files: List[Path] = []
    if target_path.is_file():
        if target_path.suffix.lower() in (".sv", ".v"):
            sv_files.append(target_path)
        elif target_path.suffix.lower() in (".sh", ".py"):
            script_files.append(target_path)
        return sv_files, script_files
    for f in sorted(target_path.rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts or ".git" in f.parts:
            continue
        suf = f.suffix.lower()
        if suf in (".sv", ".v") and (f.name.startswith("tb_") or "vip" in f.parts or "driver" in f.name):
            sv_files.append(f)
        elif suf in (".sh", ".py") and not f.name.startswith("lint_"):
            script_files.append(f)
    return sv_files, script_files


def tool_lint_tb(
    target_path: str,
    rules: Optional[Sequence[str]] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    p = Path(target_path)
    if not p.exists():
        return err_result("lint_tb", f"Target path not found: {target_path}")
    enabled = set(rules) if rules else {"clocking", "handshake", "silent_pass"}
    sv_files, script_files = _collect_targets(p)
    findings: List[Dict[str, Any]] = []
    for f in sv_files:
        if "clocking" in enabled:
            findings.extend(scan_clocking(f))
        if "handshake" in enabled:
            findings.extend(scan_handshake(f))
        if "silent_pass" in enabled:
            findings.extend(scan_sv_silent_pass(f))
    for f in script_files:
        if "silent_pass" in enabled:
            findings.extend(scan_silent_pass(f))

    errors = [x for x in findings if x["severity"] == "ERROR"]
    warnings = [x for x in findings if x["severity"] == "WARNING"]
    clean = len(errors) == 0 and (not strict or len(warnings) == 0)
    data = {
        "target_path": str(p.resolve()),
        "scanned_sv_files": len(sv_files),
        "scanned_script_files": len(script_files),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "clean": clean,
        "findings": findings,
    }
    if not clean:
        # 违规时仍带 data：调用方需要结构化的 error_count/warning_count/findings
        # 汇总（与 run_mutation 等 ok=False 也带 data 的工具保持一致），
        # 否则只能从 diagnostics 重新解析，且拿不到计数字段。
        return err_result(
            "lint_tb",
            f"Lint failed with {len(errors)} error(s) and {len(warnings)} warning(s).",
            data=data,
            diagnostics=findings,
        )
    return ok_result("lint_tb", data, diagnostics=warnings)


if __name__ == "__main__":
    import sys

    from .cli import run_single_tool

    sys.exit(run_single_tool("lint_tb"))