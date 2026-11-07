#!/usr/bin/env python3
"""TK-493, П-38 r2: запуск счёта C2 («c2_run»). Все чтения данных идут через conv_guard (защита окна §12.2). sha256 ЭТОГО файла входит в токен окна.

  python conv_run.py git-token [--addendum REL] [--params-r3 REL]   (на ПК, в git) записать c2_git_ok для копии на Deck (params, протокол, addendum, r3 закоммичены и чисты)
  python conv_run.py k0 [--gate-ready PATH]                 K0: записать $C2_HOME/calib_start.json (правило Gate) ДО чтения панелей формирования
  python conv_run.py series --out DIR --last-day 2478 [--L 1440] [--classes crypto,eq,metal] [--limit-pairs N] [--threads 8]
                                                            K1-K3: ряды s, b, sigma, r6, общий фактор c ячеек, мета пар -> кэш <out>/cache/L<L>/
  python conv_run.py grid --out DIR --tag TAG --last-day 2478 [--classpar F.json] [--q5 F.json] [--combos F.json|all] [--limit-pairs N] [--trades-out]
                                                            сетка комбинаций по компонентам -> <out>/grid_<tag>/<класс>/*.npy (+ json)
Режимы защиты: --mode calib (по умолчанию; last_day <= 2478), train (Pi_train, last_day <= 2471); window отключён: окно читает только conv_window.py (токен CEO).
--test-world DIR: синтетический мир (каталог не внутри ~/xdl9); боевые пути в этом режиме недоступны.
Gate (G1, П-38 §3.4): --with-gate (нужен валидный манифест gate_ready и таблица пар Gate, иначе отказ) / --no-gate (универсум 8 бирж, побайтно как раньше); без флага Gate входит в универсум
только при валидном манифесте (gate_ready: шесть строк ok, sha256 справочника и списка панелей) и наличии таблицы пар `<root>/asset_pairs_gate.csv`.
"""
import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_guard as G      # noqa: E402
import conv_lib as L        # noqa: E402
import conv_world as W      # noqa: E402
import conv_grid as GR      # noqa: E402
import conv_calib as CAL    # noqa: E402

REPO_PARAMS = "docs/research/P-38-params-2026-11-07.csv"
_CTX = {}
_PROBE = [False]
_TURN = [None]
t_meas = [0.0]


def make_cfg(a):
    return dict(L=a.L, step=5, nb=a.nb, ls=60, nbs=20, dedup=not a.no_dedup, delay=a.delay, entry_mode=a.entry_mode, exit_mode=a.exit_mode,
                ceiling=not a.no_ceiling, use_q6=not a.no_q6)


def class_params(path):
    out = {c: L.ClassParams(name=c) for c in W.MAIN_CLASSES}
    if path:
        for c, d in json.load(open(path, encoding="utf-8")).items():
            d = dict(d)
            for k in ("gmax", "gsig_max", "q_c", "m_shift"):
                if k in d and d[k] is None:
                    d[k] = math.inf
            if d.get("a90_edges") is not None:
                d["a90_edges"] = np.array(d["a90_edges"], float)
                d["a90_vals"] = np.array([np.nan if v is None else v for v in d["a90_vals"]], float)
            d.pop("session", None)
            out[c] = L.ClassParams(name=c, **d)
    return out


def select_pairs(world, a):
    pairs = [p for p in world.universe(check_sha=not a.test_world) if world.in_main_series(p)]
    if a.classes:
        keep = set(a.classes.split(","))
        pairs = [p for p in pairs if p["cls"] in keep]
    if a.pairs_file:
        want = set(open(a.pairs_file, encoding="utf-8").read().split())
        pairs = [p for p in pairs if p["pid"] in want]
    if a.limit_pairs:
        # детерминированная подвыборка: первые N по pid внутри каждой ячейки не берём - берём все пары первых активов (по key), пока не наберётся N
        keys = sorted({p["key"] for p in pairs})
        take, n = set(), 0
        cnt = {}
        for p in pairs:
            cnt[p["key"]] = cnt.get(p["key"], 0) + 1
        for k in keys:
            if n >= a.limit_pairs:
                break
            take.add(k)
            n += cnt[k]
        pairs = [p for p in pairs if p["key"] in take]
    return pairs


