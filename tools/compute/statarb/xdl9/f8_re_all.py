#!/usr/bin/env python3
"""TK-493 S3 (П-37 §11.3): прогон «случайных входов» (f8_randentry.py) по всем группам основного счёта F8 на ПК. Один процесс (numpy); панели группы переносятся
с Deck tar-пайпом во временный каталог и удаляются после группы. Читает торговое окно (панели) - запускать только после основного счёта F8 и только по команде CEO.

  python f8_re_all.py --mode trade --out <каталог re> [--only REGEX] [--stage w1,w2,w3] [--dry]
  python f8_re_all.py --mode form  --out <каталог re> --only REGEX         (сухой прогон на формировании: панели ~/xdl9/form, факт - ~/xdl9/sel/f8_dry/out)
  python f8_re_all.py --local --groups f8/re_groups.tsv --panels ~/xdl9/trade --funding ~/xdl9/funding --out out/re [--procs 8] [--only REGEX]
                                                                           (w5 F: НА DECK, без ssh и tar; рабочий каталог - F8_CWD; ssh-режим выше устарел)

Режим --local (стадия `re` списка единиц F8): панели и фандинг читаются прямо из каталогов команды (~/xdl9/{trade,funding}; form: ~/xdl9/{form,funding_form}), список групп, задания и клетки -
из рабочего каталога (re_groups.tsv пишет f8_lists.gen; трейд: `w1/…`; form: `f8form/main/…`), сделки факта - `out/wN/<имя>[__shardN].trades.csv`, шарды D2 - `d2/<имя>/shardN*`. Группы идут пулом процессов (--procs), результат группы пишется
атомарно (`.part` -> rename) и закрывается меткой `.done_<имя>`: упавший запуск возобновляется без пересчёта готовых групп. Все группы основного счёта (w1-w3).

Источники группы: задания и клетки S1 (docs/findings/xdl9-selection-2026-11-06/waves), сделки факта - с Deck (`out/wN/<имя>.trades.csv`, у D2 - `…__shardN.trades.csv`),
ряд d и списки заданий D2 - `d2/<имя>/shardN*`. Нужны ssh-ключ и known_hosts (переменные F8_SSH_KEY, F8_SSH_KNOWN) и хост deck@192.168.1.49 (F8_DECK).
Выход: `<out>/<имя>.jobday.csv` (+ `.trades.csv` с --out-trades); агрегатор читает `<run>/re/*.jobday.csv`.
"""
import os, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import f8_lists as L          # noqa: E402
import f8_randentry as RE     # noqa: E402

DECK = os.environ.get("F8_DECK", "deck@192.168.1.49")
KEY = os.environ.get("F8_SSH_KEY", os.path.expanduser("~/.ssh/id_rsa"))
KNOWN = os.environ.get("F8_SSH_KNOWN", os.path.expanduser("~/.ssh/known_hosts"))
SSH = ["ssh", "-i", KEY, "-o", "UserKnownHostsFile=" + KNOWN, "-o", "ConnectTimeout=20", DECK]


def deck_sh(cmd):
    return subprocess.run(SSH + [cmd], capture_output=True, text=True, check=False)


def fetch(paths, dest, base="~/xdl9"):
    """tar-пайп: файлы (относительно base, символьные ссылки разыменовываются) -> dest; список передаётся через stdin."""
    if not paths:
        return
    os.makedirs(dest, exist_ok=True)
    lst = "\n".join(paths) + "\n"
    p1 = subprocess.Popen(SSH + ["cd %s && tar -h -cf - -T - --ignore-failed-read 2>/dev/null" % base], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["tar", "-C", dest, "-xf", "-"], stdin=p1.stdout)
    p1.stdout.close()
    p1.stdin.write(lst.encode("utf-8"))
    p1.stdin.close()
    p2.wait()
    p1.wait()


