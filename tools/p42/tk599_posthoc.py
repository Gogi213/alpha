#!/usr/bin/env python3
"""П-42 §13.24.6 п. 4 (TK-599): пост-фактум аудит бинаря движка. Бинарём `a03c91df` пересчитываются по всем трём окнам: замороженная конфигурация каждой стороны
(итог полной процедуры), победитель каждой из четырёх свёрток каждой стороны и 10 случайных клеток из `npz`, посчитанных новым бинарём (зерно — sha256 файла результатов
процедуры) + одна контрольная клетка старого бинаря (проверка самого сравнения: старый против старого). Сравнение `cmp` файлов `{long,short}-{trades,daily,coinday,entryrej}.csv`
и `meta.json` с выходами основного прогона `out/<окно>/<vid>`. Любое различие обнуляет прогон (§13.24.6 п. 4).

  TK599_GO=1 python3 tk599_posthoc.py [--root /data/p42-tk599] [--audit-dir /data/p42-tk599/audit] [--threads 2] [--n-random 10]

Выход: `<audit-dir>/select.json` (выбор клеток), `<audit-dir>/out/<окно>/<vid>` (выходы старого бинаря), `<audit-dir>/audit.json` (сравнение; в отчёт — `retitr_full_report.py --audit`).
Запуск под `systemd-run` (Nice 19, малая квота ЦП): параллельно идёт `sens` на тех же окнах; выходы основного прогона только читаются. Возобновляемо: готовый пакет (метка DONE) не пересчитывается.
"""
from __future__ import annotations

import argparse
import dataclasses
import filecmp
import glob
import hashlib
import json
import os
import pickle
import random
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import retitr_cells as rc  # noqa: E402
import retitr_full_run as rr  # noqa: E402

GuardError = rf.GuardError
COMPARE_FILES = tuple("%s-%s.csv" % (s, k) for s in ("long", "short") for k in ("trades", "daily", "coinday", "entryrej")) + ("meta.json",)


@dataclasses.dataclass
class AuditPaths(rr.Paths):
    """Пути как у основного прогона (кэш, сделки, таблицы потоков — только чтение), выходы / варианты / журналы — в каталоге аудита."""
    audit: str = ""

    def out(self, w):
        return os.path.join(self.audit, "out", w)

    @property
    def var(self):
        return os.path.join(self.audit, "var")

    @property
    def log(self):
        return os.path.join(self.audit, "log")


def var_keys(var_dir):
    """{vid: ключи движка} по файлам вариантов основного прогона `var/b*.txt` (строка — «vid ключи»); одинаковый vid с разными ключами — GuardError."""
    out = {}
    for p in sorted(glob.glob(os.path.join(var_dir, "b*.txt"))):
        for ln in open(p, encoding="utf-8").read().splitlines():
            if ln.strip():
                vid, _, keys = ln.partition(" ")
                if out.setdefault(vid, keys.strip()) != keys.strip():
                    raise GuardError("vid %s: разные ключи в файлах вариантов (%s)" % (vid, p))
    return out


def series_bins(npz_dir):
    """{имя файла без .npz (sha256 клетки): md5 бинаря серии}; серия без поля — записанный в протоколе (`a03c91df`)."""
    out = {}
    for p in sorted(glob.glob(os.path.join(npz_dir, "*.npz"))):
        if p.endswith(".tmp.npz"):
            continue
        with np.load(p) as z:
            out[os.path.basename(p)[:-len(".npz")]] = str(z["bin_md5"]) if "bin_md5" in z.files else rr.RECORDED["bin_md5"]
    return out