def _init(root, test_world, calendar, mode, go_window, params, script, test_root, cfgd, out, cpd, combos, d0, d1, p0day, q5, want_trades, want_turn):
    G.configure(go_window=go_window, script_path=script, params_paths=params, mode=mode, require_k0=not (test_world or _PROBE[0]), test_root=test_root)
    G.STATE.git_checked = bool(test_world)
    _CTX.update(world=W.World(root, test=test_world, calendar=calendar, turn_dir=_TURN[0]), cfgd=cfgd, out=out, cpd=cpd, combos=combos, d0=d0, d1=d1, p0day=p0day, q5=q5,
                want_trades=want_trades, want_turn=want_turn)


def _series_task(args):
    cell, pairs = args
    cx = _CTX
    return cell, W.series_cell(cx["world"], cell, pairs, cx["d0"], cx["d1"], L.Cfg(**cx["cfgd"]), cx["out"])


def _grid_task(pairs):
    cx = _CTX
    return GR.run_component(pairs, cx["world"], cx["out"], cx["cfgd"], cx["cpd"], cx["combos"], cx["d0"], cx["d1"], cx["p0day"], cx["q5"], cx["want_trades"],
                            want_turn=cx["want_turn"])


def _meas_task(pairs):
    cx = _CTX
    cfg = L.Cfg(**cx["cfgd"])
    world = cx["world"]
    cells = {}
    pss = []
    for p in pairs:
        if p["cell"] not in cells:
            cells[p["cell"]] = W.load_cell_c(cx["out"], p["cell"], cfg.L)
        ps, _ = W.load_series(world, p, cx["d0"], cx["d1"], cfg, cx["out"], cells[p["cell"]][0], want_turn=cx["want_turn"], want_fund=False)
        pss.append(ps)
    cp = cx["cpd"][pairs[0]["cls"]]
    return pairs[0]["cls"], CAL.collect_episodes(pss, cp, cfg, (cx["p0day"] - cx["d0"]) * 1440)


def cmd_measure(a, world, pairs, cfgd, initargs, d0, d1, p0day):
    """K4/K5: структурные числа класса и допуск по полураспаду. Выход: structural_<pass>.json, classpar_<pass>.json, q5_<pass>.json в --out."""
    tag = "train" if a.mode == "train" else "full"
    last_idx = (d1 - d0 + 1) * 1440
    cfg = L.Cfg(**cfgd)
    cpd = {}
    structural = {}
    q5 = {}
    for cls in W.MAIN_CLASSES:
        pc = [p for p in pairs if p["cls"] == cls]
        if not pc:
            continue
        mdir = W.cache_dirs(a.out, cfg.L)[2]
        metas = [json.load(open(os.path.join(mdir, p["pid"] + ".json"), encoding="utf-8")) for p in pc]
        day_idx = range(p0day - d0, d1 - d0 + 1)
        for p, m in zip(pc, metas):
            q5[p["pid"]] = CAL.hl15_from_days(m["hl15_days"], day_idx)
        k_st = CAL.k_stale_from_meta(metas, tag)
        m_r = CAL.m_r_from_meta(metas, tag)
        q_st = CAL.stale_fraction_quantile(metas, tag)
        cells = sorted({p["cell"] for p in pc})
        q_c = CAL.c_quantiles(a.out, cells, cfg.L, last_idx)
        m_sh = CAL.shift_m(a.out, [p["pid"] for p in pc], cfg.L, last_idx)
        cpd[cls] = L.ClassParams(name=cls, kstale=k_st or 60, m_r=m_r or 5, q_c=q_c if q_c is not None else math.inf, m_shift=m_sh if m_sh is not None else math.inf,
                                 q_stale=q_st if q_st is not None else 0.9)
        structural[cls] = dict(k_stale=k_st, m_r=m_r, q_stale=q_st, q_c=q_c, m_shift=m_sh, pairs=len(pc), pass_tag=tag)
    initargs = list(initargs)
    initargs[10] = cpd
    tasks = []
    for cls in W.MAIN_CLASSES:
        tasks += W.components([p for p in pairs if p["cls"] == cls])
    res = pool_map(_meas_task, tasks, a.threads, tuple(initargs))
    byc = {}
    for cls, eps in res:
        byc.setdefault(cls, []).extend(eps)
    classpar = {}
    for cls, eps in sorted(byc.items()):
        g = CAL.gmax_rule(eps)
        structural[cls]["gmax_rule"] = g
        cp = cpd[cls]
        classpar[cls] = dict(gmax=g.get("gmax"), gsig_max=g.get("gsig_max"), a90_edges=g.get("a90_edges"), a90_vals=g.get("a90_vals"), kstale=cp.kstale, q_c=cp.q_c if math.isfinite(cp.q_c) else None,
                             m_shift=cp.m_shift if math.isfinite(cp.m_shift) else None, m_r=cp.m_r, q_stale=cp.q_stale)
        if g.get("empty_range"):
            classpar[cls]["gmax"] = g.get("gmax")
    CAL.write_json(os.path.join(a.out, "structural_%s.json" % a.tag), structural)
    CAL.write_json(os.path.join(a.out, "classpar_%s.json" % a.tag), classpar)
    CAL.write_json(os.path.join(a.out, "q5_%s.json" % a.tag), {k: v.get("hl_hi") for k, v in q5.items()})
    CAL.write_json(os.path.join(a.out, "q5_detail_%s.json" % a.tag), q5)
    print("measure %s: классы %s, эпизодов %s, %.1f с" % (tag, sorted(byc), {c: len(e) for c, e in byc.items()}, time.time() - t_meas[0]))
    return 0