def one_group(g, M, out_dir, tmp, out_trades=False):
    st, kind, exy, exx = g["stem"], g["kind"], g["exy"], g["exx"]
    wdir = "w%d" % g["wave"]
    root = tmp
    pan = {"form": "form", "trade": "trade"}[M["mode"]]
    fund = "funding_form" if M["mode"] == "form" else "funding"
    outroot = "sel/f8_dry/out" if M["mode"] == "form" else "sel/waves/out"
    if kind == "D2":
        jobs_rows = L.read_jobs(os.path.join(L.WAVES, g["jobs_file"]), ["y", "x"])
        shards = deck_sh("ls -d %s/%s/shard[0-9]* 2>/dev/null | sed 's#.*/##' | grep -v jobs.csv" % (
            ("~/xdl9/sel/f8_dry/d2" if M["mode"] == "form" else "~/xdl9/sel/waves/d2"), st)).stdout.split()
        d2root = "sel/f8_dry/d2" if M["mode"] == "form" else "sel/waves/d2"
        units = []
        for sh in shards:
            jf = "%s/%s/%s.jobs.csv" % (d2root, st, sh)
            units.append(dict(shard=sh, jobs=jf, trades="%s/%s/%s__%s.trades.csv" % (outroot, wdir, st, sh), fund="%s/%s/%s" % (d2root, st, sh)))
    else:
        units = [dict(shard=None, jobs=None, trades="%s/%s/%s.trades.csv" % (outroot, wdir, st), fund=None)]
    produced = 0
    for u in units:
        lf = os.path.join(root, "work")
        os.makedirs(lf, exist_ok=True)
        # задания: у D2 - файл шарда с Deck, иначе S1 (сутки по режиму)
        if u["jobs"]:
            fetch([u["jobs"]], root, base="~/xdl9")
            jobs_path = os.path.join(root, u["jobs"])
        else:
            rows = L.read_jobs(os.path.join(L.WAVES, g["jobs_file"]), ["y", "x", "from_day", "to_day"])
            jobs_path = os.path.join(lf, st + ".jobs.csv")
            with open(jobs_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("y,x,from_day,to_day\n")
                for r in rows:
                    fh.write("%s,%s,%d,%d\n" % (r[0], r[1], M["D0"], M["D1"]))
        fetch([u["trades"]], root)
        tr = os.path.join(root, u["trades"])
        if not os.path.exists(tr) or os.path.getsize(tr) < 100:
            continue
        # панели ног и фандинг
        names_y, names_x = set(), set()
        with open(jobs_path, encoding="utf-8", newline="") as fh:
            import csv
            for r in csv.DictReader(fh):
                names_y.add(r["y"])
                names_x.update(r["x"].split("+"))
        need = []
        for ex, names in ((exy, names_y), (exx, names_x)) if exy != exx else ((exy, names_y | names_x),):
            need += ["%s/%s/%s.sap" % (pan, ex, n) for n in sorted(names)]
            if kind != "D2":
                need += ["%s/%s/%s.csv.gz" % (fund, ex, n) for n in sorted(names)]
        if kind == "D2":
            need += ["%s/%s/%s.csv.gz" % (u["fund"], "", n) for n in sorted(names_y)]
            need = [p.replace("//", "/") for p in need]
        fetch(need, root)
        cells = os.path.join(lf, st + ".cells.csv")
        if kind == "D2":
            with open(cells, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(L.HDR_CELLS + "\n%s|a0,%d,%d,,1.00,0,inf,0,%d\n" % (g["cell"], L.D2_WINDOW, L.D2_WINDOW, L.D2_WINDOW))
        else:
            shutil.copy(os.path.join(L.WAVES, g["cells_file"]), cells)
        a = dict(kind={"A1": "pair", "A2": "pair", "B1": "basket", "C1": "cross", "D2": "funding-pp"}[kind],
                 panels=os.path.join(root, pan, exy), panels_b=(os.path.join(root, pan, exx) if kind in ("C1", "D2") else None),
                 funding_y=(os.path.join(root, u["fund"]) if kind == "D2" else os.path.join(root, fund, exy)),
                 funding_x=(os.path.join(root, fund, exx) if kind == "C1" else None), cells=cells, jobs=jobs_path, real_trades=tr,
                 out=os.path.join(out_dir, "%s%s.jobday.csv" % (st, ("__" + u["shard"]) if u["shard"] else "")),
                 out_trades=(os.path.join(out_dir, "%s%s.trades.csv" % (st, ("__" + u["shard"]) if u["shard"] else "")) if out_trades else None),
                 delay_x=1 if kind == "C1" else 0, seed=RE.SEED, replay=False)
        os.makedirs(out_dir, exist_ok=True)
        RE.run(a)
        produced += 1
    return produced


# ------------------------------------------------------------------ Deck: локальный режим (w5 F)
def read_groups(path):
    """re_groups.tsv (пишет f8_lists.gen): stem, kind, cell, exy, exx, wave, jobs, cells -> список словарей (без обращения к docs репозитория - на Deck их нет)."""
    import csv
    with open(path, encoding="utf-8", newline="") as fh:
        return [dict(zip(("stem", "kind", "cell", "exy", "exx", "wave", "jobs", "cells"), r)) for r in list(csv.reader(fh, delimiter="\t"))[1:] if r]


def one_group_local(g, a):
    """Группа основного счёта: случайные входы по панелям на Deck. a - dict(panels, funding, out, out_trades). -> (имя, число записанных файлов)."""
    stem, out_dir, out_trades = g["stem"], a["out"], a["out_trades"]
    kind, exy, exx, cellname = g["kind"], g["exy"], g["exx"], g["cell"]
    pan, fund = os.path.expandvars(a["panels"]), os.path.expandvars(a["funding"])
    wdir = "w%d" % int(g["wave"])
    units = []
    if kind == "D2":
        d2root = os.path.join("d2", stem)
        for sh in sorted(x for x in os.listdir(d2root) if re.fullmatch(r"shard\d+", x)) if os.path.isdir(d2root) else []:
            units.append(dict(shard=sh, jobs=os.path.join(d2root, sh + ".jobs.csv"), fund=os.path.join(d2root, sh),
                              trades=os.path.join("out", wdir, "%s__%s.trades.csv" % (stem, sh))))
    else:
        units.append(dict(shard=None, jobs=g["jobs"], fund=None, trades=os.path.join("out", wdir, stem + ".trades.csv")))
    produced = 0
    os.makedirs(out_dir, exist_ok=True)
    for u in units:
        if not os.path.exists(u["trades"]) or os.path.getsize(u["trades"]) < 100 or not os.path.exists(u["jobs"]):
            continue
        with tempfile.TemporaryDirectory(prefix="f8re_") as td:
            if kind == "D2":
                cells = os.path.join(td, stem + ".cells.csv")
                with open(cells, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(L.HDR_CELLS + "\n%s|a0,%d,%d,,1.00,0,inf,0,%d\n" % (cellname, L.D2_WINDOW, L.D2_WINDOW, L.D2_WINDOW))
            else:
                cells = g["cells"]
            base = "%s%s" % (stem, ("__" + u["shard"]) if u["shard"] else "")
            tmp_out = os.path.join(out_dir, base + ".jobday.csv.part")
            a = dict(kind={"A1": "pair", "A2": "pair", "B1": "basket", "C1": "cross", "D2": "funding-pp"}[kind],
                     panels=os.path.join(pan, exy), panels_b=(os.path.join(pan, exx) if kind in ("C1", "D2") else None),
                     funding_y=(u["fund"] if kind == "D2" else os.path.join(fund, exy)),
                     funding_x=(os.path.join(fund, exx) if kind == "C1" else None), cells=cells, jobs=u["jobs"], real_trades=u["trades"],
                     out=tmp_out, out_trades=((os.path.join(out_dir, base + ".trades.csv.part")) if out_trades else None),
                     delay_x=1 if kind == "C1" else 0, seed=RE.SEED, replay=False)
            RE.run(a)
        os.replace(tmp_out, os.path.join(out_dir, base + ".jobday.csv"))
        if out_trades:
            os.replace(a["out_trades"], os.path.join(out_dir, base + ".trades.csv"))
        produced += 1
    with open(os.path.join(out_dir, ".done_" + stem + ".part"), "w") as fh:
        fh.write("files=%d\n" % produced)
    os.replace(os.path.join(out_dir, ".done_" + stem + ".part"), os.path.join(out_dir, ".done_" + stem))
    return stem, produced


def main_local(a):
    groups = read_groups(a.groups)
    todo = [g for g in groups if (not a.only or re.search(a.only, g["stem"])) and not os.path.exists(os.path.join(a.out, ".done_" + g["stem"]))]
    print("re_all --local: групп в списке %d, к счёту %d, процессов %d" % (len(groups), len(todo), a.procs), flush=True)
    t0 = time.time()
    opts = dict(panels=a.panels, funding=a.funding, out=a.out, out_trades=a.out_trades)
    args = [(g, opts) for g in todo]
    os.makedirs(a.out, exist_ok=True)
    if a.procs > 1 and len(args) > 1:
        from multiprocessing import Pool
        with Pool(min(a.procs, len(args))) as p:
            for n, (stem, k) in enumerate(p.imap_unordered(_star, args), 1):
                print("[%d/%d] %s: файлов %d" % (n, len(args), stem, k), flush=True)
    else:
        for i, x in enumerate(args):
            stem, k = one_group_local(*x)
            print("[%d/%d] %s: файлов %d" % (i + 1, len(args), stem, k), flush=True)
    print("re_all --local: готово за %.1f с" % (time.time() - t0))


def _star(x):
    return one_group_local(*x)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["trade", "form"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    ap.add_argument("--stage")
    ap.add_argument("--out-trades", action="store_true")
    ap.add_argument("--tmp", default=None)
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--procs", type=int, default=1)
    ap.add_argument("--groups")
    ap.add_argument("--panels")
    ap.add_argument("--funding")
    a = ap.parse_args()
    if a.local:
        if not (a.groups and a.panels and a.funding):
            raise SystemExit("--local: нужны --groups, --panels, --funding")
        return main_local(a)
    if not a.mode:
        raise SystemExit("нужен --mode")
    M = dict(L.MODES[a.mode], mode=a.mode)
    stages = set(a.stage.split(",")) if a.stage else {"w1", "w2", "w3"}
    todo = [g for g in L.load_groups() if ("w%d" % g["wave"]) in stages and (not a.only or re.search(a.only, g["stem"]))]
    print("re_all: групп %d, режим %s" % (len(todo), a.mode))
    for i, g in enumerate(todo):
        done = os.path.join(a.out, ".done_" + g["stem"])
        if os.path.exists(done):
            continue
        tmp = tempfile.mkdtemp(prefix="f8re_", dir=a.tmp)
        t0 = time.time()
        try:
            n = one_group(g, M, a.out, tmp, a.out_trades)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(a.out, exist_ok=True)
        open(done, "w").write("files=%d secs=%.1f\n" % (n, time.time() - t0))
        print("[%d/%d] %s: файлов %d, %.1f с" % (i + 1, len(todo), g["stem"], n, time.time() - t0))


if __name__ == "__main__":
    main()