def select_cells(res, spec, bins, keys_of, seed_hex, n_random=10):
    """Клетки аудита → [{vid, side, keys, why, hash, bin_md5}]: итог полной процедуры и победитель каждой свёртки каждой стороны, `n_random` случайных клеток новых серий,
    одна контрольная клетка старого бинаря. Случайный выбор — `random.Random(int(seed_hex, 16))` по отсортированным именам серий (воспроизводим)."""
    cells = {}

    def add(side, keys, why, h=None, sig=False, req_keys=None):
        req = rf.Req(side, keys, sig) if (side and req_keys is None) else None
        h = h or req.hash
        vid = "c" + h[:12]
        if req is not None and vid in keys_of and req.engine_keys != keys_of[vid]:
            raise GuardError("клетка %s (%s): ключи %r ≠ записанные в варианте %r" % (vid, why, req.engine_keys, keys_of[vid]))
        if req is None and vid not in keys_of:
            raise GuardError("клетки %s (%s) нет в файлах вариантов основного прогона" % (vid, why))
        if vid not in cells:                                  # у клеток первых пакетов файлов вариантов нет (перезаписаны) — ключи из сеток
            cells[vid] = {"vid": vid, "side": side, "keys": req.engine_keys if req is not None else keys_of[vid], "req_keys": req.keys if req is not None else req_keys, "sig": bool(sig),
                          "why": [], "hash": h, "bin_md5": bins.get(h)}
        cells[vid]["why"].append(why)

    for s in rf.SIDES:
        add(s, rf.cfg_keys(spec, s, res["outcomes"]["%s:full" % s].final), "итог полной процедуры, сторона %s" % s)
        for i in range(1, len(rf.FOLDS) + 1):
            add(s, rf.cfg_keys(spec, s, res["outcomes"]["%s:f%d" % (s, i)].final), "победитель свёртки %d, сторона %s" % (i, s))
    rnd = random.Random(int(seed_hex, 16))
    new = sorted(h for h, m in bins.items() if m != rr.RECORDED["bin_md5"])
    old = sorted(h for h, m in bins.items() if m == rr.RECORDED["bin_md5"] and "c" + h[:12] in keys_of)
    for h in rnd.sample(new, min(n_random, len(new))):
        s, rk, sg = _req_of(h, keys_of["c" + h[:12]])
        add(s, keys_of["c" + h[:12]], "случайная клетка новых серий", h=h, sig=sg, req_keys=rk)
    h = rnd.choice(old)
    s, rk, sg = _req_of(h, keys_of["c" + h[:12]])
    add(s, keys_of["c" + h[:12]], "контроль: клетка старого бинаря", h=h, sig=sg, req_keys=rk)
    return [cells[v] for v in sorted(cells)]


def _req_of(h, line):
    """(сторона, ключи запроса, сигнальный двойник) клетки по хэшу и строке варианта: `Req.hash` = sha256 от (сторона, канон. ключи, двойник); у двойника в строке варианта к ключам запроса добавлен SIGMODE.
    Нужны только вариантам с фильтром (`fa=`: таблица потока по стороне и ключам без SIGMODE); нет совпадения у клетки без фильтра — (None, строка, False), у клетки с фильтром — GuardError."""
    sigpairs = set(rc.parse_keys(rc.SIGMODE)[0])
    plain, feats = rc.parse_keys(line)
    bare = " ".join("%s=%s" % kv for kv in plain if kv not in sigpairs) + "".join(" fa=%s fo=%s fv=%s" % (f["fa"], f["fo"], f["fv"]) for f in feats)
    for sig, keys in ((False, line), (True, bare)):
        for s in rf.SIDES:
            req = rf.Req(s, keys, sig)
            if req.hash == h and req.engine_keys.split() and sorted(req.engine_keys.split()) == sorted(line.split()):
                return s, keys, sig
    if "fa=" in line:
        raise GuardError("клетка с фильтром %s: сторону и ключи запроса по хэшу восстановить не удалось" % h[:12])
    return None, line, False


def launch_reqs(cells):
    """Запросы для `Engine.launch` (нужны vid, ключи, сторона, признак фильтра)."""
    return [types.SimpleNamespace(vid=c["vid"], side=c["side"], keys=c["req_keys"], engine_keys=c["keys"], sig=c["sig"]) for c in cells]


