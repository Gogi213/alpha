#!/usr/bin/env python3
"""TK-493 w5 F: ручной пример с известным ответом для цепочки «выходы ядра -> f8_agg.py cells -> cell_status.classify».

Три синтетические пары клетки A2|aster|crypto|a0 (21 сутки 15.10-04.11). Выходы ядра написаны руками (jobday, cells_out, jobext, trades) так, чтобы каждое
число отчёта считалось в уме и независимой формулой в этом файле (t-интервал по блокам из 3 суток, df = 6, t(0,95; 6) = 1,943180); агрегатор запускается как CLI
(`f8_agg.py cells`) на каталоге-прогоне и сверяется поле за полем:

  пара 1 (AAA/BTC): net на сутки 20 + k, k = (день − 2479) mod 3 (20, 21, 22 по кругу); открытая позиция на конце окна: MTM +9 bps, open_at_end = 1
  пара 2 (BBB/BTC): net −4 на сутки;  пара 3 (CCC/BTC): net +2 + 0,6·b, b = ((день − 2479) div 3) mod 2 (чередование блоков по 3 суток; ряд не вырожден);  активна каждые сутки (1 пара-сутки), по одной сделке на пару-сутки (63 сделки)
  суточное значение клетки (закрытые) = (18 + k + 0,6·b)/3;  основной ряд (закрытые + MTM) = то же + 9/3 = +3 на последних сутках (знаменатель MTM - активные пары последних суток = 3)
  «без лучшей монеты»: Y-кандидаты AAA/BBB/CCC, лучшая AAA (сумма 21·21 + 9 = 450 против −84 и 42), остаток BBB+CCC: среднее −1 + 0,3·(9/21) = −0,871 < 0 -> (в) не выполнено;
      якорь BTC - не кандидат (M3-03): раньше удалялись ВСЕ задания и (в) было ложно по построению
  p1 (+1 мин): net каждой пары-сутки на 5 меньше; MTM клетки +4;  случайные входы: по −12,6 на пару-сутки у пары 1
  доля сделок длиной <= 2 мин: сделки пары 2 длиной 2 минуты -> 21/63 = 1/3
  число пар 3 < 5 -> условие (г) не выполнено -> «кандидат» невозможен при любых числах (проверяется: unmet содержит d)

  python f8_handexample.py            печатает таблицу «ожидали / получили» и завершается кодом 0 при полном совпадении
"""
import csv, math, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import cell_status as CS  # noqa: E402

D0, D1 = 2479, 2500
CELL = "A2|aster|crypto|a0"
T95_DF6 = 1.9431802803927816   # t(1 - 0,10/2; df = 6): n = 21 сутки, блок 3, 7 блоков


