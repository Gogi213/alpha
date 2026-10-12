"""TK-216: раннер filter-cells.py — отказы, покрытие базой, admits (в т.ч. usd_min), конфиг-хеш пула."""
import importlib.util
import os
import sys
import types

import pytest

_p = os.path.join(os.path.dirname(__file__), "..", "filter-cells.py")
_s = importlib.util.spec_from_file_location("filter_cells", _p)
fc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(fc)

AP = {"age_ms": "3000000", "size_at_arm": "100", "side": "Bid", "price_tick": "50", "depth_behind_lots": "400",
      "stack_levels_at_arm": "3", "flow_1h_lots": "1000", "frontrun_lots_at_arm": "5", "born_shift_cbps": ""}


@pytest.mark.parametrize("spec", ["stop=2", "deadline_max=3", "eaten=1", "ret4h_max=1", "nope=1", "age=1,age=2"])
def test_parse_refuses(spec):
    with pytest.raises(ValueError):
        fc.parse_filter(spec)


def test_covers_weaker_than_base_refused():
    base = fc.parse_filter("age=2700,side=bid")
    assert fc.covers(base, fc.parse_filter("age=2700,side=bid,btc4h_max=-1")) is None
    assert fc.covers(base, fc.parse_filter("age=1800,side=bid"))
    assert fc.covers(base, fc.parse_filter("age=2700"))


def test_admits_basic_and_r1_missing():
    cache = types.SimpleNamespace(tick_lot={"X": (0.5, 2.0)})
    assert fc.admits(fc.parse_filter("age=2700,side=bid,behind_min=300"), AP, "d", 0, cache, "X")
    assert not fc.admits(fc.parse_filter("age=3600"), AP, "d", 0, cache, "X")
    assert not fc.admits(fc.parse_filter("r1_born_shift_cbps_min=-9000000000000000000"), AP, "d", 0, cache, "X")


def test_admits_usd_min():
    cache = types.SimpleNamespace(tick_lot={"X": (0.5, 2.0)})  # 50*0.5*100*2 = 5000
    assert fc.admits(fc.parse_filter("usd_min=5000"), AP, "d", 0, cache, "X")
    assert not fc.admits(fc.parse_filter("usd_min=5001"), AP, "d", 0, cache, "X")


def test_pool_cfg_strips_root_days(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("# lob bounce-grid: root=r1 days=all RTT=1\nx\n", encoding="utf-8")
    b.write_text("# lob bounce-grid: root=r2 days=2 RTT=1\nx\n", encoding="utf-8")
    assert fc.pool_cfg(str(a)) == fc.pool_cfg(str(b))
    b.write_text("# lob bounce-grid: root=r2 RTT=2\n", encoding="utf-8")
    assert fc.pool_cfg(str(a)) != fc.pool_cfg(str(b))
