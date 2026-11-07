#!/usr/bin/env python3
"""TK-493, П-38 r3 (§17, params r3): раннер ТОРГОВОГО ОКНА C2 - единственный путь чтения суток 2479..2499 и проверка этого пути на формировании.

Один вызов `run --go-window` (токен CEO, r3 R3.8; без флага и токена - отказ, код 3) читает окно ОДИН раз и считает по замороженным файлам (параметры раннер не выбирает):
  (а) варианты класса из spec (V1 - Q5 включён, V2 - Q5 выключен; M_окно = число вариантов): сделки, ряд y_d (закрытые + MTM открытых на конце, основной; закрытые отдельно),
      оба 90 %-интервала (блочный t и студентизованный блочный бутстреп) и поправка Холма на M_окно по каждому интервалу отдельно (R3.2);
  (б) статус «подтверждено на окне» (R3.3: (1) n >= 30, активов >= 5, суток со сделками >= 7, n_эфф >= 3; (2) нижняя граница y_d > 0 по обоим интервалам после Холма;
      (3) чистая на сделку > 0) и итоговая метка варианта с метками формирования из spec; метки окна, вне статуса: К-а перевёрнутое направление, К-б перенос входа ±5/±15 мин,
      К-в перестановка знака p_win, К-г условные случайные входы (§10.2), A8 окна (без биржи X), A3b окна (c_H0 на парах H1 и c_H1 на парах H0; справочно c_полн на H0 и H1);
      справка: статус клетки П-37 (cell_status.classify) на классе; срезы по сделкам; обрезка ±1000 bps; недели;
  (в) срез W2 «как в видео» (jobs_w2.csv, §10.7): сетка θ целиком, описательно, одним проходом (старт «с нуля», R3.6);
  (г) сводка «испытаний на окне всего» из счётчиков раннера (R3.8 п. 4): клетки F8 (133), M_окно, строки W2, контроли, срезы, прогоны A3b и A8 - числом, раздельно с ожидаемым
      числом ложных срабатываний, и суммой.
Граница окна (R3.4): старт «с нуля» с суток 2479 при истории формирования (2465..2478) для базиса, σ, общего фактора и порогов; позиции и сценарии (взвод, пауза, счётчик стопов)
через границу НЕ переносятся; сигналы окна начинаются с суток 2479, пара взведена после m_r нормальных валидных наблюдений окна; в конце 2499 открытая позиция закрывается по
последней известной цене с комиссией закрытия (MTM, P-37 D10), в основной ряд последних суток; сигнал, заполнение которого не наступило до конца данных, сделкой не становится
(счётчик signal_noend); срез W2: сделка только по пересечению порога, оба наблюдения которого лежат в окне (conv_hints.pair_table, t_start).
(д) блок «с Gate» (GW 07.11; ОПИСАТЕЛЬНЫЕ строки вне M_окно и вне статусов; spec["gate"]): те же варианты V1/V2 в универсуме с Gate (World gate=True; манифест gate_ready
      проверяется ДО чтения окна, его sha256 и таблица пар Gate входят в токен) на замороженных числах набора с Gate; основной набор (без Gate) считается и записывается ПЕРВЫМ
      и от блока Gate не зависит (побайтно); печатаются сделки, активы, сутки, gross/net, y_d с обоими 90 %-интервалами БЕЗ Холма («описательно»), контроли К-а..К-г, A8 (9 бирж),
      A3b окна, пары с ногой Gate отдельно от пар 8 бирж; в «испытаниях на окне всего» строки Gate - отдельная группа «описательные, вне M».
Ресурсы (B1): префлайт ДО захвата токена (свободный диск >= MIN_FREE_GB, память, потоки; df/free/loadavg в window_preflight.log; отказ - код 75, токен не тронут); ряды-кэш и боковые
панели оборота удаляются после прогона (--keep-work оставляет); сбой до первого открытия файла окна возвращает токен (conv_guard.finish_window).
  python conv_window.py run --spec SPEC [--go-window | --test-boundary D] [--out DIR] [--threads N] [--test-world DIR] [--limit-pairs N] [--min-free-gb G] [--keep-work]
  python conv_window.py gate9w --run DIR --base V1=DIR --base V2=DIR      G-C2-9w: сверка до бита с conv_run grid на тех же сутках (блок Gate: --run <out>/gate - G-C2-9wg)
  python conv_window.py make-spec --c2 DIR --out FILE [--params F --addendum F --params-r3 F --jobs F --kref F --gate-c2 DIR --addendum-nogate F --jobs-gate F]   (формирование) spec из выходов калибровки
  python conv_window.py token-body --spec SPEC [--label "GO <дата>, CEO"]  печатает текст токена (ТОЛЬКО печать; записывает CEO); token-check - проверка без захвата
Режимы: --go-window (окно; d1 = 2499; токен), --test-boundary D (формирование, искусственная граница 2466 <= D <= 2478; «окно» = D..2478, история до D-1; сутки >= 2479 читать
не вправе; токен не нужен и не принимается), --test-world (синтетика; не внутри и не предок ~/xdl9).
"""
import argparse
import csv
import datetime
import hashlib
import json
import math
import multiprocessing as mp
import os
import shutil
import statistics
import sys
import time
import traceback
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_guard as G      # noqa: E402
import conv_lib as L        # noqa: E402
import conv_world as W      # noqa: E402
import conv_grid as GR      # noqa: E402
import conv_agg as AG       # noqa: E402
import conv_ctrl as CT      # noqa: E402
import conv_hints as HI     # noqa: E402
import conv_run as CR       # noqa: E402

P, CS = AG.P, AG.CS
FI = GR.FI
TF = {k: i for i, k in enumerate(L.TRADE_FIELDS)}
SHIFTS = (-15, -5, 5, 15)
F8_CELLS = 133                      # §9.4: 4 + 6 + 7 + 1 + 115
N_MIN_21, MIN_ASSETS, MIN_NEFF = 30, 5, 3       # r3 R3.3: n_min_окно (при 21 сутках), активов, n_эфф; k_days = ceil(дни / 3)
W2_LABEL = "ГИПОТЕЗА из материала владельца; результат — не оценка исполнимой торговли"
W2_CRYPTO = ("H2", "H3", "H4a", "H4b", "H6")      # крипто-связки предсказания r3 (BRETT, LAB, TA, LPT); H1* - HK-связки (знак не определён)
BOUNDARY_RULE = ("старт с нуля с суток границы, история формирования только для базиса/σ/c/порогов; позиции и сценарии через границу не переносятся; "
                 "конец окна - закрытие по последней известной цене и MTM (P-37 D10), в основной ряд последних суток; signal_noend - сигнал без заполнения до конца данных")
NOT_DONE = ["чувствительность §10.8 (кривая задержки, a по Роллу, мейкер, стресс «стоп x3», без стопа, r1-выход, фандинг без TradFi); считаются только r1-вариант входа и обрезка ±1000 bps",
            "срезы §10.5: терцили оборота и ёмкости, доля протухших минут, выбросная нога, HK-пары, «разрыв после формирования»; считаются направление, код выхода, биржа, час, минуты вокруг 00/08/16, недели",
            "таблица «разрывы» §10.6 (печатаются счётчики прогона; гипотетика запрещённых входов не считается)",
            "M_эфф по часовым net (считается по суточным y_d вариантов, как в r3 R3.2)", "размер интервалов при n = 21 из size_test.py (печатается отдельно, раннер не пересчитывает)",
            "Gate: основной набор считается без Gate (R3.7 п. 4: набор окна «без Gate»); строки «с Gate» - описательный блок spec[\"gate\"] вне M_окно (без Холма и статусов)"]
MIN_FREE_GB = 10.0                  # свободное место на диске выходов и C2_HOME до захвата токена (GW 07.11: назначено CEO без Судьи, подтвердить)
EXIT_INFRA = 75                     # код выхода «инфраструктурный отказ до чтения окна» (EX_TEMPFAIL): токен не тронут, можно повторить после устранения
MEM_BASE_MB = 1760                  # память: родительский процесс (как один рабочий) - замер GW 07.11: пик RSS 703 МиБ на процесс (формирование, 14 суток, 5 потоков + родитель, оба прогона 2466/2472)
MEM_PER_THREAD_MB = 1760            # x 35/14 суток окна = 1758 -> 1760 МиБ на рабочий процесс (оценка сверху: общие страницы fork в RSS посчитаны повторно); назначено CEO без Судьи, подтвердить
PRE_EXISTING_OK = ("token_body.txt.pre", "token_body.txt.pre.sha256")      # файлы, которые CEO/исполнитель кладёт в каталог выходов ДО запуска (не выходы счёта)


# ------------------------------------------------------------------ spec и входные файлы
def _abs(base, p):
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))


def _check_variants(variants, base, where):
    ids = set()
    for v in variants:
        if v["id"] in ids:
            raise ValueError("spec%s: повтор id %s" % (where, v["id"]))
        ids.add(v["id"])
        if len(v["combo"]) != 5 or v["cls"] not in W.MAIN_CLASSES:
            raise ValueError("spec%s: вариант %s: combo из 5 значений и класс из %s" % (where, v["id"], W.MAIN_CLASSES))
        if v.get("gate"):
            raise ValueError("spec%s: флаг gate у варианта не принимается: набор с Gate задаётся блоком spec[\"gate\"]" % where)
        for k in ("classpar", "q5"):
            if v.get(k):
                v[k] = _abs(base, v[k])
        for sub in (v.get("a3b") or {}).values():
            sub["classpar"] = _abs(base, sub["classpar"])
        if v.get("sigma_form") is None:
            raise ValueError("spec%s: вариант %s без sigma_form (измеряется на формировании: make-spec)" % (where, v["id"]))


def load_spec(path):
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    for k in ("params", "addendum", "addendum_nogate", "params_r3", "w2_jobs", "w2_k_ref", "protocol"):
        if spec.get(k):
            spec[k] = _abs(base, spec[k])
    if not spec.get("variants"):
        raise ValueError("spec: нет вариантов")
    _check_variants(spec["variants"], base, "")
    g = spec.get("gate")
    if g:
        for k in ("addendum", "manifest", "pairs", "w2_jobs"):
            if g.get(k):
                g[k] = _abs(base, g[k])
        if not g.get("variants"):
            raise ValueError("spec[gate]: нет вариантов")
        _check_variants(g["variants"], base, "[gate]")
        if [v["cls"] for v in g["variants"]] != [v["cls"] for v in spec["variants"]]:
            raise ValueError("spec[gate]: классы вариантов Gate должны совпадать с основным набором")
    return spec


def _home_rel(p):
    ap = os.path.abspath(p)
    h = os.path.expanduser("~")
    return "~/" + os.path.relpath(ap, h).replace(os.sep, "/") if G._under(ap, h) else ap


def collect_inputs(spec, spec_path):
    """Входные файлы раннера для манифеста токена: [(метка-путь, путь)]. classpar_*.json и q5_*.json рядом с указанными входят все; все P-38-params-calib-*.csv рядом с addendum;
    блок Gate: манифест gate_ready, таблица пар Gate и её .meta.json, jobs_w2 Gate, addendum Gate; протокол (если задан в spec) и c2_git_ok каталога кода (d3 ревизии)."""
    seen, out = set(), []

    def add(label, p):
        ap = os.path.abspath(p)
        if ap in seen:
            return
        seen.add(ap)
        out.append((label, ap))
    add(_home_rel(spec_path), spec_path)
    gspec = spec.get("gate") or {}
    dirs = set()
    for v in list(spec["variants"]) + list(gspec.get("variants") or []):
        files = [v.get("classpar"), v.get("q5")] + [s["classpar"] for s in (v.get("a3b") or {}).values()]
        for f in files:
            if f:
                add(_home_rel(f), f)
                dirs.add(os.path.dirname(os.path.abspath(f)))
    for d in sorted(dirs):
        for f in sorted(os.listdir(d)):
            if (f.startswith("classpar_") or f.startswith("q5_")) and f.endswith(".json"):
                add(_home_rel(os.path.join(d, f)), os.path.join(d, f))
    for src in (spec, gspec):
        if src.get("w2_jobs"):
            add(_home_rel(src["w2_jobs"]), src["w2_jobs"])
    if spec.get("w2_k_ref"):
        add(_home_rel(spec["w2_k_ref"]), spec["w2_k_ref"])
    for k in ("addendum", "addendum_nogate"):
        if spec.get(k):
            d = os.path.dirname(os.path.abspath(spec[k]))
            add(G.REL_DOCS + os.path.basename(spec[k]), spec[k])
            for f in sorted(os.listdir(d)):
                if f.startswith("P-38-params-calib-") and f.endswith(".csv"):
                    add(G.REL_DOCS + f, os.path.join(d, f))
    if gspec.get("addendum"):
        add(G.REL_DOCS + os.path.basename(gspec["addendum"]), gspec["addendum"])
    if gspec:
        root = spec.get("root") or G.HOME
        man = gspec.get("manifest") or os.path.join(root, "gate_ready")
        prs = gspec.get("pairs") or os.path.join(root, "asset_pairs_gate.csv")
        for p in (man, prs, os.path.splitext(prs)[0] + ".meta.json"):
            add(_home_rel(p), p)
    if spec.get("protocol"):
        add(G.REL_DOCS + os.path.basename(spec["protocol"]), spec["protocol"])
    gt = G.git_token_path()
    if os.path.isfile(gt):
        add(G.REL_CODE + os.path.basename(gt), gt)
    return out


