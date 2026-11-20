"""TK-648 (§13.48): ω, клетки, исключение, режим метки, охрана суток. Запуск: python3 -m pytest tools/p42/tests/test_tk648.py (srv)."""
import datetime as dt
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tk648_feat as ft  # noqa: E402
import tk648_run as tr  # noqa: E402


def test_omega_60_days_without_hole():
    d = ft.omega_days()
    assert len(d) == 60 and dt.date(2026, 10, 8) not in d
    assert d[0] == dt.date(2026, 9, 14) and d[-1] == dt.date(2026, 11, 13)
    assert len(tr.OM) == 60 and sorted(set(tr.OM_MONTH.tolist())) == [2, 3, 4]
    assert [int((tr.OM_MONTH == m).sum()) for m in (2, 3, 4)] == [17, 30, 13]


def test_check_day_refuses_after_13_11():
    ft.check_day(dt.date(2026, 11, 13))
    with pytest.raises(SystemExit):
        ft.check_day(dt.date(2026, 11, 14))


def test_cell_defs_trials_and_count():
    thr = {f: {"%s%02d" % (t, s): 0.0 for t in ("lo", "hi") for s in (10, 25, 40)} for f in ft.FEATS}
    cells = tr.cell_defs(thr)
    assert len(cells) == 36
    trial = sorted(c["cid"] for c in cells if c["trial"])
    assert trial == sorted(["IMB02_lo25", "IMB1_lo25", "IMB2_lo25", "THIN5_hi25", "THIN30_hi25", "DTURN_hi25"])


def test_excluded_tails_and_nan_passes():
    v = np.array([-1.0, 0.0, 1.0, np.nan])
    lo = dict(tail="lo", thr=0.0)
    hi = dict(tail="hi", thr=0.0)
    assert tr.excluded(lo, v).tolist() == [True, False, False, False]
    assert tr.excluded(hi, v).tolist() == [False, False, True, False]


def test_day_mode():
    day0 = 20000 * 86400.0
    a = day0 + 30.0 * np.arange(100) + 3.0           # метки на 3 с после узла — A
    b = day0 + 86400.0 + 30.0 * np.arange(100)       # на узле — B
    m = ft.day_mode(np.concatenate([a, b]))
    assert m[20000] == 1.0 and m[20001] == 0.0


def test_holm_and_zq():
    assert tr.holm([0.001, 0.5, 0.2, 0.9, 0.8, 0.7]) == [True, False, False, False, False, False]
    assert abs(tr.Z_HOLM - 2.638) < 0.01 and abs(tr.Z_1 - 1.96) < 0.01
