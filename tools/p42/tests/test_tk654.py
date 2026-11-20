"""П-42 §13.54 (TK-654): G3 (причинность `bk` на склейке эпизодов) и правила клеток (пусто проходит, дубли, «≡ база»)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tk604_feat as tf  # noqa: E402
import tk654_run as t4  # noqa: E402

MIN = 60_000


def hist_of(sym, sig, y):
    return tf.History(np.array(sym, dtype=object), np.array(sig, dtype=np.int64) + tf.CLOSE_MS, np.array(y, dtype=float))


def test_g3_future_garbage_does_not_change_bk():
    t = 10_000 * MIN
    sig = [t - 200 * MIN, t - 100 * MIN, t - 65 * MIN, t - 64 * MIN, t + 5 * MIN]
    y_a = [1, 0, 1, 0, 0]
    y_b = [1, 0, 1, 1, 1]                      # будущее (закрытие после t) заменено мусором
    for y in (y_a, y_b):
        n, k, _ = tf.features(hist_of(["X"] * 5, sig, y), np.array(["X"], dtype=object), np.array([t]))
        assert (int(n[0]), int(k[0])) == (3, 2)   # граница sig + 65 мин = t входит; t − 64 мин ещё не закрыт


def test_bk_empty_below_K_passes():
    n, k = np.array([19, 20, 20]), np.array([0, 0, 1])
    ex = tf.excluded(n, k, 20, 0.05)
    assert ex.tolist() == [False, True, False]


def tab(**cols):
    t = {c: np.asarray(v, dtype=float) for c, v in cols.items()}
    t.setdefault("n", np.zeros(len(next(iter(t.values()))), dtype=np.int64))
    t.setdefault("k", np.zeros(len(next(iter(t.values()))), dtype=np.int64))
    return t


def test_empty_passes_ladders_and_named():
    t = tab(pierce_ep=[0, 2, np.nan], cons60=[0, 3, np.nan], mv5=[1.0, 2.0, 3.0], zv6h=[0, 0.1, np.nan])
    assert t4.excluded(dict(cid="CLEAN", kind="named"), t).tolist() == [False, True, False]
    assert t4.excluded(dict(cid="ZONE", kind="named"), t).tolist() == [True, False, False]
    assert t4.excluded(dict(cid="SHARP", kind="named", X=1.5), t).tolist() == [True, True, False]
    assert t4.excluded(dict(cid="DENSE", kind="named"), t).tolist() == [False, True, False]
    assert t4.excluded(dict(cid="x", kind="lad", col="mv5", dir="ge", X=2.0), t).tolist() == [True, False, False]
    assert t4.excluded(dict(cid="x", kind="lad", col="mv5", dir="le", X=2.0), t).tolist() == [False, False, True]


def test_merge_dups_and_same_as_base(monkeypatch):
    import tk604_stats as st
    t = tab(pierce_ep=[0, 1, 2], mv5=[0.5, 1.0, 1.5])
    tabs = {w.name: t for w in st.TRADE_WINDOWS}
    cells = [dict(cid="CLEAN", kind="named"), dict(cid="PE_le_q20", kind="lad", col="pierce_ep", dir="le", X=0.0),
             dict(cid="PE_ge_q20", kind="lad", col="pierce_ep", dir="ge", X=0.0), dict(cid="MV5_ge_q80", kind="lad", col="mv5", dir="ge", X=1.5)]
    out, same = t4.merge_dups(cells, tabs)
    assert same == ["PE_ge_q20"]
    assert [c["cid"] for c in out] == ["CLEAN", "MV5_ge_q80"]
    assert out[0]["also"] == ["PE_le_q20"]