def params_tuple(spec):
    return (spec.get("params"), spec.get("addendum"), spec.get("params_r3"))


# ------------------------------------------------------------------ потоки
def pool_iter2(fn, tasks, threads):
    """Результаты по порядку задач. fork без инициализатора: контекст (CR._CTX, G.STATE) наследуется от родителя; повторной проверки токена в потомках нет."""
    if threads <= 1:
        for t in tasks:
            yield fn(t)
        return
    ctx = mp.get_context("fork")
    with ctx.Pool(threads) as pool:
        for r in pool.imap(fn, tasks, chunksize=1):
            yield r


# ------------------------------------------------------------------ сетка (конвейер conv_run, один вариант)
def stage_series(world, pairs, cfgd, work, d0, d1, threads):
    CR._CTX.update(world=world, cfgd=cfgd, out=work, d0=d0, d1=d1)
    cells = {}
    for p in pairs:
        cells.setdefault(p["cell"], []).append(p)
    return list(pool_iter2(CR._series_task, sorted(cells.items()), threads))


def stage_grid(world, pairs, variant, cfgd, work, d0, d1, p0day, threads, want_trades=True):
    cls = variant["cls"]
    cpd = CR.class_params(variant.get("classpar"))
    q5 = json.load(open(variant["q5"], encoding="utf-8")) if variant.get("q5") else None
    combos = [L.Combo(*variant["combo"])]
    tasks = W.components([p for p in pairs if p["cls"] == cls])
    CR._CTX.update(world=world, cfgd=cfgd, out=work, cpd=cpd, combos=combos, d0=d0, d1=d1, p0day=p0day, q5=q5, want_trades=want_trades, want_turn=True)
    res = list(pool_iter2(CR._grid_task, tasks, threads))
    nC = len(combos)
    cn = [dict() for _ in range(nC)]
    for r in res:
        for ci in range(nC):
            for k, v in r["cnt"][ci].items():
                cn[ci][k] = cn[ci].get(k, 0) + v
    return dict(tab=np.concatenate([r["tab"] for r in res], axis=1), act=np.concatenate([r["act"] for r in res], axis=1),
                hist=sum(r["hist"] for r in res), minnet=np.minimum.reduce([r["minnet"] for r in res]),
                pids=[x for r in res for x in r["pids"]], keys=[x for r in res for x in r["keys"]], cells=[x for r in res for x in r["cells"]],
                exch=[x for r in res for x in r["exch"]], cn=cn, trades=[t for r in res for t in r["trades"]], combos=combos, components=len(tasks), cls=cls)


def write_grid(odir, g, cfgd, d0, d1, p0day, npairs, seconds):
    """Тот же состав и формат файлов, что у `conv_run grid` (сверка G-C2-9w побайтно/по содержимому)."""
    d = os.path.join(odir, g["cls"])
    os.makedirs(d, exist_ok=True)
    np.save(os.path.join(d, "tab.npy"), g["tab"])
    np.save(os.path.join(d, "act.npy"), g["act"])
    np.save(os.path.join(d, "hist.npy"), g["hist"])
    np.save(os.path.join(d, "minnet.npy"), g["minnet"])
    json.dump(g["pids"], open(os.path.join(d, "pids.json"), "w"))
    json.dump({"keys": g["keys"], "cells": g["cells"], "exch": g["exch"]}, open(os.path.join(d, "pairmeta.json"), "w"))
    json.dump(g["cn"], open(os.path.join(d, "counters.json"), "w"), sort_keys=True)
    with open(os.path.join(d, "trades.csv"), "w", encoding="utf-8", newline=chr(10)) as tf:
        tf.write("combo,pid,key," + ",".join(L.TRADE_FIELDS) + chr(10))
        for tr in g["trades"]:
            tf.write(",".join(("%.9g" % v) if isinstance(v, float) else str(v) for v in tr) + chr(10))
    json.dump([list(c) for c in g["combos"]], open(os.path.join(odir, "combos.json"), "w"))
    json.dump({"fields": GR.DF, "d0": d0, "d1": d1, "first_sig_day": p0day, "cfg": cfgd, "pairs": npairs, "components": g["components"], "seconds": round(seconds, 1)},
              open(os.path.join(odir, "meta.json"), "w"), sort_keys=True)


def mem_grid(g, d0, d1, p0day, label):
    """Сетка в памяти (без записи на диск) с тем же интерфейсом, что AG.Grid, для вспомогательных прогонов; все пары - одна «клетка» label."""
    return types.SimpleNamespace(tab=g["tab"], act=g["act"], pids=g["pids"], keys=g["keys"], cells=[label] * len(g["pids"]), exch=g["exch"], d0=d0, d1=d1, first_sig=p0day,
                                 nd=g["tab"].shape[2] - 1)


def half_of(key):
    return hashlib.sha256(key.encode("utf-8")).digest()[0] % 2


def make_world(root, test, turn_dir):
    """Универсум окна задаётся явно (r3 R3.7 п. 4-5): Gate НЕ входит, пока набор «с Gate» не зафиксирован коммитом addendum; режим auto по манифесту (conv_world.GATE_DEFAULT)
    для окна недопустим - иначе появление валидного gate_ready между токеном и запуском тихо изменило бы универсум."""
    try:
        return W.World(root, test=test, turn_dir=turn_dir, gate=False)
    except TypeError:                                   # conv_world без Gate-API (до слияния ветки G1): универсум 8 бирж
        return W.World(root, test=test, turn_dir=turn_dir)


# ------------------------------------------------------------------ статистика
def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(float(o)) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def jdump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=chr(10)) as f:
        json.dump(jsonable(obj), f, ensure_ascii=False, indent=1, sort_keys=True)


def stat_block(series, boot=True):
    """n, блок, n_эфф, df, среднее, оба 90 %-интервала, p (двусторонний, t), асимметрия."""
    if not series:
        return None
    iv = AG.interval(series)
    out = dict(n=iv["n"], block=iv["block"], n_eff=iv["n_eff"], df=iv["df"], mean=iv["mean"], lo_t=iv["lo"], hi_t=iv["hi"], p_t=iv.get("p"), lo_boot=None, hi_boot=None)
    if iv["lo"] is not None and boot:
        _, lb, hb = P.block_boot_t_interval(list(series), AG.ALPHA, CS.BOOT_B, CS.BOOT_SEED, iv["block"])
        out.update(lo_boot=lb, hi_boot=hb)
    out["skew"] = P.skewness(series)
    return out


def mean_of(ser):
    return (sum(ser) / len(ser)) if ser else None


def series_with_control(gr, ci, rows, control_net):
    """Тот же ряд, что AG.y_series, и ряд контроля «случайные входы» (CS.build_series(control_net=...))."""
    t = gr.tab[ci][rows]
    act = gr.act[ci][rows]
    num = t[:, :, FI["net"]]
    nd = gr.nd
    day_net = {gr.d0 + j: float(num[:, j].sum()) for j in range(nd)}
    day_act = {gr.d0 + j: int(act[:, j].sum()) for j in range(nd)}
    sig_days = [d for d in range(gr.d0, gr.d0 + nd) if d >= gr.first_sig]
    return CS.build_series(day_net, day_act, sig_days, mtm_net_sum=float(num[:, nd].sum()), n_open=int(t[:, nd, FI["n"]].sum()), control_net=control_net)


def series_from_trades(gr, trades, clip=None):
    """Ряд y_d из списка сделок (как AG.y_series: знаменатель - активные пары-сутки сетки); clip - обрезка net сделки до ±clip bps."""
    nd = gr.nd
    day_net = {}
    mtm = 0.0
    n_open = 0
    for row in trades:
        tr = row[3:]
        net = tr[TF["net"]]
        if clip is not None:
            net = max(-clip, min(clip, net))
        if tr[TF["mtm"]]:
            mtm += net
            n_open += 1
        else:
            d = gr.d0 + int(tr[TF["f_out"]]) // 1440
            day_net[d] = day_net.get(d, 0.0) + net
    day_act = {gr.d0 + j: int(gr.act[0][:, j].sum()) for j in range(nd)}
    sig_days = [d for d in range(gr.d0, gr.d0 + nd) if d >= gr.first_sig]
    return CS.build_series(day_net, day_act, sig_days, mtm_net_sum=mtm, n_open=n_open)


def lo_at(series, block, alpha, kind):
    """Нижняя граница интервала kind ('t' - блочный t, 'boot' - студентизованный блочный бутстреп B = 3000, seed 37) при двустороннем уровне alpha."""
    if not series:
        return None
    if kind == "t":
        return P.block_t_interval(series, alpha, block)[1]
    return P.block_boot_t_interval(list(series), alpha, CS.BOOT_B, CS.BOOT_SEED, block)[1]


def holm_block(results):
    """r3 R3.2: Холм на M_окно отдельно по каждому интервалу (блочный t и бутстреп), вариант проходит, если прошёл оба.
    M = 1: нижняя граница при alpha = 0,10 > 0. M = 2: проходит по интервалу, если (i) нижняя граница при alpha = 0,05 > 0, либо (ii) нижняя граница при alpha = 0,10 > 0 И другой
    вариант выполняет (i). M > 2: пошаговый Холм по рангу t-статистики (по p блочного t, большая t - первый ранг), alpha_j = 0,10 / (M - j + 1); вариант проходит, если пройдены все
    более высокие ранги и его граница при alpha_j > 0."""
    M = len(results)
    a_hi, a_lo = AG.ALPHA, AG.ALPHA / 2.0
    p1 = []
    for r in results:
        m = r["main"]
        p1.append(1.0 if (not m or m.get("p_t") is None or m["mean"] is None) else (m["p_t"] / 2.0 if m["mean"] > 0 else 1.0 - m["p_t"] / 2.0))
    order = sorted(range(M), key=lambda i: (p1[i], i))
    rank = {i: j + 1 for j, i in enumerate(order)}
    out = [dict(M=M, rank=rank[i], p_one_sided=p1[i], t={}, boot={}) for i in range(M)]
    for kind in ("t", "boot"):
        blk = [r["main"]["block"] if r["main"] else None for r in results]
        ser = [r["series"]["primary"] for r in results]
        if M == 1:
            lo10 = lo_at(ser[0], blk[0], a_hi, kind)
            out[0][kind] = dict(lo_a10=lo10, passed=bool(lo10 is not None and lo10 > 0), rule="M=1: alpha 0,10")
        elif M == 2:
            lo05 = [lo_at(ser[i], blk[i], a_lo, kind) for i in range(2)]
            lo10 = [lo_at(ser[i], blk[i], a_hi, kind) for i in range(2)]
            i05 = [x is not None and x > 0 for x in lo05]
            for i in range(2):
                ii = bool(lo10[i] is not None and lo10[i] > 0 and i05[1 - i])
                out[i][kind] = dict(lo_a05=lo05[i], lo_a10=lo10[i], passed=bool(i05[i] or ii), by_i=i05[i], by_ii=ii, rule="M=2: (i) alpha 0,05 либо (ii) alpha 0,10 и другой по (i)")
        else:
            prev = True
            for i in order:
                aj = a_hi / (M - rank[i] + 1)
                lo = lo_at(ser[i], blk[i], aj, kind)
                ok = bool(lo is not None and lo > 0)
                out[i][kind] = dict(alpha_j=aj, lo_alpha_j=lo, passed=bool(ok and prev), rule="M>2: пошаговый Холм, alpha_j = 0,10 / (M - j + 1)")
                prev = prev and ok
    for i, r in enumerate(results):
        out[i]["pass_both"] = bool(out[i]["t"]["passed"] and out[i]["boot"]["passed"])
        r["holm"] = out[i]


