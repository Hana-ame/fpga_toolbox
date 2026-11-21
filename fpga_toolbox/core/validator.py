"""Contract YAML/JSON loader, semantic analyzer, and live RTL port cross-checker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .sv_parser import parse_sv_file
from .types import (
    AXIL_SLAVE_EXPECTED_DIR,
    OPTIONAL_AXIL_SIGNALS,
    REQUIRED_AXIL_SIGNALS,
    RESP_CODE_MAP,
    VALID_ACCESS_MODES,
    AxiLiteBoundaryIR,
    BoundaryIR,
    ClockSpecIR,
    CompiledUnitIR,
    Diagnostic,
    IllegalAddrRuleIR,
    OutOfScopePortIR,
    ProtocolRuleIR,
    RegisterRuleIR,
    ResetSpecIR,
    StressProfileIR,
    TestPlanIR,
)


def _to_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    return val if isinstance(val, int) else int(str(val), 0)


def load_raw_spec(spec_path: Path) -> Dict[str, Any]:
    text = spec_path.read_text(encoding="utf-8")
    data = json.loads(text) if spec_path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Specification must be a YAML/JSON mapping.")
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
    for idx, oos in enumerate(boundary.out_of_scope_ports):
        if oos.port not in rports:
            diags.append(Diagnostic("ERROR", "E011", f"boundary.out_of_scope_ports[{idx}].port", f"Port '{oos.port}' missing in RTL."))


def build_and_validate_ir(
    raw: Dict[str, Any], rtl_path: Optional[Path] = None
) -> Tuple[Optional[CompiledUnitIR], List[Diagnostic]]:
    diags: List[Diagnostic] = []
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

    clk_raw, rst_raw = b_raw.get("clock", {}), b_raw.get("reset", {})
    axil_raw = b_raw.get("axi4_lite", {})
    addr_w, data_w = _to_int(axil_raw.get("addr_width"), 12), _to_int(axil_raw.get("data_width"), 32)
    role = str(axil_raw.get("role", "slave")).lower()
    pm = {str(k): str(v) for k, v in axil_raw.get("port_map", {}).items()}

    if data_w not in (32, 64):
        diags.append(Diagnostic("ERROR", "E002", "boundary.axi4_lite.data_width", f"Expected 32 or 64, got {data_w}."))
    for sig in REQUIRED_AXIL_SIGNALS:
        if sig not in pm or not pm[sig].strip():
            diags.append(Diagnostic("ERROR", "E001", f"boundary.axi4_lite.port_map.{sig}", f"Missing required '{sig}'."))
    for opt in OPTIONAL_AXIL_SIGNALS:
        if opt not in pm:
            diags.append(Diagnostic("WARNING", "W001", f"boundary.axi4_lite.port_map.{opt}", f"Optional '{opt}' unmapped."))

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
        off = _to_int(r.get("offset"), -1)
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
        regs.append(RegisterRuleIR(
            str(r.get("rule_id", f"REG_{idx:03d}")), rname, off,
            _to_int(r.get("reset_value"), 0), acc,
            _to_int(r.get("write_mask"), dmask if acc != "RO" else 0),
            _to_int(r.get("read_mask"), dmask if acc != "WO" else 0),
        ))

    ills: List[IllegalAddrRuleIR] = []
    for idx, ill in enumerate(t_raw.get("illegal_addr_rules", [])):
        off = _to_int(ill.get("offset"), -1)
        resp = str(ill.get("expected_resp", "SLVERR")).upper()
        if off in seen_off:
            diags.append(Diagnostic("ERROR", "E008", f"test_plan.illegal_addr_rules[{idx}].offset", f"Collides with '{seen_off[off]}'."))
        if resp not in RESP_CODE_MAP:
            diags.append(Diagnostic("ERROR", "E006", f"test_plan.illegal_addr_rules[{idx}].expected_resp", f"Unknown '{resp}'."))
        ills.append(IllegalAddrRuleIR(str(ill.get("rule_id", f"ILL_{idx:03d}")), off, resp))

    st_raw = t_raw.get("stress_profile", {})
    tp_ir = TestPlanIR(
        proto_ir, tuple(regs), tuple(ills),
        StressProfileIR(bool(st_raw.get("random_backpressure", True)), _to_int(st_raw.get("iterations"), 50), _to_int(st_raw.get("seed"), 20261120)),
    )
    if any(d.severity == "ERROR" for d in diags):
        return None, diags
    return CompiledUnitIR(boundary_ir, tp_ir), diags
