"""Синтетика P-TK461 §9 п.2: 3–5 свечей на случай. Запуск: python -I test_tk461_sim.py"""
import numpy as np
from tk461_sim import trade, gross_bps, fee_bps, STOP, TARGET, DEADLINE, OT

def A(*x): return np.array(x, float)
def run(o, h, l, c, **k):
    n = len(o); return trade(A(*o), A(*h), A(*l), A(*c), 0, n - 1, k.pop("entry", o[0]), **k)

def t_stop_first_same_minute():       # и стоп, и цель в одной минуте — стоп
    legs, m, r, _ = run([100, 100], [100, 111], [100, 89], [100, 100], stop=90.0, tgt=110.0)
    assert r == STOP and m == 1 and legs == [(1.0, 90.0, False)]
def t_gap_stop():                      # open ниже стопа — по open
    legs, m, r, _ = run([100, 85], [100, 86], [100, 84], [100, 85], stop=90.0)
    assert r == STOP and legs[0][1] == 85.0
def t_target_through_only():           # касание цели без прохода — не цель
    legs, m, r, _ = run([100, 100], [100, 110], [100, 99], [100, 105], stop=90.0, tgt=110.0)
    assert r == DEADLINE and legs[0][1] == 105.0
    legs, m, r, _ = run([100, 100], [100, 110.5], [100, 99], [100, 105], stop=90.0, tgt=110.0)
    assert r == TARGET and legs == [(1.0, 110.0, True)]
def t_trail_only_up():                 # трейл 5: экстремум 110 -> стоп 105; потом откат 103 -> выход по 105; стоп не опускается
    o=[100,108,104,104]; h=[100,110,108,104]; l=[100,107,103,103]; c=[100,109,104,104]
    legs, m, r, _ = run(o, h, l, c, stop=90.0, trail=5.0)
    assert r == STOP and m == 2 and legs[0][1] == 104.0, (legs, m, r)   # стоп 105, open 104 < 105 -> гэп: по open
def t_trail_no_lowering():             # высокий падает: стоп остаётся на прежнем (ext 110 -> 105), следующая минута low 106 > 105 — жив
    legs, m, r, _ = run([100,108,108],[100,110,108],[100,107,106],[100,109,107], stop=90.0, trail=5.0)
    assert r == DEADLINE and legs[0][1] == 107.0
def t_partial_then_trail():            # 1/2 на 110 (лимит), остаток: ext 115, трейл 5 -> 110, выход по стопу 110
    o=[100,100,114,112]; h=[100,111,115,113]; l=[100,99,112,109]; c=[100,110,114,110]
    legs, m, r, _ = run(o, h, l, c, stop=90.0, part=(110.0, 0.5, 5.0))
    assert legs[0] == (0.5, 110.0, True) and r == STOP and legs[1][:2] == (0.5, 110.0), legs
    assert abs(gross_bps(legs, 100.0) - 1000.0) < 1e-9
def t_breakeven():                     # +10 достигнут -> стоп на входе; откат -> выход по 100
    legs, m, r, _ = run([100,105,102],[100,110.5,103],[100,104,99],[100,106,100], stop=90.0, be=110.0)
    assert r == STOP and m == 2 and legs[0][1] == 100.0, (legs, m)
def t_be_not_same_minute():            # БУ действует со следующей минуты: в минуте достижения low 95 > 90 — жив
    legs, m, r, _ = run([100,100],[100,111],[100,95],[100,105], stop=90.0, be=110.0)
    assert r == DEADLINE
def t_ot_on_bar_close():               # бар = минуты 0..1, ОТ срабатывает на закрытии минуты 1 по close, тейкер
    legs, m, r, _ = trade(A(100,100,100,100), A(100,101,101,101), A(100,99,99,99), A(100,100,97,97), 0, 3, 100.0, 90.0,
                          bar_end=(1, 3), ot=lambda mm, st: st["c"] < 98 and mm == 1)
    assert r == DEADLINE and legs[0][1] == 97.0   # close m=1 = 100 -> ОТ нет; m=3 — дедлайн по close
    legs, m, r, _ = trade(A(100,100,100,97), A(100,101,101,98), A(100,99,99,96), A(100,100,99,97), 0, 3, 100.0, 90.0,
                          bar_end=(1, 3), ot=lambda mm, st: mm == 1)
    assert r == OT and m == 1 and legs == [(1.0, 100.0, False)]
def t_ot_stop_earlier_wins():          # стоп внутри бара раньше ОТ
    legs, m, r, _ = trade(A(100,100,100), A(100,100,100), A(100,89,89), A(100,95,95), 0, 2, 100.0, 90.0, bar_end=(1, 2), ot=lambda mm, st: True)
    assert r == STOP and m == 1
def t_ot_since():                      # нет нового максимума: бары 1,2 без нового max после бара 0 -> since == 2 на баре 2
    seen = []
    trade(A(100,102,101,101,100), A(100,103,102,102,101), A(100,101,100,100,99), A(100,102,101,101,100), 0, 4, 100.0, 90.0,
          bar_end=(0, 1, 2, 3, 4), ot=lambda mm, st: seen.append((mm, st["since"])) or False)
    assert seen == [(0, 1), (1, 0), (2, 1), (3, 2), (4, 3)], seen
def t_costs():
    legs = [(0.5, 110.0, True), (0.5, 110.0, False)]
    assert abs(fee_bps(legs, True) - (1.26 + 0.5 * 1.26 + 0.5 * 3.15)) < 1e-12

if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("t_"): v(); n += 1
    print("ok", n)
