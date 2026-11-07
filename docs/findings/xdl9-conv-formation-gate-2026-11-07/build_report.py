#!/usr/bin/env python3
"""TK-493, задача C: сборка отчёта docs/findings/xdl9-conv-formation-gate-2026-11-07.md из файлов расчёта (json/csv этого каталога и каталога списков).

Каждое число из расчёта вставляется функцией n(спецификация, формат); спецификация записывается в report_numbers.json вместе с номером строки отчёта.
Проверка (check_numbers.py) читает спецификации заново своим кодом и сверяет значение с текстом на указанной строке.
Запуск из корня репозитория:  python docs/findings/xdl9-conv-formation-gate-2026-11-07/build_report.py
"""
import csv
import json
import math
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
T = "docs/findings/xdl9-conv-formation-gate-2026-11-07"
L1 = "docs/research/P-38-calib-gate-2026-11-07"
OUT_MD = "docs/findings/xdl9-conv-formation-gate-2026-11-07.md"
OUT_LEDGER = T + "/report_numbers.json"

SETS = {"g1": "gate_q5", "g2": "gate_noq5", "n1": "nogate_q5", "n2": "nogate_noq5"}
SEL = {"g1": "gate", "g2": "gate_noq5", "n1": "nogate", "n2": "nogate_noq5"}
GRID = {"g1": L1 + "/grid_class_crypto.csv", "g2": T + "/bundle/grid_class_crypto_noq5.csv", "n1": T + "/bundle_nogate/grid_class_crypto_nogate.csv",
        "n2": T + "/bundle_nogate/grid_class_crypto_noq5_nogate.csv"}
CELLS = {"g1": L1 + "/cells.csv", "g2": T + "/bundle/cells_crypto_noq5.csv", "n1": T + "/bundle_nogate/cells_crypto_nogate.csv", "n2": T + "/bundle_nogate/cells_crypto_noq5_nogate.csv"}


def path_of(key):
    if key in SETS:
        return T + "/data/summary_%s.json" % SETS[key]
    return key


