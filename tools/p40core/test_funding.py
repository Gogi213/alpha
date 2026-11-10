"""TK-522: funding.py — метки из mark, начисление по знакам, покрытие, загрузка CSV. Запуск: python -m pytest tools/p40core/test_funding.py"""
import os, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import funding as F

H = 3_600_000


def test_from_mark_rate_before_settlement():
    t = [0, H, 2 * H, 3 * H, 4 * H]
    r = [1e-4, 2e-4, 3e-4, 4e-4, 5e-4]
    n = [2 * H, 2 * H, 2 * H, 6 * H, 6 * H]           # метка 2H: ставка — последняя строго до неё (t=H); метка 6H за концом ряда — не берётся
    T, rr = F.from_mark(t, r, n)
    assert list(T) == [2 * H] and list(rr) == [2e-4]


def test_from_mark_nan_and_empty():
    T, rr = F.from_mark([0, H], [float("nan"), float("nan")], [H, H])
    assert len(T) == 0 and len(rr) == 0


def test_accrue_signs_and_bounds():
    T, r = np.array([H, 2 * H, 3 * H], dtype="float64"), np.array([1e-4, -2e-4, 5e-4])
    assert F.accrue_bps(T, r, 0, 2 * H, +1) == -(1e-4 - 2e-4) * 1e4         # лонг платит; метка на выходе включена
    assert F.accrue_bps(T, r, 0, 2 * H, -1) == (1e-4 - 2e-4) * 1e4          # шорт получает
    assert F.accrue_bps(T, r, H, 3 * H, +1) == -(-2e-4 + 5e-4) * 1e4        # метка на входе не включена
    assert F.accrue_bps(T, r, float("nan"), 3 * H, +1) == 0.0               # нога не исполнена
    assert F.accrue_bps(T, r, 2 * H, 2 * H, +1) == 0.0


def test_episode_sum_and_unfilled_leg():
    A = (np.array([H, 2 * H], dtype="float64"), np.array([1e-4, 1e-4]))
    B = (np.array([H, 2 * H], dtype="float64"), np.array([3e-4, 3e-4]))
    tot, per = F.episode_bps((A, B), 1, (0.0, 0.0), 2 * H)      # B шорт: +6 bps, A лонг: −2 bps
    assert abs(tot - 4.0) < 1e-9 and abs(per[0] + 2.0) < 1e-9 and abs(per[1] - 6.0) < 1e-9
    tot, per = F.episode_bps((A, B), 1, (0.0, float("nan")), 2 * H)
    assert abs(tot + 2.0) < 1e-9 and per[1] == 0.0


def test_gaps():
    T = np.array([8 * H, 16 * H], dtype="float64")
    assert F.gaps(T, 0, 24 * H, 8 * H) == []
    assert F.gaps(T, 0, 24 * H + 1, 8 * H) == [(16 * H, 24 * H + 1)]
    assert F.gaps(np.empty(0), 0, 9 * H, 8 * H) == [(0, 9 * H)]


def test_load_csv_header_and_dups():
    p = os.path.join(tempfile.mkdtemp(), "f.csv")
    open(p, "w").write("exchange,symbol,ts_ms,rate\nbybit,X,2000,0.1\nbybit,X,1000,0.2\nexchange,symbol,ts_ms,rate\nbybit,X,2000,0.3\n")
    T, r = F.load_csv(p)[("bybit", "X")]
    assert list(T) == [1000.0, 2000.0] and list(r) == [0.2, 0.3]


def test_mark_rows_from_parquet():
    try:
        _mark_rows_case()
    except ValueError as e:                        # pyarrow/pandas не собраны под numpy ПК — тест идёт на srv
        assert "numpy.dtype" in str(e), e


def _mark_rows_case():
    import pyarrow as pa, pyarrow.parquet as pq
    base = tempfile.mkdtemp()
    d0 = 1_789_603_200_000                             # 2026-09-17 00:00 UTC
    for h in (0, 1, 3):                                # часа 02 нет у поставщика: пропуск
        p = os.path.join(base, "okx_futures", "2026-09-17", f"{h:02d}")
        os.makedirs(p)
        t = d0 + h * H + np.array([0, 1800_000])
        pq.write_table(pa.table({"event_time": t, "funding_rate": [1e-4 * (h + 1)] * 2, "next_funding_time": [d0 + 2 * H] * 2}), os.path.join(p, "X-USDT-SWAP_mark_price.parquet"))
    rows = F.mark_rows("okx", "X-USDT-SWAP", base, d0 // 86_400_000, d0 // 86_400_000)
    assert rows == [(d0 + 2 * H, 2e-4)], rows         # ставка — последняя строго до метки 02:00 (час 01, вторая строка 01:30)


def test_universe_legs_by_exchange_and_fail_isolation(tmp_path, monkeypatch, capsys):
    p = tmp_path / "pairs.csv"
    p.write_text("asset,pair,exA,symA,exB,symB\n1,1,bybit,AUSDT,gate,A_USDT\n1,2,bybit,AUSDT,okx,A-USDT-SWAP\n1,3,bybit,BADUSDT,gate,A_USDT\n")

    def fake(sym, s, e):
        if sym == "BADUSDT":
            raise RuntimeError("нет контракта")
        return [(s + 1, 0.0001), (s + 2, -0.0002)]
    monkeypatch.setitem(F.FETCH, "bybit", fake)
    monkeypatch.setitem(F.FETCH, "gate", fake)
    bad = F.universe(str(p), 1000, 2000, {"bybit", "gate"}, 2)
    out, err = capsys.readouterr()
    rows = [l.split(",") for l in out.strip().splitlines()]
    assert bad == 1 and len(rows) == 4 and {r[0] for r in rows} == {"bybit", "gate"}   # okx не запрошен, BAD изолирован
    assert "FAIL bybit BADUSDT RuntimeError: нет контракта" in err and "legs=3 fail=1" in err
