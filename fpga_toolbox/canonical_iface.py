"""Canonical bus interface definitions + DUT port binding.

## Why this module exists

The old classifier matched DUT port names against **one regex per canonical
signal** (``psel`` / ``p_sel`` / ``psel_i`` ...).  Every naming convention
therefore needed a new pattern, and that space is unbounded:  ``psel``,
``p_sel``, ``psel_i``, ``psel_in``, ``s0_psel``, ``s_axi_psel``, ``PSel``,
``p_sel_o`` ...  Three separate defects (131 prefix groups, 166 direction
suffixes, 170 burst signals) were all the *same* root cause, and each fix was a
new patch on the same pile of patterns.

## The architecture used here

1. **Standard interface** (declared once per protocol): for each canonical
   signal we record the accepted roles -- direction for the slave viewpoint,
   width role, and whether it is required.  Nothing about spelling lives here
   beyond the canonical name itself.

2. **Binding layer**: maps arbitrary DUT port names onto a standard interface.
   A port name is normalized into *tokens* (lowercase, camelCase split, common
   direction suffix stripped).  The binding then looks for a **token suffix
   whose concatenation equals the canonical name** (so ``psel`` / ``p_sel`` /
   ``p_sel_i`` / ``s0_psel`` / ``s_axi_psel`` all bind to ``psel``), and treats
   the remaining leading tokens as the channel/instance prefix that forms the
   interface group.

3. The testbench is generated from the standard interface and connected through
   the binding, so naming style no longer influences emission at all.

The binding reports its evidence (which spelling matched, how many tokens were
consumed) and any ambiguity (two ports binding to the same canonical signal),
so a wrong or missing mapping is *visible* instead of silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Direction words that may be appended to (or prefixed onto) a port name.  They
# carry no protocol meaning -- they only tell the reader which way the signal
# flows -- so the binder strips them before matching.
#   psel_i / psel_o / psel_in / psel_out / psel_ib / psel_ob
_DIR_WORDS = frozenset(("i", "o", "in", "out", "ib", "ob", "ip", "op"))

# Polarity words that belong to the *canonical* name in some protocols
# (cs_n, ras_n, we_n, rst_n ...).  They must NOT be stripped as direction.
_POLARITY_WORDS = frozenset(("n", "b"))

_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def tokenize_port_name(name: str) -> List[str]:
    """Split a port name into lowercase tokens.

    Handles the naming styles seen in real RTL:

    * snake_case: ``s0_p_sel_i`` -> ``["s0", "p", "sel", "i"]``
    * camelCase / PascalCase: ``sAwAddr`` -> ``["s", "aw", "addr"]``
    * digit-separated channels: ``ch0_psel`` -> ``["ch0", "psel"]``
    * trailing direction word: stripped (``psel_i`` -> ``["psel"]``)

    The raw tokens are returned as split; stripping happens in
    :func:`_strip_trailing_dir` so callers can see what was removed.
    """
    # Split camelCase first on the original spacing, then split on '_'.
    parts: List[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", name):
        if not chunk:
            continue
        camel = _CAMEL_RE.findall(chunk)
        parts.extend(t.lower() for t in camel if t)
    return parts


def _strip_trailing_dir(tokens: List[str], canonical_concat: str) -> List[str]:
    """Remove trailing direction words unless they are part of the canonical name.

    ``psel_i`` -> ``["psel"]``; ``cs_n`` keeps ``n`` because ``csn`` is the
    canonical concatenation (so the suffix already matches without stripping).
    """
    out = list(tokens)
    while out and out[-1] in _DIR_WORDS:
        if "".join(out) == canonical_concat and len(out) > 1:
            break  # stripping would destroy a legitimate match
        candidate = out[:-1]
        if not candidate:
            break
        # Only strip when the remainder is still non-empty and the stripped word
        # is not needed for the canonical spelling.
        if "".join(candidate) and out[-1] not in _POLARITY_WORDS:
            out = candidate
        else:
            break
    return out


def _match_suffix(tokens: List[str], canonical: str) -> Optional[Tuple[int, str]]:
    """Find the longest token suffix whose concatenation equals ``canonical``.

    Returns ``(prefix_token_count, matched_concat)`` or ``None``.

    Comparing *concatenations* is what makes the binder spelling-agnostic:
    ``p_sel``, ``psel`` and ``PSel`` all concatenate to ``psel``; ``aw_addr``
    and ``awaddr`` both concatenate to ``awaddr``.
    """
    canon_concat = canonical.replace("_", "").lower()
    n = len(tokens)
    for take in range(n, 0, -1):
        suffix = tokens[n - take:]
        if "".join(suffix) == canon_concat:
            return (n - take, "".join(suffix))
    return None


@dataclass(frozen=True)
class CanonSignal:
    """One signal of a standard interface."""

    name: str
    #: expected direction from the **slave** device's viewpoint
    direction: str
    #: what the width means: addr / data / strb / id / len / size / burst / None
    width_role: Optional[str] = None
    #: required for the interface to be considered present
    required: bool = True
    #: alternative canonical spellings that must also bind (e.g. PCIe uses the
    #: AXI-Stream style tvalid/tdata/tlast on top of its own tx_/rx_ roles)
    aliases: Tuple[str, ...] = ()

    @property
    def concat(self) -> str:
        return self.name.replace("_", "").lower()

    def all_spellings(self) -> Tuple[str, ...]:
        return (self.name,) + tuple(self.aliases)


@dataclass(frozen=True)
class CanonInterface:
    """A standard interface: the contract every DUT is bound onto."""

    protocol: str
    signals: Tuple[CanonSignal, ...]
    #: minimum number of bound signals before we believe this protocol is present
    min_signals: int
    #: canonical names whose direction determines the DUT role (master/slave)
    role_probes: Tuple[str, ...] = ()
    #: canonical names that promote axi4_lite -> axi4
    burst_probes: Tuple[str, ...] = ("awlen", "arlen", "wlast", "rlast",
                                     "awburst", "arburst", "awsize", "arsize")
    #: tokens that MUST appear in the port name for this protocol (ddr3_*,
    #: hbm_* ...).  They are not stripped as a channel prefix; they are proof of
    #: the protocol and are consumed as part of the group identification.
    required_tokens: Tuple[str, ...] = ()
    #: tokens that must NOT appear (used to keep AXI4 bindings from swallowing
    #: HBM/PCIe ports that share the same AW/W/B/AR/R canonicals)
    forbidden_tokens: Tuple[str, ...] = ()
    #: tokens that carry protocol-internal meaning (PCIe rx_/tx_ direction) and
    #: therefore must NOT become part of the channel group prefix, otherwise the
    #: same interface would be split into rx-group and tx-group halves
    group_neutral_tokens: Tuple[str, ...] = ()

    def by_name(self) -> Dict[str, CanonSignal]:
        return {s.name: s for s in self.signals}


def _sig(name: str, direction: str, role: Optional[str] = None,
         required: bool = True, aliases: Tuple[str, ...] = ()) -> CanonSignal:
    return CanonSignal(name=name, direction=direction, width_role=role,
                       required=required, aliases=aliases)


CANONICAL_INTERFACES: Dict[str, CanonInterface] = {
    "apb": CanonInterface(
        protocol="apb",
        min_signals=4,
        role_probes=("psel", "penable"),
        signals=(
            _sig("psel", "in"), _sig("penable", "in"), _sig("pwrite", "in"),
            _sig("paddr", "in", "addr"), _sig("pwdata", "in", "data"),
            _sig("pstrb", "in", "strb", required=False),
            _sig("pprot", "in", None, required=False),
            _sig("pready", "out"), _sig("prdata", "out", "data"),
            _sig("pslverr", "out", required=False),
        ),
    ),
    "axi4_lite": CanonInterface(
        protocol="axi4_lite",
        min_signals=6,
        role_probes=("awvalid", "arvalid"),
        signals=(
            _sig("awaddr", "in", "addr"), _sig("awprot", "in", None, required=False),
            _sig("awvalid", "in"), _sig("awready", "out"),
            _sig("wdata", "in", "data"), _sig("wstrb", "in", "strb"),
            _sig("wvalid", "in"), _sig("wready", "out"),
            _sig("bresp", "out"), _sig("bvalid", "out"), _sig("bready", "in"),
            _sig("araddr", "in", "addr"), _sig("arprot", "in", None, required=False),
            _sig("arvalid", "in"), _sig("arready", "out"),
            _sig("rdata", "out", "data"), _sig("rresp", "out"),
            _sig("rvalid", "out"), _sig("rready", "in"),
            # full-AXI4 additions (also used to promote the protocol)
            _sig("awid", "in", "id", required=False), _sig("awlen", "in", "len", required=False),
            _sig("awsize", "in", "size", required=False), _sig("awburst", "in", "burst", required=False),
            _sig("awlock", "in", None, required=False), _sig("awcache", "in", None, required=False),
            _sig("awqos", "in", None, required=False), _sig("awregion", "in", None, required=False),
            _sig("wlast", "in", None, required=False),
            _sig("bid", "out", "id", required=False), _sig("bresp", "out", None, required=False),
            _sig("arid", "in", "id", required=False), _sig("arlen", "in", "len", required=False),
            _sig("arsize", "in", "size", required=False), _sig("arburst", "in", "burst", required=False),
            _sig("arlock", "in", None, required=False), _sig("arcache", "in", None, required=False),
            _sig("arqos", "in", None, required=False), _sig("arregion", "in", None, required=False),
            _sig("rlast", "out", None, required=False),
            _sig("rid", "out", "id", required=False),
        ),
    ),
    "ahb_lite": CanonInterface(
        protocol="ahb_lite",
        min_signals=5,
        role_probes=("htrans", "hsel"),
        signals=(
            _sig("hsel", "in"), _sig("haddr", "in", "addr"),
            _sig("htrans", "in"), _sig("hwrite", "in"), _sig("hsize", "in", "size"),
            _sig("hburst", "in", None, required=False), _sig("hprot", "in", None, required=False),
            _sig("hwdata", "in", "data"), _sig("hready", "in"),
            _sig("hreadyout", "out", required=False),
            _sig("hrdata", "out", "data"), _sig("hresp", "out"),
        ),
    ),

    # ---- memory / interconnect buses -------------------------------------
    # These carry a mandatory protocol token in every port name (ddr3_*,
    # hbm_*, pcie_*), so the binder requires it instead of treating it as a
    # channel prefix.
    "ddr3": CanonInterface(
        protocol="ddr3",
        min_signals=5,
        role_probes=("cs_n",),
        required_tokens=("ddr3",),
        forbidden_tokens=("ddr4", "hbm", "pcie", "axi", "apb", "ahb"),
        signals=(
            _sig("ck_p", "out"), _sig("ck_n", "out"),
            _sig("cke", "out", required=False), _sig("cs_n", "out"),
            _sig("ras_n", "out", required=False), _sig("cas_n", "out", required=False),
            _sig("we_n", "out", required=False), _sig("ba", "out"),
            _sig("addr", "out", "addr"), _sig("dq", "inout", "data"),
        ),
    ),
    "ddr4": CanonInterface(
        protocol="ddr4",
        min_signals=5,
        role_probes=("cs_n",),
        required_tokens=("ddr4",),
        forbidden_tokens=("ddr3", "hbm", "pcie", "axi", "apb", "ahb"),
        signals=(
            _sig("ck_t", "out"), _sig("ck_c", "out"),
            _sig("act_n", "out"), _sig("bg", "out", required=False),
            _sig("ba", "out"), _sig("adr", "out", "addr"),
            _sig("cs_n", "out"), _sig("odt", "out", required=False),
            _sig("dq", "inout", "data"),
            _sig("dm_dbi_n", "out", required=False),
            _sig("rd_valid", "out", required=False),
        ),
    ),
    "hbm": CanonInterface(
        protocol="hbm",
        min_signals=6,
        role_probes=("awvalid", "arvalid"),
        required_tokens=("hbm",),
        forbidden_tokens=("ddr3", "ddr4", "pcie"),
        signals=(
            _sig("awid", "in", "id"), _sig("awaddr", "in", "addr"),
            _sig("awvalid", "in"), _sig("awready", "out"),
            _sig("wdata", "in", "data"), _sig("wvalid", "in"), _sig("wready", "out"),
            _sig("bid", "out", "id"), _sig("bvalid", "out"), _sig("bready", "in"),
            _sig("arid", "in", "id"), _sig("araddr", "in", "addr"),
            _sig("arvalid", "in"), _sig("arready", "out"),
            _sig("rid", "out", "id"), _sig("rdata", "out", "data"),
            _sig("rvalid", "out"), _sig("rready", "in"),
        ),
    ),
    "pcie": CanonInterface(
        protocol="pcie",
        min_signals=4,
        role_probes=("rx_tvalid", "tx_tvalid"),
        required_tokens=("pcie",),
        forbidden_tokens=("ddr3", "ddr4", "hbm"),
        group_neutral_tokens=("rx", "tx", "cfg"),
        signals=(
            # PCIe TLP streams are conventionally named in AXI-Stream style
            # (tx_tvalid / rx_tdata).  The tx/rx direction token is therefore part
            # of the canonical spelling: binding on the bare "tvalid" alias would
            # make tx_valid and rx_valid compete for the same port and silently
            # pick whichever comes first.
            _sig("tx_tvalid", "out", aliases=("tx_valid",)),
            _sig("tx_tready", "in", aliases=("tx_ready",)),
            _sig("tx_tdata", "out", "data", aliases=("tx_data",)),
            _sig("tx_tlast", "out", required=False, aliases=("tx_last",)),
            _sig("rx_tvalid", "in", aliases=("rx_valid",)),
            _sig("rx_tready", "out", aliases=("rx_ready",)),
            _sig("rx_tdata", "in", "data", aliases=("rx_data",)),
            _sig("rx_tlast", "in", required=False, aliases=("rx_last",)),
            _sig("link_up", "out", required=False),
            _sig("speed", "out", required=False),
            _sig("lanes", "out", required=False),
        ),
    ),
    "spi": CanonInterface(
        protocol="spi",
        min_signals=4,
        role_probes=("sclk", "cs_n"),
        signals=(
            _sig("sclk", "out", required=False), _sig("cs_n", "out", required=False),
            _sig("mosi", "out", required=False), _sig("miso", "in", required=False),
            _sig("sdo", "out", required=False), _sig("sdi", "in", required=False),
            _sig("sck", "out", required=False),
        ),
    ),
    "ethernet": CanonInterface(
        protocol="ethernet",
        min_signals=4,
        role_probes=("rx_dv", "tx_en"),
        signals=(
            _sig("tx_en", "in", required=False), _sig("tx_data", "in", "data", required=False),
            _sig("rx_dv", "in", required=False), _sig("rx_data", "in", "data", required=False),
            _sig("rx_clk", "in", required=False), _sig("tx_clk", "in", required=False),
        ),
    ),
    "axi4_stream": CanonInterface(
        protocol="axi4_stream",
        min_signals=2,
        role_probes=("tvalid",),
        signals=(
            _sig("tdata", "in", "data"), _sig("tvalid", "in"),
            _sig("tready", "out"), _sig("tlast", "in", required=False),
            _sig("tkeep", "in", required=False), _sig("tstrb", "in", required=False),
            _sig("tid", "in", "id", required=False), _sig("tdest", "in", required=False),
            _sig("tuser", "in", required=False),
        ),
    ),
}


@dataclass
class BoundSignal:
    canon: str
    port: Any                      # SVPortAST
    matched: str                   # concatenation that matched
    prefix_tokens: List[str]       # channel/instance prefix tokens
    preference: int                # higher = better (exact-name match wins)

    @property
    def group(self) -> str:
        return "_".join(self.prefix_tokens) if self.prefix_tokens else "default"


@dataclass
class BoundInterface:
    """Result of binding one protocol onto a group of DUT ports."""

    protocol: str
    group: str
    signals: Dict[str, BoundSignal] = field(default_factory=dict)
    ambiguous: List[Dict[str, Any]] = field(default_factory=list)

    def port_map(self) -> Dict[str, str]:
        return {k: v.port.name for k, v in self.signals.items()}

    def port_details(self) -> Dict[str, Dict[str, Any]]:
        return {
            k: {"port": v.port.name, "direction": v.port.direction,
                "width": v.port.width}
            for k, v in self.signals.items()
        }


def _filter_group_tokens(
    prefix_tokens: Sequence[str], canon_iface: "CanonInterface"
) -> List[str]:
    """Drop direction/protocol-internal tokens from the channel prefix.

    Two kinds of tokens must never become part of the channel group:

    * **direction words** anywhere in the prefix (``i_psel`` / ``o_pready``):
      they say which way this one signal flows, they do not identify a channel.
      Without this, ``i_psel`` and ``o_pready`` land in different groups ("i"
      and "o") and the interface is split in half.
    * **protocol-internal direction** declared by the interface itself: PCIe
      ports are ``pcie_rx_tvalid`` / ``pcie_tx_tdata``; ``rx``/``tx`` describe
      the TLP stream direction of this very interface, not separate channels.

    Only genuinely channel-like tokens (``s0``, ``ch1``, ``port2``, ``s_axi``)
    remain in the prefix.
    """
    neutral = set(_DIR_WORDS) | set(canon_iface.group_neutral_tokens)
    return [t for t in prefix_tokens if t.lower() not in neutral]


def _tokens_ok(tokens: Sequence[str], canon_iface: "CanonInterface") -> bool:
    """Check the protocol's required/forbidden token rules."""
    low = [t.lower() for t in tokens]
    if canon_iface.forbidden_tokens and any(t in low for t in canon_iface.forbidden_tokens):
        return False
    if canon_iface.required_tokens and not any(t in low for t in canon_iface.required_tokens):
        return False
    return True