def w(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        wr = csv.writer(fh, lineterminator="\n")
        wr.writerow(header)
        wr.writerows(rows)


def block_t(series, b=3, t=T95_DF6):
    """Независимая запись блочного t-интервала (формула кластерной устойчивости из docstring portfolio.block_t_interval)."""
    n = len(series)
    mean = sum(series) / n
    nb = math.ceil(n / b)
    ss = sum((sum(series[k * b:(k + 1) * b]) - mean * len(series[k * b:(k + 1) * b])) ** 2 for k in range(nb))
    se = math.sqrt(nb / (nb - 1) * ss / (n * n))
    return mean, mean - t * se, mean + t * se


def build(tmp):
    jd_h = ["cell", "job", "y", "x", "day", "net_bps", "gross_bps", "fee_bps", "funding_bps", "trades", "active"]
    pair = [("AAA", lambda k, b: 20.0 + k), ("BBB", lambda k, b: -4.0), ("CCC", lambda k, b: 2.0 + 0.6 * b)]
    rows, rows_p1, rows_re, tr = [], [], [], []
    for j, (nm, f) in enumerate(pair):
        for d in range(D0, D1):
            k, bb = (d - D0) % 3, ((d - D0) // 3) % 2
            net = f(k, bb)
            rows.append([CELL, j, nm + "USDT", "BTCUSDT", d, net, net + 12.6, 12.6, 0, 1, 1])
            rows_p1.append([CELL, j, nm + "USDT", "BTCUSDT", d, net - 5.0, net + 7.6, 12.6, 0, 1, 1])
            if j == 0:
                rows_re.append([CELL, j, nm + "USDT", "BTCUSDT", d, -12.6, 0, 12.6, 0, 1])
            dur = 2 if j == 1 else 30
            t_out = d * 1440 + 600
            tr.append([CELL, j, nm + "USDT", "BTCUSDT", 1, t_out - dur - 2, t_out - dur, t_out - 2, t_out, net + 12.6, 12.6, 0, net, "rule", 1])
    w(os.path.join(tmp, "w1", "A2_aster_crypto__aster__aster.jobday.csv"), jd_h, rows)
    w(os.path.join(tmp, "p1", "A2_aster_crypto__aster__aster.jobday.csv"), jd_h, rows_p1)
    w(os.path.join(tmp, "re", "A2_aster_crypto__aster__aster.jobday.csv"), jd_h[:-1], rows_re)
    w(os.path.join(tmp, "w1", "A2_aster_crypto__aster__aster.trades.csv"),
      ["cell", "job", "y", "x", "dir", "sig_in", "fill_in", "sig_out", "fill_out", "gross_bps", "fee_bps", "funding_bps", "net_bps", "exit_reason", "beta_in"], tr)
    co_h = ["cell", "jobs", "done", "missing", "no_span", "trades", "pre_window", "entries", "entry_deferred", "exit_deferred", "x_deferred", "gap_minutes", "minutes_in_pos",
            "open_at_end", "open_pre_window", "mtm_n", "mtm_net_bps", "mtm_gross_bps", "mtm_fee_bps", "mtm_funding_bps", "mtm_stale_max_min"]
    w(os.path.join(tmp, "w1", "A2_aster_crypto__aster__aster.cells_out.csv"), co_h, [[CELL, 3, 3, 0, 0, 63, 0, 64, 0, 0, 0, 0, 1000, 1, 0, 1, 9.0, 21.6, 12.6, 0, 1]])
    w(os.path.join(tmp, "p1", "A2_aster_crypto__aster__aster.cells_out.csv"), co_h, [[CELL, 3, 3, 0, 0, 63, 0, 64, 0, 0, 0, 0, 1000, 1, 0, 1, 4.0, 16.6, 12.6, 0, 1]])
    je_h = ["cell", "job", "y", "x", "status", "eval_minutes", "z_def_minutes", "minutes_in_pos", "pos_z_nan_minutes", "noentry_window_minutes", "entries", "trades", "pre_window",
            "pre_window_net_bps", "open_at_end", "open_pre_window", "warmup_blocked", "mtm_n", "mtm_net_bps", "mtm_gross_bps", "mtm_fee_bps", "mtm_funding_bps", "mtm_stale_min"]
    je = []
    for j, (nm, f) in enumerate(pair):
        mtm = 9.0 if j == 0 else 0.0
        je.append([CELL, j, nm + "USDT", "BTCUSDT", "done", 30240, 30000, 400, 0, 240, 22, 21, 0, 0, 1 if j == 0 else 0, 0, 0, 1 if j == 0 else 0, mtm, mtm + 12.6 if j == 0 else 0, 12.6 if j == 0 else 0, 0, 1])
    w(os.path.join(tmp, "w1", "A2_aster_crypto__aster__aster.jobext.csv"), je_h, je)
    w(os.path.join(tmp, "contracts.csv"), ["exchange", "symbol", "asset_id", "slug"],
      [["aster", s + "USDT", s + "|k", "crypto"] for s in ("AAA", "BBB", "CCC", "BTC")])
    w(os.path.join(tmp, "cells_params.csv"), ["cell", "sigma_formation_bps_day_per_active_pair"], [["A2|aster|crypto", "a0=5.0000;a1=5.0000"]])


def main():
    tmp = tempfile.mkdtemp(prefix="f8hand_")
    ok = True
    try:
        build(tmp)
        out = os.path.join(tmp, "agg")
        cmd = [sys.executable, os.path.join(HERE, "f8_agg.py"), "cells", "--run", tmp, "--out", out, "--contracts", os.path.join(tmp, "contracts.csv"),
               "--cells-params", os.path.join(tmp, "cells_params.csv"), "--d0", str(D0), "--d1", str(D1)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode != 0:
            print("ОШИБКА: агрегатор вернул", r.returncode, r.stderr[-800:])
            return 1
        sm = list(csv.DictReader(open(os.path.join(out, "cell_summary.csv"), encoding="utf-8")))[0]
        st = list(csv.DictReader(open(os.path.join(out, "cell_status.csv"), encoding="utf-8")))[0]
        closed = [(18 + (d - D0) % 3 + 0.6 * (((d - D0) // 3) % 2)) / 3.0 for d in range(D0, D1)]
        primary = closed[:-1] + [closed[-1] + 3.0]
        cm, cl, ch = block_t(closed)
        pm, pl, ph = block_t(primary)
        p1_primary = [v - 5.0 for v in closed[:-1]] + [closed[-1] - 5.0 + 4.0 / 3.0]
        wo_mean = sum((-4.0 + 2.0 + 0.6 * (((d - D0) // 3) % 2)) / 2.0 for d in range(D0, D1)) / 21.0
        ctrl = [-12.6 / 3.0] * 21
        ctrl_aligned = ctrl                                   # знаменатель контроля - активные пары основного счёта (3)
        diff = [p - c for p, c in zip(primary, ctrl_aligned)]
        dm, dl, dh = block_t(diff)
        exp = [
            ("jobs", 3, int(sm["jobs"])), ("trades", 63, int(sm["trades"])), ("pairs_traded", 3, int(sm["pairs_traded"])), ("active_pair_days", 63, int(sm["active_pair_days"])),
            ("closed_mean", cm, float(sm["closed_mean"])), ("closed_lo90", cl, float(sm["closed_lo90"])), ("closed_hi90", ch, float(sm["closed_hi90"])),
            ("primary_mean", pm, float(sm["primary_mean"])), ("primary_lo90_t", pl, float(sm["primary_lo90_t"])), ("primary_hi90_t", ph, float(sm["primary_hi90_t"])),
            ("mtm_net_sum_bps", 9.0, float(sm["mtm_net_sum_bps"])), ("open_at_end", 1.0, float(sm["open_at_end"])), ("mtm_denominator", 3.0, float(sm["mtm_denominator"])),
            ("best_base", "AAA", sm["best_base"]), ("best_base_jobs_removed_share", 1.0 / 3.0, float(sm["best_base_jobs_removed_share"])),
            ("mean_without_best_base", wo_mean, float(sm["mean_without_best_base"])),
            ("net_plus1", sum(p1_primary) / 21.0, float(sm["net_plus1"])),
            ("short_trade_share_le_2min", 1.0 / 3.0, float(sm["short_trade_share_le_2min"])),
            ("fee_sum_bps", 63 * 12.6, float(sm["fee_sum_bps"])), ("orphans", 0, int(sm["trades_on_job_days_without_active"])),
            ("status.mean", pm, float(st["mean"])), ("status.lo_t", pl, float(st["lo_t"])), ("status.hi_t", ph, float(st["hi_t"])),
            ("status.closed_mean", cm, float(st["closed_mean"])), ("status.diff_mean", dm, float(st["diff_mean"])), ("status.diff_lo_t", dl, float(st["diff_lo_t"])),
            ("status.cond_a", 1, int(st["cond_a"])), ("status.cond_b", 1, int(st["cond_b"])), ("status.cond_c", 0, int(st["cond_c"])), ("status.cond_d", 0, int(st["cond_d"])),
            ("status.cond_e", 1, int(st["cond_e"])), ("status.cond_g", 1, int(st["cond_g"])), ("status.status", "unclear", st["status"]),
            ("status.unmet", "c+d", st["unmet"]), ("status.open_semantics", "first_trade", st["open_semantics"]),
        ]
        print("%-34s %-22s %-22s %s" % ("поле", "ожидали", "получили", ""))
        for name, e, g in exp:
            good = (abs(e - g) <= 1e-9 * max(1.0, abs(e))) if isinstance(e, (int, float)) and not isinstance(e, bool) else (e == g)
            ok &= good
            print("%-34s %-22s %-22s %s" % (name, ("%.9g" % e) if isinstance(e, float) else e, ("%.9g" % g) if isinstance(g, float) else g, "OK" if good else "РАСХОЖДЕНИЕ"))
        # без --contracts агрегатор обязан отказать (аудит M3-02)
        r2 = subprocess.run([c for c in cmd if c not in ("--contracts", os.path.join(tmp, "contracts.csv"))], capture_output=True, text=True)
        refused = r2.returncode != 0 and "contracts" in (r2.stdout + r2.stderr)
        ok &= refused
        print("отказ без --contracts: %s" % ("OK" if refused else "НЕТ ОТКАЗА"))
        # опечатка в пути контрактов - тоже отказ (файла нет)
        cmd3 = list(cmd)
        cmd3[cmd3.index("--contracts") + 1] = os.path.join(tmp, "contracts.csv.typo")
        r3 = subprocess.run(cmd3, capture_output=True, text=True)
        refused3 = r3.returncode != 0
        ok &= refused3
        print("отказ при несуществующем файле контрактов: %s" % ("OK" if refused3 else "НЕТ ОТКАЗА"))
        print("РУЧНОЙ ПРИМЕР: %s" % ("ВСЕ ПОЛЯ СОВПАЛИ" if ok else "ЕСТЬ РАСХОЖДЕНИЯ"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
