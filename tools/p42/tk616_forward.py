#!/usr/bin/env python3
"""TK-616 / П-42 §13.28 п. 7: контрольные точки накопления вперёд (L1…L5) для семейства F = {S_base} ∪ M (M — клетки, прошедшие G_mk).
Всё, что в `docs/research/freeze/tk616-frozen.json` (бинарь, клетки, α, точки, окна `w1…w5`), — единственный источник; код только исполняет.

  python3 tk616_forward.py plan  [--freeze …]                       # точки, окна, состояние охран — без чтения данных и без запуска движка
  TK616F_GO=1 python3 tk616_forward.py point L2 --bin <alpha-mk> [--freeze-sha256 … --freeze-commit …]   # ОДНО чтение точки: движок по окнам, таблицы, охраны, вердикт

Охраны (каждая — остановка с сообщением координатору, не правка):
  • заморозка закоммичена и файл не изменён с коммита (git), либо — на машине без git — заданы `--freeze-commit` и `--freeze-sha256` = sha256 файла;
  • бинарь = замороженному (md5 и sha256); точка существует; сегодня (UTC) позже даты точки (сутки точки закончились);
  • в деревьях окон (`<корень>/<точка>/<окно>/aggtrades|klines`) нет суток позже даты точки;
  • одно чтение точки: маркер `READ-<точка>.started` создаётся атомарно ДО движка; есть — отказ (повтор — только решением координатора);
  • префикс-охрана: таблицы точки k = префикс таблиц точки k + 1 (по (монета, вход, выход, gross, fees) у сделок с входом ≤ D_k − 4 сут.;
    сделки точки k, закрытые обрезкой конца данных (`forced`), печатаются числом и допускаются с другим выходом);
  • G3 швов: у каждой пары соседних окон множества сделок на куске перекрытия [T0_{k+1} + 32, min(T0_k + 72, входы ≤ D − 4)] равны;
  • сделок за концом данных точки нет (вход и выход ≤ D).
Метрика — средний $ на сделку = ΣS/ΣN (gross − fees, без фандинга); единица статистики — сутки (блочный бутстреп, блок 4, ≥ 100 000 повторов, зерно md5 как §13.24.1 п. 2);
медиан и t-статистик по сделкам нет. Подтверждение/опровержение — по правилу §13.28 п. 7 дословно (см. `decide`).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
FREEZE = os.path.join(REPO, "docs", "research", "freeze", "tk616-frozen.json")
GO_ENV = "TK616F_GO"
ROOT = "/data/p42-tk616/fwd"
F0 = datetime.date(2026, 11, 17)                       # первые сутки накопления вперёд
DAY_MS = rf.DAY_MS
NBOOT = 100_000
MIN_TRADES = 60                                        # §13.28 п. 7: n ≥ 60 сделок
DROP = 2                                               # «без 2 лучших суток / монет»
TAIL_DAYS = 4                                          # хвост точки: входы зачитываются до D − 4 сут.
CREDIT_FIRST, CREDIT_LAST, STEP = 32, 69, 38           # зачёт окна [T0 + 32, T0 + 69] = 38 суток, T0 следующего окна = T0 + 38
SEAM_LAST = 72                                         # последние сутки данных раннего окна, кроме 73-х (хвост)
GuardError = rf.GuardError


def d(s):
    return datetime.date.fromisoformat(s)


def t0_ms(day):
    return int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def sha256_text(path):
    return rf.sha256_file(path)


# ---- заморозка ---------------------------------------------------------------------------------------------------------------------

def load_freeze(path):
    """Читает заморозку и проверяет её геометрию (окна, точки, α) — до любых данных."""
    if not os.path.exists(path):
        raise GuardError("нет файла заморозки %s: читать вперёд нельзя до коммита заморозки (§13.28 п. 8)" % path)
    fz = json.load(open(path, encoding="utf-8"))
    for k in ("binary", "cells", "alpha", "point_shares", "points", "windows", "symbols_sha256"):
        if k not in fz:
            raise GuardError("в заморозке нет поля %r" % k)
    wins = windows_of(fz)
    for i, w in enumerate(wins):
        if w.lod != w.t0d + datetime.timedelta(days=CREDIT_FIRST) or w.hid != w.t0d + datetime.timedelta(days=CREDIT_LAST):
            raise GuardError("окно %s: зачёт %s…%s ≠ [T0 + %d, T0 + %d]" % (w.name, w.lo, w.hi, CREDIT_FIRST, CREDIT_LAST))
        if i and w.t0d != wins[i - 1].t0d + datetime.timedelta(days=STEP):
            raise GuardError("окно %s: T0 %s ≠ T0 предыдущего + %d" % (w.name, w.t0, STEP))
    if wins[0].lod != F0:
        raise GuardError("зачёт первого окна начинается %s, а не %s" % (wins[0].lo, F0))
    fam = [c for c, v in fz["cells"].items() if v.get("family")]
    if not fam or "S_base" not in fam:
        raise GuardError("семейство вперёд обязано содержать S_base")
    if set(fz["alpha"]) != set(fam):
        raise GuardError("α заданы не для всех клеток семейства: %s ≠ %s" % (sorted(fz["alpha"]), sorted(fam)))
    if sum(fz["alpha"].values()) > 0.05 + 1e-12:
        raise GuardError("сумма α %.4f > 0,05" % sum(fz["alpha"].values()))
    sh = fz["point_shares"]
    if abs(sum(sh.values()) - 1.0) > 1e-9:
        raise GuardError("доли α по точкам дают %.4f, а не 1" % sum(sh.values()))
    ids = [p["id"] for p in fz["points"]]
    for p in fz["points"]:
        dd, to = d(p["d"]), d(p["entries_to"])
        if to != dd - datetime.timedelta(days=TAIL_DAYS):
            raise GuardError("точка %s: входы до %s ≠ D − %d сут." % (p["id"], p["entries_to"], TAIL_DAYS))
        need = [w.name for w in wins if w.lod <= to and w.hid >= F0]
        if p["windows"] != need:
            raise GuardError("точка %s: окна %s ≠ вычисленным по зачёту %s" % (p["id"], p["windows"], need))
        if p["decision"] != (p["id"] in sh):
            raise GuardError("точка %s: признак решения расходится с долями α" % p["id"])
    if ids != sorted(ids, key=lambda x: int(x[1:])) or len(set(ids)) != len(ids):
        raise GuardError("точки идут не по порядку: %s" % ids)
    return fz


def windows_of(fz):
    return [rf.Window(w["name"], w["t0"], w["lo"], w["hi"]) for w in fz["windows"]]


def alpha_k(fz, cell, point):
    return fz["alpha"][cell] * fz["point_shares"].get(point, 0.0)


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def check_freeze_committed(path, repo=None, commit="", sha256=""):
    """Заморозка закоммичена (файл в истории) и не изменена с коммита. На машине без git (srv — архив дерева) — `--freeze-commit` и `--freeze-sha256` (= sha256 файла)."""
    if not os.path.exists(path):
        raise GuardError("нет файла заморозки %s" % path)
    actual = sha256_text(path)
    if repo and os.path.isdir(os.path.join(repo, ".git")):
        rel = os.path.relpath(path, repo)
        r = _git(repo, "log", "-1", "--format=%H", "--", rel)
        if r.returncode != 0 or not r.stdout.strip():
            raise GuardError("заморозка %s не закоммичена: читать вперёд нельзя" % rel)
        if _git(repo, "diff", "--quiet", "HEAD", "--", rel).returncode != 0:
            raise GuardError("заморозка %s изменена после коммита %s" % (rel, r.stdout.strip()))
        return r.stdout.strip(), actual
    if not commit or not sha256:
        raise GuardError("нет git: нужны --freeze-commit и --freeze-sha256 (sha256 файла заморозки)")
    if sha256 != actual:
        raise GuardError("sha256 заморозки %s ≠ заданному %s" % (actual, sha256))
    return commit, actual


def check_binary(path, fz):
    if not path or not os.path.exists(path):
        raise GuardError("--bin: нет файла %r" % path)
    sha, md5 = rr.file_hashes(path)
    b = fz["binary"]
    if md5 != b["md5"] or sha != b["sha256"]:
        raise GuardError("бинарь %s: md5 %s / sha256 %s… ≠ замороженным %s / %s…" % (path, md5, sha[:12], b["md5"], b["sha256"][:12]))
    return md5, sha


def get_point(fz, pid):
    for p in fz["points"]:
        if p["id"] == pid:
            return p
    raise GuardError("точка %r не из заморозки: %s" % (pid, [p["id"] for p in fz["points"]]))


def check_point_date(pt, today):
    if not today > d(pt["d"]):
        raise GuardError("точка %s: дата %s ещё не закончилась (сегодня UTC %s) — читать нельзя" % (pt["id"], pt["d"], today))


_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def check_tree_days(root, wins, last_day):
    """В деревьях окон нет суток позже даты точки (имена файлов `…-YYYY-MM-DD.zip` / `YYYY-MM-DD.csv`); нет дерева — остановка."""
    late, n = [], 0
    for w in wins:
        for sub in ("aggtrades", "klines"):
            base = os.path.join(root, w.name, sub)
            if not os.path.isdir(base):
                raise GuardError("нет дерева данных окна %s: %s (готовится отдельным шагом до точки)" % (w.name, base))
            for sym in sorted(os.listdir(base)):
                p = os.path.join(base, sym)
                if not os.path.isdir(p):
                    continue
                for name in os.listdir(p):
                    m = _DATE.search(name)
                    if m:
                        n += 1
                        if d(m.group(1)) > last_day:
                            late.append("%s/%s/%s" % (w.name, sub, name))
    if late:
        raise GuardError("в деревьях окон есть сутки позже %s (%d файлов), например: %s" % (last_day, len(late), late[:3]))
    return n


def claim_read(root, pid, info):
    """Одно чтение точки: маркер создаётся атомарно (O_EXCL) до запуска движка."""
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, "READ-%s.started" % pid)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise GuardError("точка %s уже читалась (%s): повторное чтение запрещено — решение координатора" % (pid, p))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return p


# ---- таблицы сделок -------------------------------------------------------------------------------------------------------------------

def rows_of(tab, w):
    """Строки таблицы окна в абсолютном времени: [(монета, вход мс, выход мс, gross, fees, funding, вид)]."""
    off = t0_ms(w.t0d)
    return [(tab.sym[i], int(tab.entry_ms[i]) + off, int(tab.exit_ms[i]) + off, round(float(tab.gross[i]), 6), round(float(tab.fees[i]), 6),
             round(float(tab.funding[i]), 6), tab.kind[i]) for i in range(len(tab))]


def date_of(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).date()


def credited(rows, w, entries_to):
    """Зачётные строки окна: дата входа в [max(w.lo, F0), min(w.hi, entries_to)]."""
    lo, hi = max(w.lod, F0), min(w.hid, entries_to)
    return [r for r in rows if lo <= date_of(r[1]) <= hi]


def end_of_data_check(rows, w, last_day):
    """Сделок за концом данных окна нет: вход и выход не позже конца суток min(D, T0 + 73) (выход закрытых обрезкой — ровно конец данных)."""
    lim = t0_ms(min(last_day, w.t0d + datetime.timedelta(days=rf.TRADE_END))) + DAY_MS
    bad = [r for r in rows if r[1] >= lim or r[2] > lim]
    if bad:
        raise GuardError("окно %s: сделки позже конца данных %s: %s" % (w.name, last_day, bad[:2]))


def prefix_guard(prev, cur, prev_entries_to):
    """Таблицы точки k = префикс точки k + 1: у сделок с входом ≤ D_k − 4 сут. множества (монета, вход) равны, а у не `forced` в точке k выход, gross, fees совпадают.
    prev, cur — {клетка: [строки]} (после склейки окон, уже с отсечкой по своим датам). Возвращает ({клетка: [различия]}, {клетка: число forced точки k})."""
    diffs, forced = {}, {}
    for c, a in prev.items():
        A = {(r[0], r[1]): r for r in a if date_of(r[1]) <= prev_entries_to}
        B = {(r[0], r[1]): r for r in cur.get(c, []) if date_of(r[1]) <= prev_entries_to}
        forced[c] = sum(1 for r in A.values() if r[6] == "forced")
        out = ["только в точке k: %s" % (k,) for k in sorted(set(A) - set(B))] + ["только в точке k + 1: %s" % (k,) for k in sorted(set(B) - set(A))]
        for k in sorted(set(A) & set(B)):
            ra, rb = A[k], B[k]
            if ra[6] == "forced":
                continue
            if ra[2] != rb[2] or abs(ra[3] - rb[3]) > 1e-6 or abs(ra[4] - rb[4]) > 1e-6:
                out.append("различие %s: %s ≠ %s" % (k, ra[2:5], rb[2:5]))
        diffs[c] = out
    return diffs, forced


def seam_pairs(wins_used):
    """Пары соседних окон точки и кусок перекрытия [T0_{k+1} + 32, T0_k + 72] (даты)."""
    out = []
    for a, b in zip(wins_used, wins_used[1:]):
        out.append((a, b, b.t0d + datetime.timedelta(days=CREDIT_FIRST), a.t0d + datetime.timedelta(days=SEAM_LAST)))
    return out


def seams_check(tabs, wins_used, entries_to):
    """G3 швов: на куске перекрытия (не дальше входов ≤ D − 4) множества сделок соседних окон равны по (монета, абсолютный вход, выход, gross, fees)."""
    diff = []
    for a, b, lo, hi in seam_pairs(wins_used):
        hi = min(hi, entries_to)
        if lo > hi:
            continue
        diff += ["%s–%s: %s" % (a.name, b.name, x) for x in rf.overlap_diff(tabs[a.name], a, tabs[b.name], b, lo.isoformat(), hi.isoformat())]
    return diff


# ---- ряды, статистика, вердикт ----------------------------------------------------------------------------------------------------------------

def splice_rows(by_window, wins_used, entries_to):
    out = []
    for w in wins_used:
        out += credited(by_window[w.name], w, entries_to)
    out.sort(key=lambda r: (r[1], r[0]))
    return out


def daily(rows, ndays):
    day = np.array([(date_of(r[1]) - F0).days for r in rows], dtype=np.int64)
    val = np.array([r[3] - r[4] for r in rows], dtype=float)
    return np.bincount(day, weights=val, minlength=ndays), np.bincount(day, minlength=ndays).astype(float), day, val


def mean_without(keys_sum, keys_n, k, best=True):
    """Среднее $/сделку без k лучших (худших) групп (суток или монет) по сумме."""
    order = np.argsort(keys_sum)[::-1] if best else np.argsort(keys_sum)
    drop = order[:k]
    n = keys_n.sum() - keys_n[drop].sum()
    return float((keys_sum.sum() - keys_sum[drop].sum()) / n) if n > 0 else float("nan")


def coin_arrays(rows):
    names = sorted({r[0] for r in rows})
    ix = {s: i for i, s in enumerate(names)}
    cs, cn = np.zeros(len(names)), np.zeros(len(names))
    for r in rows:
        cs[ix[r[0]]] += r[3] - r[4]
        cn[ix[r[0]]] += 1
    return cs, cn


def funding_bound_of(rows, rates):
    """Пессимистичная граница фандинга B: по ставкам (`rf.funding_bound`, Σ|ставка|·$500), если заданы; иначе Σ|фандинг движка| по сделкам."""
    if rates is not None:
        names = [r[0] for r in rows]
        t_in = np.array([r[1] for r in rows], dtype=np.int64)
        t_out = np.array([r[2] for r in rows], dtype=np.int64)
        return float(rf.funding_bound(names, t_in, t_out, rates).sum()), "ставки"
    return float(sum(abs(r[5]) for r in rows)), "|фандинг движка|"


def decide(point, is_last, st):
    """Правило §13.28 п. 7 дословно. st: n, sum, mean, p, alpha_k, mean_wo_days, mean_wo_coins, fund_ok, worst_days, worst_coins.
    Подтверждено: p ≤ α_k, ΣS > 0, среднее > 0 без 2 лучших суток и без 2 лучших монет, (ΣS − B)/N > 0, n ≥ 60. Опровергнуто — только на последней точке:
    среднее < 0 и без 2 худших суток и без 2 худших монет < 0. Иначе — не ясно."""
    if st["alpha_k"] <= 0:
        return "описательно"
    if (st["p"] <= st["alpha_k"] and st["sum"] > 0 and st["mean_wo_days"] > 0 and st["mean_wo_coins"] > 0 and st["fund_ok"] and st["n"] >= MIN_TRADES):
        return "подтверждено"
    if is_last and st["mean"] < 0 and st["worst_days"] < 0 and st["worst_coins"] < 0:
        return "опровергнуто"
    return "не ясно"


def cell_point_stats(fz, cell, point, rows, entries_to, rates=None, nboot=NBOOT):
    ndays = (entries_to - F0).days + 1
    S, N, _, _ = daily(rows, ndays)
    cs, cn = coin_arrays(rows)
    n = int(N.sum())
    st = {"cell": cell, "n": n, "days": ndays, "sum": float(S.sum()), "mean": float(S.sum() / n) if n else float("nan")}
    if n == 0:
        st.update({"p": 1.0, "alpha_k": 0.0, "mean_wo_days": float("nan"), "mean_wo_coins": float("nan"), "fund_ok": False, "worst_days": float("nan"),
                   "worst_coins": float("nan"), "fund_bound": 0.0, "fund_mode": "—"})
    else:
        o = rf.own_stat(S, N, nboot)
        B, mode = funding_bound_of(rows, rates)
        st.update({"p": o.p, "se": o.se, "lower": o.lower, "upper": o.upper, "mean_wo_days": mean_without(S, N, DROP), "mean_wo_coins": mean_without(cs, cn, DROP),
                   "worst_days": mean_without(S, N, DROP, best=False), "worst_coins": mean_without(cs, cn, DROP, best=False),
                   "fund_bound": B, "fund_mode": mode, "fund_ok": bool((S.sum() - B) / n > 0)})
    st["alpha_k"] = alpha_k(fz, cell, point["id"]) if cell in fz["alpha"] else 0.0
    st["decision"] = decide(point, point["id"] == fz["points"][-1]["id"], st)
    return st


# ---- запуск точки -------------------------------------------------------------------------------------------------------------------

def reqs_of(fz):
    out = {}
    for c, v in fz["cells"].items():
        r = rf.Req(v["side"], v["keys"], False)
        if r.vid != v["vid"]:
            raise GuardError("клетка %s: vid %s ≠ замороженному %s" % (c, r.vid, v["vid"]))
        out[c] = r
    return out


def prev_point(fz, pid):
    ids = [p["id"] for p in fz["points"]]
    i = ids.index(pid)
    return fz["points"][i - 1] if i else None


def tables_path(root, pid):
    return os.path.join(root, "tables-%s.json" % pid)


def read_cell_tables(paths, reqs, wins_used):
    tabs, rows = {}, {}
    for c, r in reqs.items():
        tabs[c] = {w.name: rf.read_trades_csv(os.path.join(paths.out(w.name), r.vid, rf.ENGINE_SIDE[r.side] + "-trades.csv")) for w in wins_used}
        rows[c] = {w.name: rows_of(tabs[c][w.name], w) for w in wins_used}
    return tabs, rows


def run_point(a, runner=None, today=None, repo=REPO):
    if os.environ.get(GO_ENV) != "1" and not getattr(a, "go", False):
        raise GuardError("%s=1 не задан: чтение вперёд запрещено" % GO_ENV)
    fz = load_freeze(a.freeze)
    commit, fsha = check_freeze_committed(a.freeze, repo, a.freeze_commit, a.freeze_sha256)
    pt = get_point(fz, a.point)
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    check_point_date(pt, today)
    md5, sha = check_binary(a.bin, fz)
    wins_all = {w.name: w for w in windows_of(fz)}
    wins_used = [wins_all[n] for n in pt["windows"]]
    D, to = d(pt["d"]), d(pt["entries_to"])
    pdir = os.path.join(a.root, pt["id"])
    ntree = check_tree_days(pdir, wins_used, D)
    if rf.sha256_file(a.symbols) != fz["symbols_sha256"]:
        raise GuardError("список монет %s не совпадает с замороженным sha256 %s" % (a.symbols, fz["symbols_sha256"]))
    text, coins = rr.read_symbols(a.symbols)
    prev = prev_point(fz, pt["id"])
    prev_rows = None
    if prev is not None:
        pp = tables_path(a.root, prev["id"])
        if not os.path.exists(pp):
            raise GuardError("нет таблиц точки %s (%s): префикс-охрана невозможна, точки читаются по порядку" % (prev["id"], pp))
        prev_rows = {c: [tuple(r) for r in v] for c, v in json.load(open(pp, encoding="utf-8"))["rows"].items()}
    claim_read(a.root, pt["id"], {"point": pt["id"], "bin_md5": md5, "bin_sha256": sha, "freeze_commit": commit, "freeze_sha256": fsha, "tree_files": ntree,
                                 "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    paths = rr.Paths(pdir, tick_store=a.tick_store, sig_cache=a.sig_cache)
    reqs = reqs_of(fz)
    eng = rr.Engine(paths, runner or rr.SubprocessRunner(), text, a.bin, a.threads, None, windows=tuple(wins_used), prefix="f")
    eng.launch(list(reqs.values()))
    tabs, byw = read_cell_tables(paths, reqs, wins_used)
    for c in reqs:
        for w in wins_used:
            end_of_data_check(byw[c][w.name], w, D)
    seam = {c: seams_check(tabs[c], wins_used, to) for c in reqs}
    bad = {c: x[:5] for c, x in seam.items() if x}
    if bad:
        raise GuardError("G3 швов окон: %s" % bad)
    rows = {c: splice_rows(byw[c], wins_used, to) for c in reqs}
    pref, forced = {}, {}
    if prev_rows is not None:
        pref, forced = prefix_guard(prev_rows, rows, d(prev["entries_to"]))
        badp = {c: x[:5] for c, x in pref.items() if x}
        if badp:
            raise GuardError("префикс-охрана точки %s против %s: %s" % (pt["id"], prev["id"], badp))
    os.makedirs(a.root, exist_ok=True)
    json.dump({"point": pt["id"], "entries_to": pt["entries_to"], "rows": rows}, open(tables_path(a.root, pt["id"]), "w", encoding="utf-8"))
    rates = None
    if a.funding:
        months = sorted({(F0 + datetime.timedelta(days=i)).strftime("%Y-%m") for i in range((to - F0).days + 1)})
        rates = rf.load_funding_rates(a.funding, coins, months=tuple(months)) or None
    stats = {c: cell_point_stats(fz, c, pt, rows[c], to, rates, a.nboot) for c in reqs}
    res = {"point": pt, "freeze_commit": commit, "freeze_sha256": fsha, "bin_md5": md5, "stats": stats, "seams": {c: len(x) for c, x in seam.items()},
           "prefix": {"forced_prev": forced, "diffs": {c: len(x) for c, x in pref.items()}}, "tree_files": ntree}
    json.dump(res, open(os.path.join(a.root, "point-%s.json" % pt["id"]), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    open(os.path.join(a.root, "READ-%s.done" % pt["id"]), "w").close()
    print(render(res))
    return 0


def f2(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else ("%+." + str(nd) + "f") % x


def render(res):
    pt = res["point"]
    L = ["# TK-616 — точка %s: данные до %s, входы ≤ %s (окна %s)" % (pt["id"], pt["d"], pt["entries_to"], ", ".join(pt["windows"])), "",
         "| клетка | сделок | ΣS $ | $/сделку | p | α точки | без 2 лучших суток | без 2 лучших монет | граница B $ (%s) | (ΣS − B)/N > 0 | решение |" % "режим", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, s in res["stats"].items():
        L.append("| %s | %d | %s | %s | %.4f | %.4f | %s | %s | %s (%s) | %s | **%s** |" % (c, s["n"], f2(s["sum"]), f2(s["mean"], 3), s["p"], s["alpha_k"], f2(s["mean_wo_days"], 3),
                                                                                          f2(s["mean_wo_coins"], 3), f2(s["fund_bound"]), s["fund_mode"], "да" if s["fund_ok"] else "нет", s["decision"]))
    L += ["", "Швы окон: различий %s; префикс-охрана: %s; forced точки-предшественницы: %s" % (res["seams"], res["prefix"]["diffs"] or "нет предшественницы", res["prefix"]["forced_prev"] or "—")]
    return "\n".join(L) + "\n"


def cmd_plan(a):
    fz = load_freeze(a.freeze)
    print("заморозка: бинарь md5 %s; семейство %s; α %s; доли %s" % (fz["binary"]["md5"], [c for c, v in fz["cells"].items() if v.get("family")], fz["alpha"], fz["point_shares"]))
    for w in fz["windows"]:
        print("окно %s T0 %s зачёт %s…%s" % (w["name"], w["t0"], w["lo"], w["hi"]))
    for p in fz["points"]:
        mark = "прочитана" if os.path.exists(os.path.join(a.root, "READ-%s.started" % p["id"])) else "не читалась"
        print("точка %s: данные до %s, входы ≤ %s, окна %s, решение %s, α(S_base) %.4f — %s" % (
            p["id"], p["d"], p["entries_to"], ",".join(p["windows"]), "да" if p["decision"] else "нет (описательно)", alpha_k(fz, "S_base", p["id"]), mark))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "point"])
    ap.add_argument("point", nargs="?", default="")
    ap.add_argument("--freeze", default=FREEZE)
    ap.add_argument("--freeze-commit", default="")
    ap.add_argument("--freeze-sha256", default="")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--bin", default="")
    ap.add_argument("--symbols", default=rr.SYMBOLS)
    ap.add_argument("--threads", type=int, default=rr.THREADS)
    ap.add_argument("--tick-store", default="")
    ap.add_argument("--sig-cache", default="")
    ap.add_argument("--funding", default="")
    ap.add_argument("--nboot", type=int, default=NBOOT)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            return cmd_plan(a)
        if not a.point:
            raise GuardError("point: укажите L1…L5")
        return run_point(a)
    except GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