def load_json(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def load_csv(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def jget(obj, path):
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def fnum(x):
    return None if x in (None, "", "None") else float(x)


def fmt(x, code):
    if x is None:
        return "—"
    if code == "i":
        return str(int(round(x)))
    if code == "yn":
        return "да" if x else "нет"
    if code == "s":
        return str(x)
    if code == "e2":
        mant, ex_ = ("%.2e" % x).split("e")
        sup = str(int(ex_)).replace("-", "\u207b").translate(str.maketrans("0123456789", "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"))
        return mant.replace(".", ",") + "\u00b710" + sup
    m = re.match(r"^(s?)(pc)?f(\d)$", code)
    if not m:
        raise ValueError(code)
    sign, pc, nd = m.group(1), m.group(2), int(m.group(3))
    v = x * 100.0 if pc else x
    txt = "%.*f" % (nd, abs(v))
    if float(txt) == 0.0:
        out = txt
    elif v < 0:
        out = "−" + txt
    else:
        out = ("+" if sign else "") + txt
    out = out.replace(".", ",")
    return out + (" %" if pc else "")


def combo_txt(c):
    th, k, p, t, s = c
    return "θ %s; k %d; p %d; T %s ч; %s" % (("%g" % th).replace(".", ","), k, p, ("%g" % (t / 60.0)).replace(".", ","), "S0 = 2" if s else "только S1")


def cf_row(s):
    c = load_json(T + "/data/k7_%s_selection_crypto.json" % SEL[s])["c_full"]
    for r in load_csv(GRID[s]):
        if float(r["theta"]) == c[0] and int(r["k"]) == c[1] and int(r["p"]) == c[2] and int(r["T"]) == c[3] and (fnum(r["s_stop"]) == (float(c[4]) if c[4] is not None else None)):
            return r
    raise KeyError(c)


def cnt_pred(s, pred):
    rows = load_csv(GRID[s])
    f = {"pos": lambda r: fnum(r["mean"]) is not None and fnum(r["mean"]) > 0,
         "pos_p3": lambda r: fnum(r["mean"]) is not None and fnum(r["mean"]) > 0 and int(r["p"]) == 3,
         "ok": lambda r: r["ok"] == "True",
         "pos_lo": lambda r: fnum(r["lo"]) is not None and fnum(r["lo"]) > 0,
         "pos_lo_ok": lambda r: r["ok"] == "True" and fnum(r["lo"]) is not None and fnum(r["lo"]) > 0,
         "p3": lambda r: int(r["p"]) == 3,
         "all": lambda r: True}[pred]
    return sum(1 for r in rows if f(r))


def cells_a(s, i):
    rows = [r for r in load_csv(CELLS[s]) if s != "g1" or r["class"] == "crypto"]
    if i == 0:
        return len(rows)
    if i == 99:
        return sum(1 for r in rows if r["tradable"] == "1")
    return sum(1 for r in rows if r["A%d" % i] == "1")


def hx_pred(name):
    h = load_json(T + "/hints_gate_extra/hints_c2_summary.json")
    isg = [r for r in h if "gate" in r["name"].lower()]
    crypto = [r for r in isg if r["name"].split()[0] in ("H2", "H3", "H4", "H4c", "H6")]
    main = [r for r in crypto if r.get("in_main_series")]
    if name == "gate_all":
        return len(isg)
    if name == "gate_in_universe":
        return sum(1 for r in isg if r["in_universe"])
    if name == "gate_crypto":
        return len(crypto)
    if name == "gate_crypto_main":
        return len(main)
    if name == "gate_hk_tradfi":
        return sum(1 for r in isg if r["name"].split()[0] == "H1")
    if name == "gate_crypto_main_grid_pos":
        return sum(1 for r in main if (r.get("grid_trades_total_all_combos") or 0) > 0)
    if name == "gate_crypto_main_sel_trades":
        return sum((r.get("at_selected") or {}).get("trades", 0) for r in main)
    if name == "gate_crypto_main_grid_trades":
        return sum(r.get("grid_trades_total_all_combos") or 0 for r in main)
    raise KeyError(name)


def w2_agg(name):
    rows = load_csv(T + "/hints_gate_extra/hints_w2_formation.csv")
    g = [r for r in rows if "gate" in r["name"].lower()]
    crypto = [r for r in g if r["name"].split()[0] in ("H2", "H3", "H4", "H4c", "H6")]
    hk = [r for r in g if r["name"].split()[0] == "H1"]
    def sel20(rs):
        return [r for r in rs if r["var"] == "rel" and float(r["theta"]) == 20 and fnum(r["net_taker_p1_mean"]) is not None]
    def le80(rs):
        return [r for r in rs if float(r["theta"]) <= 80 and fnum(r["net_taker_p1_mean"]) is not None]
    if name == "crypto_rel20_pairs":
        return len(sel20(crypto))
    if name == "crypto_rel20_trades":
        return sum(int(r["trades_p1"]) for r in sel20(crypto))
    if name == "crypto_rel20_net":
        rs = sel20(crypto)
        return sum(int(r["trades_p1"]) * float(r["net_taker_p1_mean"]) for r in rs) / sum(int(r["trades_p1"]) for r in rs)
    if name == "crypto_rel20_net_max":
        return max(float(r["net_taker_p1_mean"]) for r in sel20(crypto))
    if name == "crypto_rel20_net_min":
        return min(float(r["net_taker_p1_mean"]) for r in sel20(crypto))
    if name == "crypto_le80_rows":
        return len(le80(crypto))
    if name == "crypto_le80_pos":
        return sum(1 for r in le80(crypto) if float(r["net_taker_p1_mean"]) > 0)
    if name == "crypto_le80_pos_trades_max":
        return max([int(r["trades_p1"]) for r in le80(crypto) if float(r["net_taker_p1_mean"]) > 0] or [0])
    if name == "hk_le80_rows":
        return len(le80(hk))
    if name == "hk_le80_pos":
        return sum(1 for r in le80(hk) if float(r["net_taker_p1_mean"]) > 0)
    if name == "hk_le80_pos_ge20":
        return sum(1 for r in le80(hk) if float(r["net_taker_p1_mean"]) > 0 and int(r["trades_p1"]) >= 20)
    raise KeyError(name)


def g8(K, what):
    w = load_json(L1 + "/g_c2_8.json")["winner"]
    v = [r["p_winner_a2"] for r in w if r["K"] == K]
    return 100.0 * (min(v) if what == "min" else max(v))


def a8(s, what):
    d = load_json(T + "/data/k7_%s_selection_crypto.json" % SEL[s])["a8"]
    return sum(1 for v in d.values() if v) if what == "true" else len(d)


def r31(s):
    sel_ = load_json(T + "/data/k7_%s_selection_crypto.json" % SEL[s])
    return bool(fnum(cf_row(s)["lo"]) > 0 and sel_["plateau"] and sel_["agree"])


def eqc(s1, s2):
    return load_json(T + "/data/k7_%s_selection_crypto.json" % SEL[s1])["c_full"] == load_json(T + "/data/k7_%s_selection_crypto.json" % SEL[s2])["c_full"]


def hxn(name, field):
    h = load_json(T + "/hints_gate_extra/hints_c2_summary.json")
    return [r for r in h if r["name"] == name][0][field]


def turn_panels():
    t = open(os.path.join(ROOT, T, "logs/turn.log"), encoding="utf-8").read()
    return int(re.search(r"панелей оборота (\d+)", t).group(1))


def _ts(line):
    m = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", line)
    import datetime
    return datetime.datetime.strptime(m[0], "%Y-%m-%dT%H:%M:%SZ")


def dur(p1, p2):
    lines = open(os.path.join(ROOT, T, "logs/pipe.status"), encoding="utf-8").read().split("\n")
    a = [ln for ln in lines if p1 in ln][0]
    b = [ln for ln in lines if p2 in ln][0]
    return (_ts(b) - _ts(a)).total_seconds() / 60.0


def w2row(name, var, theta, col):
    for r in load_csv(T + "/hints_gate_extra/hints_w2_formation.csv"):
        if r["name"] == name and r["var"] == var and float(r["theta"]) == theta:
            return fnum(r[col])
    raise KeyError(name)


def resolve(spec):
    k = spec[0]
    if k == "j":
        return jget(load_json(path_of(spec[1])), spec[2])
    if k == "cf":
        return fnum(cf_row(spec[1])[spec[2]])
    if k == "cnt":
        return cnt_pred(spec[1], spec[2])
    if k == "cells":
        return cells_a(spec[1], spec[2])
    if k == "hx":
        return hx_pred(spec[1])
    if k == "w2":
        return w2_agg(spec[1])
    if k == "const":
        return spec[1]
    if k == "g8":
        return g8(spec[1], spec[2])
    if k == "a8":
        return a8(spec[1], spec[2])
    if k == "r31":
        return r31(spec[1])
    if k == "eqc":
        return eqc(spec[1], spec[2])
    if k == "hxn":
        return hxn(spec[1], spec[2])
    if k == "turn":
        return turn_panels()
    if k == "dur":
        return dur(spec[1], spec[2])
    if k == "logre":
        t = open(os.path.join(ROOT, T, spec[1]), encoding="utf-8").read()
        return float(re.findall(spec[2], t)[spec[4]][spec[3] - 1] if isinstance(re.findall(spec[2], t)[spec[4]], tuple) else re.findall(spec[2], t)[spec[4]])
    if k == "w2row":
        return w2row(spec[1], spec[2], spec[3], spec[4])
    if k == "x":
        a, b = resolve(spec[2]), resolve(spec[3])
        op = spec[1]
        if a is None or b is None:
            return None
        return {"sub": a - b, "div": (a / b if b else None), "mul21div13": a / 13.0 * 21.0, "pct": 100.0 * a / b if b else None, "add": a + b}[op]
    raise KeyError(spec)


# ------------------------------------------------------------------ сборка
LEDGER = []
TOKENS = {}


def n(spec, code="f2"):
    v = resolve(spec)
    if isinstance(v, (list, tuple)) and code == "combo":
        txt = combo_txt(v)
    else:
        txt = fmt(v, code)
    i = len(LEDGER)
    LEDGER.append({"id": i, "spec": spec, "code": code, "text": txt})
    return "\x01%d\x02" % i


def J(s, path, code="f2"):
    return n(["j", s, path], code)


def C(s, col, code="f2"):
    return n(["cf", s, col], code)


def X(op, a, b, code="f2"):
    return n(["x", op, a, b], code)


def cr(s, path, code="f2"):
    return J(s, "classes.crypto." + path, code)


def ex(s, path, code="f2"):
    return n(["j", T + "/data/extra_%s.json" % SETS[s], "crypto." + path], code)


def sel(s, path, code="f2"):
    return n(["j", T + "/data/k7_%s_selection_crypto.json" % SEL[s], path], code)


def build():
    L = []
    A = L.append
    cmb = lambda s: sel(s, "c_full", "combo")
    cmt = lambda s: sel(s, "c_train", "combo")
    A("# TK-493, вид C2 (схождения): перекалибровка на формировании 01.10–14.10.2026 с Gate (G0 часть 2, вторая; П-38 r2 + r3 R3.7)")
    A("")
    A("**Статус:** 07.11.2026, итог задачи C. Протокол `docs/research/P-38-convergence-xdl9.md` (r2 + раздел 17 r3), params G0.1 `docs/research/P-38-params-2026-11-07.csv`, params r3 `docs/research/P-38-params-r3-2026-11-07.csv`, "
      "addendum без Gate `docs/research/P-38-params-calib-2026-11-07.csv` (`f47dff41`, не менялся) и addendum с Gate `docs/research/P-38-params-calib-gate-2026-11-07.csv` (числа и списки: `docs/research/P-38-calib-gate-2026-11-07/`; "
      "таблицы, выжимки, скрипты: `docs/findings/xdl9-conv-formation-gate-2026-11-07/`). Код — коммит `5162113f` (HEAD при развёртывании на Deck), ветка `tk493-xdl9`, в origin не пушили. "
      "Методика и числа назначены CEO-сессией без Судьи; всё помеченное «назначено … подтвердить» ждёт подтверждения. Это сито на формировании, не свидетельство (§0.6 и).")
    A("")
    A("**Торговое окно (сутки ≥ 2479) не читалось.** В `~/xdl9/c2g` нет `window_reads.log` и `go_window.token`; все `meta.json` сеток (%s шт.) имеют `d1` ≤ 2478 (сутки 2478: %s, Pi_train 2471: %s); ни один одномерный `.npy` не длиннее 20 160 минут (%s файлов проверено); "
      "аргументы всех запусков — `--last-day` только 2471 и 2478 (скрипты `scripts/pipe*.sh`). Токен и `--go-window` не создавались и не использовались. Проверка: `data/window_not_read.json`." % (
          n(["x", "add", ["j", T + "/data/window_not_read.json", "grid_meta_d1_counts.2478"], ["j", T + "/data/window_not_read.json", "grid_meta_d1_counts.2471"]], "i"),
          n(["j", T + "/data/window_not_read.json", "grid_meta_d1_counts.2478"], "i"), n(["j", T + "/data/window_not_read.json", "grid_meta_d1_counts.2471"], "i"),
          n(["j", T + "/data/window_not_read.json", "npy_files"], "i")))
    A("")
    A("## Главное")
    A("- **Gate в универсуме, набор окна — «без Gate».** Манифест `gate_ready` валиден (символов %s), калибровка с Gate выполнена по тем же правилам; крипта с Gate: %s пар (+%s пар Gate), ячеек %s. "
      "c_полн не изменился ни у V1 (%s), ни у V2 (%s)." % (
          n(["j", T + "/data/calib_start.json", "gate_nsym"], "s"),
          cr("g1", "pairs", "i"), cr("g1", "pairs_gate", "i"), cr("g1", "cells", "i"), cmb("g1"), cmb("g2")))
    A("- **Числа V1 с Gate** (без Gate): сделок %s (%s), активов %s (%s), чистая %s (%s) bps на сделку, `y_d` %s (%s), нижняя граница 90 %%-интервала %s (%s). Пары с ногой Gate сами: сделок %s, чистая %s bps, `y_d` %s [нижняя %s] — от нуля не отличимы." % (
        C("g1", "trades", "i"), C("n1", "trades", "i"), C("g1", "assets", "i"), C("n1", "assets", "i"), C("g1", "mean_trade", "sf1"), C("n1", "mean_trade", "sf1"), C("g1", "mean", "sf3"), C("n1", "mean", "sf3"),
        C("g1", "lo", "sf3"), C("n1", "lo", "sf3"), cr("g1", "cfull_gate_pairs.trades", "i"), cr("g1", "cfull_gate_pairs.net", "sf1"), cr("g1", "cfull_gate_pairs.mean_yd", "sf3"), cr("g1", "cfull_gate_pairs.lo", "sf3")))
    A("- **R3.1 с Gate не выполнено:** (а) нижняя граница > 0 и (б) плато выполнены у V1 и V2, но (в) согласие c_обуч / c_полн — нет (расстояние %s шага по T у обоих). Поэтому по R3.7 п. 4 в окно идёт набор **без Gate** (V1 и V2, `M_окно` = 2, числа r3 без изменений: "
      "c_полн V1 %s, нижняя граница %s; V2 %s, %s); варианты с Gate — только описательные строки вне `M_окно`." % (
          ex("g1", "step_distance", "i"), cmb("n1"), C("n1", "lo", "sf3"), cmb("n2"), C("n2", "lo", "sf3")))
    A("- **Контроль:** `--no-gate` нового кода побайтно равен прежнему прогону. Метки формирования без Gate прежние (A3b: V1 не пройдено, V2 пройдено; A3 по клеткам не пройдено; A8 пройдено). Метка «кандидат» недостижима. "
      "Раннер окна `conv_window.py` Gate-варианты не принимает: строки «с Gate» на окне требуют его доработки.")
    A("")
    A("## 0. Чего тест не показывает (строка 0; числа обновлены с Gate)")
    A("(а) Одна фаза рынка: 13 сигнальных суток (6 для Pi_train). При `n_эфф` = %s нижние границы консервативны, а выбор одной из 180 комбинаций по максимуму нижней границы на тех же сутках сам не ограничен. "
      "G-C2-8 (синтетика, перенесена из прежнего прогона): у победителя из 180 независимых нулевых рядов правило A2 проходит в %s–%s %% случаев, из 12 рядов — в %s–%s %%." % (
          C("g1", "n_eff", "i"), n(["g8", 180, "min"], "f1"), n(["g8", 180, "max"], "f1"), n(["g8", 12, "min"], "f1"), n(["g8", 12, "max"], "f1")))
    A("(б) Допуск клеток r2 пуст и с Gate (M = %s из %s клеток крипты и %s клеток акций+ETF): это результат. **Раскрытие:** правило подтверждения изменено ПОСЛЕ первой калибровки (r3: класс-уровневое подтверждение, решение CEO 07.11), "
      "и Gate включён в универсум решением CEO ПОСЛЕ калибровки без Gate (R3.7); оба изменения записаны в протокол и params до этой калибровки и до окна, но принимались, видя числа формирования без Gate." % (
          n(["cells", "g1", 99], "i"), n(["cells", "g1", 0], "i"), J("g1", "classes.eq.cells", "i")))
    A("(в) Охват: в основном ряду крипта %s пар (из них с ногой Gate %s; %s ячеек, из них %s с Gate; %s активов) и акции+ETF %s пар. Gate-акции (%s пар), Gate-металлы и энергия (%s пар) — вне основного ряда (нет метки США/не-США и календаря). "
      "Остальное — как в прежнем отчёте: металлы+энергия 8 бирж %s пар (нет календаря CME), прочие TradFi %s, индексы %s, фиат %s." % (
          cr("g1", "pairs", "i"), cr("g1", "pairs_gate", "i"), cr("g1", "cells", "i"), cr("g1", "cells_gate", "i"), cr("g1", "assets", "i"), J("g1", "classes.eq.pairs", "i"),
          n(["j", T + "/data/k7_gate_universe.json", "pairs_by_class_market.eq|gate_tradfi.pairs"], "i"), n(["j", T + "/data/k7_gate_universe.json", "pairs_by_class_market.metal|gate_tradfi.pairs"], "i"),
          n(["j", T + "/data/k7_gate_universe.json", "pairs_by_class_market.metal|no_calendar.pairs"], "i"), n(["j", T + "/data/k7_gate_universe.json", "pairs_by_class_market.eq|other_tradfi.pairs"], "i"),
          n(["j", T + "/data/k7_gate_universe.json", "pairs_by_class_market.idx|US.pairs"], "i"), n(["j", T + "/data/k7_gate_universe.json", "pairs_by_class_market.fx|other_tradfi.pairs"], "i")))
    A("(г) Нет стакана и ёмкости; проскальзывание — оценка Ролла по 1-минутным `close` (таблица по биржам, включая Gate, — `tables_gate.md`, T1). Издержки круга 12,6 bps (4 × 3,15, В-88), фандинг фактический.")
    A("(з) Разрешение — минута по последней сделке: всплески короче минуты и спред по стакану не наблюдаются; «нет плюса при +1 мин» видео не опровергает.")
    A("(и) Параметры и сетка (θ до 320 bps, задержка +1 мин, ось `p`) выбраны после чтения подсказок на тех же 14 сутках.")
    A("(к) Семантика `open` Gate — «первая сделка» (медиана доли `open[t] = close[t−1]` = %s, доля минут со сделкой %s); в пределах минуты Gate-нога «видит» цену не так, как цепочечные биржи (Bybit, BingX, Bitget, Kraken, Lighter). "
      "Единицы оборота Gate: `close · v · mult`, mult = 1 в файле (G-C2-7 на %s символах: макс. относительное расхождение %s). У 15 контрактов `enable_decimal` минуты с дробными сделками имеют v = 0 (оборот занижен); 15 пред-рыночных контрактов Gate вне теста." % (
          n(["j", T + "/data/k7_gate_universe.json", "open_semantics_by_exchange.gate.chain_share_median"], "f3"),
          n(["j", T + "/data/k7_gate_universe.json", "open_semantics_by_exchange.gate.valid_share_median"], "f3"),
          n(["logre", "logs/turn_verify_gate.log", r"проверено символов (\d+)", 1, 0], "i"), n(["logre", "logs/turn_verify_gate.log", r"расхождения ([0-9.e+-]+)", 1, 0], "e2")))
    A("")
    A("## 1. Итоги")
    A("")
    A("### 1.1 Манифест Gate и код")
    A("`~/xdl9/gate_ready` — манифест (строки `rows, gaps, units, open_semantics, funding, classes` = ok, `symbols=1072`), валиден по `conv_gate.py manifest` и по K0 (`gate_ready_valid = true`, `data/calib_start.json`, время K0 04:14:27Z, 08:14 GMT+4). "
      "Код развёрнут из `git archive HEAD` (LF, sha256 params и r3 равны blob). Тесты на Deck (`logs/tests1.log`, `logs/tests2.log`): не прошли тестов: `test_conv.py` %s из %s, `test_conv_gate.py` %s из %s, `test_conv_window.py` %s из %s. "
      "Прогон `--no-gate` нового кода побайтно совпал с прежним `~/xdl9/c2/out` и `out_noq5` (сетки, выбор, ячейки, числа класса): %s из %s сравнений равны, расхождений %s (`data/nogate_vs_old.txt`)." % (
          n(["logre", "logs/tests1.log", r"итог: (\d+) из (\d+) тестов", 1, 0], "i"), n(["logre", "logs/tests1.log", r"итог: (\d+) из (\d+) тестов", 2, 0], "i"),
          n(["logre", "logs/tests2.log", r"итог: (\d+) из (\d+) тестов", 1, 0], "i"), n(["logre", "logs/tests2.log", r"итог: (\d+) из (\d+) тестов", 2, 0], "i"),
          n(["logre", "logs/tests2.log", r"итог: (\d+) из (\d+) тестов", 1, 1], "i"), n(["logre", "logs/tests2.log", r"итог: (\d+) из (\d+) тестов", 2, 1], "i"),
          n(["j", T + "/data/nogate_vs_old_counts.json", "eq"], "i"), n(["j", T + "/data/nogate_vs_old_counts.json", "total"], "i"), n(["j", T + "/data/nogate_vs_old_counts.json", "diff"], "i")))
    A("")
    A("### 1.2 Сетка 180 × класс с Gate (крипта, Π_full, +1 мин, издержки 12,6 bps)")
    A("Полная сетка: `P-38-calib-gate-2026-11-07/grid_class_crypto.csv` (V1, Q5 включён), `bundle/grid_class_crypto_noq5.csv` (V2), без Gate — `bundle_nogate/`. Комбинаций с `y_d` > 0 из 180: %s (без Gate %s); при p = 3 — %s из %s (без Gate %s); "
      "среди допустимых по `n_min`, суткам и активам (%s строк, без Gate %s) нижняя граница > 0 у %s (без Gate %s)." % (
          n(["cnt", "g1", "pos"], "i"), n(["cnt", "n1", "pos"], "i"), n(["cnt", "g1", "pos_p3"], "i"), n(["cnt", "g1", "p3"], "i"), n(["cnt", "n1", "pos_p3"], "i"),
          n(["cnt", "g1", "ok"], "i"), n(["cnt", "n1", "ok"], "i"), n(["cnt", "g1", "pos_lo_ok"], "i"), n(["cnt", "n1", "pos_lo_ok"], "i")))
    A("")
    A("| | без Gate, V1 (Q5) | с Gate, V1 (Q5) | без Gate, V2 (без Q5) | с Gate, V2 (без Q5) |")
    A("|---|---|---|---|---|")
    def row(label, f):
        A("| %s | %s | %s | %s | %s |" % (label, f("n1"), f("g1"), f("n2"), f("g2")))
    row("пар / активов / ячеек", lambda s: "%s / %s / %s" % (cr(s, "pairs", "i"), cr(s, "assets", "i"), cr(s, "cells", "i")))
    row("из них пар с ногой Gate", lambda s: cr(s, "pairs_gate", "i"))
    row("c_полн (θ; k; p; T; S0)", cmb)
    row("сделок / активов / суток", lambda s: "%s / %s / %s" % (C(s, "trades", "i"), C(s, "assets", "i"), C(s, "days", "i")))
    row("валовая, bps на сделку", lambda s: C(s, "gross_trade", "sf1"))
    row("чистая, bps на сделку", lambda s: C(s, "mean_trade", "sf1"))
    row("`y_d` [90 %: нижняя; верхняя]", lambda s: "%s [%s; %s]" % (C(s, "mean", "sf3"), C(s, "lo", "sf3"), C(s, "hi", "sf3")))
    row("`n_эфф` / df", lambda s: "%s / %s" % (C(s, "n_eff", "i"), C(s, "df", "i")))
    row("доля сделок в плюсе; выигрыш/проигрыш", lambda s: "%s; %s" % (C(s, "win_rate", "pcf1"), C(s, "win_loss_ratio", "f2")))
    row("доля сделок ≤ 2 мин", lambda s: C(s, "share_le2", "pcf1"))
    A("")
    A("Выходы V1 с Gate (`data/summary_gate_q5.json`, 426 сделок): схождение %s (чистая %s), стоп S3 %s (%s), S1 %s (%s), S2 %s, таймаут %s (%s); отложенных входов %s, выходов по последней цене %s. "
      "Без Gate: схождение %s (%s), S3 %s (%s), таймаут %s, S2 %s; отложенных %s, по последней цене %s." % (
          cr("g1", "exits.by_code.CV.n", "i"), cr("g1", "exits.by_code.CV.net_mean", "sf1"), cr("g1", "exits.by_code.S3.n", "i"), cr("g1", "exits.by_code.S3.net_mean", "sf1"),
          cr("g1", "exits.by_code.S1.n", "i"), cr("g1", "exits.by_code.S1.net_mean", "sf1"), cr("g1", "exits.by_code.S2.n", "i"), cr("g1", "exits.by_code.TO.n", "i"), cr("g1", "exits.by_code.TO.net_mean", "sf1"),
          cr("g1", "exits.deferred", "i"), cr("g1", "exits.ffill", "i"),
          cr("n1", "exits.by_code.CV.n", "i"), cr("n1", "exits.by_code.CV.net_mean", "sf1"), cr("n1", "exits.by_code.S3.n", "i"), cr("n1", "exits.by_code.S3.net_mean", "sf1"),
          cr("n1", "exits.by_code.TO.n", "i"), cr("n1", "exits.by_code.S2.n", "i"), cr("n1", "exits.deferred", "i"), cr("n1", "exits.ffill", "i")))
    A("")
    A("**Контроли формирования для c_полн V1.** Перевёрнутое направление: `y_d` %s (без Gate %s). Перенос входа на +5 / +15 / −5 / −15 мин: сделок %s / %s / %s / %s из %s, `y_d` %s / %s / %s / %s (без Gate: %s / %s / %s / %s из %s; %s / %s / %s / %s). "
      "`p_win` (перестановка знака, 1 000, seed 37; приближение — выходы не пересчитываются): %s (максимум нижней границы %s) — без Gate %s." % (
          sel("g1", "k8_reversed_mean_yd", "sf3"), sel("n1", "k8_reversed_mean_yd", "sf3"),
          cr("g1", "k8_shift.shifts.5.trades_done", "i"), cr("g1", "k8_shift.shifts.15.trades_done", "i"), cr("g1", "k8_shift.shifts.-5.trades_done", "i"), cr("g1", "k8_shift.shifts.-15.trades_done", "i"), cr("g1", "k8_shift.real_closed_trades", "i"),
          cr("g1", "k8_shift.shifts.5.mean_yd", "f3"), cr("g1", "k8_shift.shifts.15.mean_yd", "f3"), cr("g1", "k8_shift.shifts.-5.mean_yd", "f3"), cr("g1", "k8_shift.shifts.-15.mean_yd", "f3"),
          cr("n1", "k8_shift.shifts.5.trades_done", "i"), cr("n1", "k8_shift.shifts.15.trades_done", "i"), cr("n1", "k8_shift.shifts.-5.trades_done", "i"), cr("n1", "k8_shift.shifts.-15.trades_done", "i"), cr("n1", "k8_shift.real_closed_trades", "i"),
          cr("n1", "k8_shift.shifts.5.mean_yd", "f3"), cr("n1", "k8_shift.shifts.15.mean_yd", "f3"), cr("n1", "k8_shift.shifts.-5.mean_yd", "f3"), cr("n1", "k8_shift.shifts.-15.mean_yd", "f3"),
          sel("g1", "p_win.p_win", "f3"), sel("g1", "p_win.obs_best", "f3"), sel("n1", "p_win.p_win", "f3")))
    A("Описательно (половины активов, c_полн V1 с Gate): H0 — %s сделок, `y_d` %s [нижняя %s]; H1 — %s сделок, `y_d` %s [нижняя %s]. Без Gate: H0 %s / %s [%s], H1 %s / %s [%s]." % (
        cr("g1", "cfull_H0.trades", "i"), cr("g1", "cfull_H0.mean_yd", "sf3"), cr("g1", "cfull_H0.lo", "sf3"), cr("g1", "cfull_H1.trades", "i"), cr("g1", "cfull_H1.mean_yd", "sf3"), cr("g1", "cfull_H1.lo", "sf3"),
        cr("n1", "cfull_H0.trades", "i"), cr("n1", "cfull_H0.mean_yd", "sf3"), cr("n1", "cfull_H0.lo", "sf3"), cr("n1", "cfull_H1.trades", "i"), cr("n1", "cfull_H1.mean_yd", "sf3"), cr("n1", "cfull_H1.lo", "sf3")))
    A("Структурные числа крипты (Π_full; без Gate → с Gate): K_stale %s → %s мин, q_stale %s → %s, q_c %s → %s bps, сдвиг базиса m %s → %s σ, G_max %s → %s bps («граница опоры данных, не измеренный разрыв»), Gσ_max %s → %s, "
      "T_struct %s → %s мин. K2: медиана отношения MAD(d_4320)/MAD(d_1440) в крипте %s → %s (L = 1440 в обоих конвейерах, и Π_train)." % (
          cr("n1", "classpar_full.kstale", "i"), cr("g1", "classpar_full.kstale", "i"), cr("n1", "classpar_full.q_stale", "f3"), cr("g1", "classpar_full.q_stale", "f3"),
          cr("n1", "classpar_full.q_c", "f2"), cr("g1", "classpar_full.q_c", "f2"), cr("n1", "classpar_full.m_shift", "f3"), cr("g1", "classpar_full.m_shift", "f3"),
          cr("n1", "classpar_full.gmax", "f0"), cr("g1", "classpar_full.gmax", "f0"), cr("n1", "classpar_full.gsig_max", "f0"), cr("g1", "classpar_full.gsig_max", "f0"),
          cr("n1", "structural_full.gmax_rule.t_struct", "f0"), cr("g1", "structural_full.gmax_rule.t_struct", "f0"),
          n(["j", T + "/data/nogate_k2_calib.json", "by_class.crypto.median"], "f4"), n(["j", T + "/data/gate_k2_calib.json", "by_class.crypto.median"], "f4")))
    A("Акции+ETF Gate не меняет (Gate-акции вне основного ряда): %s пар, c_полн %s; сделок %s, чистая %s bps, `y_d` %s [нижняя %s] — числа те же, что без Gate; класс не сформирован." % (
        J("g1", "classes.eq.pairs", "i"), J("g1", "classes.eq.sel.c_full", "combo"), J("g1", "classes.eq.at_cfull.trades", "i"), J("g1", "classes.eq.at_cfull.mean_trade", "sf1"),
        J("g1", "classes.eq.at_cfull.mean", "sf3"), J("g1", "classes.eq.at_cfull.lo", "sf3")))
    A("")
    A("### 1.3 Условия R3.1 и метки (проверка на новых числах до окна)")
    A("R3.1: в окно идёт класс, у которого на Π_full (а) нижняя граница блочного t `y_d` у c_полн > 0, (б) плато, (в) согласие c_обуч/c_полн (не дальше одного шага по θ, k, T). A3 по клеткам, A3b, A8 — метки, не условия.")
    A("")
    A("| набор, вариант | c_полн | (а) нижняя граница `y_d` | (б) плато | (в) согласие; c_обуч (расстояние в шагах) | R3.1 | A3b: H0→H1 / H1→H0 | A3 по клеткам | A8 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for lab, s in (("без Gate, V1", "n1"), ("без Gate, V2", "n2"), ("с Gate, V1", "g1"), ("с Gate, V2", "g2")):
        A("| %s | %s | %s | %s | %s; %s (%s) | **%s** | %s (%s сд.) / %s (%s сд.) | %s из %s | %s из %s бирж |" % (
            lab, cmb(s), C(s, "lo", "sf3"), sel(s, "plateau", "yn"), sel(s, "agree", "yn"), cmt(s), ex(s, "step_distance", "i"),
            n(["r31", s], "yn"),
            sel(s, "a3b_detail.detail.H0.mean", "sf3"), sel(s, "a3b_detail.detail.H0.trades", "i"),
            sel(s, "a3b_detail.detail.H1.mean", "sf3"), sel(s, "a3b_detail.detail.H1.trades", "i"),
            n(["cells", s, 3], "i"), n(["cells", s, 0], "i"),
            n(["a8", s, "true"], "i"), n(["a8", s, "len"], "i")))
    A("")
    A("«A3 по клеткам» — число клеток крипты, прошедших A3 (c_обуч на 2472…2478), из всех клеток; метка «пройдено» ставится кодом (`conv_window.py make-spec`) только если A3 проходят ВСЕ клетки; без Gate A3 не проходит ни одна клетка (по построению: c_обуч выбрана на 6 сутках Π_train, сделок на 2472…2478 почти нет). "
      "С Gate A3 проходят %s (V1) и %s (V2) клеток — метка «не пройдено (N из M)». Допущено клеток по A1…A7 целиком: %s." % (
          n(["cells", "g1", 3], "i"), n(["cells", "g2", 3], "i"), n(["cells", "g1", 99], "i")))
    A("")
    A("**Итог R3.1 по числам с Gate.** (а) выполнено у V1 и V2; (б) выполнено; (в) **не выполнено у обоих вариантов**: у V1 c_полн = %s, c_обуч = %s (T отличается на два шага); у V2 c_полн = %s, c_обуч = %s (T на два шага). "
      "Класс «крипта с Gate» условию R3.1 не удовлетворяет. **По R3.7 п. 4 набор для окна — «без Gate»** (числа таблицы R3.1/R3.2 прежние: V1 и V2 без Gate проходят (а)–(в)); с Gate — отдельные описательные строки вне `M_окно`." % (
          cmb("g1"), cmt("g1"), cmb("g2"), cmt("g2")))
    A("Описательно по (в): оба выбора с Gate положительны на обеих выборках. V1 с Gate: c_полн на Π_train — сделок %s, `y_d` %s [нижняя %s]; c_обуч на Π_full — сделок %s, `y_d` %s [нижняя %s]. V2 с Gate: c_полн на Π_train — `y_d` %s [нижняя %s] (интервал накрывает ноль); c_обуч на Π_full — `y_d` %s [нижняя %s]. "
      "Правило согласия по протоколу ступенчатое (шаг сетки T: 2 ч → 6 ч → 24 ч); оно не смягчалось." % (
          ex("g1", "c_full_on_train.trades", "i"), ex("g1", "c_full_on_train.mean_yd", "sf3"), ex("g1", "c_full_on_train.lo", "sf3"), ex("g1", "c_train_on_full.trades", "i"), ex("g1", "c_train_on_full.mean_yd", "sf3"), ex("g1", "c_train_on_full.lo", "sf3"),
          ex("g2", "c_full_on_train.mean_yd", "sf3"), ex("g2", "c_full_on_train.lo", "sf3"), ex("g2", "c_train_on_full.mean_yd", "sf3"), ex("g2", "c_train_on_full.lo", "sf3")))
    A("")
    A("**Допуск клеток A1–A7 (описательно, как в r2; на окне не применяется).** Клеток крипты %s (без Gate %s). Проходят A1…A7: с Gate V1 %s / %s / %s / %s / %s / %s / %s, без Gate V1 %s / %s / %s / %s / %s / %s / %s; допущено 0 в обоих. "
      "Лучшие ячейки с Gate — в `tables_gate.md` (T7); новые ячейки Gate (`*~gate`) в `cells.csv`." % (
          n(["cells", "g1", 0], "i"), n(["cells", "n1", 0], "i"),
          *[n(["cells", "g1", i], "i") for i in range(1, 8)], *[n(["cells", "n1", i], "i") for i in range(1, 8)]))
    A("")
    A("### 1.4 Что изменил Gate (крипта, Π_full)")
    A("")
    A("| показатель | без Gate → с Gate, V1 (Q5) | без Gate → с Gate, V2 (без Q5) |")
    A("|---|---|---|")
    def d2(label, key, code, delta_code=None):
        pass
    def cmp_row(label, f1, f2):
        A("| %s | %s | %s |" % (label, f1, f2))
    cmp_row("пар в основном ряду", "%s → %s" % (cr("n1", "pairs", "i"), cr("g1", "pairs", "i")), "%s → %s" % (cr("n2", "pairs", "i"), cr("g2", "pairs", "i")))
    cmp_row("активов в основном ряду", "%s → %s" % (cr("n1", "assets", "i"), cr("g1", "assets", "i")), "%s → %s" % (cr("n2", "assets", "i"), cr("g2", "assets", "i")))
    cmp_row("c_полн тот же, что без Gate?", "%s (%s)" % (n(["eqc", "n1", "g1"], "yn"), cmb("g1")), "%s (%s)" % (n(["eqc", "n2", "g2"], "yn"), cmb("g2")))
    cmp_row("c_обуч", "%s → %s" % (cmt("n1"), cmt("g1")), "%s → %s" % (cmt("n2"), cmt("g2")))
    cmp_row("сделок на c_полн", "%s → %s" % (C("n1", "trades", "i"), C("g1", "trades", "i")), "%s → %s" % (C("n2", "trades", "i"), C("g2", "trades", "i")))
    cmp_row("активов со сделками", "%s → %s" % (C("n1", "assets", "i"), C("g1", "assets", "i")), "%s → %s" % (C("n2", "assets", "i"), C("g2", "assets", "i")))
    cmp_row("чистая на сделку, bps", "%s → %s" % (C("n1", "mean_trade", "sf1"), C("g1", "mean_trade", "sf1")), "%s → %s" % (C("n2", "mean_trade", "sf1"), C("g2", "mean_trade", "sf1")))
    cmp_row("`y_d`", "%s → %s" % (C("n1", "mean", "sf3"), C("g1", "mean", "sf3")), "%s → %s" % (C("n2", "mean", "sf3"), C("g2", "mean", "sf3")))
    cmp_row("нижняя граница `y_d` (c_полн)", "%s → %s" % (C("n1", "lo", "sf3"), C("g1", "lo", "sf3")), "%s → %s" % (C("n2", "lo", "sf3"), C("g2", "lo", "sf3")))
    cmp_row("верхняя граница `y_d`", "%s → %s" % (C("n1", "hi", "sf3"), C("g1", "hi", "sf3")), "%s → %s" % (C("n2", "hi", "sf3"), C("g2", "hi", "sf3")))
    cmp_row("из сделок c_полн — пары с ногой Gate: сделок; чистая; `y_d` [нижняя]", "%s; %s; %s [%s]" % (cr("g1", "cfull_gate_pairs.trades", "i"), cr("g1", "cfull_gate_pairs.net", "sf1"), cr("g1", "cfull_gate_pairs.mean_yd", "sf3"), cr("g1", "cfull_gate_pairs.lo", "sf3")),
            "%s; %s; %s [%s]" % (cr("g2", "cfull_gate_pairs.trades", "i"), cr("g2", "cfull_gate_pairs.net", "sf1"), cr("g2", "cfull_gate_pairs.mean_yd", "sf3"), cr("g2", "cfull_gate_pairs.lo", "sf3")))
    cmp_row("из сделок c_полн — пары 8 бирж в наборе с Gate: сделок; чистая; `y_d` [нижняя] (без Gate: сделок; `y_d`)", "%s; %s; %s [%s] (%s; %s)" % (cr("g1", "cfull_nongate_pairs.trades", "i"), cr("g1", "cfull_nongate_pairs.net", "sf1"), cr("g1", "cfull_nongate_pairs.mean_yd", "sf3"), cr("g1", "cfull_nongate_pairs.lo", "sf3"), cr("n1", "cfull_nongate_pairs.trades", "i"), cr("n1", "cfull_nongate_pairs.mean_yd", "sf3")),
            "%s; %s; %s [%s] (%s; %s)" % (cr("g2", "cfull_nongate_pairs.trades", "i"), cr("g2", "cfull_nongate_pairs.net", "sf1"), cr("g2", "cfull_nongate_pairs.mean_yd", "sf3"), cr("g2", "cfull_nongate_pairs.lo", "sf3"), cr("n2", "cfull_nongate_pairs.trades", "i"), cr("n2", "cfull_nongate_pairs.mean_yd", "sf3")))
    cmp_row("комбинаций 180 с `y_d` > 0; допустимых с нижней границей > 0", "%s → %s; %s → %s" % (n(["cnt", "n1", "pos"], "i"), n(["cnt", "g1", "pos"], "i"), n(["cnt", "n1", "pos_lo_ok"], "i"), n(["cnt", "g1", "pos_lo_ok"], "i")),
            "%s → %s; %s → %s" % (n(["cnt", "n2", "pos"], "i"), n(["cnt", "g2", "pos"], "i"), n(["cnt", "n2", "pos_lo_ok"], "i"), n(["cnt", "g2", "pos_lo_ok"], "i")))
    cmp_row("(в) согласие", "%s → %s" % (sel("n1", "agree", "yn"), sel("g1", "agree", "yn")), "%s → %s" % (sel("n2", "agree", "yn"), sel("g2", "agree", "yn")))
    cmp_row("A3b: H0→H1 (`y_d`, сделок); H1→H0", "%s (%s); %s (%s) → %s (%s); %s (%s)" % (sel("n1", "a3b_detail.detail.H0.mean", "sf3"), sel("n1", "a3b_detail.detail.H0.trades", "i"), sel("n1", "a3b_detail.detail.H1.mean", "sf3"), sel("n1", "a3b_detail.detail.H1.trades", "i"),
                                                                                       sel("g1", "a3b_detail.detail.H0.mean", "sf3"), sel("g1", "a3b_detail.detail.H0.trades", "i"), sel("g1", "a3b_detail.detail.H1.mean", "sf3"), sel("g1", "a3b_detail.detail.H1.trades", "i")),
            "%s (%s); %s (%s) → %s (%s); %s (%s)" % (sel("n2", "a3b_detail.detail.H0.mean", "sf3"), sel("n2", "a3b_detail.detail.H0.trades", "i"), sel("n2", "a3b_detail.detail.H1.mean", "sf3"), sel("n2", "a3b_detail.detail.H1.trades", "i"),
                                                                                       sel("g2", "a3b_detail.detail.H0.mean", "sf3"), sel("g2", "a3b_detail.detail.H0.trades", "i"), sel("g2", "a3b_detail.detail.H1.mean", "sf3"), sel("g2", "a3b_detail.detail.H1.trades", "i")))
    cmp_row("A3b пройдено: H0→H1; H1→H0", "%s; %s → %s; %s" % (sel("n1", "a3b.0", "yn"), sel("n1", "a3b.1", "yn"), sel("g1", "a3b.0", "yn"), sel("g1", "a3b.1", "yn")),
            "%s; %s → %s; %s" % (sel("n2", "a3b.0", "yn"), sel("n2", "a3b.1", "yn"), sel("g2", "a3b.0", "yn"), sel("g2", "a3b.1", "yn")))
    A("")
    A("Читать так: Gate добавил %s пар (на %s активах, из них %s новых базовых активов), c_полн обоих вариантов не изменился, число сделок на нём выросло с %s до %s (V1): сделок пар Gate %s и пар 8 бирж %s (против %s без Gate: часть событий общих ног "
      "перераспределилась при дедупликации). Пары Gate дают чистую %s bps на сделку против %s у пар 8 бирж, `y_d` пар Gate %s [нижняя %s] — не отличим от нуля. Нижняя граница класса выросла с %s до %s за счёт сужения интервала "
      "(верхняя %s → %s, среднее %s → %s), а не за счёт среднего. Число допустимых комбинаций с нижней границей > 0 выросло с %s до %s из %s. Условие (в) перестало выполняться: c_обуч (6 суток Π_train) выбирает другой T." % (
          cr("g1", "pairs_gate", "i"), cr("g1", "assets_gate", "i"), n(["x", "sub", ["j", path_of("g1"), "classes.crypto.assets"], ["j", path_of("n1"), "classes.crypto.assets"]], "i"),
          C("n1", "trades", "i"), C("g1", "trades", "i"), cr("g1", "cfull_gate_pairs.trades", "i"), cr("g1", "cfull_nongate_pairs.trades", "i"), cr("n1", "cfull_nongate_pairs.trades", "i"),
          cr("g1", "cfull_gate_pairs.net", "sf1"), cr("g1", "cfull_nongate_pairs.net", "sf1"), cr("g1", "cfull_gate_pairs.mean_yd", "sf3"), cr("g1", "cfull_gate_pairs.lo", "sf3"),
          C("n1", "lo", "sf3"), C("g1", "lo", "sf3"), C("n1", "hi", "sf3"), C("g1", "hi", "sf3"), C("n1", "mean", "sf3"), C("g1", "mean", "sf3"),
          n(["cnt", "n1", "pos_lo_ok"], "i"), n(["cnt", "g1", "pos_lo_ok"], "i"), n(["cnt", "g1", "ok"], "i")))
    A("")
    A("### 1.5 Подсказки видео со связками Gate (формирование, описательно, вне семьи)")
    A("В срезе `hints_gate_extra/` — все связки подсказок с ногой Gate при c1d2 = 1 (из `hints_membership_gate.csv`): связок Gate всего %s, из них в универсуме C2 %s; крипты (BRETT, LAB, TA, LPT) %s, из них в основном ряду %s; HK-акции (H1) %s (все в Gate-TradFi вне основного ряда, считались только по правилу W2). "
      "Четыре подсказки 8 бирж и H4c (TA Binance × Gate) входят в штатный срез конвейера (`bundle/hints_*`)." % (
          n(["hx", "gate_all"], "i"), n(["hx", "gate_in_universe"], "i"), n(["hx", "gate_crypto"], "i"), n(["hx", "gate_crypto_main"], "i"), n(["hx", "gate_hk_tradfi"], "i")))
    A("**C2 на c_полн V1:** у связок Gate-крипты в основном ряду сделок на c_полн V1 — %s; по всей сетке из 180 комбинаций сделки есть у %s из %s связок (сумма по комбинациям %s). "
      "TA Binance × Gate (H4c): максимум |отклонения| %s bps при доле общих валидных минут %s; TA BingX × Gate — %s bps (доля валидных минут %s)." % (
          n(["hx", "gate_crypto_main_sel_trades"], "i"), n(["hx", "gate_crypto_main_grid_pos"], "i"), n(["hx", "gate_crypto_main"], "i"), n(["hx", "gate_crypto_main_grid_trades"], "i"),
          n(["hxn", "H4c TA Binance x Gate", "dev_abs_max_bps"], "f0"), n(["hxn", "H4c TA Binance x Gate", "valid_share"], "f2"),
          n(["hxn", "H4 TA bingx_TA-USDT x gate_TA_USDT", "dev_abs_max_bps"], "f0"), n(["hxn", "H4 TA bingx_TA-USDT x gate_TA_USDT", "valid_share"], "f2")))
    A("**Правило «как в видео» (W2), θ = 20, rel, +1 мин, тейкер:** по %s связкам Gate-крипты сделок %s, взвешенная чистая %s bps на сделку (от %s до %s); при θ ≤ 80 положительная чистая — в %s строках из %s, максимум сделок в такой строке — %s. "
      "HK-связки Gate: положительная чистая при θ ≤ 80 в %s строках из %s, из них с ≥ 20 сделками — %s: XIAOMI Gate × Bitget (`abs`, θ 40) — %s сделок, %s bps; XIAOMI OKX × Gate (`rel`, θ 40) — %s сделок, %s bps (подробности `hints_gate_extra/hints_w2_formation.csv`). "
      "Предсказание §10.7 r3 (крипто-связки: знак чистой отрицательный при θ ≤ 80) на связках Gate выполняется, кроме двух строк с одной сделкой." % (
          n(["w2", "crypto_rel20_pairs"], "i"), n(["w2", "crypto_rel20_trades"], "i"), n(["w2", "crypto_rel20_net"], "sf1"), n(["w2", "crypto_rel20_net_min"], "sf1"), n(["w2", "crypto_rel20_net_max"], "sf1"),
          n(["w2", "crypto_le80_pos"], "i"), n(["w2", "crypto_le80_rows"], "i"), n(["w2", "crypto_le80_pos_trades_max"], "i"),
          n(["w2", "hk_le80_pos"], "i"), n(["w2", "hk_le80_rows"], "i"), n(["w2", "hk_le80_pos_ge20"], "i"),
          n(["w2row", "H1 XIAOMI gate_XIAOMI_USDT x bitget_XIAOMIUSDT", "abs", 40.0, "trades_p1"], "i"), n(["w2row", "H1 XIAOMI gate_XIAOMI_USDT x bitget_XIAOMIUSDT", "abs", 40.0, "net_taker_p1_mean"], "sf1"),
          n(["w2row", "H1 XIAOMI okx_XIAOMI-USDT-SWAP x gate_XIAOMI_USDT", "rel", 40.0, "trades_p1"], "i"), n(["w2row", "H1 XIAOMI okx_XIAOMI-USDT-SWAP x gate_XIAOMI_USDT", "rel", 40.0, "net_taker_p1_mean"], "sf1")))
    A("")
    A("### 1.6 Клетки, M, M_окно, кандидаты окна")
    A("**M (допущенные клетки r2):** 0 из %s клеток крипты и 0 из 28 клеток акций+ETF (с Gate и без); `M_эфф` по формированию = 0. Клетки на окне не используются (r3). **`M_окно` = 2** (V1 и V2; r3 R3.2); Холм исполняется раннером на окне. "
      "Ожидание числа сделок на окне по темпу формирования (сделок на c_полн / 13 · 21): V1 без Gate ≈ %s, V2 без Gate ≈ %s; с Gate (описательно) V1 ≈ %s, V2 ≈ %s." % (
          n(["cells", "g1", 0], "i"), n(["x", "mul21div13", ["cf", "n1", "trades"], ["const", 1]], "i"), n(["x", "mul21div13", ["cf", "n2", "trades"], ["const", 1]], "i"),
          n(["x", "mul21div13", ["cf", "g1", "trades"], ["const", 1]], "i"), n(["x", "mul21div13", ["cf", "g2", "trades"], ["const", 1]], "i")))
    A("")
    A("| набор | класс | вариант | c_полн (θ; k; p; T; S0) | в `M_окно` | метки формирования (R3.3) |")
    A("|---|---|---|---|---|---|")
    A("| **без Gate (набор окна по R3.7 п. 4)** | крипта | V1 (Q5 включён) | %s | да | A3b не пройдено (H0→H1 %s; %s сд.); A3 по клеткам не пройдено (%s из %s); A8 пройдено (%s бирж) |" % (
        cmb("n1"), sel("n1", "a3b_detail.detail.H0.mean", "sf3"), sel("n1", "a3b_detail.detail.H0.trades", "i"), n(["cells", "n1", 3], "i"), n(["cells", "n1", 0], "i"), n(["a8", "n1", "true"], "i")))
    A("| **без Gate (набор окна)** | крипта | V2 (Q5 выключен) | %s | да | A3b пройдено (%s / %s); A3 по клеткам не пройдено (%s из %s); A8 пройдено (%s бирж) |" % (
        cmb("n2"), sel("n2", "a3b_detail.detail.H0.mean", "sf3"), sel("n2", "a3b_detail.detail.H1.mean", "sf3"), n(["cells", "n2", 3], "i"), n(["cells", "n2", 0], "i"), n(["a8", "n2", "true"], "i")))
    A("| с Gate (описательно, отдельные строки вне `M_окно`) | крипта | V1 | %s | нет | R3.1 (в) не выполнено |" % cmb("g1"))
    A("| с Gate (описательно) | крипта | V2 | %s | нет | R3.1 (в) не выполнено |" % cmb("g2"))
    A("| — | акции+ETF | — | %s | нет | граница `y_d` < 0 (%s), плато и согласие не выполнены |" % (J("n1", "classes.eq.sel.c_full", "combo"), J("n1", "classes.eq.at_cfull.lo", "sf3")))
    A("")
    A("Метка «кандидат» по текущим числам недостижима (A3 по клеткам не пройден ни у одного варианта; r3). Классы металлы+энергия, прочие TradFi, индексы, фиат, Gate-TradFi — вне окна. "
      "Файлы входов раннера для набора «без Gate» (sha256 по LF в addendum): `classpar_full_nogate.json`, `classpar_H0_nogate.json`, `classpar_H1_nogate.json`, `q5_full_nogate.json` (V1); `classpar_*_nogate_noq5.json` (V2); `jobs_w2.csv`. "
      "`sigma_form` (std суточного `y_d` по суткам 2472…2478, n = 7; `conv_window.py make-spec`): без Gate V1 %s, V2 %s; с Gate V1 %s, V2 %s (spec: `window_spec_nogate.json`, `window_spec_gate.json` в каталоге списков; пути Deck внутри них — справочные)." % (
        n(["j", L1 + "/window_spec_nogate.json", "variants.0.sigma_form"], "f4"), n(["j", L1 + "/window_spec_nogate.json", "variants.1.sigma_form"], "f4"),
        n(["j", L1 + "/window_spec_gate.json", "variants.0.sigma_form"], "f4"), n(["j", L1 + "/window_spec_gate.json", "variants.1.sigma_form"], "f4")))
    A("")
    A("## 2. Что запускалось (Deck, 4 потока, `nice -n 10`, ПК не нагружался)")
    A("K0 в новом каталоге `~/xdl9/c2g` (старый `~/xdl9/c2` не менялся) → боковые панели оборота (%s ног, в т. ч. Gate; G-C2-7: %s символов вообще и %s символов Gate, макс. относительное расхождение %s и %s) → ряды L = 1440 и 4320 (Π_full и Π_train: %s пар в %s ячейках) → "
      "K2 (L = 1440, обе) → меры класса (Π_train, Π_full, H0, H1) → сетки 180 × (Π_train, Π_full, H0, H1) → выбор → прогоны c_обуч, c_H0 на H1, c_H1 на H0, K8 → условия класса, допуск A1–A7 → описательные таблицы (универсум, подсказки, сдвиги K8, пол оборота) → вариант без Q5. "
      "Тот же конвейер с `--no-gate` (ряды общие: ячейки 8 бирж не зависят от Gate) — контроль. Время (по `logs/pipe.status`, мин): набор Gate %s (из них все ряды и K2 до `L_full` — %s), вариант без Q5 %s; контроль без Gate %s + без Q5 %s; всего %s. Скрипты: `scripts/pipeAll.sh` и др. " % (
          n(["turn"], "i"), n(["logre", "logs/turn_verify.log", r"проверено символов (\d+)", 1, 0], "i"), n(["logre", "logs/turn_verify_gate.log", r"проверено символов (\d+)", 1, 0], "i"),
          n(["logre", "logs/turn_verify.log", r"расхождения ([0-9.e+-]+)", 1, 0], "e2"), n(["logre", "logs/turn_verify_gate.log", r"расхождения ([0-9.e+-]+)", 1, 0], "e2"),
          n(["x", "add", ["j", path_of("g1"), "classes.crypto.pairs"], ["j", path_of("g1"), "classes.eq.pairs"]], "i"),
          n(["x", "add", ["j", path_of("g1"), "classes.crypto.cells"], ["j", path_of("g1"), "classes.eq.cells"]], "i"),
          n(["dur", "k0 rc", "DONE_G"], "f0"), n(["dur", "k0 rc", "L_full"], "f0"), n(["dur", "DONE_G", "DONE_Q out_noq5"], "f0"), n(["dur", "DONE_Q out_noq5", "DONE_N"], "f0"),
          n(["dur", "DONE_N", "DONE_Q out_nogate_noq5"], "f0"), n(["dur", "k0 rc", "ALLDONE"], "f0")))
    A("")
    A("## 3. Отступления и прочтения (назначено исполнителем по заданию CEO без Судьи, подтвердить)")
    A("- **Условия R3.1 для двух вариантов.** (а)–(в) проверены отдельно для V1 и V2 каждого набора; набор окна определяется по V1 и V2 вместе (с Gate оба не проходят (в)); при расхождении вариантов правила в r3 нет.")
    A("- **Контроль «без Gate».** Ряды (кэш `cache/`) общие с набором Gate: ячейки 8 бирж содержат только пары 8 бирж (G1 проверила это побайтно на двух ячейках; итоговые массивы сеток контроля равны прежним — см. 1.1); K2, меры, сетки, выбор, проверки, итог — отдельно (`out_nogate`, `out_nogate_noq5`).")
    A("- **K1b и G-C2-8** перенесены из прежнего `~/xdl9/c2/out` (sha256 `conv_k1b.py` и `conv_size.py` равны addendum 07.11; данные от Gate не зависят). K1b по парам Gate не считался.")
    A("- **Обёртки вне репозитория кода.** `conv_report.py` и `conv_addendum.py` зашивают путь K0 `~/xdl9/c2/calib_start.json`; запуск через `scripts/gx_tables.py` и `scripts/gx_addendum.py` с подменой пути в процессе (код `conv_*` не менялся). Срезы подсказок со всеми связками Gate — `scripts/gx_hints_extra.py` (расширяет список `HINTS` в процессе).")
    A("- **jobs_w2.** Список W2 для окна пересобран с H4c (TA Binance × Gate), 17 строк (`jobs_w2.csv`, `jobs_w2_nogate.csv`); прежний `P-38-calib-2026-11-07/jobs_w2.csv` (16 строк) заменяется. W2 читается всегда и в наборе «без Gate».")
    A("- **K9 не выполнено** (как в прежнем отчёте): лид-лаг и атрибуция выбросной ноги, `σ` и net по часам суток, частота стартов у границ сессий, чувствительность `L_s`, K5b, K8 (г) для Gate.")
    A("- **Раннер окна не принимает варианты Gate** (`conv_window.py`: Gate-варианты отклоняются, универсум раннера — 8 бирж). Набор «без Gate» исполним им без правок; строки «с Gate» на окне потребуют отдельной доработки раннера и нового токена. Токен CEO этим файлом не выдаётся.")
    A("")
    A("## 4. Где что лежит")
    A("Репозиторий: addendum `docs/research/P-38-params-calib-gate-2026-11-07.csv`; списки `docs/research/P-38-calib-gate-2026-11-07/` (`classpar_*`, `q5_full*.json`, `jobs_w2*.csv`, `grid_class_*.csv`, `cells.csv`, `pairs_q5_floor.csv` и др.); "
      "отчёт `docs/findings/xdl9-conv-formation-gate-2026-11-07.md`; `docs/findings/xdl9-conv-formation-gate-2026-11-07/` — `tables_gate.md`, `tables_nogate.md`, `bundle*/`, `data/` (выжимки, выбор, K2), `hints_gate_extra/`, `scripts/`, `logs/`, "
      "`build_report.py`, `check_numbers.py`, `report_numbers.json`, `SHA256.txt`. Deck: `~/xdl9/c2g/{out,out_noq5,out_nogate,out_nogate_noq5}` (полные выходы).")
    A("")
    A("## 5. Сверка «числа отчёта = числа csv»")
    A("%%CHECK%%")
    return "\n".join(L) + "\n"


def main():
    # вспомогательные файлы выжимок (счётчики), которые строятся из исходных текстов расчёта
    txt = open(os.path.join(ROOT, T, "data/nogate_vs_old.txt"), encoding="utf-8").read().split("\n")
    eq = sum(1 for ln in txt if ln.endswith(" eq"))
    tot = sum(1 for ln in txt if ln.endswith(" eq") or ln.endswith(" DIFF"))
    json.dump({"eq": eq, "total": tot, "diff": tot - eq}, open(os.path.join(ROOT, T, "data/nogate_vs_old_counts.json"), "w"), ensure_ascii=False)
    md = build()
    # номера строк и подстановка
    lines = md.split("\n")
    out = []
    for ln_no, ln in enumerate(lines, 1):
        def sub(m):
            i = int(m.group(1))
            LEDGER[i]["line"] = ln_no
            return LEDGER[i]["text"]
        out.append(re.sub("\x01(\\d+)\x02", sub, ln))
    chk = os.path.join(ROOT, T, "check_result.txt")
    res = open(chk, encoding="utf-8").read().strip() if os.path.isfile(chk) else "(результат сверки будет записан после запуска `check_numbers.py`)"
    final = "\n".join(out).replace("%%CHECK%%", res)
    with open(os.path.join(ROOT, OUT_MD), "w", encoding="utf-8", newline="\n") as f:
        f.write(final)
    with open(os.path.join(ROOT, OUT_LEDGER), "w", encoding="utf-8", newline="\n") as f:
        json.dump(LEDGER, f, ensure_ascii=False, indent=0)
    print("отчёт: %s, чисел из расчёта: %d" % (OUT_MD, len(LEDGER)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
