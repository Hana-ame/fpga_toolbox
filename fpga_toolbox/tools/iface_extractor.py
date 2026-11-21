"""Tool 4: Extract AXI4-Lite, AXI4-Stream, clk/rst, and sideband ports from RTL AST."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..core.sv_parser import SVModuleAST, SVPortAST, parse_sv_file
from ..core.types import REQUIRED_AXIL_SIGNALS, err_result, ok_result

_AXIL_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("awaddr",  re.compile(r"(?:^|_)aw_?addr$", re.I)),
    ("awprot",  re.compile(r"(?:^|_)aw_?prot$", re.I)),
    ("awvalid", re.compile(r"(?:^|_)aw_?(?:valid|vld)$", re.I)),
    ("awready", re.compile(r"(?:^|_)aw_?(?:ready|rdy)$", re.I)),
    ("wdata",   re.compile(r"(?:^|_)w_?data$", re.I)),
    ("wstrb",   re.compile(r"(?:^|_)w_?strb$", re.I)),
    ("wvalid",  re.compile(r"(?:^|_)w_?(?:valid|vld)$", re.I)),
    ("wready",  re.compile(r"(?:^|_)w_?(?:ready|rdy)$", re.I)),
    ("bresp",   re.compile(r"(?:^|_)b_?resp$", re.I)),
    ("bvalid",  re.compile(r"(?:^|_)b_?(?:valid|vld)$", re.I)),
    ("bready",  re.compile(r"(?:^|_)b_?(?:ready|rdy)$", re.I)),
    ("araddr",  re.compile(r"(?:^|_)ar_?addr$", re.I)),
    ("arprot",  re.compile(r"(?:^|_)ar_?prot$", re.I)),
    ("arvalid", re.compile(r"(?:^|_)ar_?(?:valid|vld)$", re.I)),
    ("arready", re.compile(r"(?:^|_)ar_?(?:ready|rdy)$", re.I)),
    ("rdata",   re.compile(r"(?:^|_)r_?data$", re.I)),
    ("rresp",   re.compile(r"(?:^|_)r_?resp$", re.I)),
    ("rvalid",  re.compile(r"(?:^|_)r_?(?:valid|vld)$", re.I)),
    ("rready",  re.compile(r"(?:^|_)r_?(?:ready|rdy)$", re.I)),
)

_AXIS_PATS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("tdata",  re.compile(r"(?:^|_)t_?data$", re.I)),
    ("tvalid", re.compile(r"(?:^|_)t_?(?:valid|vld)$", re.I)),
    ("tready", re.compile(r"(?:^|_)t_?(?:ready|rdy)$", re.I)),
    ("tlast",  re.compile(r"(?:^|_)t_?last$", re.I)),
    ("tkeep",  re.compile(r"(?:^|_)t_?keep$", re.I)),
)


# Clock / reset naming. Anchored, so ``reset_done`` or ``clk_div2`` never match,
# while every common prefix style (``s_axi_aresetn``, ``i_rst_n``, ``core_clk``) does.
_CLK_RE = re.compile(r"^(?:[a-z0-9]+_)*(?:[aps]?clk|clock)$", re.I)
_RST_RE = re.compile(r"^(?:[a-z0-9]+_)*(?:[aps]?reset|[aps]?rst|nreset|nrst)(?:_?[nb])?$", re.I)


def _norm_prefix(raw: str) -> str:
    return re.sub(r"^(?:i_|o_|in_|out_)", "", raw, flags=re.I).strip("_").lower() or "default"


def _is_clock(name: str) -> bool:
    return bool(_CLK_RE.match(name))


def _is_reset(name: str) -> bool:
    return bool(_RST_RE.match(name))


def _reset_active_low(name: str) -> bool:
    n = name.lower()
    if re.search(r"(?:^|_)n(?:rst|reset)$", n):
        return True
    if n.endswith(("_n", "_b")):
        return True
    return bool(re.match(r"^(?:[a-z0-9]+_)*[aps]?(?:reset|rst)[nb]$", n))


def classify_module(mod: SVModuleAST) -> Dict[str, Any]:
    claimed, clk, rst = set(), None, None
    for p in mod.ports:
        if p.direction != "input" or p.width != 1:
            continue
        if clk is None and _is_clock(p.name):
            clk = p
            claimed.add(p.name)
        elif rst is None and _is_reset(p.name):
            rst = p
            claimed.add(p.name)


    ifaces: List[Dict[str, Any]] = []
    for proto, pats, min_cnt in (("axi4_lite", _AXIL_PATS, 6), ("axi4_stream", _AXIS_PATS, 2)):
        groups: Dict[str, Dict[str, SVPortAST]] = {}
        for p in mod.ports:
            if p.name in claimed:
                continue
            for canon, pat in pats:
                m = pat.search(p.name)
                if m:
                    groups.setdefault(_norm_prefix(p.name[:m.start()]), {})[canon] = p
                    break
        for grp, sigs in groups.items():
            if len(sigs) < min_cnt:
                continue
            claimed.update(p.name for p in sigs.values())
            v_port = sigs.get("awvalid") or sigs.get("arvalid") or sigs.get("tvalid")
            role = ("slave" if v_port.direction == "input" else "master") if v_port else "unknown"
            aw_p = sigs.get("awaddr") or sigs.get("araddr")
            dw_p = sigs.get("wdata") or sigs.get("rdata") or sigs.get("tdata")
            req = REQUIRED_AXIL_SIGNALS if proto == "axi4_lite" else ("tdata", "tvalid", "tready")
            ifaces.append({
                "protocol": proto,
                "group_prefix": grp,
                "role": role,
                "addr_width": aw_p.width if aw_p else None,
                "data_width": dw_p.width if dw_p else None,
                "port_map": {k: v.name for k, v in sigs.items()},
                "missing_signals": [s for s in req if s not in sigs],
            })

    oos = []
    for p in mod.ports:
        if p.name in claimed:
            continue
        if p.direction == "input":
            item = {"port": p.name, "dir": "input", "width": p.width, "tie_off": f"{p.width}'b0"}
            if _is_clock(p.name) or _is_reset(p.name):
                item["warning"] = "clock/reset-like port left out of scope; verify tie-off is intended"
            oos.append(item)
        else:
            oos.append({"port": p.name, "dir": p.direction, "width": p.width, "observe_only": True})
    return {
        "module_name": mod.name,
        "rtl_file": mod.file_path,
        "parameters": mod.parameters,
        "total_ports": len(mod.ports),
        "clock": {"port": clk.name, "freq_mhz": 100.0} if clk else None,
        "reset": {"port": rst.name, "active_low": _reset_active_low(rst.name), "sync": True} if rst else None,
        "interfaces": ifaces,
        "out_of_scope_ports": oos,
    }


def tool_extract_interfaces(
    rtl_file: str,
    module_name: Optional[str] = None,
    emit_draft_yaml: Optional[str] = None,
    staged_rtl_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    p = Path(rtl_file)
    if not p.is_file():
        return err_result("extract_interfaces", f"RTL file not found: {rtl_file}")
    mods = parse_sv_file(p)
    if not mods:
        return err_result("extract_interfaces", f"No modules found in {rtl_file}")
    selected = [m for m in mods if m.name == module_name] if module_name else mods
    if not selected:
        return err_result("extract_interfaces", f"Module '{module_name}' not found.")

    extracted = [classify_module(m) for m in selected]
    data: Dict[str, Any] = {"rtl_file": str(p.resolve()), "modules": extracted}

    diagnostics: List[Dict[str, Any]] = []
    for m in extracted:
        if m["clock"] is None:
            diagnostics.append({"severity": "WARNING", "code": "W010", "path": m["module_name"], "message": "No clock port detected; defaulting to aclk@100MHz."})
        if m["reset"] is None:
            diagnostics.append({"severity": "WARNING", "code": "W011", "path": m["module_name"], "message": "No reset port detected; defaulting to aresetn active-low."})

    if emit_draft_yaml:
        m0 = extracted[0]
        axil = next((i for i in m0["interfaces"] if i["protocol"] == "axi4_lite"), None)
        boundary: Dict[str, Any] = {
            "module_name": m0["module_name"],
            "rtl_files": staged_rtl_files or [m0["rtl_file"]],
            "parameters": m0["parameters"],
            "clock": m0["clock"] or {"port": "aclk", "freq_mhz": 100.0},
            "reset": m0["reset"] or {"port": "aresetn", "active_low": True, "sync": True},
            "axi4_lite": {
                "addr_width": (axil["addr_width"] or 12) if axil else 12,
                "data_width": (axil["data_width"] or 32) if axil else 32,
                "role": "slave",
                "port_map": axil["port_map"] if axil else {},
            },
            "out_of_scope_ports": m0["out_of_scope_ports"],
        }
        if m0.get("instance_hierarchy"):
            boundary["instance_hierarchy"] = m0["instance_hierarchy"]
        draft = {
            "boundary": boundary,
            "test_plan": {
                "protocol_rules": {"check_valid_stability": True, "check_reset_outputs": True, "max_resp_cycles": 16, "allow_unaligned_addr": False},
                "register_rules": [{"rule_id": "REG_001", "name": "CTRL_REG", "offset": 0, "reset_value": 0, "access": "RW", "write_mask": 0xFFFFFFFF, "read_mask": 0xFFFFFFFF}],
                "illegal_addr_rules": [{"rule_id": "ILL_001", "offset": 0xFC, "expected_resp": "SLVERR"}],
                "stress_profile": {"random_backpressure": True, "iterations": 32, "seed": 20261120},
            },
        }
        out_p = Path(emit_draft_yaml)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(yaml.safe_dump(draft, sort_keys=False, allow_unicode=True), encoding="utf-8")
        data["draft_yaml_path"] = str(out_p.resolve())

    return ok_result("extract_interfaces", data, diagnostics=diagnostics)
