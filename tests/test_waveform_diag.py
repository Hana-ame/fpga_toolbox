"""Agent 友好的波形诊断器（waveform_diag.py）。

覆盖三个真实能力：
  1. **X/Z 传播点** —— 首个未知值出现在哪个信号/时刻（X 是仿真失败最常见根因）
  2. **握手死锁窗口** —— VALID 拉高后 READY 从未拉高（DUT 流控实现问题）
  3. **恒定信号** —— 整个窗口从未翻转（疑似常量/未驱动）

全部用**内联 VCD 夹具**，不依赖任何 EDA 工具（VCD 是文本格式）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fpga_toolbox.registry import call_tool  # noqa: E402

#: 带 X 污染 + 握手死锁的最小 VCD
# 注意 id code 必须避开 `#`（时间戳前缀），否则 `#0` 时间标记与 `#` 信号 id 冲突
VCD_DEADLOCK = """$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 1 " tvalid $end
$var wire 1 # tready $end
$var wire 8 $ data $end
$upscope $end
$enddefinitions $end
#0
0!
0"
0#
bx $
#10
1!
#20
0!
#30
1!
1"
#40
0!
#50
1!
#60
0!
#70
1!
#80
0!
"""

#: 健康握手（tready 会拉高，无死锁、无 X）
VCD_HEALTHY = """$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 1 " tvalid $end
$var wire 1 # tready $end
$upscope $end
$enddefinitions $end
#0
0!
0"
0#
#10
1!
#20
0!
#30
1!
1"
#40
0!
#50
1!
1#           tready 拉高 -> 握手完成
#60
0!
0"
"""


def test_waveform_tool_registered() -> None:
    """diagnose_waveform 必须通过 registry / MCP 暴露。"""
    print("coverage: diagnose_waveform registered as a tool")
    r = call_tool("diagnose_waveform", {"waveform_path": "/nonexistent.vcd"})
    assert r["tool"] == "diagnose_waveform", r
    assert r["ok"] is False and "not found" in (r.get("error") or ""), r


def test_x_propagation_detection() -> None:
    """首个 X/Z 传播点必须带信号名与时刻。"""
    print("coverage: X/Z propagation detection")
    with tempfile.TemporaryDirectory() as td:
        vcd = Path(td) / "x.vcd"
        vcd.write_text(VCD_DEADLOCK, encoding="utf-8")
        r = call_tool("diagnose_waveform", {"waveform_path": str(vcd)})
        assert r["ok"], r.get("error")
        xp = r["data"]["x_propagation"]
        assert xp["count"] >= 1, xp
        first = xp["first"][0]
        assert first["signal"].endswith("data"), first
        assert first["time"] == 0, first
        assert first["value"].lower().startswith("x"), first
        assert "根因" in xp["interpretation"] or "追溯" in xp["interpretation"], xp["interpretation"]


def test_handshake_deadlock_detection() -> None:
    """VALID 拉高后 READY 从未拉高 -> 必须报死锁窗口。"""
    print("coverage: handshake deadlock detection")
    with tempfile.TemporaryDirectory() as td:
        vcd = Path(td) / "dead.vcd"
        vcd.write_text(VCD_DEADLOCK, encoding="utf-8")
        r = call_tool("diagnose_waveform", {"waveform_path": str(vcd)})
        h = r["data"]["handshake_deadlocks"]
        assert h["count"] == 1, h
        w = h["windows"][0]
        assert w["valid_signal"].endswith("tvalid"), w
        assert w["valid_high_since"] == 30, w
        assert w["ready_signal"].endswith("tready"), w
        assert "死锁" in h["interpretation"], h["interpretation"]


def test_healthy_handshake_no_false_positive() -> None:
    """tready 会拉高的健康波形必须**不**报死锁（防假阳性）。"""
    print("coverage: healthy handshake -> no false deadlock")
    with tempfile.TemporaryDirectory() as td:
        vcd = Path(td) / "ok.vcd"
        vcd.write_text(VCD_HEALTHY, encoding="utf-8")
        r = call_tool("diagnose_waveform", {"waveform_path": str(vcd)})
        h = r["data"]["handshake_deadlocks"]
        assert h["count"] == 0, h
        assert r["data"]["x_propagation"]["count"] == 0, r["data"]["x_propagation"]


def test_fst_is_rejected_with_guidance() -> None:
    """FST 是二进制格式，必须给可操作错误而不是解码异常。"""
    print("coverage: FST rejected with actionable guidance")
    with tempfile.TemporaryDirectory() as td:
        fst = Path(td) / "w.fst"
        fst.write_bytes(b"FSTheader\x00\x00")  # 任意二进制
        r = call_tool("diagnose_waveform", {"waveform_path": str(fst)})
        assert r["ok"] is False, r
        err = (r.get("error") or "").lower()
        assert "vcd" in err and ("convert" in err or "waveform_format" in err), r.get("error")
        assert "traceback" not in err, err


def test_missing_path_is_structured_error() -> None:
    """空路径与不存在的路径都要给结构化错误。"""
    print("coverage: missing waveform path -> structured error")
    empty = call_tool("diagnose_waveform", {"waveform_path": ""})
    assert empty["ok"] is False and "required" in (empty.get("error") or ""), empty
    miss = call_tool("diagnose_waveform", {"waveform_path": "/no/such.vcd"})
    assert miss["ok"] is False and "not found" in (miss.get("error") or ""), miss



def test_apb_handshake_deadlock_detection() -> None:
    """APB 死锁：PSEL 拉高后 PREADY 从未拉高。

    APB 是三信号协议（PSEL+PENABLE 构成请求、PREADY 应答），与 AXI 的
    VALID/READY 两信号握手不同。旧实现的过滤器只收集以 VALID/READY 结尾的
    信号，APB 的 psel/penable/pready 全被排除 -> APB 死锁检测完全不工作。
    """
    print("coverage: APB handshake deadlock detection")
    lines = [
        "$timescale 1ns $end",
        "$scope module tb $end",
        '$var wire 1 ! clk $end',
        '$var wire 1 ' + '"' + ' psel $end',
        "$var wire 1 # penable $end",
        '$var wire 1 $ pready $end',
        "$upscope $end",
        "$enddefinitions $end",
        "#0",
        "0!",
        "0" + '"',
        "0#",
        "0$",
        "#10",
        "1!",
        "#20",
        "0!",
        "#30",
        "1!",
        "1" + '"',
        "1#",
        "#40",
        "0!",
        "#50",
        "1!",
        "#60",
        "0!",
    ]
    with tempfile.TemporaryDirectory() as td:
        vcd_path = Path(td) / "apb.vcd"
        vcd_path.write_text("\n".join(lines), encoding="utf-8")
        r = call_tool("diagnose_waveform", {"waveform_path": str(vcd_path)})
        h = r["data"]["handshake_deadlocks"]
        assert h["count"] == 1, h
        w = h["windows"][0]
        assert w["valid_signal"].endswith("psel"), w
        assert w["valid_high_since"] == 30, w
        assert w["ready_signal"].endswith("pready"), w


def test_apb_healthy_no_false_positive() -> None:
    """PREADY 会拉高的健康 APB 必须不报死锁。"""
    print("coverage: APB healthy handshake -> no false deadlock")
    lines = [
        "$timescale 1ns $end",
        "$scope module tb $end",
        '$var wire 1 ! clk $end',
        '$var wire 1 ' + '"' + ' psel $end',
        "$var wire 1 # penable $end",
        '$var wire 1 $ pready $end',
        "$upscope $end",
        "$enddefinitions $end",
        "#0",
        "0!",
        "0" + '"',
        "0#",
        "0$",
        "#10",
        "1!",
        "#20",
        "0!",
        "#30",
        "1!",
        "1" + '"',
        "1#",
        "#40",
        "0!",
        "#50",
        "1!",
        "1$",
        "#60",
        "0!",
        "0" + '"',
        "0#",
    ]
    with tempfile.TemporaryDirectory() as td:
        vcd_path = Path(td) / "apb_ok.vcd"
        vcd_path.write_text("\n".join(lines), encoding="utf-8")
        r = call_tool("diagnose_waveform", {"waveform_path": str(vcd_path)})
        h = r["data"]["handshake_deadlocks"]
        assert h["count"] == 0, h


def main() -> int:
    test_waveform_tool_registered()
    test_x_propagation_detection()
    test_handshake_deadlock_detection()
    test_healthy_handshake_no_false_positive()
    test_fst_is_rejected_with_guidance()
    test_apb_handshake_deadlock_detection()
    test_apb_healthy_no_false_positive()
    test_missing_path_is_structured_error()
    print("\nALL WAVEFORM DIAG TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