# ------------------------------------------------------------------ контроли (потоки)
_WC = {}


def _ctl_task(item):
    pid, trs, seed_idx = item
    cx = _WC
    p = cx["pairs"][pid]
    cfg = L.Cfg(**cx["cfgd"])
    cell_c = W.load_cell_c(cx["out"], p["cell"], cfg.L)[0]
    ps, _ = W.load_series(cx["world"], p, cx["d0"], cx["d1"], cfg, cx["out"], cell_c, want_turn=True, want_fund=True)
    cp, combo = cx["cp"], cx["combo"]
    period0 = (cx["p0day"] - cx["d0"]) * 1440
    ft = L.Feats(ps, cp, cfg, period0)
    res = {"shift": {dl: CT.shifted_entries(ps, cp, cfg, combo, ft, trs, dl, period0) for dl in SHIFTS}}
    closed = [t for t in trs if not t[TF["mtm"]]]
    holds = [int(t[TF["hold"]]) for t in closed]
    rng = np.random.default_rng([37, seed_idx])
    res["rand"] = CT.random_entries(ps, cp, cfg, combo, ft, len(closed), holds, period0, rng)
    return pid, res


def stage_controls(world, pairs_by_pid, g, variant, cfgd, work, d0, d1, p0day, threads):
    trs = {}
    for row in g["trades"]:
        trs.setdefault(row[1], []).append(tuple(row[3:]))
    idx = {pid: i for i, pid in enumerate(sorted(g["pids"]))}
    cp = CR.class_params(variant.get("classpar"))[variant["cls"]]
    _WC.update(world=world, pairs=pairs_by_pid, cfgd=cfgd, out=work, d0=d0, d1=d1, p0day=p0day, cp=cp, combo=L.Combo(*variant["combo"]))
    items = [(pid, t, idx[pid]) for pid, t in sorted(trs.items())]
    parts = list(pool_iter2(_ctl_task, items, threads))
    shift = {dl: dict(days={}, done=0, skipped=0) for dl in SHIFTS}
    rand = dict(days={}, done=0)
    for pid, res in parts:
        for dl in SHIFTS:
            days, done, skipped = res["shift"][dl]
            for dd, v in days.items():
                shift[dl]["days"][dd] = shift[dl]["days"].get(dd, 0.0) + v
            shift[dl]["done"] += done
            shift[dl]["skipped"] += skipped
        for dd, v in res["rand"][0].items():
            rand["days"][dd] = rand["days"].get(dd, 0.0) + v
        rand["done"] += res["rand"][1]
    return shift, rand, len(items)


# ------------------------------------------------------------------ анализ варианта
def read_addendum(path):
    out = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                v = r.get("value")
                try:
                    out[r["key"]] = json.loads(v)
                except (TypeError, ValueError):
                    out[r["key"]] = v
    return out


def thresholds_window(days):
    """r3 R3.3: n_min_окно = 30 и k_days = 7 при 21 сутках; для другой длины окна (проверка на формировании) - по формуле r2 §7.4: ceil(30 * дни / 21), ceil(дни / 3)."""
    return int(math.ceil(N_MIN_21 * days / 21.0)), int(math.ceil(days / 3.0))


def sub_run_stats(gx):
    s = AG.y_series(gx, 0, np.arange(len(gx.pids)))["primary"]
    n = int(gx.tab[0][:, :, FI["n"]].sum())
    return dict(stat=stat_block(s), mean=mean_of(s), trades=n, pairs=len(gx.pids), assets=len(set(gx.keys)))


