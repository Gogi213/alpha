#!/usr/bin/env python3
"""TK-599: сухой повтор готовых раундов `tk599-run` по кэшу (npz серий, метки ready, выходы движка) — след раундов и шагов для сверки при смене кода (критик, условие 3).
Старый процесс раундов не журналирует, поэтому «журнал до остановки» = след, снятый СТАРЫМ кодом на остановленном состоянии; после смены кода тот же повтор НОВЫМ кодом обязан дать тот же след.

  python3 tk599_replay.py trace <каталог кода> <out.json> [--root /data/p42-tk599] [--live]
  python3 tk599_replay.py compare <a.json> <b.json>             # код возврата 0 — следы равны; иначе первое расхождение
  python3 tk599_replay.py shadow <каталог кода> <окно> <vid> [--procs 2] [--root ...]   # теневая пересборка таблицы потока ПИТОНОМ + побайтное сравнение (критик, условие 4)

`trace` не запускает движок и сборщик признаков: на первом раунде с недостающей серией/таблицей — граница (`frontier`), повтор кончается. След: на раунд — число уникальных прогонов, sha256 списка
хэшей и sha256 содержимого серий; на каждый шаг оси / совместную сетку — сторона, ось, победитель, принят ли, sha256 всех строк (кандидат, Δ, условия, причины). Без `--live` отказывается работать при
активном юните `tk599-run`; с `--live` читает кэш, не удаляя оборванных файлов (только для проверки инструмента — след на движущемся состоянии сравнивать нельзя).
Код процедуры и ворот берётся из <каталога кода> (`sys.path`), байткод туда не пишется."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

UNIT = "tk599-run"


class Frontier(Exception):
    """Первый раунд, которому не хватает серии или таблицы признаков: дальше повтор без движка невозможен."""


def _sha(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def series_digest(np, s):
    """sha256 содержимого серии (все массивы, в порядке полей); у таблицы признаков (`True`) — метка."""
    if s is True:
        return "ftab-ready"
    h = hashlib.sha256()
    for name in ("day", "sym", "val", "fund", "fb", "t_in", "t_out"):
        a = np.asarray(getattr(s, name))
        h.update(name.encode() + b":" + str(a.dtype).encode() + b":" + str(a.shape).encode() + b":")
        h.update(repr(a.tolist()).encode("utf-8") if a.dtype.kind == "O" else np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def _dump(rr, o):
    return json.dumps(rr.jsonable(o), sort_keys=True, default=str, ensure_ascii=True)


def replay_trace(rf, rr, ctx, g5, c595=None):
    """След готовых раундов и шагов на кэше `ctx` (серии, ready). `ev.fulfil` и `rf.eval_axis` / `rf.run_joint` подменяются на время вызова и возвращаются в `finally`."""
    import numpy as np
    ev = ctx.ev
    tr = {"rounds": [], "steps": [], "frontier": None, "done": False}
    sd = {}

    def fulfil(reqs):
        uniq = {}
        for r in reqs:
            uniq.setdefault(r.hash, r)
        miss = ev.missing(list(uniq.values()))                         # подгружает готовые серии из кэша в ev.cache
        if miss:
            tr["frontier"] = {"round": len(tr["rounds"]), "requests": len(uniq), "missing": len(miss)}
            raise Frontier()
        hs = sorted(uniq)
        for h in hs:
            if h not in sd:
                sd[h] = series_digest(np, ev.cache[h])
        tr["rounds"].append({"n": len(hs), "keys": _sha(*hs), "series": _sha(*[sd[h] for h in hs])})

    def wrap(name, rec):
        orig = getattr(rf, name)

        def w(*args, **kw):
            res = yield from orig(*args, **kw)
            tr["steps"].append(rec(args, res))
            return res
        setattr(rf, name, w)
        return orig

    def rec_axis(args, st):
        best = max([r["d"] for r in st.rows if r["d"] == r["d"]], default=None)
        return {"kind": "axis", "side": args[2], "axis": st.axis, "cur": st.cur, "winner": st.winner, "accepted": bool(st.accepted), "best_d": best,
                "digest": _sha(_dump(rr, st))}

    def rec_joint(args, jt):
        return {"kind": "joint", "side": args[2], "axes": list(jt.axes), "n_cells": jt.n_cells, "chosen": None if jt.chosen is None else {a: c["id"] for a, c in jt.chosen.items()},
                "digest": _sha(_dump(rr, jt))}

    old = {"eval_axis": wrap("eval_axis", rec_axis), "run_joint": wrap("run_joint", rec_joint)}
    old_fulfil = ev.__dict__.get("fulfil")
    ev.fulfil = fulfil
    try:
        rr.step0(ctx, c595)
        provider = rr.FtabProvider(ev, ctx.store, ctx.spec, rr.avail_of(g5))
        outcomes = rf.drive(rf.procedure_gens(ev, ctx.spec, len(ctx.coins), provider=provider, nboot=ctx.nboot), ev)
        tr["done"] = True
        tr["final"] = {k: rr.jsonable(rf.cfg_ids(o.final)) for k, o in outcomes.items()}
        tr["m"] = {k: o.m for k, o in outcomes.items()}
    except Frontier:
        pass
    finally:
        for k, f in old.items():
            setattr(rf, k, f)
        if old_fulfil is None:
            del ev.fulfil
        else:
            ev.fulfil = old_fulfil
    tr["digest"] = _sha(json.dumps(tr["rounds"], sort_keys=True), json.dumps(tr["steps"], sort_keys=True), json.dumps(tr["frontier"]), tr["done"], json.dumps(tr.get("final"), sort_keys=True))
    return tr


def compare_traces(a, b):
    """[] — следы равны; иначе список первых расхождений (раунд / шаг / граница / итог)."""
    out = []
    for key in ("rounds", "steps"):
        la, lb = a[key], b[key]
        for i in range(min(len(la), len(lb))):
            if la[i] != lb[i]:
                out.append("%s[%d]: %s ≠ %s" % (key, i, json.dumps(la[i], sort_keys=True)[:200], json.dumps(lb[i], sort_keys=True)[:200]))
                break
        if len(la) != len(lb):
            out.append("%s: %d ≠ %d" % (key, len(la), len(lb)))
    for key in ("frontier", "done", "final", "m"):
        if a.get(key) != b.get(key):
            out.append("%s: %s ≠ %s" % (key, a.get(key), b.get(key)))
    return out


def unit_active():
    try:
        return subprocess.run(["systemctl", "is-active", "-q", UNIT], check=False).returncode == 0
    except OSError:
        return False


def cmd_trace(a):
    if unit_active() and not a.live:
        print("юнит %s активен: повтор снимается на остановленном состоянии (или --live — только проверка инструмента)" % UNIT, file=sys.stderr)
        return 2
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(a.code, "tools", "p42"))
    import retitr_full as rf
    import retitr_full_run as rr
    if a.live and hasattr(rf.OutDirEvaluator, "_load_npz"):
        rf.OutDirEvaluator._load_npz = staticmethod(lambda p: _safe_load(rf, p))      # не удалять файл, который пишет живой процесс
    ctx = rr.make_ctx(a.root, nboot=rf.NBOOT, chunk=0, procs=1, threads=rr.THREADS, bin_path="", prod=rr.PROD)
    with open(os.path.join(a.root, "gate", "gates.json"), encoding="utf-8") as f:
        g5 = json.load(f)["G5"]
    tr = replay_trace(rf, rr, ctx, g5)
    tr["code"], tr["live"] = os.path.abspath(a.code), bool(a.live)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(tr, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("раундов %d, шагов %d, готово целиком: %s, граница: %s, sha256 следа %s" % (len(tr["rounds"]), len(tr["steps"]), tr["done"], tr["frontier"], tr["digest"][:16]))
    return 0


def _safe_load(rf, p):
    import zipfile
    import numpy as np
    try:
        with np.load(p) as z:
            return rf.Series(z["day"], z["sym"], z["val"], z["fund"], z["fb"], z["t_in"], z["t_out"])
    except (OSError, ValueError, EOFError, KeyError, zipfile.BadZipFile):
        return None


def cmd_shadow(a):
    """Теневая пересборка таблицы потока Python-эталоном (`retitr_ftab.py build` без `P42_FEAT_BIN`) в `<root>/shadow/<окно>/<vid>/` и побайтное сравнение `ftab.csv` с боевой таблицей (repr float — без потерь:
    равенство байт = равенство битов). Результат — `result.json`; код возврата 0 — равны."""
    import filecmp
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(a.code, "tools", "p42"))
    import retitr_full as rf
    import retitr_full_run as rr
    from types import SimpleNamespace
    paths = rr.Paths(a.root)
    d = paths.j("ftab", a.window, a.vid)
    try:
        with open(os.path.join(d, "built-by.json"), encoding="utf-8") as f:
            built = json.load(f)
    except FileNotFoundError:
        built = {"feat_bin": "python", "note": "без built-by.json — собрано старым кодом (Python)"}
    eps = [n for n in os.listdir(d) if n.startswith("eps-") and n.endswith(".parquet")]
    if len(eps) != 1:
        print("не удалось определить сторону потока по eps-*.parquet в %s: %s" % (d, eps), file=sys.stderr)
        return 2
    need = SimpleNamespace(vid=a.vid, side=eps[0][4:5])
    w = next(w for w in rf.WINDOWS if w.name == a.window)
    store = rr.FtabStore(paths, rr.SubprocessRunner(), None, rr.PROD, a.procs)
    sh = paths.j("shadow", a.window, a.vid)
    os.makedirs(sh, exist_ok=True)
    cmd = store.build_cmd(w, need)
    for flag, val in (("--work", os.path.join(sh, "work")), ("--out", os.path.join(sh, "ftab.csv"))):
        cmd[cmd.index(flag) + 1] = val
    env = {k: v for k, v in os.environ.items() if k != "P42_FEAT_BIN"}
    env.update({k: v for k, v in store.build_env(w).items() if k != "P42_FEAT_BIN"})
    print("теневая сборка (Python): %s" % " ".join(cmd), flush=True)
    rc = subprocess.run(cmd, env=env, check=False).returncode
    res = {"window": a.window, "vid": a.vid, "side": need.side, "reference_built_by": built, "python_rc": rc}
    if rc == 0:
        ref, shd = paths.need_tab(a.window, need), os.path.join(sh, "ftab.csv")
        res.update({"equal": bool(filecmp.cmp(ref, shd, shallow=False)), "bytes_ref": os.path.getsize(ref), "bytes_shadow": os.path.getsize(shd),
                    "md5_ref": _md5(ref), "md5_shadow": _md5(shd)})
    with open(os.path.join(sh, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("equal") else 1


def _md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def cmd_compare(a):
    ta, tb = (json.load(open(p, encoding="utf-8")) for p in (a.a, a.b))
    diff = compare_traces(ta, tb)
    if diff:
        print("СЛЕДЫ РАЗНЫЕ (%d):\n  %s" % (len(diff), "\n  ".join(diff)))
        return 1
    print("следы равны: раундов %d, шагов %d, готово целиком: %s, граница: %s, sha256 %s" % (len(ta["rounds"]), len(ta["steps"]), ta["done"], ta["frontier"], ta["digest"][:16]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("trace")
    t.add_argument("code")
    t.add_argument("out")
    t.add_argument("--root", default="/data/p42-tk599")
    t.add_argument("--live", action="store_true")
    s = sub.add_parser("shadow")
    s.add_argument("code")
    s.add_argument("window")
    s.add_argument("vid")
    s.add_argument("--root", default="/data/p42-tk599")
    s.add_argument("--procs", type=int, default=2)
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    a = ap.parse_args(argv)
    return {"trace": cmd_trace, "compare": cmd_compare, "shadow": cmd_shadow}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
