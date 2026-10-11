#!/usr/bin/env python3
"""Фикстурный тест p10-exitsim.py (П-10, TK-024): выходы клеток B1/Г-24/Г-25/Г-109 на синтетических минутных свечах —
стоп, трейл, выход по разгону RV, люстра, половина на 1:1 — плюс метрики ворот и перепись rounds.csv.

Запуск: `python -m pytest tools/compute/tests/test_p10_exitsim.py -q` (на Windows — PYTHONIOENCODING=utf-8).
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "p10-exitsim.py"
MIN_MS = 60_000
M_SIG = 1_000_000 * MIN_MS  # минута сигнала, мс
FEE = 6.3  # bps круга: тейкер-нога 3.15 на входе и на выходе
ENTRY = 100.0
TAKER, MAKER = 3.15, 1.26


def load():
    spec = importlib.util.spec_from_file_location("p10_exitsim", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P10 = load()
X = P10.X


def bars(path, pre=60):
    """Свечи (o, h, l, c): `pre` плоских минут до сигнала + `path` с минуты сигнала."""
    b = X.Bars([])
    flat = (ENTRY, ENTRY, ENTRY, ENTRY)
    seq = [flat] * pre + list(path)
    b.d = {M_SIG + (i - pre) * MIN_MS: v for i, v in enumerate(seq)}
    b.k = sorted(b.d)
    return b


def trade(reason="trail"):
    return {"t0": M_SIG * 1_000_000, "entry": ENTRY, "fee": FEE, "reason": reason, "net": 0.0, "usd": 500.0, "fill": 1.0,
            "sym": "TESTUSDT", "t1": 0}


def flat_to_deadline(n=300):
    return [(ENTRY, ENTRY, ENTRY, ENTRY)] * n


def bps(px):
    return (px / ENTRY - 1) * 1e4


# ---------- B1 как есть (base exit-sim): стоп, трейл, дедлайн ----------

def test_b1_stop_at_two_percent():
    c = bars([(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, ENTRY, 97.0, 97.5)] + flat_to_deadline())
    net, reason, m = P10.evaluate("b1-sim", trade(), c, None)[:3]
    assert reason == "stop" and m == M_SIG + MIN_MS
    assert net == pytest.approx(bps(98.0) - FEE)


def test_b1_stop_gap_down_executes_at_open():
    c = bars([(ENTRY, ENTRY, ENTRY, ENTRY), (96.0, 96.5, 95.0, 96.0)] + flat_to_deadline())
    net, reason, _ = P10.evaluate("b1-sim", trade(), c, None)[:3]
    assert reason == "stop" and net == pytest.approx(bps(96.0) - FEE)


def test_b1_trail_after_one_percent():
    c = bars([(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, 101.5, ENTRY, 101.2), (101.0, 101.0, 100.4, 100.6)] + flat_to_deadline())
    net, reason, m = P10.evaluate("b1-sim", trade(), c, None)[:3]
    assert reason == "trail" and m == M_SIG + 2 * MIN_MS
    assert net == pytest.approx(bps(100.5) - FEE)  # лучший 101.5 − 1 % входа


def test_b1_deadline_after_four_hours_at_open():
    c = bars([(ENTRY, ENTRY, ENTRY, ENTRY)] + [(100.2, 100.4, 99.9, 100.2)] * 300)
    net, reason, m = P10.evaluate("b1-sim", trade(), c, None)[:3]
    assert reason == "deadline" and m == M_SIG + 240 * MIN_MS
    assert net == pytest.approx(bps(100.2) - FEE)


def test_no_candles_returns_none():
    assert P10.evaluate("b1-sim", trade(), X.Bars([]), None) is None
    assert P10.evaluate("g109-part", trade(), X.Bars([]), None) is None


# ---------- Г-24: выход по разгону RV (быстрая RV 5 м ≥ k × медленной, только в минусе) ----------

JUMP = [(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, ENTRY, 99.4, 99.5)]


def test_g24_exits_at_open_when_rv_spikes_and_position_is_under_water():
    c = bars(JUMP + [(99.5, 99.6, 99.4, 99.5)] + flat_to_deadline())
    net, reason, m = P10.evaluate("g24-x25", trade(), c, 0.002)[:3]
    assert reason == "rv" and m == M_SIG + 2 * MIN_MS
    assert net == pytest.approx(bps(99.5) - FEE)


def test_g24_x4_is_stricter_than_x25():
    # 5-минутная RV одного скачка 0.5 % ≈ 5e-3; порог ×4 при σ24 = 0.02 равен 4·0.02·√(5/1440) ≈ 4.7e-3 — ещё сработает,
    # а при σ24 = 0.03 (порог 7.1e-3) — уже нет и позиция доживает до дедлайна
    c = bars(JUMP + [(99.5, 99.6, 99.4, 99.5)] + flat_to_deadline())
    assert P10.evaluate("g24-x4", trade(), c, 0.02)[1] == "rv"
    assert P10.evaluate("g24-x4", trade(), c, 0.03)[1] == "deadline"


def test_g24_does_not_exit_in_profit():
    up = [(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, 100.6, ENTRY, 100.5), (100.5, 100.6, 100.4, 100.5)]
    c = bars(up + flat_to_deadline())
    assert P10.evaluate("g24-x25", trade(), c, 0.002)[1] == "deadline"  # режим exit: поджатия нет


def test_g24_window_is_five_minutes():
    assert P10.cell_spec("g24-x25") == {"kind": "volexp", "p": 2.5, "n": 5, "mode": "exit"}
    assert P10.cell_spec("g24-x4")["p"] == 4


# ---------- Г-25: люстра для спокойных монет (RV суток ≤ 262 bps) ----------

CHAND_PATH = [(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, 103.0, ENTRY, 102.8), (102.5, 102.6, 101.9, 102.0)]


def test_g25_chandelier_for_quiet_coin():
    c = bars(CHAND_PATH + flat_to_deadline())
    s24 = 0.0100  # спокойная
    net, reason, m = P10.evaluate("g25-chand", trade(), c, s24)[:3]
    stop = 103.0 - 2.5 * s24 * (240 / 1440) ** 0.5 * ENTRY
    assert reason == "trail" and m == M_SIG + 2 * MIN_MS
    assert net == pytest.approx(bps(stop) - FEE)


def test_g25_noisy_coin_keeps_base_trail():
    c = bars(CHAND_PATH + flat_to_deadline())
    net, reason, _ = P10.evaluate("g25-chand", trade(), c, 0.05)[:3]  # RV суток 500 bps > 262 — база B1
    assert reason == "trail" and net == pytest.approx(bps(102.0) - FEE)  # лучший 103 − 1 % входа
    assert net == pytest.approx(P10.evaluate("b1-sim", trade(), c, 0.05)[0])


def test_g25_threshold_is_262_bps():
    assert P10.cell_spec("g25-chand") == {"kind": "chand_quiet", "p": 0.0262}


# ---------- Г-109: половина на 1:1 (+STOP), остаток под трейлом B1 ----------

def test_g109_half_taken_then_trail_on_remainder():
    path = [(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, 102.5, ENTRY, 102.4), (102.4, 102.4, 101.4, 101.6)] + flat_to_deadline()
    c = bars(path)
    net, reason, m, half = P10.evaluate("g109-part", trade(), c, None)
    net_a = bps(102.0) - (FEE - TAKER + MAKER)  # лимитный тейк: мейкер-нога
    net_b = bps(101.5) - FEE  # трейл: лучший 102.5 − 1 %
    assert reason == "trail" and m == M_SIG + 2 * MIN_MS and half == M_SIG + MIN_MS
    assert net == pytest.approx(0.5 * (net_a + net_b))


def test_g109_target_never_reached_equals_b1():
    path = [(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, 100.5, 97.0, 97.5)] + flat_to_deadline()
    c = bars(path)
    net, reason, m, half = P10.evaluate("g109-part", trade(), c, None)
    b = P10.evaluate("b1-sim", trade(), c, None)
    assert half is None and reason == "stop" and (net, reason, m) == b[:3]


def test_g109_trail_exit_before_target_takes_whole_position_at_trail():
    path = [(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, 101.5, ENTRY, 101.2), (101.0, 101.0, 100.4, 100.6),
            (100.6, 102.5, 100.5, 102.0)] + flat_to_deadline()
    c = bars(path)
    net, reason, m, half = P10.evaluate("g109-part", trade(), c, None)
    assert half is None and reason == "trail" and net == pytest.approx(bps(100.5) - FEE)


def test_g109_same_bar_target_and_trail_is_not_a_half():
    path = [(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, 101.5, ENTRY, 101.4), (101.4, 102.5, 100.4, 100.6)] + flat_to_deadline()
    c = bars(path)
    assert P10.evaluate("g109-part", trade(), c, None)[3] is None  # порядок внутри минуты неизвестен — защитный выход первым


# ---------- ворота: метрики и таблица ----------

def pair(net_e, net_s, reason_e="trail", reason_s="trail", usd=500.0):
    return ({"net": net_e, "reason": reason_e, "usd": usd}, (net_s, reason_s, 0, None))


def test_agreement_metrics():
    pairs = [pair(10.0, 11.0), pair(20.0, 20.0), pair(-200.0, -210.0, "stop", "stop"), pair(50.0, 80.0, "trail", "deadline")]
    g = P10.agreement(pairs)
    assert g["n"] == 4 and g["reason_share"] == 0.75
    assert g["median"] == pytest.approx(5.5)  # |Δ| = 0, 1, 10, 30
    assert g["p95"] == 30.0
    assert g["usd_engine"] == pytest.approx((10 + 20 - 200 + 50) / 1e4 * 500)
    assert g["usd_sim"] == pytest.approx((11 + 20 - 210 + 80) / 1e4 * 500)


def test_gate_table_yes_no():
    good = P10.agreement([pair(10.0, 10.5)] * 30)
    assert [r[3] for r in P10.gate_table(good)] == ["да"] * 5
    bad = P10.agreement([pair(10.0, 60.0, "trail", "stop")] * 29)  # 29 сделок, причины и Δ вне допуска
    assert [r[3] for r in P10.gate_table(bad)] == ["нет"] * 5


# ---------- перепись rounds.csv ----------

def test_write_rounds_rewrites_exit_and_reason(tmp_path):
    src = tmp_path / "br" / "aug" / "2026-08-03" / P10.SET_
    src.mkdir(parents=True)
    head = ["symbol", "day_utc", "form", "signal_index", "t0_ns", "dir", "entry_px", "entry_vwap", "qty", "exit_px", "exit_ns",
            "net_bps", "reason", "fill_frac"]
    other = ["ETHUSDT", "2026-08-03", "otherform", "1", "5", "1", "10", "10", "1", "10", "6", "0", "stop", "1"]
    mine = ["TESTUSDT", "2026-08-03", P10.FORM_B1, "2", "7", "1", "100", "100", "5", "100.5", "9000000000", "44.0", "trail", "1"]
    with (src / "rounds.csv").open("w", encoding="utf-8", newline="") as f:
        f.write("# busy_replay=keep:all\n")
        w = csv.writer(f)
        w.writerow(head)
        w.writerows([other, mine])
    res = {("TESTUSDT", 7): {"net": 100.0, "reason": "deadline", "t1": 12_000_000_000}}
    dst = tmp_path / "run" / "aug"
    assert P10.write_rounds(str(dst), str(tmp_path / "br" / "aug"), res) == 1
    out = list(csv.DictReader(l for l in (dst / "2026-08-03" / P10.SET_ / "rounds.csv").open(encoding="utf-8") if not l.startswith("#")))
    assert len(out) == 1 and out[0]["symbol"] == "TESTUSDT"
    assert out[0]["exit_ns"] == "12000000000" and out[0]["reason"] == "deadline" and float(out[0]["net_bps"]) == 100.0
    fee = 50.0 - 44.0  # gross 50 bps (100.5/100) − net 44
    assert float(out[0]["exit_px"]) == pytest.approx(100 * (1 + (100.0 + fee) / 1e4))
