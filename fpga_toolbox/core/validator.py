"""Contract YAML/JSON loader, semantic analyzer, and live RTL port cross-checker."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .sv_parser import parse_sv_file
from .types import (
    APB_MASTER_EXPECTED_DIR,
    APB_SLAVE_EXPECTED_DIR,
    AXIL_SLAVE_EXPECTED_DIR,
    OPTIONAL_AXIL_SIGNALS,
    REQUIRED_APB_SIGNALS,
    REQUIRED_AXIL_SIGNALS,
    RESP_CODE_MAP,
    VALID_ACCESS_MODES,
    ApbBoundaryIR,
    AxiLiteBoundaryIR,
    BitFieldIR,
    BoundaryIR,
    ClockSpecIR,
    CompiledUnitIR,
    Diagnostic,
    IllegalAddrRuleIR,
    OutOfScopePortIR,
    ProtocolRuleIR,
    QualityGatesIR,
    RegisterRuleIR,
    ResetSpecIR,
    StressProfileIR,
    TestPlanIR,
)

# SystemVerilog 标识符（用于校验会被拼进文件名的名字，见 E016）。
_SV_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _to_int(val: Any, default: int = 0) -> int:
    """把 spec 里的整数字段解析成 int；无法解析时返回 default 而不是抛。

    背景（真实踩坑）：`offset` 早先已改成容错的 `_to_int_or_none`（缺陷 103），
    但其余 17 处调用点（addr_width / data_width / reset_value / write_mask /
    max_resp_cycles / iterations / width / seed ...）仍是裸 `int(str(val), 0)`。
    契约 spec 由用户/AI 手写，`boundary.axi4_lite.addr_width: "abc"` 会让
    `validate_contract` 直接抛 `ValueError: invalid literal for int() with
    base 0: 'abc'` —— 裸堆栈、无字段定位、不进诊断码体系，与 offset 那条
    修好的路径完全不一致。

    返回 default 比返回 None 更合适：这些字段都有明确默认值，非法值退化为
    默认不会让下游拿到 None 再二次崩溃；需要「必须报错」的字段
    （offset）走 `_to_int_or_none` + E005 诊断。
    """
    if val is None:
        return default
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    try:
        return int(str(val), 0)
    except (ValueError, TypeError):
        return default


def _to_int_or_none(val: Any) -> Optional[int]:
    """把用户手写 spec 里的整数字段解析成 int；解析不出返回 None 而不抛。

    背景（真实踩坑）：spec 由用户/AI 手写，``offset`` 可能写成非数字字符串
    （``"abc"``）或浮点（``4.5``）。旧实现 ``_to_int`` 里 ``int(str(val), 0)``
    直接抛 ``ValueError`` 冒泡 —— 用户只看到一句 ``invalid literal for int()``
    堆栈，完全没指向是哪个字段的 offset 写错了，也没落进诊断码体系。返回
    None 让调用方按既有 E005（offset 非法）路径给出可定位的诊断。
    """
    if val is None:
        return None
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    try:
        return int(str(val), 0)
    except (ValueError, TypeError):
        return None


def _parse_bits(bits_raw: Any) -> Optional[Tuple[int, int]]:
    s = str(bits_raw).strip()
    if ":" in s:
        parts = s.split(":", 1)
        try:
            hi, lo = int(parts[0].strip()), int(parts[1].strip())
            return (hi, lo) if hi >= lo >= 0 else None
        except ValueError:
            return None
    try:
        v = int(s)
        return (v, v) if v >= 0 else None
    except ValueError:
        return None


def _normalize_regfile_spec_v2(data: Dict[str, Any]) -> Dict[str, Any]:
    """Adapt agent/verilog apb-regfile-spec/v2 JSON directly into unified boundary + test_plan shape."""
    bus = data.get("bus", {})
    addr_w = _to_int(bus.get("addr_width"), 8)
    data_w = _to_int(bus.get("data_width"), 32)
    wait_cycles = _to_int(bus.get("wait_cycles"), 0)
    # `registers` 必须是列表。旧实现直接 `for idx, r in enumerate(data.get(...))`：
    # 用户把 registers 写成 mapping（`{"CTRL": {...}}`）时，迭代得到的是**键
    # 字符串**，随后 `r.get(...)` 抛裸 `AttributeError: 'str' object has no
    # attribute 'get'` —— 未捕获异常漏出，ok=false 但错误信息与"registers 类型
    # 不对"毫无关联。
    _regs = data.get("registers", [])
    if not isinstance(_regs, list):
        raise ValueError(
            "apb-regfile-spec: 'registers' must be a list of register objects, got "
            f"{type(_regs).__name__}. Use a JSON/YAML array (e.g. registers: [...])."
        )
    regs_out: List[Dict[str, Any]] = []
    _bad_offset: List[str] = []
    _bad_reset: List[str] = []
    for idx, r in enumerate(_regs):
        if not isinstance(r, dict):
            raise ValueError(
                f"apb-regfile-spec: registers[{idx}] must be an object, got "
                f"{type(r).__name__}."
            )
        # offset / reset 是**必须能解析**的字段：静默回退到默认值会让
        # `offset: "zzz"`（应为 0x 开头或十进制）被判成合法，寄存器被悄悄
        # 挪到 idx*4 的位置；`reset: "zz"` 同样被当成 0。实测两者都返回
        # ok=true + 零诊断。改为收集后统一报 E005。
        _off_raw = r.get("offset")
        _off = _to_int_or_none(_off_raw) if _off_raw is not None else idx * 4
        if _off is None:
            _bad_offset.append(f"{r.get('name', f'REG_{idx}')}={_off_raw!r}")
            _off = idx * 4
        _rst_raw = r.get("reset")
        _rst = _to_int_or_none(_rst_raw) if _rst_raw is not None else 0
        if _rst is None:
            _bad_reset.append(f"{r.get('name', f'REG_{idx}')}={_rst_raw!r}")
            _rst = 0
        entry: Dict[str, Any] = {
            "rule_id": f"REG_{idx:03d}",
            "name": str(r.get("name", f"REG_{idx}")),
            "offset": _off,
            "reset_value": _rst,
            "access": str(r.get("attr", "RW")).upper(),
        }
        if isinstance(r.get("fields"), list):
            entry["fields"] = r["fields"]
        regs_out.append(entry)
    # 无法解析的字段通过 `_spec_warnings` 通道带回（load_raw_spec 之后会转成
    # diagnostics）。放在 boundary 之外的自定义键上，不影响 IR 结构。
    _warn: List[str] = []
    if _bad_offset:
        _warn.append("BitfieldSpec: invalid offset value(s) "
                     + ", ".join(_bad_offset)
                     + " — expected an integer or 0x-prefixed hex; fell back to index*4.")
    if _bad_reset:
        _warn.append("BitfieldSpec: invalid reset value(s) "
                     + ", ".join(_bad_reset)
                     + " — expected an integer or 0x-prefixed hex; fell back to 0.")
    return {
        "__spec_warnings__": _warn,
        "boundary": {
            "module_name": str(data.get("module", "apb_slave_regfile")),
            "rtl_files": [],
            "clock": {"port": "PCLK", "freq_mhz": 100.0},
            "reset": {"port": "PRESETn", "active_low": True, "sync": True},
            "apb": {
                "addr_width": addr_w,
                "data_width": data_w,
                "role": "slave",
                "wait_cycles": wait_cycles,
                "port_map": {s: s.upper() for s in REQUIRED_APB_SIGNALS},
            },
            "out_of_scope_ports": [],
        },
        "test_plan": {
            "protocol_rules": {"check_valid_stability": True, "check_reset_outputs": True, "max_resp_cycles": 16, "allow_unaligned_addr": False},
            "register_rules": regs_out,
            "illegal_addr_rules": [],
            "stress_profile": {"random_backpressure": True, "iterations": 32, "seed": 20261120},
        },
    }


def load_raw_spec(spec_path: Path) -> Dict[str, Any]:
    # 契约 YAML/JSON 可能由用户手写，注释里带 GBK/Shift-JIS/latin-1 字节
    # （实测：latin-1 注释 0xE9 会让 utf-8 解码抛 UnicodeDecodeError 直接冒泡，
    # 报一句 CPython 内部异常、完全没指向编码）。用 errors="replace" 容错：
    # 注释里的乱码不影响 key/value 结构解析。JSON 是 ASCII 子集、不受影响；
    # YAML 注释行被解析器忽略。
    text = spec_path.read_text(encoding="utf-8", errors="replace")
    data = json.loads(text) if spec_path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Specification must be a YAML/JSON mapping.")
    if str(data.get("$schema", "")).startswith("apb-regfile-spec/") and "boundary" not in data:
        normalized = _normalize_regfile_spec_v2(data)
        # 归一化过程中发现的可疑字段（非法 offset/reset）通过保留键带回，
        # 由 build_and_validate_ir 转成诊断。不直接在这里 append diags 是因为
        # load_raw_spec 的返回类型是裸 dict（无 diags 通道）。
        _warns = normalized.pop("__spec_warnings__", [])
        if _warns:
            normalized["__spec_warnings__"] = _warns
        return normalized
    return data


def _cross_check_rtl(boundary: BoundaryIR, rtl_path: Path, diags: List[Diagnostic]) -> None:
    mods = parse_sv_file(rtl_path)
    target = next((m for m in mods if m.name == boundary.module_name), None)
    if target is None:
        diags.append(Diagnostic("ERROR", "E011", "boundary.module_name", f"Module '{boundary.module_name}' not in '{rtl_path}'."))
        return
    rports = {p.name: p for p in target.ports}
    for label, pname in (("boundary.clock.port", boundary.clock.port), ("boundary.reset.port", boundary.reset.port)):
        if pname not in rports:
            diags.append(Diagnostic("ERROR", "E011", label, f"Port '{pname}' missing in RTL '{target.name}'."))
    for canon, rname in boundary.axil.port_map.items():
        if rname not in rports:
            diags.append(Diagnostic("ERROR", "E011", f"boundary.axi4_lite.port_map.{canon}", f"Port '{rname}' missing in RTL."))
            continue
        exp = AXIL_SLAVE_EXPECTED_DIR.get(canon)
        if exp and rports[rname].direction != exp:
            diags.append(Diagnostic("ERROR", "E012", f"boundary.axi4_lite.port_map.{canon}", f"Expected '{exp}', got '{rports[rname].direction}'."))
    if boundary.apb is not None:
        exp_map = APB_SLAVE_EXPECTED_DIR if boundary.apb.role == "slave" else APB_MASTER_EXPECTED_DIR
        for canon, rname in boundary.apb.port_map.items():
            if rname not in rports:
                diags.append(Diagnostic("ERROR", "E011", f"boundary.apb.port_map.{canon}", f"Port '{rname}' missing in RTL."))
                continue
            exp = exp_map.get(canon)
            if exp and rports[rname].direction != exp:
                diags.append(Diagnostic("ERROR", "E012", f"boundary.apb.port_map.{canon}", f"Expected '{exp}', got '{rports[rname].direction}'."))
    for idx, oos in enumerate(boundary.out_of_scope_ports):
        if oos.port not in rports:
            diags.append(Diagnostic("ERROR", "E011", f"boundary.out_of_scope_ports[{idx}].port", f"Port '{oos.port}' missing in RTL."))


def build_and_validate_ir(
    raw: Dict[str, Any], rtl_path: Optional[Path] = None
) -> Tuple[Optional[CompiledUnitIR], List[Diagnostic]]:
    diags: List[Diagnostic] = []
    # 归一化阶段（_normalize_regfile_spec_v2）发现的非法字段：offset/reset
    # 解析失败时曾静默回退到默认值，导致 ok=true + 零诊断。这里统一转成 E005。
    for _w in raw.pop("__spec_warnings__", []) or []:
        diags.append(Diagnostic("ERROR", "E005", "registers", str(_w)))
    b_raw, t_raw = raw.get("boundary"), raw.get("test_plan")
    if not isinstance(b_raw, dict):
        diags.append(Diagnostic("ERROR", "E000", "boundary", "Missing 'boundary' section."))
    if not isinstance(t_raw, dict):
        diags.append(Diagnostic("ERROR", "E000", "test_plan", "Missing 'test_plan' section."))
    if diags:
        return None, diags

    mod_name = str(b_raw.get("module_name", "")).strip()
    if not mod_name:
        diags.append(Diagnostic("ERROR", "E000", "boundary.module_name", "module_name is required."))
    elif not _SV_IDENT_RE.match(mod_name):
        # module_name 不只是一个字符串：它会被拼进**生成产物的文件名**
        # （`<mod>_axil_wrap.sv` / `test_<mod>_axil.py` / `Makefile` 的
        # TOPLEVEL+MODULE），而契约 spec 由用户/AI 手写。实测两类真实后果：
        #   * `../../EVILMOD` → 3 个产物被写到 out_dir **之外**
        #     （Path 拼接里的 `..` 直接生效，工具仍报 ok=True）；
        #   * `m$(shell touch PWNEDMK)_x` → Makefile 里出现 `$(shell ...)`，
        #     用户执行 `make` 即执行任意命令（实测文件被真实创建）。
        # SV 标识符本身只允许 [A-Za-z_][A-Za-z0-9_$]，因此按该集合收口即可，
        # 既不误伤合法模块名（含 `$` 的 SV 标识符是合法的），也堵死路径分隔符、
        # 空白、shell/Make 元字符与点号。
        diags.append(Diagnostic(
            "ERROR", "E016", "boundary.module_name",
            f"Invalid module_name {mod_name!r}: must be a SystemVerilog identifier "
            "([A-Za-z_][A-Za-z0-9_$]*). It is interpolated into generated artifact "
            "filenames and the cocotb Makefile, so path separators, '..', spaces, "
            "dots and shell/Make metacharacters are rejected (path traversal / "
            "command injection).",
        ))

    clk_raw, rst_raw = b_raw.get("clock", {}), b_raw.get("reset", {})
    axil_raw = b_raw.get("axi4_lite")
    apb_raw = b_raw.get("apb")

    # Preserve full backward compatibility: if neither axi4_lite nor apb is provided, validate axi4_lite as before.
    has_apb = isinstance(apb_raw, dict)
    has_axil = isinstance(axil_raw, dict) or not has_apb

    axil_dict = axil_raw if isinstance(axil_raw, dict) else {}
    addr_w = _to_int(axil_dict.get("addr_width"), _to_int((apb_raw or {}).get("addr_width"), 12))
    data_w = _to_int(axil_dict.get("data_width"), _to_int((apb_raw or {}).get("data_width"), 32))
    role = str(axil_dict.get("role", "slave")).lower()
    pm = {str(k): str(v) for k, v in axil_dict.get("port_map", {}).items()}

    # 非法整数字段不再崩溃（_to_int 容错），但静默退化到默认值同样是假信息：
    # 用户写了 addr_width: "abc" 会得到"一切正常"的 12 位默认值。给一条
    # WARNING 说明该字段被忽略并用了默认值，值本身仍带在 message 里。
    for _label, _raw, _eff in (
        ("boundary.axi4_lite.addr_width", axil_dict.get("addr_width"), addr_w),
        ("boundary.axi4_lite.data_width", axil_dict.get("data_width"), data_w),
    ):
        if _raw is not None and _to_int_or_none(_raw) is None:
            diags.append(Diagnostic(
                "WARNING", "W010", _label,
                f"Ignoring non-integer value {_raw!r}; using default {_eff}.",
            ))

    if has_axil:
        if data_w not in (32, 64):
            diags.append(Diagnostic("ERROR", "E002", "boundary.axi4_lite.data_width", f"Expected 32 or 64, got {data_w}."))
        for sig in REQUIRED_AXIL_SIGNALS:
            if sig not in pm or not pm[sig].strip():
                diags.append(Diagnostic("ERROR", "E001", f"boundary.axi4_lite.port_map.{sig}", f"Missing required '{sig}'."))
        for opt in OPTIONAL_AXIL_SIGNALS:
            if opt not in pm:
                diags.append(Diagnostic("WARNING", "W001", f"boundary.axi4_lite.port_map.{opt}", f"Optional '{opt}' unmapped."))

    apb_ir: Optional[ApbBoundaryIR] = None
    if has_apb:
        apb_aw = _to_int(apb_raw.get("addr_width"), 8)
        apb_dw = _to_int(apb_raw.get("data_width"), 32)
        apb_role = str(apb_raw.get("role", "slave")).lower()
        apb_pm = {str(k): str(v) for k, v in apb_raw.get("port_map", {}).items()}
        if apb_dw not in (32, 64):
            diags.append(Diagnostic("ERROR", "E002", "boundary.apb.data_width", f"Expected 32 or 64, got {apb_dw}."))
        for sig in REQUIRED_APB_SIGNALS:
            if sig not in apb_pm or not apb_pm[sig].strip():
                diags.append(Diagnostic("ERROR", "E001", f"boundary.apb.port_map.{sig}", f"Missing required APB '{sig}'."))
        apb_ir = ApbBoundaryIR(apb_aw, apb_dw, apb_role, apb_pm, _to_int(apb_raw.get("wait_cycles"), 0))

    oos_list = []
    for idx, p in enumerate(b_raw.get("out_of_scope_ports", [])):
        p_dir = str(p.get("dir", "input")).lower()
        if p_dir == "input" and p.get("tie_off") is None:
            diags.append(Diagnostic("ERROR", "E010", f"boundary.out_of_scope_ports[{idx}].tie_off", "Input port needs tie_off."))
        oos_list.append(OutOfScopePortIR(
            port=str(p.get("port", "")),
            direction=p_dir,
            width=_to_int(p.get("width"), 1),
            tie_off=str(p["tie_off"]) if p.get("tie_off") is not None else None,
            observe_only=bool(p.get("observe_only", p_dir == "output")),
        ))

    boundary_ir = BoundaryIR(
        module_name=mod_name,
        instance_hierarchy=str(b_raw["instance_hierarchy"]) if b_raw.get("instance_hierarchy") else None,
        rtl_files=tuple(str(f) for f in b_raw.get("rtl_files", [])),
        parameters=dict(b_raw.get("parameters", {})),
        clock=ClockSpecIR(str(clk_raw.get("port", "aclk")), float(clk_raw.get("freq_mhz", 100.0))),
        reset=ResetSpecIR(str(rst_raw.get("port", "aresetn")), bool(rst_raw.get("active_low", True)), bool(rst_raw.get("sync", True))),
        axil=AxiLiteBoundaryIR(addr_w, data_w, role, pm),
        out_of_scope_ports=tuple(oos_list),
        apb=apb_ir,
    )

    if rtl_path is not None:
        _cross_check_rtl(boundary_ir, rtl_path, diags)

    proto_raw = t_raw.get("protocol_rules", {})
    max_resp = _to_int(proto_raw.get("max_resp_cycles"), 16)
    if max_resp <= 0:
        diags.append(Diagnostic("ERROR", "E009", "test_plan.protocol_rules.max_resp_cycles", "Must be > 0."))
    proto_ir = ProtocolRuleIR(
        bool(proto_raw.get("check_valid_stability", True)),
        bool(proto_raw.get("check_reset_outputs", True)),
        max_resp,
        bool(proto_raw.get("allow_unaligned_addr", False)),
    )

    align, max_addr, dmask = max(1, data_w // 8), 1 << min(addr_w, 64), (1 << data_w) - 1 if data_w in (32, 64) else 0xFFFFFFFF
    seen_off: Dict[int, str] = {}
    regs: List[RegisterRuleIR] = []
    for idx, r in enumerate(t_raw.get("register_rules", [])):
        off = _to_int_or_none(r.get("offset"))
        if off is None:
            # offset 写成非数字（"abc" / 4.5）——按 E005 报可定位诊断而非让
            # int() 的 ValueError 冒泡成堆栈（缺陷 103）。用 continue 跳过对齐/
            # 重复判定，避免多个非法 offset 都落成 -1 而级联出假 E004 重复。
            diags.append(Diagnostic("ERROR", "E005", f"test_plan.register_rules[{idx}].offset",
                                    f"Offset must be an integer, got {r.get('offset')!r}."))
            continue
        acc = str(r.get("access", "RW")).upper()
        rname = str(r.get("name", f"reg_{idx}"))
        if off < 0 or off >= max_addr:
            diags.append(Diagnostic("ERROR", "E005", f"test_plan.register_rules[{idx}].offset", f"Out of range: 0x{off:X}."))
        elif off % align != 0 and not proto_ir.allow_unaligned_addr:
            diags.append(Diagnostic("ERROR", "E003", f"test_plan.register_rules[{idx}].offset", f"Unaligned offset 0x{off:X}."))
        if off in seen_off:
            diags.append(Diagnostic("ERROR", "E004", f"test_plan.register_rules[{idx}].offset", f"Duplicate offset 0x{off:X}."))
        else:
            seen_off[off] = rname
        if acc not in VALID_ACCESS_MODES:
            diags.append(Diagnostic("ERROR", "E006", f"test_plan.register_rules[{idx}].access", f"Invalid access '{acc}'."))

        # Bitfield validation (E013 overlap, E014 out-of-width)
        fields_ir: List[BitFieldIR] = []
        used_bits: Dict[int, str] = {}
        for fidx, fld in enumerate(r.get("fields", []) or []):
            fname = str(fld.get("name", f"field_{fidx}"))
            facc = str(fld.get("attr", fld.get("access", acc))).upper()
            if facc not in VALID_ACCESS_MODES:
                diags.append(Diagnostic("ERROR", "E006", f"test_plan.register_rules[{idx}].fields[{fidx}].attr", f"Invalid field access '{facc}'."))
            bits_raw = fld.get("bits", "0")
            rng = _parse_bits(bits_raw)
            if rng is None:
                # 区分两种失败：bits 写成 hi<lo（如 "0:3" 低位在前）vs 非数字。
                # 旧实现两者都报 E014 "exceed data_width"，对反序 bits 是误导
                # （"0:3" 明明在 32 位内）。这里给专门的 E015 提示真实原因。
                sbits = str(bits_raw).strip()
                if ":" in sbits:
                    diags.append(Diagnostic("ERROR", "E015", f"test_plan.register_rules[{idx}].fields[{fidx}].bits", f"Bitfield '{fname}' bits '{sbits}' has hi < lo — use high:low order (e.g. '3:0')."))
                else:
                    diags.append(Diagnostic("ERROR", "E014", f"test_plan.register_rules[{idx}].fields[{fidx}].bits", f"Bitfield '{fname}' bits '{sbits}' is not a valid bit range."))
                continue
            if rng[0] >= data_w:
                diags.append(Diagnostic("ERROR", "E014", f"test_plan.register_rules[{idx}].fields[{fidx}].bits", f"Bitfield '{fname}' bits '{bits_raw}' exceed data_width {data_w}."))
                continue
            hi, lo = rng
            for bpos in range(lo, hi + 1):
                if bpos in used_bits:
                    diags.append(Diagnostic("ERROR", "E013", f"test_plan.register_rules[{idx}].fields[{fidx}].bits", f"Bit {bpos} of '{fname}' overlaps with '{used_bits[bpos]}'."))
                    break
                used_bits[bpos] = fname
            fields_ir.append(BitFieldIR(fname, hi, lo, facc, _to_int(fld.get("reset", fld.get("reset_value")), 0), str(fld.get("description", ""))))

        regs.append(RegisterRuleIR(
            str(r.get("rule_id", f"REG_{idx:03d}")), rname, off,
            _to_int(r.get("reset_value"), 0), acc,
            _to_int(r.get("write_mask"), dmask if acc not in ("RO", "RSVD", "RC") else 0),
            _to_int(r.get("read_mask"), dmask if acc != "WO" else 0),
            tuple(fields_ir),
        ))

    ills: List[IllegalAddrRuleIR] = []
    for idx, ill in enumerate(t_raw.get("illegal_addr_rules", [])):
        # 缺陷 103：非法 addr 的 offset 同样可能是非数字，用 _to_int_or_none
        # 而非 _to_int，避免 int() ValueError 冒泡成堆栈。
        off = _to_int_or_none(ill.get("offset"))
        if off is None:
            diags.append(Diagnostic("ERROR", "E008", f"test_plan.illegal_addr_rules[{idx}].offset",
                                    f"Offset must be an integer, got {ill.get('offset')!r}."))
            continue
        resp = str(ill.get("expected_resp", "SLVERR")).upper()
        if off in seen_off:
            diags.append(Diagnostic("ERROR", "E008", f"test_plan.illegal_addr_rules[{idx}].offset", f"Collides with '{seen_off[off]}'."))
        if resp not in RESP_CODE_MAP:
            diags.append(Diagnostic("ERROR", "E006", f"test_plan.illegal_addr_rules[{idx}].expected_resp", f"Unknown '{resp}'."))
        ills.append(IllegalAddrRuleIR(str(ill.get("rule_id", f"ILL_{idx:03d}")), off, resp))

    st_raw = t_raw.get("stress_profile", {})
    qg_raw = raw.get("quality_gates", t_raw.get("quality_gates", {})) or {}
    qg_ir = QualityGatesIR(
        min_rtl_coverage=float(qg_raw.get("min_rtl_coverage", 90.0)),
        min_mutation_score=float(qg_raw.get("min_mutation_score", 80.0)),
        require_lint_clean=bool(qg_raw.get("require_lint_clean", True)),
        require_suite_identity_check=bool(qg_raw.get("require_suite_identity_check", True)),
    )
    tp_ir = TestPlanIR(
        proto_ir, tuple(regs), tuple(ills),
        StressProfileIR(bool(st_raw.get("random_backpressure", True)), _to_int(st_raw.get("iterations"), 50), _to_int(st_raw.get("seed"), 20261120)),
        qg_ir,
    )
    if any(d.severity == "ERROR" for d in diags):
        return None, diags
    return CompiledUnitIR(boundary_ir, tp_ir), diags