def pool_map(fn, tasks, threads, initargs):
    return list(pool_iter(fn, tasks, threads, initargs))


def pool_iter(fn, tasks, threads, initargs):
    """Результаты задач по одному, в порядке задач (imap): память не растёт с числом задач (потоковая запись сетки, 07.11 отступление R-MEM)."""
    if threads <= 1:
        _init(*initargs)
        for t in tasks:
            yield fn(t)
        return
    ctx = mp.get_context("fork")
    with ctx.Pool(threads, initializer=_init, initargs=initargs) as pool:
        for r in pool.imap(fn, tasks, chunksize=1):
            yield r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["git-token", "k0", "series", "measure", "grid", "turn", "turn-verify"])
    ap.add_argument("--root", default=G.HOME)
    ap.add_argument("--test-world", default=None)
    ap.add_argument("--out", default=os.path.join(G.HOME, "audit3", "conv", "out"))
    ap.add_argument("--tag", default="x")
    ap.add_argument("--mode", default="calib", choices=["calib", "train", "window"])
    ap.add_argument("--go-window", action="store_true")
    ap.add_argument("--last-day", type=int, default=None)
    ap.add_argument("--first-sig-day", type=int, default=None)
    ap.add_argument("--L", type=int, default=1440)
    ap.add_argument("--nb", type=int, default=240)
    ap.add_argument("--classes", default="")
    ap.add_argument("--limit-pairs", type=int, default=0)
    ap.add_argument("--pairs-file", default=None)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--classpar", default=None)
    ap.add_argument("--q5", default=None)
    ap.add_argument("--combos", default="all")
    ap.add_argument("--trades-out", action="store_true")
    ap.add_argument("--no-dedup", action="store_true")
    ap.add_argument("--no-ceiling", action="store_true")
    ap.add_argument("--no-q6", action="store_true")
    ap.add_argument("--no-turn", action="store_true")
    ap.add_argument("--delay", type=int, default=1)
    ap.add_argument("--entry-mode", default="defer", choices=["defer", "drop", "close"])
    ap.add_argument("--exit-mode", default="ffill", choices=["ffill", "wait"])
    ap.add_argument("--calendar", default=None)
    ap.add_argument("--params", default=None)
    ap.add_argument("--addendum", default=None)
    ap.add_argument("--params-r3", default=None, help="путь (в git: относительный) params r3 для git-token")
    ap.add_argument("--gate-ready", default=None, help="k0: путь маркера; прочее: путь манифеста Gate (по умолчанию <root>/gate_ready)")
    W.add_gate_args(ap, ready=False)
    ap.add_argument("--verify-ex", default="", help="turn-verify: сверять только ноги этой биржи (например gate)")
    ap.add_argument("--verify-n", type=int, default=12, help="turn-verify: число символов")
    ap.add_argument("--turn-dir", default=None)
    ap.add_argument("--raw-root", default=os.path.expanduser("~/xdl/out"))
    ap.add_argument("--probe", action="store_true", help="технический замер скорости на формировании: без K0, вывод только под <out>/probe, пишет probe_reads.log")
    a = ap.parse_args(argv)
    W.apply_gate_args(a)
    if a.mode == "window":
        # дельта 07.11 (закрытие B1): окно читает ЕДИНСТВЕННЫЙ раннер conv_window.py; через conv_run режим window отключён (токен покрывает весь код одним путём)
        raise G.Refused("окно читает только conv_window.py (conv_run.py --mode window отключён)")
    script = os.path.abspath(__file__)
    _PROBE[0] = bool(a.probe)
    repo_params = a.params or os.path.join(G.REPO_HINT, REPO_PARAMS)
    if not os.path.isfile(repo_params):
        alt = os.path.join(HERE, "..", "P-38-params-2026-11-07.csv")
        repo_params = alt if os.path.isfile(alt) else os.path.join(G.HOME, "audit3", "conv", "code", "P-38-params-2026-11-07.csv")
    pparams = (repo_params, a.addendum)
    test_world = a.test_world
    root = os.path.abspath(test_world) if test_world else a.root
    mode = "test" if test_world else a.mode
    G.configure(go_window=a.go_window, script_path=script, params_paths=pparams, mode=("window" if (a.mode == "window" and not test_world) else (mode)),
                require_k0=not (test_world or a.probe), test_root=root if test_world else None)
    if a.cmd == "git-token":
        print(json.dumps(G.write_git_token(a.addendum, a.params_r3), indent=1))
        return 0
    if a.cmd == "k0":
        G.configure(mode="calib", params_paths=pparams, script_path=script)
        print(json.dumps(G.write_k0(a.gate_ready, pparams), indent=1, ensure_ascii=False))
        return 0
    if a.last_day is None:
        ap.error("--last-day обязателен")
    a.out = os.path.join(a.out, a.mode)           # кэш и выходы каждого конвейера (calib / train / window) раздельно: Pi_train не видит кэш с поздними сутками
    if not test_world:
        G.check_days(a.last_day, what="запуск %s" % a.cmd)           # отказ ДО любого чтения (код 3)
        G.require_k0()
        G.ensure_params_committed()
        if a.probe:
            if a.mode == "window" or a.last_day >= G.D_WIN0:
                raise G.Refused("--probe только для формирования")
            os.makedirs(G.c2_home(), exist_ok=True)
            with open(os.path.join(G.c2_home(), "probe_reads.log"), "a", encoding="utf-8") as lf:
                lf.write(chr(9).join([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), a.cmd, "last_day=%d" % a.last_day]) + chr(10))
    world = W.World(root, test=bool(test_world), calendar=a.calendar, turn_dir=a.turn_dir)
    gs = world.gate_state()
    gate_on = bool(gs["active"])
    print("Gate: %s (%s)" % ("в универсуме" if gate_on else "вне универсума", "манифест валиден" if gate_on else "; ".join(gs["reasons"])))
    _TURN[0] = a.turn_dir
    d0 = G.D_FORM0
    d1 = a.last_day
    p0day = a.first_sig_day if a.first_sig_day is not None else (G.D_WIN0 if (a.mode == "window") else G.D_SIG0)
    cfgd = make_cfg(a)
    pairs = select_pairs(world, a)
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    initargs = (root, bool(test_world), a.calendar, ("test" if test_world else a.mode), a.go_window, pparams, script, root if test_world else None, cfgd, a.out, None,
                None, d0, d1, p0day, None, False, not a.no_turn)
    t_meas[0] = t0
    if a.cmd == "turn":
        n = W.build_turnover(world, pairs, d0, d1, a.raw_root, outdir=a.turn_dir or os.path.join(a.out, "turn"))
        print("turn: боковых панелей оборота %d, %.1f с" % (n, time.time() - t0))
        return 0
    if a.cmd == "turn-verify":
        n, mx, bad = W.verify_turnover(world, a.raw_root, d0, d1, pairs, n_sym=a.verify_n, only_ex=a.verify_ex or None)
        print("G-C2-7%s: проверено символов %d, максимум относительного расхождения %.3g, расхождений %d" % (" (%s)" % a.verify_ex if a.verify_ex else "", n, mx, len(bad)))
        return 0 if (n >= (a.verify_n if a.verify_ex else 10) and not bad) else 1
    if a.cmd == "measure":
        return cmd_measure(a, world, pairs, cfgd, initargs, d0, d1, p0day)
    if a.cmd == "series":
        cells = {}
        for p in pairs:
            cells.setdefault(p["cell"], []).append(p)
        tasks = sorted(cells.items())
        res = pool_map(_series_task, tasks, a.threads, initargs)
        npairs = sum(len(v) for _, v in tasks)
        json.dump({"cells": len(tasks), "pairs": npairs, "seconds": round(time.time() - t0, 1), "last_day": d1, "L": a.L, "gate": gate_on}, open(os.path.join(a.out, "series_summary.json"), "w"))
        print("series: ячеек %d, пар %d, %.1f с (%.2f пар/с)" % (len(tasks), npairs, time.time() - t0, npairs / max(time.time() - t0, 1e-9)))
        return 0
    # grid
    combos = GR.grid_combos() if a.combos == "all" else [L.Combo(*c) for c in json.load(open(a.combos, encoding="utf-8"))]
    cpd = class_params(a.classpar)
    q5 = json.load(open(a.q5, encoding="utf-8")) if a.q5 else None
    tasks = []
    for cls in W.MAIN_CLASSES:
        pc = [p for p in pairs if p["cls"] == cls]
        tasks += W.components(pc)
    initargs = (root, bool(test_world), a.calendar, ("test" if test_world else a.mode), a.go_window, pparams, script, root if test_world else None, cfgd, a.out, cpd,
                combos, d0, d1, p0day, q5, a.trades_out, not a.no_turn)
    odir = os.path.join(a.out, "grid_" + a.tag)
    nd = d1 - d0 + 1
    nC = len(combos)
    npairs_cls = {}
    for t in tasks:
        npairs_cls[t[0]["cls"]] = npairs_cls.get(t[0]["cls"], 0) + len(t)
    acc = {}
    # потоковая запись (отступление R-MEM 07.11): результат компоненты пишется в заранее выделенные файлы .npy сразу; содержимое файлов то же, что при записи после сбора
    for r, t in zip(pool_iter(_grid_task, tasks, a.threads, initargs), tasks):
        cls = t[0]["cls"]
        st = acc.get(cls)
        if st is None:
            d = os.path.join(odir, cls)
            os.makedirs(d, exist_ok=True)
            st = acc[cls] = dict(d=d, off=0, hist=0, minnet=None, pids=[], keys=[], cells=[], exch=[], cn=[dict() for _ in range(nC)], tf=None,
                                 tab=np.lib.format.open_memmap(os.path.join(d, "tab.npy"), mode="w+", dtype=np.float64, shape=(nC, npairs_cls[cls], nd + 1, GR.NF)),
                                 act=np.lib.format.open_memmap(os.path.join(d, "act.npy"), mode="w+", dtype=np.uint8, shape=(nC, npairs_cls[cls], nd)))
            if a.trades_out:
                st["tf"] = open(os.path.join(d, "trades.csv"), "w", encoding="utf-8", newline=chr(10))
                st["tf"].write("combo,pid,key," + ",".join(L.TRADE_FIELDS) + chr(10))
        n = len(t)
        st["tab"][:, st["off"]:st["off"] + n] = r["tab"]
        st["act"][:, st["off"]:st["off"] + n] = r["act"]
        st["off"] += n
        st["hist"] = st["hist"] + r["hist"]
        st["minnet"] = r["minnet"] if st["minnet"] is None else np.minimum(st["minnet"], r["minnet"])
        st["pids"] += r["pids"]
        st["keys"] += r["keys"]
        st["cells"] += r["cells"]
        st["exch"] += r["exch"]
        for ci in range(nC):
            tot = st["cn"][ci]
            for k, v in r["cnt"][ci].items():
                tot[k] = tot.get(k, 0) + v
        if st["tf"] is not None:
            for tr in r["trades"]:
                st["tf"].write(",".join(("%.9g" % v) if isinstance(v, float) else str(v) for v in tr) + chr(10))
    for cls, st in acc.items():
        d = st["d"]
        st["tab"].flush()
        st["act"].flush()
        del st["tab"], st["act"]
        np.save(os.path.join(d, "hist.npy"), st["hist"])
        np.save(os.path.join(d, "minnet.npy"), st["minnet"])
        json.dump(st["pids"], open(os.path.join(d, "pids.json"), "w"))
        json.dump({"keys": st["keys"], "cells": st["cells"], "exch": st["exch"]}, open(os.path.join(d, "pairmeta.json"), "w"))
        json.dump(st["cn"], open(os.path.join(d, "counters.json"), "w"), sort_keys=True)
        if st["tf"] is not None:
            st["tf"].close()
    json.dump([list(c) for c in combos], open(os.path.join(odir, "combos.json"), "w"))
    json.dump({"fields": GR.DF, "d0": d0, "d1": d1, "first_sig_day": p0day, "cfg": cfgd, "pairs": len(pairs), "components": len(tasks), "gate": gate_on,
               "seconds": round(time.time() - t0, 1)}, open(os.path.join(odir, "meta.json"), "w"), sort_keys=True)
    print("grid: пар %d, компонент %d, комбинаций %d, %.1f с" % (len(pairs), len(tasks), len(combos), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