def json_diff(a, b, prefix="", limit=12):
    """Различающиеся листья двух JSON (путь: a ≠ b) — для строки отчёта о различии `meta.json`."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append("%s%s: %s" % (prefix, k, "только в выходе аудита" if k in b else "только в основном"))
            else:
                out += json_diff(a[k], b[k], "%s%s." % (prefix, k), limit)
    elif a != b:
        out.append("%s: %s ≠ %s" % (prefix.rstrip("."), str(a)[:60], str(b)[:60]))
    return out[:limit]


def compare_cell(main_out, audit_out, vid, windows):
    """{окно: {файл: "равны" | "РАЗЛИЧИЕ" | "нет у одного"}}; для различающегося `meta.json` — ещё `meta_diff`."""
    res = {}
    for w in windows:
        a, b = os.path.join(main_out, w, vid), os.path.join(audit_out, w, vid)
        r = {}
        for fn in COMPARE_FILES:
            pa, pb = os.path.join(a, fn), os.path.join(b, fn)
            ea, eb = os.path.exists(pa), os.path.exists(pb)
            if ea != eb:
                r[fn] = "нет у одного"
            elif ea:
                same = filecmp.cmp(pa, pb, shallow=False)
                r[fn] = "равны" if same else "РАЗЛИЧИЕ"
                if not same and fn == "meta.json":
                    r["meta_diff"] = json_diff(json.load(open(pa, encoding="utf-8")), json.load(open(pb, encoding="utf-8")))
        res[w] = r
    return res


def summarize(cells, compared):
    files = sum(1 for v in compared.values() for r in v.values() for k, s in r.items() if k != "meta_diff")
    bad = [(vid, w, fn, s) for vid, v in compared.items() for w, r in v.items() for fn, s in r.items() if fn != "meta_diff" and s != "равны"]
    return {"cells": len(cells), "windows": sorted({w for v in compared.values() for w in v}), "files_compared": files, "diffs": len(bad), "diff_list": bad[:40]}


def run(a):
    rr.require_go()
    root, adir = a.root, a.audit_dir
    old_bin = a.old_bin or os.path.join(root, "bin", "alpha-master.a03c91df")
    sha, md5 = rr.file_hashes(old_bin)
    if md5 != rr.RECORDED["bin_md5"] or sha != rr.RECORDED["bin_sha256"]:
        raise GuardError("%s: md5/sha256 не равны записанным для a03c91df (%s / %s)" % (old_bin, md5, sha))
    pkl = os.path.join(root, "results", "results.pkl")
    seed_hex = hashlib.sha256(open(pkl, "rb").read()).hexdigest()
    res = pickle.load(open(pkl, "rb"))
    spec = rf.Spec.load(rr.GRIDS)
    bins = series_bins(os.path.join(root, "npz"))
    cells = select_cells(res, spec, bins, var_keys(os.path.join(root, "var")), seed_hex, a.n_random)
    for c in cells:
        for w in rf.WINDOWS:
            if not os.path.exists(os.path.join(root, "out", w.name, c["vid"], rr.DONE_MARK)):
                raise GuardError("нет выхода основного прогона out/%s/%s (метка %s)" % (w.name, c["vid"], rr.DONE_MARK))
    os.makedirs(adir, exist_ok=True)
    sel = {"results_sha256": seed_hex, "old_bin": old_bin, "old_bin_md5": md5, "old_bin_sha256": sha, "series_by_bin": {m: sum(1 for x in bins.values() if x == m) for m in sorted(set(bins.values()))},
           "cells": cells}
    with open(os.path.join(adir, "select.json"), "w", encoding="utf-8") as f:
        json.dump(sel, f, ensure_ascii=False, indent=1)
    paths = AuditPaths(root, audit=adir)                              # без склада и кэша сигналов: старый бинарь таких ключей не знает
    text, _coins = rr.read_symbols(rr.SYMBOLS)

    def need_ready(needs):
        for n in needs:
            for w in rf.WINDOWS:
                if not os.path.exists(paths.need_tab(w.name, n)):
                    raise GuardError("нет таблицы потока %s (окно %s): аудит читает таблицы основного прогона" % (n.vid, w.name))

    eng = rr.Engine(paths, rr.SubprocessRunner(), text, bin_path=old_bin, threads=a.threads, chunk=None, ensure_tables=need_ready, windows=rf.WINDOWS, prefix="a")
    eng.launch(launch_reqs(cells))
    compared = {c["vid"]: compare_cell(os.path.join(root, "out"), os.path.join(adir, "out"), c["vid"], [w.name for w in rf.WINDOWS]) for c in cells}
    out = dict(sel, summary=summarize(cells, compared), compared=compared)
    with open(os.path.join(adir, "audit.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(json.dumps(out["summary"], ensure_ascii=False))
    return 0 if out["summary"]["diffs"] == 0 else 3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=rr.ROOT)
    ap.add_argument("--audit-dir", default=os.path.join(rr.ROOT, "audit"))
    ap.add_argument("--old-bin", default="")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--n-random", type=int, default=10)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