def _preference(port_name: str, canon: str) -> int:
    """Prefer bindings whose port name is closest to the canonical spelling.

    ``paddr`` (exact) should beat ``p_addr_o`` (split + suffixed) when both
    exist, so a stray alias cannot hijack the canonical signal.
    """
    base = port_name.lower().replace("_", "")
    if base == canon:
        return 3
    if base.rstrip("ioinub").startswith(canon):
        return 2
    return 1


def bind_protocol(
    canon_iface: CanonInterface,
    ports: Sequence[Any],
    claimed: Optional[set] = None,
) -> List[BoundInterface]:
    """Bind a standard interface onto the given DUT ports.

    Returns one :class:`BoundInterface` per discovered channel/instance group.
    Ports already in ``claimed`` are skipped (they belong to another protocol or
    to clock/reset), but the caller decides whether to add the bound ports to
    ``claimed`` -- only interfaces that reach ``min_signals`` are meaningful.
    """
    claimed = claimed or set()
    by_canon = canon_iface.by_name()
    groups: Dict[str, Dict[str, List[BoundSignal]]] = {}
    ungrouped: List[BoundSignal] = []

    for p in ports:
        if p.name in claimed:
            continue
        tokens = tokenize_port_name(p.name)
        if not tokens:
            continue
        best: Optional[Tuple[str, BoundSignal]] = None
        for canon, csig in by_canon.items():
            matched = None
            spelling = ""
            for _spelling in csig.all_spellings():
                matched = _match_suffix(tokens, _spelling)
                if matched is not None:
                    spelling = matched[1]
                    break
            if matched is None:
                continue
            prefix_count = matched[0]
            bs = BoundSignal(
                canon=canon,
                port=p,
                matched=spelling,
                prefix_tokens=_filter_group_tokens(
                    tokens[:prefix_count], canon_iface),
                preference=_preference(p.name, canon),
            )
            if not _tokens_ok(tokens, canon_iface):
                continue
            if best is None or bs.preference > best[1].preference:
                best = (canon, bs)
        if best is None:
            continue
        canon, bs = best
        if bs.prefix_tokens:
            groups.setdefault(bs.group, {}).setdefault(canon, []).append(bs)
        else:
            ungrouped.append(bs)

    # Ports whose trailing direction word blocked an otherwise valid match:
    #   psel_i -> tokens ["psel","i"]; no suffix concatenates to "psel".
    # Handle them in a second pass by stripping the direction word.
    for p in ports:
        if p.name in claimed:
            continue
        tokens = tokenize_port_name(p.name)
        if not tokens or tokens[-1] not in _DIR_WORDS:
            continue
        stripped = _strip_trailing_dir(tokens, "\0")
        if stripped == tokens:
            continue
        best2: Optional[Tuple[str, BoundSignal]] = None
        for canon, csig in by_canon.items():
            matched = None
            spelling = ""
            for _spelling in csig.all_spellings():
                matched = _match_suffix(stripped, _spelling)
                if matched is not None:
                    spelling = matched[1]
                    break
            if matched is None:
                continue
            prefix_count = matched[0]
            bs = BoundSignal(
                canon=canon, port=p, matched=spelling,
                prefix_tokens=_filter_group_tokens(
                    stripped[:prefix_count], canon_iface),
                preference=_preference(p.name, canon),
            )
            if not _tokens_ok(stripped, canon_iface):
                continue
            if best2 is None or bs.preference > best2[1].preference:
                best2 = (canon, bs)
        if best2 is None:
            continue
        canon, bs = best2
        if bs.prefix_tokens:
            groups.setdefault(bs.group, {}).setdefault(canon, []).append(bs)
        else:
            ungrouped.append(bs)

    # Ungrouped ports (no channel prefix) all belong to the "default" group.
    if ungrouped:
        bucket = groups.setdefault("default", {})
        for bs in ungrouped:
            bucket.setdefault(bs.canon, []).append(bs)

    out: List[BoundInterface] = []
    for grp, by_c in groups.items():
        bi = BoundInterface(protocol=canon_iface.protocol, group=grp)
        for canon, candidates in by_c.items():
            candidates.sort(key=lambda b: (-b.preference, b.port.name))
            bi.signals[canon] = candidates[0]
            if len(candidates) > 1:
                bi.ambiguous.append({
                    "canonical": canon,
                    "chosen": candidates[0].port.name,
                    "rejected": [c.port.name for c in candidates[1:]],
                    "reason": "multiple DUT ports bind to the same canonical signal",
                })
        out.append(bi)
    return out


def interface_score(bi: BoundInterface, canon_iface: CanonInterface) -> Tuple[int, int]:
    """Rank a candidate binding: (required signals bound, total bound)."""
    required = [s.name for s in canon_iface.signals if s.required]
    got_req = sum(1 for r in required if r in bi.signals)
    return (got_req, len(bi.signals))