def analyze_variant(gr, variant, g, ctl, addendum, r1_mean, a3b_cross):
    """Класс-вариант: все числа окна по одной комбинации (параметры не выбираются). gr - AG.Grid, у которого cells переопределены одной меткой класса."""
    shift, rand, n_ctl_pairs = ctl
    rows = np.arange(len(gr.pids))
    cls = variant["cls"]
    R = dict(id=variant["id"], cls=cls, combo=variant["combo"], q5=bool(variant.get("q5")), sigma_form=variant["sigma_form"], sigma_form_src=variant.get("sigma_form_src"),
             pairs=int(len(rows)), assets=len(set(gr.keys)), cells=len(set(gr.cells_real)), first_sig=gr.first_sig, d1=gr.d1)
    ser = AG.y_series(gr, 0, rows)
    R["series"] = dict(days=ser["days"], closed=ser["closed"], primary=ser["primary"], primary_days=ser["primary_days"], mtm_available=ser["mtm_available"],
                       mtm_value=ser["mtm_value"], mtm_day_added=ser["mtm_day_added"])
    prim = ser["primary"]
    R["main"] = stat_block(prim)
    R["closed"] = stat_block(ser["closed"])
    tn = gr.tab[0]
    n_all = int(tn[:, :, FI["n"]].sum())
    n_mtm = int(tn[:, gr.nd, FI["n"]].sum())
    n_cl = n_all - n_mtm
    net_cl = float(tn[:, :gr.nd, FI["net"]].sum())
    a_cl = np.asarray(tn[:, :gr.nd, FI["n"]]).sum(axis=1)
    R["trades"] = dict(all=n_all, closed=n_cl, mtm_open=n_mtm, assets=AG.n_assets_with_trades(gr, 0, rows), assets_closed=len({gr.keys[r] for r in rows if a_cl[r] > 0}),
                       days_with_trades=AG.n_days_with_trades(gr, 0, rows), net_closed_trade=(net_cl / n_cl) if n_cl else None,
                       gross_trade=float(tn[:, :, FI["gross"]].sum()) / n_all if n_all else None, fund_trade=float(tn[:, :, FI["fund"]].sum()) / n_all if n_all else None,
                       net_trade=float(tn[:, :, FI["net"]].sum()) / n_all if n_all else None, share_le2=float(tn[:, :, FI["n_le2"]].sum()) / n_all if n_all else None,
                       share_deferred=float(tn[:, :, FI["n_def"]].sum()) / n_all if n_all else None, share_ffill=float(tn[:, :, FI["n_ff"]].sum()) / n_all if n_all else None,
                       net_trade_maker=(float(tn[:, :, FI["net"]].sum()) / n_all + 12.6 - 5.04) if n_all else None)
    R["counters"] = g["cn"][0]
    tm = AG.tail_metrics(gr, 0, rows)
    kb = (addendum.get("katok_quartiles") or {}).get(cls)
    R["tail"] = tm
    R["katok_bounds"] = kb
    R["katok"] = bool(kb and AG.is_katok(tm, kb))
    # без лучшего базового актива и без трёх лучших (справка; условие (в) статуса П-37)
    sums = AG.asset_sums(gr, 0, rows)
    order = sorted(sums, key=lambda k: (-sums[k], k))
    wo = {}
    for kcut in (1, 3):
        s = AG.y_series(gr, 0, gr.rows(assets_excl=order[:kcut]))["primary"] if len(order) > kcut else None
        wo[kcut] = mean_of(s)
    R["without_best"] = dict(best_assets=order[:3], mean_wo1=wo[1], mean_wo3=wo[3])
    mwb = None if (wo[1] is None or wo[3] is None) else min(wo[1], wo[3])
    # контроли
    rev = AG.y_series(gr, 0, rows, rev=True)["primary"]
    pw = AG.perm_pwin(gr, rows, n_perm=1000, seed=37)
    first = gr.first_sig - gr.d0
    den = np.asarray(gr.act[0]).sum(axis=0).astype(float)
    days_ok = [j for j in range(first, gr.nd) if den[j] > 0]
    real_closed = np.zeros(gr.nd)
    for row in g["trades"]:
        tr = row[3:]
        if not tr[TF["mtm"]]:
            real_closed[int(tr[TF["f_out"]]) // 1440] += tr[TF["net"]]

    def mean_y(num):
        return float(np.mean([num[j] / den[j] for j in days_ok])) if days_ok else None
    sh = {}
    for dl in SHIFTS:
        num = np.zeros(gr.nd)
        for dd, v in shift[dl]["days"].items():
            num[dd - gr.d0] += v
        sh[str(dl)] = dict(trades_done=shift[dl]["done"], skipped=shift[dl]["skipped"], mean_yd_closed=mean_y(num))
    rev_stat = stat_block(rev)
    sc = series_with_control(gr, 0, rows, rand["days"])
    ctrl = sc["control"]
    diff = [a - b for a, b in zip(prim, ctrl)] if (prim and ctrl is not None and len(ctrl) == len(prim)) else None
    diff_stat = stat_block(diff)
    R["controls"] = dict(reversed=rev_stat, reversed_mean=mean_of(rev), p_win=pw, real_closed_mean_yd=mean_y(real_closed), shifts=sh,
                         random_entries=dict(trades_done=rand["done"], real_closed_trades=n_cl, pairs_with_trades=n_ctl_pairs, seed=37, mean_control=mean_of(ctrl), diff=diff_stat))
    # метки окна (r3 R3.3; в статус не входят)
    labels = {}
    labels["K_a"] = "К-а: нет" if (mean_of(rev) is not None and mean_of(rev) >= 0) else "К-а: ок"
    labels["K_g"] = ("К-г: нет" if (diff_stat is None or diff_stat["lo_t"] is None or diff_stat["lo_t"] <= 0 or diff_stat["lo_boot"] is None or diff_stat["lo_boot"] <= 0) else "К-г: ок")
    exchanges = sorted({e for ex in gr.exch for e in ex})
    a8 = {}
    for e in exchanges:
        rr = gr.rows(without_exch=e)
        s = AG.y_series(gr, 0, rr)["primary"] if len(rr) else None
        a8[e] = dict(pairs=int(len(rr)), mean=mean_of(s), stat=stat_block(s, boot=False), ok=bool(s and mean_of(s) > 0))
    R["a8"] = a8
    bad8 = [e for e, x in a8.items() if not x["ok"]]
    labels["A8"] = "A8 окна: нет (%s)" % ",".join(bad8) if bad8 else "A8 окна: ок (все %d бирж)" % len(a8)
    h3 = {}
    for h in (0, 1):
        rr = gr.rows(asset_half=h)
        s = AG.y_series(gr, 0, rr)["primary"] if len(rr) else None
        h3["H%d" % h] = dict(pairs=int(len(rr)), assets=len({gr.keys[r] for r in rr}), trades=AG.n_trades(gr, 0, rr) if len(rr) else 0, mean=mean_of(s), stat=stat_block(s, boot=False),
                             ok=bool(s and mean_of(s) > 0))
    R["a3b_window_reference"] = h3
    if a3b_cross is not None:
        R["a3b_window"] = a3b_cross
        ms = [a3b_cross[k]["mean"] for k in ("h1on0", "h0on1")]
        labels["A3b"] = "A3b окна: нет" if any(m is None or m <= 0 for m in ms) else "A3b окна: ок"
    else:
        R["a3b_window"] = None
        labels["A3b"] = "A3b окна: не посчитано (в spec нет чисел половин)"
    R["window_labels"] = labels
    # справка: статус клетки П-37 на классе (r3: в статус окна не входит)
    mean_ns = mean_of(AG.y_series(gr, 0, rows, field="net_ns")["primary"])
    R["mean_no_stale"] = mean_ns
    R["r1_mean"] = r1_mean
    R["p37_status"] = None
    if prim and diff is not None:
        st = AG.cell_status(gr, 0, gr.cells[0], ctrl, variant["sigma_form"], mwb, exchanges, r1_mean=r1_mean, mean_no_stale=mean_ns, katok_ok=not R["katok"])
        R["p37_status"] = {k: st[k] for k in ("status", "label", "unmet", "r2_extra_unmet", "mean", "lo", "hi", "lo_boot", "hi_boot", "cond_a", "cond_b", "cond_c", "cond_d", "cond_e", "cond_g") if k in st}
    # обрезка net сделки до ±1000 bps; число сделок с |net| > 1000
    clip = series_from_trades(gr, g["trades"], clip=1000.0)["primary"]
    R["clip1000"] = dict(mean=mean_of(clip), stat=stat_block(clip, boot=False), n_abs_gt_1000=sum(1 for row in g["trades"] if abs(row[3:][TF["net"]]) > 1000.0))
    chk = series_from_trades(gr, g["trades"])["primary"]
    R["series_from_trades_equals_grid"] = bool(chk is not None and prim is not None and len(chk) == len(prim) and all(abs(a - b) < 1e-9 for a, b in zip(chk, prim)))
    weeks = {}
    for d, v in zip(ser["primary_days"] or [], prim or []):
        weeks.setdefault((d - gr.first_sig) // 7, []).append(v)
    R["weeks"] = {str(k): dict(days=len(v), mean=mean_of(v)) for k, v in sorted(weeks.items())}
    return R


RANK = {"не подтверждено": 0, "подтверждено на окне при слабом формировании: повторить на новых сутках": 1, "кандидат": 2}


def r3_status(R, window_days, formation_labels):
    """r3 R3.3: «подтверждено на окне» = (1) n закрытых >= n_min, активов >= 5, суток со сделками >= k_days, n_эфф >= 3; (2) Холм по обоим интервалам; (3) чистая на сделку > 0.
    Метка варианта: «кандидат» (подтверждено И все метки формирования пройдены), «подтверждено на окне при слабом формировании: повторить на новых сутках», «не подтверждено»."""
    nmin, kd = thresholds_window(window_days)
    t, m = R["trades"], R["main"]
    c1 = bool(t["closed"] >= nmin and t["assets_closed"] >= MIN_ASSETS and t["days_with_trades"] >= kd and m is not None and m["n_eff"] >= MIN_NEFF)
    c2 = bool(R["holm"]["pass_both"])
    c3 = bool(t["net_closed_trade"] is not None and t["net_closed_trade"] > 0)
    confirmed = c1 and c2 and c3
    fl = formation_labels or {}
    f_ok = bool(fl) and all(v.get("ok") for v in fl.values())
    if confirmed and f_ok:
        label = "кандидат"
    elif confirmed:
        label = "подтверждено на окне при слабом формировании: повторить на новых сутках"
    else:
        label = "не подтверждено"
    return dict(confirmed=confirmed, c1=c1, c2=c2, c3=c3, n_min=nmin, k_days=kd, min_assets=MIN_ASSETS, label=label, formation_ok=f_ok,
                formation={k: v.get("text") for k, v in fl.items()}, note="метка «подтверждено на окне» - статистический статус заранее названной гипотезы, не подтверждение исполнимой торговли")


def slices_rows(vid, g):
    """Срезы по сделкам (закрытые; MTM отдельной строкой): направление, код выхода, биржа ноги, час входа, минуты вокруг 00/08/16 UTC, отложенный вход, протухшая нога."""
    pm = {pid: ex for pid, ex in zip(g["pids"], g["exch"])}
    buckets = {}

    def add(sl, val, tr):
        b = buckets.setdefault((sl, str(val)), [0, 0.0, 0.0])
        b[0] += 1
        b[1] += tr[TF["net"]]
        b[2] += tr[TF["gross"]]
    for row in g["trades"]:
        tr = row[3:]
        pid = row[1]
        add("kind", "mtm" if tr[TF["mtm"]] else "closed", tr)
        if tr[TF["mtm"]]:
            continue
        add("dir", int(tr[TF["dir"]]), tr)
        add("exit_code", L.CODES[int(tr[TF["code"]])], tr)
        for e in sorted(set(pm[pid])):
            add("exchange", e, tr)
        mod = int(tr[TF["f_in"]]) % 1440
        add("hour_in", "%02d" % (mod // 60), tr)
        add("near_00_08_16", "yes" if any(abs(mod - c) <= 1 or abs(mod - c) >= 1439 for c in (0, 480, 960)) else "no", tr)
        add("deferred", int(bool(tr[TF["deferred"]])), tr)
        add("stale_leg", int(bool(tr[TF["stale"]])), tr)
    return [dict(variant=vid, slice=k[0], value=k[1], trades=v[0], net_mean=v[1] / v[0], gross_mean=v[2] / v[0]) for k, v in sorted(buckets.items())]


# ------------------------------------------------------------------ W2
def w2_window(root, spec, d0, d1, boundary):
    if not spec.get("w2_jobs") or not os.path.isfile(spec["w2_jobs"]):
        return []
    kref = {}
    if spec.get("w2_k_ref") and os.path.isfile(spec["w2_k_ref"]):
        for r in csv.DictReader(open(spec["w2_k_ref"], encoding="utf-8")):
            kref[(r["ex1"], r["sym1"], r["ex2"], r["sym2"])] = int(float(r["k"] or 0))
    rows = []
    for j in csv.DictReader(open(spec["w2_jobs"], encoding="utf-8")):
        tag = j["name"].split()[0]
        crypto = tag in W2_CRYPTO
        fa = os.path.join(root, "panels", j["ex1"], W.safe_name(j["sym1"]) + ".sap")
        fb = os.path.join(root, "panels", j["ex2"], W.safe_name(j["sym2"]) + ".sap")
        if not (os.path.isfile(fa) and os.path.isfile(fb)):
            rows.append(dict(name=j["name"], var="", theta="", note="нет панели (вне данных)", label=W2_LABEL))
            continue
        oA, cA = G.load_panel_range(fa, d0, d1, what="w2")
        oB, cB = G.load_panel_range(fb, d0, d1, what="w2")
        hkd = "HKD" in j["sym1"] or "HKD" in j["sym2"]
        tab = HI.pair_table(oA, cA, oB, cB, kref.get((j["ex1"], j["sym1"], j["ex2"], j["sym2"]), 0), t_start=(boundary - d0) * 1440)
        for r in tab:
            n, n1 = r["n"], r["n_g1"]
            net = (r["sum_g1"] / n1 - HI.FEE_T) if n1 else None
            rows.append(dict(name=j["name"], link="крипто" if crypto else "HK", var=r["var"], theta=r["theta"], n_ep=r["n_ep"], n_ep_1min=r["n_d1"], n_ep_2min=r["n_d2"], n_ep_3p=r["n_d3p"],
                             trades=n, n_conv=r["n_conv"], n_to=r["n_to"], n_end=r["n_end"], gross0_mean=(r["sum_g0"] / n) if n else None, go1_mean=(r["sum_go1"] / n1) if n1 else None,
                             trades_p1=n1, gross_p1_mean=(r["sum_g1"] / n1) if n1 else None, net_taker_p1_mean=net, net_maker_p1_mean=(r["sum_g1"] / n1 - HI.FEE_M) if n1 else None,
                             pred_violation=bool(crypto and n1 and r["theta"] <= 80 and net is not None and net > 0),
                             note="abs для HKD-контрактов не интерпретируется (офсет курса)" if (hkd and r["var"] == "abs") else "", label=W2_LABEL))
    return rows


def write_csv(path, rows, cols=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        open(path, "w", encoding="utf-8", newline=chr(10)).write("")
        return
    cols = cols or list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator=chr(10))
        w.writerow(cols)
        for r in rows:
            w.writerow([("%.10g" % r.get(c)) if isinstance(r.get(c), float) else r.get(c) for c in cols])


# ------------------------------------------------------------------ префлайт (B1, GW 07.11): ресурсы Deck ДО захвата токена
class InfraRefused(SystemExit):
    """Инфраструктурный отказ ДО захвата токена (диск, память, потоки): код 75, токен не тронут, прогон можно повторить после устранения причины."""

    def __init__(self, msg):
        sys.stderr.write("ОТКАЗ (инфраструктура, токен не тронут): %s\n" % msg)
        super().__init__(EXIT_INFRA)
        self.msg = msg


def _meminfo_mb():
    d = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for ln in f:
                k, _, v = ln.partition(":")
                d[k] = int(v.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return d


def _existing_ancestor(p):
    p = os.path.abspath(p)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return p


def preflight(out, threads, min_free_gb, mode, log_path=None):
    """Снимок ресурсов (df, память, нагрузка, потоки) и проверка до захвата токена: свободно >= min_free_gb ГиБ на диске выходов, C2_HOME и ~ (на каждом устройстве); MemAvailable >=
    MEM_BASE_MB + потоки x MEM_PER_THREAD_MB; 1 <= потоки <= число ядер. Снимок дописывается в log_path (window_preflight.log: одна JSON-строка на проверку); при нехватке - InfraRefused (код 75)."""
    snap = dict(time_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), mode=mode, out=os.path.abspath(out), threads=threads, cpu_count=os.cpu_count(),
                min_free_gb=min_free_gb, mem_base_mb=MEM_BASE_MB, mem_per_thread_mb=MEM_PER_THREAD_MB, disks=[], mem_mb=_meminfo_mb(), loadavg=None, problems=[])
    try:
        snap["loadavg"] = list(os.getloadavg())
    except (AttributeError, OSError):
        pass
    seen = set()
    for label, path in (("out", out), ("c2_home", G.c2_home()), ("home", os.path.expanduser("~"))):
        ap = _existing_ancestor(path)
        dev = os.stat(ap).st_dev
        du = shutil.disk_usage(ap)
        snap["disks"].append(dict(what=label, path=ap, free_gb=round(du.free / 2 ** 30, 2), total_gb=round(du.total / 2 ** 30, 2)))
        if dev in seen:
            continue
        seen.add(dev)
        if du.free / 2 ** 30 < min_free_gb:
            snap["problems"].append("свободно %.2f ГиБ на %s (%s), нужно >= %.1f" % (du.free / 2 ** 30, ap, label, min_free_gb))
    avail = snap["mem_mb"].get("MemAvailable")
    need = MEM_BASE_MB + threads * MEM_PER_THREAD_MB
    if avail is not None and avail < need:
        snap["problems"].append("доступно памяти %d МиБ, нужно >= %d (%d + %d потоков x %d)" % (avail, need, MEM_BASE_MB, threads, MEM_PER_THREAD_MB))
    if threads < 1 or (snap["cpu_count"] and threads > snap["cpu_count"]):
        snap["problems"].append("потоков %d при %s ядрах" % (threads, snap["cpu_count"]))
    snap["ok"] = not snap["problems"]
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8", newline=chr(10)) as f:
            f.write(json.dumps(snap, ensure_ascii=False, sort_keys=True) + chr(10))
    if snap["problems"]:
        raise InfraRefused("; ".join(snap["problems"]))
    return snap


def check_out_dir(out):
    """Каталог выходов не существует либо пуст (допустимы только файлы PRE_EXISTING_OK: текст токена-заготовки, лежащий до запуска)."""
    if os.path.isdir(out):
        extra = [f for f in os.listdir(out) if f not in PRE_EXISTING_OK]
        if extra:
            raise G.Refused("каталог выходов не пуст: %s (окно читается один раз; для проверки на формировании - новый каталог): %s" % (out, ", ".join(sorted(extra)[:5])))


def clean_out_dir(out):
    """После сбоя до первого чтения окна: убрать всё, что создал прогон (кроме файлов PRE_EXISTING_OK), чтобы повтор не упёрся в «каталог выходов не пуст»."""
    if not os.path.isdir(out):
        return
    for f in os.listdir(out):
        if f in PRE_EXISTING_OK:
            continue
        fp = os.path.join(out, f)
        if os.path.isdir(fp) and not os.path.islink(fp):
            shutil.rmtree(fp, ignore_errors=True)
        else:
            try:
                os.remove(fp)
            except OSError:
                pass


def dir_bytes(d):
    n = 0
    for root_, _, fs in os.walk(d):
        for f in fs:
            try:
                n += os.path.getsize(os.path.join(root_, f))
            except OSError:
                pass
    return n


def release_intermediate(out, keep):
    """Освобождает промежуточные массивы (кэш рядов work/ и боковые панели оборота turn/), оставляя итоговые таблицы, сделки (grid_*/trades.csv), суточные ряды (daily_y.csv)."""
    freed = {}
    if keep:
        return dict(kept=True)
    for sub in ("work", "turn"):
        d = os.path.join(out, sub)
        if os.path.isdir(d):
            freed[sub] = dir_bytes(d)
            shutil.rmtree(d)
    return freed


# ------------------------------------------------------------------ команда run
def refuse_modes(a):
    """Проверка режима ДО чтения spec и данных: отказ с кодом 3."""
    if a.go_window and a.test_boundary is not None:
        raise G.Refused("--go-window и --test-boundary несовместимы")
    if a.go_window and a.test_world:
        raise G.Refused("--go-window нельзя сочетать с --test-world")
    if not a.go_window and a.test_boundary is None:
        raise G.Refused("не задан режим: --go-window (окно, токен CEO) или --test-boundary D (проверка на формировании)")
    if a.test_boundary is not None and not (G.D_SIG0 <= a.test_boundary <= G.D_FORM_LAST):
        raise G.Refused("--test-boundary: граница в %d..%d (задано %d); сутки >= %d раннер в этом режиме читать не вправе" % (G.D_SIG0, G.D_FORM_LAST, a.test_boundary, G.D_WIN0))
    if a.go_window and a.limit_pairs:
        raise G.Refused("--limit-pairs в режиме окна запрещён")


def cmd_run(a):
    refuse_modes(a)
    spec = load_spec(a.spec)
    inputs = collect_inputs(spec, a.spec)
    tw = os.path.abspath(a.test_world) if a.test_world else None
    root = tw or spec.get("root") or G.HOME
    if a.go_window:
        mode, d1, boundary = "window", G.D_WIN_LAST, G.D_WIN0
    else:
        mode, boundary = ("test" if tw else "testb"), a.test_boundary
        d1 = (a.last_day or G.D_FORM_LAST) if tw else G.D_FORM_LAST
        if not tw and a.last_day not in (None, G.D_FORM_LAST):
            raise G.Refused("--last-day допустим только в синтетическом мире")
    if mode in ("testb", "test") and d1 >= G.D_WIN0 and not tw:
        raise G.Refused("test-boundary не читает сутки >= %d" % G.D_WIN0)
    G.configure(go_window=a.go_window, script_path=os.path.abspath(__file__), params_paths=params_tuple(spec), mode=mode, require_k0=(mode != "test"),
                test_root=tw, runner=G.RUNNER, extra_inputs=inputs, boundary=boundary if mode == "testb" else None)
    out = a.out or (os.path.join(G.c2_home(), "window") if a.go_window else (os.path.join(G.c2_home(), "window_test", "D%d" % boundary) if not tw else None))
    if out is None:
        raise G.Refused("--out обязателен в синтетическом мире")
    check_out_dir(out)
    pre = None
    if mode == "window":                                     # всё, что можно проверить БЕЗ захвата токена, - до него (d2 ревизии)
        why = G.check_window_token()
        if why:
            raise G.Refused(why)
        G.require_k0()
        G.ensure_params_committed()
    if mode != "test":
        pre = preflight(out, a.threads, a.min_free_gb, mode, log_path=os.path.join(G.c2_home(), "window_preflight.log") if mode == "window" else None)
    G.check_days(d1, what="раннер: старт")                   # отказ ДО любого чтения; в window - проверка и захват токена (дальше любой исход - через finally)
    t0 = time.time()
    outcome = "failed"
    reason = ""
    try:
        if mode != "test":
            G.require_k0()
            G.ensure_params_committed()
        os.makedirs(out, exist_ok=True)
        if pre:
            jdump(os.path.join(out, "preflight.json"), pre)
        if mode == "window":
            G.log_window_event("run_start", "out=%s inputs=%d threads=%d" % (out, len(inputs), a.threads))
        res = run_all(a, spec, inputs, root, tw, mode, d1, boundary, out, t0)
        outcome = "done" if res == 0 else "done_gate_block_failed"
        return res
    except BaseException as e:                               # noqa: BLE001  (причина - в журнал; исключение не гасится)
        reason = "%s: %s" % (type(e).__name__, e)
        raise
    finally:
        if mode == "window":
            if G.finish_window(outcome, reason):             # токен возвращён: окно не читалось, в каталоге выходов только наши служебные файлы - убрать
                clean_out_dir(out)


def prepare_gate(spec, root, tw, turn_dir, pairs, classes):
    """Блок «с Gate»: мир с Gate (gate='on'), манифест и универсум проверяются ДО любого чтения окна (отказ - исключение до первого открытия файла: токен возвращается).
    Пары 8 бирж в универсуме с Gate обязаны совпасть с универсумом основного набора (ряды этих пар общие, кэш рядов используется совместно)."""
    gspec = spec["gate"]
    world_g = W.World(root, test=bool(tw), turn_dir=turn_dir, gate=True, gate_ready=gspec.get("manifest"), gate_pairs=gspec.get("pairs"))
    gs = world_g.gate_state()
    pairs_g = CR.select_pairs(world_g, types.SimpleNamespace(test_world=bool(tw), classes=",".join(classes), pairs_file=None, limit_pairs=0))
    gate_only = [p for p in pairs_g if "gate" in (p["exA"], p["exB"])]
    rest = {p["pid"] for p in pairs_g if "gate" not in (p["exA"], p["exB"])}
    base = {p["pid"] for p in pairs}
    if rest != base:
        raise RuntimeError("универсум с Gate: пары 8 бирж (%d) не равны универсуму основного набора (%d); общий кэш рядов недопустим" % (len(rest), len(base)))
    if not gate_only:
        raise RuntimeError("универсум с Gate: пар с ногой Gate нет")
    return world_g, pairs_g, gate_only, gs


def gate_split(gr):
    """Пары с ногой Gate отдельно от пар 8 бирж (описательно): ряд y_d подмножества (знаменатель - активные пары-сутки подмножества), оба интервала, сделки, активы, сутки, gross/net."""
    is_g = np.array([("gate" in ex) for ex in gr.exch])
    out = {}
    for name, m in (("gate_leg", is_g), ("exch8", ~is_g)):
        rows = np.flatnonzero(m)
        if not len(rows):
            out[name] = dict(pairs=0, trades=0, assets=0, days_with_trades=0, gross_trade=None, net_trade=None, mean=None, main=None)
            continue
        ser = AG.y_series(gr, 0, rows)["primary"]
        tn = gr.tab[0][rows]
        n_all = float(tn[:, :, FI["n"]].sum())
        out[name] = dict(pairs=int(len(rows)), trades=int(n_all), assets=AG.n_assets_with_trades(gr, 0, rows), days_with_trades=AG.n_days_with_trades(gr, 0, rows),
                         gross_trade=(float(tn[:, :, FI["gross"]].sum()) / n_all) if n_all else None, net_trade=(float(tn[:, :, FI["net"]].sum()) / n_all) if n_all else None,
                         mean=mean_of(ser), main=stat_block(ser))
    return out


def run_variant_set(tag, world, pairs, variants, addendum, raw_root, work, d0, d1, boundary, threads, cfgd, out_root, log, split_gate=False):
    """Все варианты набора по одной комбинации (параметры не выбираются): сетка, прогон r1, A3b окна, контроли, анализ, срезы, клетки. Сетка варианта пишется в out_root/grid_<id>.
    -> список результатов (без Холма и статуса: они зависят от набора и считаются снаружи)."""
    pairs_by_pid = {p["pid"]: p for p in pairs}
    results = []
    for v in variants:
        t1 = time.time()
        g = stage_grid(world, pairs, v, cfgd, work, d0, d1, boundary, threads)
        gdir = os.path.join(out_root, "grid_" + v["id"])
        write_grid(gdir, g, cfgd, d0, d1, boundary, len(pairs), time.time() - t1)
        gr = AG.Grid(gdir, v["cls"])
        gr.cells_real = list(gr.cells)
        gr.cells = [v["cls"]] * len(gr.pids)                  # класс как «клетка» для cell_status
        r1 = stage_grid(world, pairs, v, dict(cfgd, entry_mode="drop"), work, d0, d1, boundary, threads, want_trades=False)
        r1_mean = mean_of(AG.y_series(mem_grid(r1, d0, d1, boundary, v["cls"]), 0, np.arange(len(r1["pids"])))["primary"])
        # A3b окна (R3.3): c_H0 (числа H0) на парах H1 и c_H1 (числа H1) на парах H0
        cross = None
        if v.get("a3b"):
            cross = {}
            for name, half in (("h1on0", 1), ("h0on1", 0)):
                sub = v["a3b"][name]
                pv = dict(cls=v["cls"], combo=sub["combo"], classpar=sub["classpar"], q5=v.get("q5"))
                gx = stage_grid(world, [p for p in pairs if half_of(p["key"]) == half], pv, cfgd, work, d0, d1, boundary, threads, want_trades=False)
                cross[name] = dict(sub_run_stats(mem_grid(gx, d0, d1, boundary, v["cls"])), combo=sub["combo"], on_half="H%d" % half)
        ctl = stage_controls(world, pairs_by_pid, g, v, cfgd, work, d0, d1, boundary, threads)
        R = analyze_variant(gr, v, g, ctl, addendum, r1_mean, cross)
        R["formation_labels"] = v.get("formation_labels")
        R["slices"] = slices_rows(v["id"], g)
        if split_gate:
            R["split_gate"] = gate_split(gr)
        cells = []
        for cell in sorted(set(gr.cells_real)):
            rr = np.array([i for i, c in enumerate(gr.cells_real) if c == cell])
            s = AG.y_series(gr, 0, rr)["primary"]
            tn = gr.tab[0][rr]
            n = float(tn[:, :, FI["n"]].sum())
            cells.append(dict(variant=v["id"], cell=cell, pairs=int(len(rr)), trades=int(n), assets=AG.n_assets_with_trades(gr, 0, rr), days=AG.n_days_with_trades(gr, 0, rr),
                              net_trade=(float(tn[:, :, FI["net"]].sum()) / n) if n else None, mean_yd=mean_of(s)))
        R["cells_table"] = cells
        results.append(R)
        log("[%s] вариант %s: сделок %d (закрытых %d), y_d среднее %s" % (tag, v["id"], R["trades"]["all"], R["trades"]["closed"], R["main"] and R["main"]["mean"]))
        del g, gr, r1
    return results


def write_common_tables(odir, results):
    """Таблицы, общие для основного набора и блока Gate: суточные ряды, срезы, клетки, A8, A3b окна (имена и содержимое - как у основного набора)."""
    drows = []
    for r in results:
        pd_ = r["series"]["primary_days"] or []
        for d, v in zip(r["series"]["days"], r["series"]["closed"]):
            drows.append(dict(variant=r["id"], day=d, closed=v, primary=(r["series"]["primary"][pd_.index(d)] if d in pd_ else None)))
    write_csv(os.path.join(odir, "daily_y.csv"), drows)
    write_csv(os.path.join(odir, "slices.csv"), [x for r in results for x in r["slices"]])
    write_csv(os.path.join(odir, "cells_table.csv"), [x for r in results for x in r["cells_table"]])
    write_csv(os.path.join(odir, "a8.csv"), [dict(variant=r["id"], exchange=e, pairs=x["pairs"], mean=x["mean"], ok=int(x["ok"])) for r in results for e, x in r["a8"].items()])
    a3rows = []
    for r in results:
        for h, x in r["a3b_window_reference"].items():
            a3rows.append(dict(variant=r["id"], kind="c_полн на половине", half=h, pairs=x["pairs"], assets=x["assets"], trades=x["trades"], mean=x["mean"], ok=int(x["ok"])))
        for k, x in (r["a3b_window"] or {}).items():
            st_ = x["stat"] or {}
            a3rows.append(dict(variant=r["id"], kind="перекрёстно %s (%s на %s)" % (k, json.dumps(x["combo"]), x["on_half"]), half=x["on_half"], pairs=x["pairs"], assets=x["assets"],
                               trades=x["trades"], mean=x["mean"], lo_t=st_.get("lo_t"), lo_boot=st_.get("lo_boot"), ok=int(x["mean"] is not None and x["mean"] > 0)))
    write_csv(os.path.join(odir, "a3b_window.csv"), a3rows)


def trial_counts(results, w2_rows):
    """Счётчики испытаний набора (число, не вручную): контроли К-а..К-г, A8, A3b, срезы."""
    n_ctl = 4 + 1 + 1 + 1                                   # К-б (±5, ±15), К-в p_win, К-а, К-г
    return dict(n_ctl=n_ctl, a8=sum(len(r["a8"]) for r in results), a3b=sum(4 if r["a3b_window"] else 2 for r in results),      # два перекрёстных прогона и два справочных на половинах
                slices=sum(len(r["slices"]) for r in results), w2=w2_rows)


def gate_summary_lines(results, window_days, boundary, d1, extra):
    lines = ["[с Gate, ОПИСАТЕЛЬНО, вне M_окно и вне статусов; без Холма] сутки %d..%d (%d), пар %d (с ногой Gate %d), %s" % (boundary, d1, window_days, extra["pairs"], extra["pairs_gate_only"], extra["note"])]
    for r in results:
        m, lb, sg = r["main"] or {}, r["window_labels"], r["split_gate"]
        t = r["trades"]
        lines.append("[с Gate] %s %s: сделок %d (закрытых %d), активов %d, суток со сделками %d, gross %s / net %s bps на сделку; y_d = %s [t %s; %s] [бутстреп %s; %s] n=%s (90 %%-интервалы, описательно); %s; %s; %s; %s" % (
            r["id"], r["combo"], t["all"], t["closed"], t["assets_closed"], t["days_with_trades"], _f(t["gross_trade"]), _f(t["net_trade"]), _f(m.get("mean")), _f(m.get("lo_t")), _f(m.get("hi_t")),
            _f(m.get("lo_boot")), _f(m.get("hi_boot")), m.get("n"), lb["K_a"], lb["K_g"], lb["A8"], lb["A3b"]))
        ct, ab = r["controls"], r["a3b_window"] or {}
        lines.append("[с Gate]   %s контроли: К-а перевёрнутое y_d %s; К-б сдвиг входа (среднее y_d закрытых, сделок) %s; К-в p_win %s; К-г случайные входы: разность факт - контроль %s [t %s; бутстреп %s]; A3b окна: c_H0 на парах H1 %s, c_H1 на парах H0 %s" % (
            r["id"], _f(ct["reversed_mean"]), "; ".join("%s мин: %s (%s)" % (k, _f(x["mean_yd_closed"]), x["trades_done"]) for k, x in sorted(ct["shifts"].items(), key=lambda kv: int(kv[0]))),
            _f((ct["p_win"] or {}).get("p_win")), _f(((ct["random_entries"] or {}).get("diff") or {}).get("mean")), _f(((ct["random_entries"] or {}).get("diff") or {}).get("lo_t")),
            _f(((ct["random_entries"] or {}).get("diff") or {}).get("lo_boot")), _f((ab.get("h1on0") or {}).get("mean")), _f((ab.get("h0on1") or {}).get("mean"))))
        for nm, lab in (("gate_leg", "пары с ногой Gate"), ("exch8", "пары 8 бирж")):
            x = sg[nm]
            mm = x["main"] or {}
            lines.append("[с Gate]   %s %s: пар %d, сделок %d, активов %d, суток %d, gross %s / net %s; y_d = %s [t %s; %s] [бутстреп %s; %s] n=%s" % (
                r["id"], lab, x["pairs"], x["trades"], x["assets"], x["days_with_trades"], _f(x["gross_trade"]), _f(x["net_trade"]), _f(mm.get("mean")), _f(mm.get("lo_t")), _f(mm.get("hi_t")),
                _f(mm.get("lo_boot")), _f(mm.get("hi_boot")), mm.get("n")))
    return lines


def run_gate_block(a, spec, gspec, world_g, pairs_g, gate_only, gs, root, tw, mode, d0, d1, boundary, window_days, out, work, cfgd, raw_root, w2_main, log):
    """Блок «с Gate» (описательные строки вне M_окно): отдельный подкаталог out/gate; основной набор к этому моменту записан и от блока не зависит. -> словарь для run_meta."""
    t1 = time.time()
    gout = os.path.join(out, "gate")
    os.makedirs(gout, exist_ok=True)
    turn_dir = os.path.join(out, "turn")
    n_turn = W.build_turnover(world_g, gate_only, d0, d1, raw_root, outdir=turn_dir)
    log("[Gate] боковых панелей оборота: %d (ноги пар с Gate)" % n_turn)
    stage_series(world_g, gate_only, cfgd, work, d0, d1, a.threads)
    log("[Gate] ряды s, b, sigma, r6, c готовы (ячейки с ногой Gate; ряды пар 8 бирж - общие с основным набором)")
    addendum_g = read_addendum(gspec.get("addendum") or spec.get("addendum"))
    results = run_variant_set("Gate", world_g, pairs_g, gspec["variants"], addendum_g, raw_root, work, d0, d1, boundary, a.threads, cfgd, gout, log, split_gate=True)
    # W2: не зависит от универсума (читает панели по списку заданий); тот же список заданий по ключам - результат основного набора без повторного чтения
    def jobs_key(p):
        return [(r["name"], r["ex1"], r["sym1"], r["ex2"], r["sym2"]) for r in csv.DictReader(open(p, encoding="utf-8"))] if p and os.path.isfile(p) else None
    same_jobs = bool(jobs_key(gspec.get("w2_jobs")) == jobs_key(spec.get("w2_jobs")))
    if tw:
        w2_g, w2_new = [], 0
    elif same_jobs:
        w2_g, w2_new = w2_main, 0
    else:
        w2_g = w2_window(root, dict(w2_jobs=gspec.get("w2_jobs"), w2_k_ref=spec.get("w2_k_ref")), d0, d1, boundary)
        w2_new = len(w2_g)
    log("[Gate] W2: строк %d (%s)" % (len(w2_g), "список заданий равен основному: результат основного набора, повторного чтения нет" if same_jobs else "свой список заданий"))
    chk7 = None
    if mode != "test":
        n7, mx7, bad7 = W.verify_turnover(world_g, raw_root, d0, d1, pairs_g, only_ex="gate")
        chk7 = dict(symbols=n7, max_rel=mx7, bad=len(bad7))
    cnt = trial_counts(results, w2_new)
    M = len(results)
    groups = [("строки Gate: варианты (описательно, без Холма)", M), ("контроли К-а..К-г (Gate)", cnt["n_ctl"] * M), ("срезы «пары с ногой Gate / пары 8 бирж»", 2 * M),
              ("прогоны A8 (Gate, 9 бирж)", cnt["a8"]), ("прогоны A3b окна (Gate)", cnt["a3b"]), ("строки срезов (Gate)", cnt["slices"]), ("строки W2 (новые, если список заданий иной)", w2_new)]
    trials_g = dict(M_gate_variants=M, groups=[dict(group=g_[0], count=g_[1], expected_false_low=0.05 * g_[1], expected_false_high=0.05 * g_[1]) for g_ in groups],
                    total_tests_gate=sum(g_[1] for g_ in groups), expected_false_total_high=sum(0.05 * g_[1] for g_ in groups), w2_same_jobs=same_jobs, w2_rows_reused=len(w2_g) if same_jobs else 0,
                    note="описательные строки вне M_окно; число испытаний на тех же сутках окна; ожидаемо ложных при размере 0,05 - ориентир")
    jdump(os.path.join(gout, "variants.json"), results)
    jdump(os.path.join(gout, "trials_gate.json"), trials_g)
    jdump(os.path.join(gout, "formation_labels_gate.json"), {k: addendum_g.get(k) for k in ("katok_quartiles", "p_win", "calib_multiplicity_result", "cells_admitted", "tstruct_by_class", "gmax_by_class",
                                                                                        "k_stale_by_class", "calib_start_json") if k in addendum_g})
    write_common_tables(gout, results)
    if w2_new:
        write_csv(os.path.join(gout, "w2_window_gate.csv"), w2_g)
    srows, sprows = [], []
    for r in results:
        m, lb, t = r["main"] or {}, r["window_labels"], r["trades"]
        fl = r.get("formation_labels") or {}
        srows.append(dict(variant=r["id"], cls=r["cls"], combo=json.dumps(r["combo"]), q5=int(r["q5"]), trades_all=t["all"], trades_closed=t["closed"], assets=t["assets_closed"], days_with_trades=t["days_with_trades"],
                          gross_trade=t["gross_trade"], net_trade=t["net_trade"], n_days=m.get("n"), n_eff=m.get("n_eff"), mean=m.get("mean"), lo_t=m.get("lo_t"), hi_t=m.get("hi_t"),
                          lo_boot=m.get("lo_boot"), hi_boot=m.get("hi_boot"), K_a=lb["K_a"], K_g=lb["K_g"], A8=lb["A8"], A3b=lb["A3b"], p_win=(r["controls"]["p_win"] or {}).get("p_win"),
                          reversed_mean=r["controls"]["reversed_mean"], katok=int(r["katok"]), note="описательно: вне M_окно, без Холма и статуса",
                          formation=" | ".join(str(x.get("text")) for x in fl.values())))
        for nm, x in r["split_gate"].items():
            mm = x["main"] or {}
            sprows.append(dict(variant=r["id"], subset=nm, pairs=x["pairs"], trades=x["trades"], assets=x["assets"], days_with_trades=x["days_with_trades"], gross_trade=x["gross_trade"],
                               net_trade=x["net_trade"], n_days=mm.get("n"), mean=mm.get("mean"), lo_t=mm.get("lo_t"), hi_t=mm.get("hi_t"), lo_boot=mm.get("lo_boot"), hi_boot=mm.get("hi_boot")))
    write_csv(os.path.join(gout, "gate_lines.csv"), srows)
    write_csv(os.path.join(gout, "split_gate.csv"), sprows)
    extra = dict(pairs=len(pairs_g), pairs_gate_only=len(gate_only), note="манифест gate_ready: символов %s, множитель оборота %s" % (gs["info"].get("symbols"), gs["info"].get("mult")))
    lines = gate_summary_lines(results, window_days, boundary, d1, extra)
    lines.append("[с Gate] испытаний (описательных, вне M): " + "; ".join("%s %d" % (g_["group"], g_["count"]) for g_ in trials_g["groups"]) + "; сумма %d" % trials_g["total_tests_gate"])
    open(os.path.join(gout, "summary_gate.txt"), "w", encoding="utf-8", newline=chr(10)).write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    return dict(results=results, trials=trials_g, lines=lines, pairs=len(pairs_g), pairs_gate_only=len(gate_only), seconds=round(time.time() - t1, 1), g_c2_7=chk7,
                manifest=dict(symbols=gs["info"].get("symbols"), mult=gs["info"].get("mult"), open_label=gs["info"].get("open_label")), w2_same_jobs=same_jobs)


def run_all(a, spec, inputs, root, tw, mode, d1, boundary, out, t0):
    d0 = G.D_FORM0
    work = os.path.join(out, "work")
    turn_dir = os.path.join(out, "turn")
    window_days = d1 - boundary + 1
    cfgd = CR.make_cfg(types.SimpleNamespace(L=1440, nb=240, no_dedup=False, delay=1, entry_mode="defer", exit_mode="ffill", no_ceiling=False, no_q6=False))
    world = make_world(root, bool(tw), turn_dir)
    CR._TURN[0] = turn_dir
    classes = sorted({v["cls"] for v in spec["variants"]})
    pairs = CR.select_pairs(world, types.SimpleNamespace(test_world=bool(tw), classes=",".join(classes), pairs_file=None, limit_pairs=a.limit_pairs or 0))
    raw_root = spec.get("raw_root") or os.path.expanduser("~/xdl/out")
    gspec = spec.get("gate")
    gprep = prepare_gate(spec, root, tw, turn_dir, pairs, classes) if gspec else None      # манифест Gate и универсум - ДО первого открытия файла окна

    def log(s):
        print("[%7.1f с] %s" % (time.time() - t0, s), flush=True)
    log("режим %s, сутки %d..%d (окно %d суток), граница %d, пар %d, классы %s, потоков %d%s" % (mode, d0, d1, window_days, boundary, len(pairs), classes, a.threads,
                                                                                         "; блок Gate: пар %d (с ногой Gate %d)" % (len(gprep[1]), len(gprep[2])) if gprep else ""))
    n_turn = W.build_turnover(world, pairs, d0, d1, raw_root, outdir=turn_dir)
    log("боковых панелей оборота: %d" % n_turn)
    stage_series(world, pairs, cfgd, work, d0, d1, a.threads)
    log("ряды s, b, sigma, r6, c готовы")
    addendum = read_addendum(spec.get("addendum_nogate") or spec.get("addendum"))
    results = run_variant_set("основной", world, pairs, spec["variants"], addendum, raw_root, work, d0, d1, boundary, a.threads, cfgd, out, log)
    holm_block(results)
    for R in results:
        R["status"] = r3_status(R, window_days, R.get("formation_labels"))
        log("вариант %s: Холм по обоим интервалам %s; метка %s" % (R["id"], R["holm"]["pass_both"], R["status"]["label"]))
    w2 = w2_window(root, spec, d0, d1, boundary) if not tw else []
    log("W2: строк %d" % len(w2))
    chk7 = None
    if mode != "test":
        n7, mx7, bad7 = W.verify_turnover(world, raw_root, d0, d1, pairs)
        chk7 = dict(symbols=n7, max_rel=mx7, bad=len(bad7))
    # итог по классам: высшая метка среди вариантов
    class_label = {}
    for R in results:
        c = class_label.setdefault(R["cls"], dict(label=None, variant=None, rank=-1))
        rk = RANK[R["status"]["label"]]
        if rk > c["rank"]:
            c.update(label=R["status"]["label"], variant=R["id"], rank=rk)
    # сводка «испытаний на окне всего» (R3.8 п. 4)
    days_sets = [set(r["series"]["primary_days"] or []) for r in results]
    common = sorted(set.intersection(*days_sets)) if days_sets and all(days_sets) else []
    if len(results) > 1 and len(common) >= 2:
        mat = np.array([[r["series"]["primary"][r["series"]["primary_days"].index(d)] for r in results] for d in common])
        m_eff = CS.m_eff(mat)[0]
    else:
        m_eff = float(len(results))
    M = len(results)
    cnt = trial_counts(results, len(w2))
    n_ctl, n_a8, n_a3b, n_slices = cnt["n_ctl"], cnt["a8"], cnt["a3b"], cnt["slices"]
    m_eff_ok = m_eff == m_eff
    groups = [("клетки F8", F8_CELLS, 0.05 * F8_CELLS, 0.05 * F8_CELLS), ("M_окно (варианты C2)", M, 0.05 * min(m_eff, M) if m_eff_ok else None, 0.05 * M),
              ("строки W2", len(w2), 0.05 * len(w2), 0.05 * len(w2)), ("контроли К-а..К-г", n_ctl * M, 0.05 * n_ctl * M, 0.05 * n_ctl * M),
              ("прогоны A8 окна", n_a8, 0.05 * n_a8, 0.05 * n_a8), ("прогоны A3b окна", n_a3b, 0.05 * n_a3b, 0.05 * n_a3b), ("строки срезов", n_slices, 0.05 * n_slices, 0.05 * n_slices)]
    trials = dict(M_window=M, M_eff=m_eff, M_eff_note="по суточным y_d вариантов (r3 R3.2); в поправку не идёт (поправка - Холм на M_окно)",
                  expected_false_range=CS.expected_false_range(M, m_eff) if m_eff_ok else None, expected_false_independent=CS.expected_false_candidates(M),
                  w2_rows=len(w2), w2_pairs=len({r["name"] for r in w2}), controls_per_variant=n_ctl, controls_total=n_ctl * M, a8_runs=n_a8, a3b_runs=n_a3b, slices_rows=n_slices,
                  f8_cells=F8_CELLS, groups=[dict(group=g_[0], count=g_[1], expected_false_low=g_[2], expected_false_high=g_[3]) for g_ in groups],
                  total_tests_on_window=sum(g_[1] for g_ in groups), expected_false_total_high=sum(g_[3] for g_ in groups),
                  note="число испытаний на одних и тех же сутках окна; ожидаемое число ложных срабатываний при размере 0,05 - ориентир (испытания зависимы)")
    meta = dict(mode=mode, boundary=boundary, d0=d0, d1=d1, window_days=window_days, pairs=len(pairs), classes=classes, variants=[v["id"] for v in spec["variants"]],
                seconds=round(time.time() - t0, 1), boundary_rule=BOUNDARY_RULE, not_done=NOT_DONE, g_c2_7=chk7, spec=spec.get("name"), threads=a.threads, class_label=class_label,
                code_manifest_sha256=hashlib.sha256("\n".join(G.manifest_lines()).encode("utf-8")).hexdigest() if mode != "test" else None, limit_pairs=a.limit_pairs or None,
                gate_block="pending" if gspec else "нет блока Gate в spec")
    jdump(os.path.join(out, "run_meta.json"), meta)
    jdump(os.path.join(out, "variants.json"), results)
    jdump(os.path.join(out, "trials_total.json"), trials)
    jdump(os.path.join(out, "formation_labels.json"), {k: addendum.get(k) for k in ("katok_quartiles", "p_win", "calib_multiplicity_result", "cells_admitted", "tstruct_by_class",
                                                                                      "gmax_by_class", "k_stale_by_class", "calib_start_json") if k in addendum})
    srows = []
    for r in results:
        s, h, m, lb = r["status"], r["holm"], r["main"] or {}, r["window_labels"]
        srows.append(dict(variant=r["id"], cls=r["cls"], combo=json.dumps(r["combo"]), q5=int(r["q5"]), label=s["label"], confirmed=int(s["confirmed"]), c1_n_assets_days=int(s["c1"]),
                          c2_holm_both=int(s["c2"]), c3_net_trade_pos=int(s["c3"]), n_min=s["n_min"], k_days=s["k_days"], trades_closed=r["trades"]["closed"], assets=r["trades"]["assets_closed"],
                          days_with_trades=r["trades"]["days_with_trades"], n_days=m.get("n"), n_eff=m.get("n_eff"), mean=m.get("mean"), lo_t=m.get("lo_t"), hi_t=m.get("hi_t"),
                          lo_boot=m.get("lo_boot"), hi_boot=m.get("hi_boot"), skew=m.get("skew"), holm_M=h["M"], holm_rank=h["rank"], holm_t=int(h["t"]["passed"]),
                          holm_boot=int(h["boot"]["passed"]), net_closed_trade=r["trades"]["net_closed_trade"], K_a=lb["K_a"], K_g=lb["K_g"], A8=lb["A8"], A3b=lb["A3b"],
                          p_win=(r["controls"]["p_win"] or {}).get("p_win"), reversed_mean=r["controls"]["reversed_mean"], katok=int(r["katok"]),
                          formation_ok=int(s["formation_ok"]), formation=" | ".join(str(x) for x in s["formation"].values())))
    write_csv(os.path.join(out, "class_status.csv"), srows)
    write_common_tables(out, results)
    if w2:
        write_csv(os.path.join(out, "w2_window.csv"), w2)
    lines = ["окно C2: режим %s, граница %d, сутки %d..%d (%d), пар %d, %.0f с" % (mode, boundary, boundary, d1, window_days, len(pairs), time.time() - t0)]
    viol = [r for r in w2 if r.get("pred_violation")]
    if w2:
        lines.append("W2: предсказание r3 (крипто-связки: net при +1м отрицателен при θ <= 80): %s" % (("РАСХОЖДЕНИЕ в %d строках: %s" % (len(viol), "; ".join("%s %s θ=%s net %.1f" % (r["name"], r["var"], r["theta"], r["net_taker_p1_mean"])
                                                                                                                                          for r in viol[:6]))) if viol else "расхождений нет"))
    for r in results:
        m, h, s, lb = r["main"] or {}, r["holm"], r["status"], r["window_labels"]
        lines.append("%s %s: сделок %d (закрытых %d), активов %d, y_d = %s [t %s; %s] [бутстреп %s; %s] n=%s; Холм M=%d ранг %d: t %s, бутстреп %s; чистая на сделку %s; метка: %s; %s; %s; %s; %s" % (
            r["id"], r["combo"], r["trades"]["all"], r["trades"]["closed"], r["trades"]["assets_closed"], _f(m.get("mean")), _f(m.get("lo_t")), _f(m.get("hi_t")), _f(m.get("lo_boot")),
            _f(m.get("hi_boot")), m.get("n"), h["M"], h["rank"], h["t"]["passed"], h["boot"]["passed"], _f(r["trades"]["net_closed_trade"]), s["label"], lb["K_a"], lb["K_g"], lb["A8"], lb["A3b"]))
    lines.append("испытаний на окне всего: " + "; ".join("%s %d (ожидаемо ложных %s-%s)" % (g_["group"], g_["count"], _f(g_["expected_false_low"]), _f(g_["expected_false_high"])) for g_ in trials["groups"]) +
                 "; сумма %d (ожидаемо ложных до %s); M_эфф %s" % (trials["total_tests_on_window"], _f(trials["expected_false_total_high"]), _f(m_eff)))
    open(os.path.join(out, "summary_main.txt"), "w", encoding="utf-8", newline=chr(10)).write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    # ---- блок «с Gate»: после записи основного набора; сбой блока не теряет основной результат
    rc = 0
    gres = None
    if gspec:
        try:
            gres = run_gate_block(a, spec, gspec, gprep[0], gprep[1], gprep[2], gprep[3], root, tw, mode, d0, d1, boundary, window_days, out, work, cfgd, raw_root, w2, log)
            lines += gres["lines"]
            tg = gres["trials"]
            grand = dict(main=trials, gate_descriptive=tg, total_tests_main=trials["total_tests_on_window"], total_tests_gate_descriptive=tg["total_tests_gate"],
                         total_tests_all=trials["total_tests_on_window"] + tg["total_tests_gate"],
                         note="основной набор (M_окно = %d) и описательные строки Gate (вне M) - раздельные группы; сумма - число испытаний на одних и тех же сутках окна" % M)
            jdump(os.path.join(out, "trials_grand.json"), grand)
            lines.append("испытаний на окне всего: основной набор %d; описательные, вне M (Gate) %d; сумма %d" % (grand["total_tests_main"], grand["total_tests_gate_descriptive"], grand["total_tests_all"]))
            print(lines[-1], flush=True)
        except Exception:                                   # noqa: BLE001  (основной набор записан; Gate - описательный)
            tb = traceback.format_exc()
            os.makedirs(os.path.join(out, "gate"), exist_ok=True)
            open(os.path.join(out, "gate", "FAILED.txt"), "w", encoding="utf-8", newline=chr(10)).write(tb)
            lines.append("[с Gate] БЛОК НЕ ПОСЧИТАН (сбой, основной набор записан): %s" % tb.strip().split(chr(10))[-1])
            print(lines[-1], flush=True)
            sys.stderr.write(tb)
            rc = 4
    open(os.path.join(out, "summary.txt"), "w", encoding="utf-8", newline=chr(10)).write("\n".join(lines) + "\n")
    freed = release_intermediate(out, a.keep_work)
    meta.update(seconds=round(time.time() - t0, 1), released_bytes=freed,
                gate_block=(dict(rc=0, seconds=gres["seconds"], pairs=gres["pairs"], pairs_gate_only=gres["pairs_gate_only"], g_c2_7=gres["g_c2_7"], manifest=gres["manifest"], w2_same_jobs=gres["w2_same_jobs"])
                            if gres else ("сбой (gate/FAILED.txt)" if gspec else "нет блока Gate в spec")))
    jdump(os.path.join(out, "run_meta.json"), meta)
    log("промежуточные массивы освобождены: %s" % ({k: "%.2f ГиБ" % (v / 2 ** 30) for k, v in freed.items()} if not freed.get("kept") else "оставлены (--keep-work)"))
    shas = []
    for sub in ("", "gate"):
        d_ = os.path.join(out, sub) if sub else out
        if not os.path.isdir(d_):
            continue
        for fn in sorted(os.listdir(d_)):
            fp = os.path.join(d_, fn)
            if os.path.isfile(fp) and fn != "SHA256.txt":
                shas.append("%s  %s" % (hashlib.sha256(open(fp, "rb").read()).hexdigest(), (sub + "/" if sub else "") + fn))
    open(os.path.join(out, "SHA256.txt"), "w", encoding="utf-8", newline=chr(10)).write("\n".join(shas) + "\n")
    return rc


def _f(x):
    return "None" if x is None else ("%.4f" % x)


# ------------------------------------------------------------------ G-C2-9w: сверка с conv_run grid
def cmd_gate9w(a):
    """Сверка выходов раннера (grid_<V>/<cls>) с `conv_run grid --trades-out` (тот же вариант, те же сутки и граница): массивы tab/act/hist/minnet, pids, pairmeta, counters,
    trades.csv, combos; затем числа класса (ряд y_d, интервалы) по обоим каталогам."""
    base = dict(x.split("=", 1) for x in a.base)
    allok = True
    rep = {}
    gname = "G-C2-9wg" if os.path.basename(os.path.normpath(a.run)) == "gate" else "G-C2-9w"      # блок Gate лежит в <out>/gate: тот же раннер, универсум с Gate
    for vid, bdir in sorted(base.items()):
        rd = os.path.join(a.run, "grid_" + vid)
        cls = [d for d in os.listdir(rd) if os.path.isdir(os.path.join(rd, d))][0]
        r = {}
        for fn in ("tab.npy", "act.npy", "hist.npy", "minnet.npy"):
            x, y = np.load(os.path.join(rd, cls, fn)), np.load(os.path.join(bdir, cls, fn))
            r[fn] = bool(x.shape == y.shape and x.dtype == y.dtype and np.array_equal(x, y, equal_nan=True))
        for fn in ("pids.json", "pairmeta.json", "counters.json"):
            r[fn] = json.load(open(os.path.join(rd, cls, fn), encoding="utf-8")) == json.load(open(os.path.join(bdir, cls, fn), encoding="utf-8"))
        t1 = open(os.path.join(rd, cls, "trades.csv"), "rb").read()
        t2 = open(os.path.join(bdir, cls, "trades.csv"), "rb").read()
        r["trades.csv"] = t1 == t2
        r["trades_n"] = t1.count(b"\n") - 1
        r["combos.json"] = json.load(open(os.path.join(rd, "combos.json"))) == json.load(open(os.path.join(bdir, "combos.json")))
        m1, m2 = json.load(open(os.path.join(rd, "meta.json"))), json.load(open(os.path.join(bdir, "meta.json")))
        r["meta"] = all(m1[k] == m2[k] for k in ("d0", "d1", "first_sig_day", "cfg", "pairs", "components", "fields"))
        g1, g2 = AG.Grid(rd, cls), AG.Grid(bdir, cls)
        rows = np.arange(len(g1.pids))
        s1, s2 = AG.y_series(g1, 0, rows), AG.y_series(g2, 0, rows)
        i1, i2 = AG.interval(s1["primary"]), AG.interval(s2["primary"])
        r["series_equal"] = bool(s1["primary"] == s2["primary"] and s1["closed"] == s2["closed"] and s1["days"] == s2["days"])
        r["interval_equal"] = bool(all(i1[k] == i2[k] for k in i1))
        r["n_days"], r["mean_yd"] = i1["n"], i1["mean"]
        r["ok"] = all(v for k, v in r.items() if isinstance(v, bool))
        allok = allok and r["ok"]
        rep[vid] = r
        print("%s %s: %s; сделок %d; y_d среднее %.6f по %d суткам" % (gname, vid, "СОВПАЛО до бита" if r["ok"] else "РАСХОЖДЕНИЕ", r["trades_n"], i1["mean"], i1["n"]), {k: v for k, v in r.items() if v is False})
    jdump(os.path.join(a.run, "g_c2_9wg.json" if gname == "G-C2-9wg" else "g_c2_9w.json"), dict(ok=allok, gate=gname, variants=rep))
    print("%s итог:" % gname, "PASS" if allok else "FAIL")
    return 0 if allok else 1


# ------------------------------------------------------------------ make-spec (формирование), token-body, token-check
def _a3b_text(det):
    h0, h1 = det["H0"], det["H1"]
    return "H0->H1 %+.3f (%d сделок); H1->H0 %+.3f (%d)" % (h0["mean"], h0["trades"], h1["mean"], h1["trades"])


def _spec_variants(c2dir, cls):
    """V1 (с Q5) и V2 (без Q5) из выходов калибровки <c2dir>/out и <c2dir>/out_noq5 (числа берутся из файлов, не выбираются)."""
    variants = []
    for vid, sub, use_q5 in (("V1", "out", True), ("V2", "out_noq5", False)):
        od = os.path.join(c2dir, sub, "calib")
        combo = json.load(open(os.path.join(od, "combo_full_%s.json" % cls), encoding="utf-8"))[0]
        gr = AG.Grid(os.path.join(od, "grid_full"), cls)
        ci = gr.cindex[tuple(combo)]
        s = AG.y_series(gr, ci, np.arange(len(gr.pids)), days_from=2472)["primary"]
        sel = json.load(open(os.path.join(c2dir, sub, "k7", "selection_%s.json" % cls), encoding="utf-8"))
        det = sel["a3b_detail"]["detail"]
        cells = sel.get("cells") or []
        a3 = sum(1 for c in cells if c["A"][2])
        fl = dict(a3b=dict(ok=bool(all(sel["a3b"])), text="формирование: A3b %s" % ("пройдено (" if all(sel["a3b"]) else "не пройдено (") + _a3b_text(det) + ")"),
                  a3_cells=dict(ok=bool(a3 > 0 and a3 == len(cells)), text="формирование: A3 по клеткам %s (%d из %d)" % ("пройдено" if a3 == len(cells) and a3 > 0 else "не пройдено", a3, len(cells))),
                  a8=dict(ok=bool(all(sel["a8"].values())), text="формирование: A8 %s (%d бирж из %d)" % ("пройдено" if all(sel["a8"].values()) else "не пройдено", sum(sel["a8"].values()), len(sel["a8"]))))
        a3b = {}
        for name, h in (("h1on0", "H0"), ("h0on1", "H1")):
            a3b[name] = dict(combo=json.load(open(os.path.join(od, "combo_%s_%s.json" % (h, cls)), encoding="utf-8"))[0], classpar=os.path.join(od, "classpar_%s.json" % h))
        variants.append(dict(id=vid, cls=cls, combo=combo, classpar=os.path.join(od, "classpar_full.json"), q5=(os.path.join(od, "q5_full.json") if use_q5 else None),
                             sigma_form=statistics.stdev(s), sigma_form_src="std суточного ряда y_d (закрытые + MTM) по суткам 2472..2478 (n=%d) из %s" % (len(s), os.path.join(od, "grid_full")),
                             a3b=a3b, formation_labels=fl))
    return variants


def cmd_make_spec(a):
    """spec V1/V2 по выходам калибровки (формирование; токен не нужен, панели не читаются). Числа берутся из файлов калибровки, не выбираются: комбинации - combo_full_<класс>.json,
    числа половин для A3b окна - combo_H0/H1 и classpar_H0/H1, σ_форм - std ряда y_d варианта по суткам 2472..2478 из grid_full (P-37 §6.6: вторая половина формирования),
    метки формирования - selection_<класс>.json (a3b_detail, a8, допуск клеток). --c2: набор окна («без Gate»: <c2>/out, <c2>/out_noq5); --gate-c2: набор «с Gate» -> блок spec[\"gate\"]
    (описательные строки вне M_окно). addendum - addendum, закоммиченный c2_git_ok (слот git-токена); --addendum-nogate - addendum набора окна для квартилей «каток» и формирования."""
    cls = a.cls
    spec = dict(name="P-38 r3, окно: класс %s, варианты V1 (с Q5) и V2 (без Q5)" % cls, note="spec по §17 r3 (07.11); числа из выходов калибровки (формирование)", root=G.HOME,
                raw_root=os.path.expanduser("~/xdl/out"), params=a.params, addendum=a.addendum, params_r3=a.params_r3, w2_jobs=a.jobs, w2_k_ref=a.kref, variants=_spec_variants(a.c2, cls))
    if a.addendum_nogate:
        spec["addendum_nogate"] = a.addendum_nogate
    if a.protocol:
        spec["protocol"] = a.protocol
    if a.gate_c2:
        spec["gate"] = dict(note="строки «с Gate»: описательные, вне M_окно и вне статусов (R3.7 п. 4); универсум с Gate, числа набора с Gate", addendum=a.addendum,
                            manifest=os.path.join(G.HOME, "gate_ready"), pairs=os.path.join(G.HOME, "asset_pairs_gate.csv"), w2_jobs=a.jobs_gate or a.jobs, variants=_spec_variants(a.gate_c2, cls))
    jdump(a.out, spec)
    print("spec записан:", a.out, [(v["id"], v["combo"], round(v["sigma_form"], 4), v["formation_labels"]["a3b"]["text"]) for v in spec["variants"]],
          [(v["id"], v["combo"], round(v["sigma_form"], 4)) for v in spec["gate"]["variants"]] if a.gate_c2 else "")
    return 0


def _cfg_for_token(a):
    spec = load_spec(a.spec)
    inputs = collect_inputs(spec, a.spec)
    G.configure(mode="calib", params_paths=params_tuple(spec), extra_inputs=inputs, script_path=os.path.abspath(__file__))
    return spec


def cmd_token_body(a):
    _cfg_for_token(a)
    sys.stdout.write(G.token_text(a.label))
    return 0


def cmd_token_check(a):
    _cfg_for_token(a)
    why = G.check_window_token()
    print("токен: OK (манифест совпал)" if why is None else "токен: ОТКАЗ - %s" % why)
    return 0 if why is None else 3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "gate9w", "make-spec", "token-body", "token-check"])
    ap.add_argument("--spec", default=None)
    ap.add_argument("--go-window", action="store_true")
    ap.add_argument("--test-boundary", type=int, default=None)
    ap.add_argument("--test-world", default=None)
    ap.add_argument("--last-day", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--limit-pairs", type=int, default=0)
    ap.add_argument("--run", default=None)
    ap.add_argument("--base", action="append", default=[])
    ap.add_argument("--c2", default=os.path.join(G.HOME, "c2"))
    ap.add_argument("--cls", default="crypto")
    ap.add_argument("--params", default=None)
    ap.add_argument("--addendum", default=None)
    ap.add_argument("--params-r3", default=None, dest="params_r3")
    ap.add_argument("--jobs", default=None)
    ap.add_argument("--kref", default=None)
    ap.add_argument("--label", default="GO <дата>, CEO")
    ap.add_argument("--min-free-gb", type=float, default=MIN_FREE_GB, dest="min_free_gb", help="префлайт: свободно ГиБ на диске выходов, C2_HOME и ~ до захвата токена (по умолчанию %g)" % MIN_FREE_GB)
    ap.add_argument("--keep-work", action="store_true", dest="keep_work", help="не удалять кэш рядов (work/) и боковые панели оборота (turn/) после прогона")
    ap.add_argument("--gate-c2", default=None, dest="gate_c2", help="make-spec: каталог набора с Gate (<dir>/out, <dir>/out_noq5) -> блок spec[gate]")
    ap.add_argument("--addendum-nogate", default=None, dest="addendum_nogate", help="make-spec: addendum набора окна «без Gate» (квартили «каток» и метки формирования основного набора)")
    ap.add_argument("--jobs-gate", default=None, dest="jobs_gate", help="make-spec: jobs_w2.csv блока Gate")
    ap.add_argument("--protocol", default=None, help="make-spec: файл протокола (входит в токен)")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        if a.spec is None and not (a.go_window or a.test_boundary is not None):
            raise G.Refused("не задан режим: --go-window или --test-boundary D")
        refuse_modes(a)                                        # отказ раньше чтения spec (красные тесты: без spec и данных)
        if a.spec is None:
            ap.error("--spec обязателен")
        return cmd_run(a)
    if a.cmd == "gate9w":
        return cmd_gate9w(a)
    if a.cmd == "make-spec":
        return cmd_make_spec(a)
    if a.spec is None:
        ap.error("--spec обязателен")
    return cmd_token_body(a) if a.cmd == "token-body" else cmd_token_check(a)


if __name__ == "__main__":
    sys.exit(main())
