#!/usr/bin/env python3
"""TK-616 / TK-626, П-42 §13.28 п. 7 + §13.28.1: контрольные точки накопления вперёд (L1…L5) для семейства F = {S_base, S_mk1, L_base} на честной вселенной
(775 контрактов «как был» + прирост точки, ключ `t24=12`); вселенные чтения: `all` (решающая), `crypto`, `tradfi` (описательно, α = 0) — три отдельных прогона движка.
Всё, что в `docs/research/freeze/tk626-frozen.json` (бинарь, клетки, α, вселенная, точки, окна `w1…w5`), — единственный источник; код только исполняет.

TK-638 / §13.36 п. 13: рядом с `tk626-frozen.json` лежит `tk638-frozen.json` — кандидат вперёд `L_n24` (`L_base` + таблица `--ftab` по числу сделок монеты за 24 ч, `n24 ≥ N`). Условия критика C1–C3:
  C1 — α берётся ИЗ `tk638-frozen.json` (`S_base` 0,025, `L_n24` 0,005, `S_mk1` / `L_base` 0,01; сумма 0,05 проверяется охраной); при наличии файла он ПЕРЕКРЫВАЕТ α из `tk626-frozen.json`
       (`S_base` 0,03 в нём остаётся — историческая запись); файл `tk626-frozen.json` должен совпадать с записанным в кандидате sha256;
  C2 — на каждой точке печатается доля кандидатов `L_base` с `m24 = 1` (нет файла суток окна — значение пусто и проходит) и число кандидатов без строки таблицы (`no_key`); `m24 = 1` у > 1 % зачётных
       кандидатов точки или `no_key` > 0 (и любая ошибка таблицы) — `L_n24` на точке НЕ читается (иначе недостающие сутки молча превратили бы клетку в `L_base`); остальные клетки считаются как обычно;
  C3 — `replay-n24` прогоняет `L_n24` в выборке (wa / wb / wc, h775) ТЕМ ЖЕ путём (`run_n24`) и сверяет выходы побайтно с выходами G-638a.

TK-641 / §13.38: третий файл цепочки `tk641-frozen.json` — кандидат вперёд `L_mk2` (`L_base` + `mk=2`, обычная клетка движка, без таблиц). Условия критика C1–C3:
  C1 — цепочка tk626 → tk638 → tk641 без молчаливого возврата: файл обязателен всегда (нет — остановка, α tk638 `S_base` 0,025 не применяются); его α (`S_base` 0,020, `L_mk2` 0,005)
       перекрывают α tk638; каждое звено проверяет sha256 предыдущего; `--no-candidate2` — единственный обход: α tk641 применяются, `L_mk2` не читается (в маркере «кандидат 2 не читан»);
  C2 — в файле полный список `alpha` / `alpha_k` всех клеток; охрана: |Σ α − 0,05| ≤ 1e-12, α_k = α × доли точек у всех клеток;
  C3 — `replay-mk2` прогоняет `L_mk2` в выборке (wa / wb / wc, h775) замороженным бинарём и сверяет `long-{trades,daily,coinday,entryrej}.csv`, `long-maker.json` побайтно с выходами G-626c.

TK-670 / §13.70: четвёртый файл цепочки `tk670-frozen.json` — кандидат вперёд `D_S` (E4 / D шорт TK-668: ключи `S_base` + `lx=1 tf=60`). Клетка читается ОТДЕЛЬНЫМ бинарём `ds_binary` (`--ds-bin`; ключ `lx` есть только в нём), в `cells` заморозки не входит:
  C1 — файл обязателен всегда (нет — остановка), его α (`S_mk1` 0,005, `D_S` 0,005) перекрывают α tk641; каждое звено проверяет sha256 предыдущих; бинарь `D_S` сверяется с md5 / sha256 файла ДО маркера чтения; `--no-candidate3` — единственный обход (α применяются, `D_S` не читается);
  C2 — доля пар (монета, сутки зачёта) без klines предыдущих суток > 1 % на вселенной точки — `D_S` на ней не читается, остальные клетки считаются; `missing_days` движка печатается, в решение не входит;
  C3 — `replay-ds` (G-670c) сверяет `short-{trades,daily,coinday,entryrej}.csv` побайтно с выходами TK-668 (`--ref-dir /data/p42-tk668/e4/cells`, `<окно>/S_lx1/`; `short-sigsx.csv` — дамп TK-668, пакетный путь его не пишет: сверен в G-670a `tk670_replay.py`);
  совместное чтение `S_base` + `D_S` (вселенная all) — ОПИСАТЕЛЬНО (`joint_describe`): без вердикта и α; L1 по `D_S` — описательно, L2–L5 — `decide` как у всех.

  python3 tk616_forward.py plan  [--freeze …]                       # точки, окна, вселенные, состояние охран — без чтения данных и без запуска движка
  TK616F_GO=1 python3 tk616_forward.py point L2 --bin <alpha-mk> --feat-bin <alpha-feat> [--freeze-sha256 … --freeze-commit … --candidate-sha256 … --candidate-commit …]   # ОДНО чтение точки: движок по окнам и вселенным, таблицы, охраны, вердикт
  python3 tk616_forward.py replay-ds --bin <alpha-mk> --ds-bin <alpha-lx> --replay-root /data/p42-tk670/replay-c --windows-root /data/p42-tk621 --ref-dir /data/p42-tk668/e4/cells --tick-store /data/p42-tk599/tick-store --sig-cache …   # C3 TK-670
  python3 tk616_forward.py replay-mk2 --bin <alpha-mk> --replay-root /data/p42-tk641/replay --windows-root /data/p42-tk621 --tick-store /data/p42-tk599/tick-store --ref-dir /data/p42-tk626/gmk/out   # C3 TK-641
  python3 tk616_forward.py replay-n24 --bin <alpha-mk> --feat-bin <alpha-feat> --replay-root … --windows-root /data/p42-tk621 --symbols-file …/symbols-all.txt --ref <gate-638a.json> --tick-store …   # C3, суток ≥ 14.11 не читает

Охраны (каждая — остановка с сообщением координатору, не правка):
  • заморозка закоммичена и файл не изменён с коммита (git), либо — на машине без git — заданы `--freeze-commit` и `--freeze-sha256` = sha256 файла; версия заморозки `tk626-frozen-1`;
  • бинарь = замороженному (md5 и sha256); точка существует; сегодня (UTC) позже даты точки (сутки точки закончились);
  • списки вселенной точки (`<корень>/<точка>/universe/`): базовые sha256 = замороженным, базовые ⊆ списки точки, all = crypto ⊔ tradfi, прирост — первые сутки > 13.11 и ≤ D, U_{k-1} ⊆ U_k;
  • в деревьях окон (`<корень>/<точка>/<окно>/aggtrades|klines`) нет суток позже даты точки и нет монет вне `all`;
  • одно чтение точки: маркер `READ-<точка>.started` создаётся атомарно ДО движка; есть — отказ (повтор — только решением координатора);
  • префикс-охрана: таблицы точки k = префикс таблиц точки k + 1 (по (монета, вход, выход, gross, fees) у сделок с входом ≤ D_k − 4 сут.;
    сделки точки k, закрытые обрезкой конца данных (`forced`), печатаются числом и допускаются с другим выходом) — по каждой паре «клетка × вселенная»;
  • G3 швов: у каждой пары соседних окон множества сделок на куске перекрытия [T0_{k+1} + 32, min(T0_k + 72, входы ≤ D − 4)] равны — по каждой паре «клетка × вселенная»;
  • сделок за концом данных точки нет (вход и выход ≤ D).
Метрика — средний $ на сделку = ΣS/ΣN (gross − fees, без фандинга); единица статистики — сутки (блочный бутстреп, блок 4, ≥ 100 000 повторов, зерно md5 как §13.24.1 п. 2);
медиан и t-статистик по сделкам нет; шарп (среднее / σ суточного P&L с нулями × √365) — описательно, в решении не участвует. Подтверждение/опровержение — по правилу §13.28 п. 7 дословно (см. `decide`),
только для клеток на решающей вселенной.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime
import hashlib
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
FREEZE = os.path.join(REPO, "docs", "research", "freeze", "tk626-frozen.json")
BASE_DIR = os.path.join(REPO, "docs", "findings", "p42-honest-universe-2026-11-19")      # базовые списки 775 (TK-621); на srv те же файлы — /data/p42-tk621/universe
VERSION = "tk626-frozen-1"
UNI_NAMES = ("all", "crypto", "tradfi")
LISTING_AFTER = "2026-11-13"                                       # прирост вселенной: первые опубликованные сутки позже этой даты
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
CAND_NAME, CAND_VERSION, CAND_CELL = "tk638-frozen.json", "tk638-frozen-1", "L_n24"      # TK-638: кандидат вперёд рядом с заморозкой
ALPHA_TOTAL = 0.05                                     # §13.28.1 п. 3: сумма α всех клеток (C1)
M24_MAX_SHARE = 0.01                                   # C2: доля зачётных кандидатов точки с m24 = 1, выше которой `L_n24` на точке не читается
CAND2_NAME, CAND2_VERSION, CAND2_CELL = "tk641-frozen.json", "tk641-frozen-1", "L_mk2"    # TK-641: второй кандидат (звено цепочки после tk638)
REQUIRE_CANDIDATE2 = True                              # нет `tk641-frozen.json` рядом с `--freeze` — остановка (α tk638 перекрыты); False — только тесты прежнего поведения
FILES_641 = ("long-trades.csv", "long-daily.csv", "long-coinday.csv", "long-entryrej.csv", "long-maker.json")
REQUIRE_CANDIDATE = True                               # кандидат — часть заморозки вперёд: нет `tk638-frozen.json` рядом с `--freeze` — остановка (второго источника α нет); False — только тесты прежнего поведения tk626
CAND3_NAME, CAND3_VERSION, CAND3_CELL = "tk670-frozen.json", "tk670-frozen-1", "D_S"    # TK-670: третий кандидат (звено цепочки после tk641), читается ОТДЕЛЬНЫМ бинарём (ключ `lx`)
REQUIRE_CANDIDATE3 = True                              # нет `tk670-frozen.json` рядом с `--freeze` — остановка (α tk641 перекрыты); False — только тесты прежнего поведения
DS_ALPHA_FLOOR_S_BASE = 0.02                           # В-167: пол α(S_base); кандидат берёт α только из долей S_mk1 / L_base (≥ 0,005 остаётся у каждой)
DS_ALPHA_DONORS = ("S_mk1", "L_base")
DS_MISSING_MAX_SHARE = 0.01                            # C2 `D_S`: доля пар (монета, сутки зачёта) без klines предыдущих суток, выше которой `D_S` на точке не читается
FILES_670 = ("short-trades.csv", "short-daily.csv", "short-coinday.csv", "short-entryrej.csv")      # `short-sigsx.csv` пакетный путь точки не пишет (дамп сигналов TK-668 — только `tk670_replay.py`, G-670a: 5 файлов)
DS_REF_SUB = "S_lx1"                                   # G-670c: каталог клетки в выходах TK-668 (`<окно>/S_lx1/short-*.csv`)
GuardError = rf.GuardError


def d(s):
    return datetime.date.fromisoformat(s)


def t0_ms(day):
    return int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def sha256_text(path):
    return rf.sha256_lf(path)                      # LF-нормализованный хеш: блоб git, копия с autocrlf и файл на srv дают одно число


# ---- заморозка ---------------------------------------------------------------------------------------------------------------------

def load_freeze(path):
    """Читает заморозку и проверяет её геометрию (окна, точки, α) — до любых данных."""
    if not os.path.exists(path):
        raise GuardError("нет файла заморозки %s: читать вперёд нельзя до коммита заморозки (§13.28 п. 8)" % path)
    fz = json.load(open(path, encoding="utf-8"))
    if fz.get("version") != VERSION:
        raise GuardError("версия заморозки %r ≠ %r: заморозка 96e302480 (`tk616-frozen-1`) заменена §13.28.1" % (fz.get("version"), VERSION))
    for k in ("binary", "cells", "alpha", "point_shares", "points", "windows", "universe"):
        if k not in fz:
            raise GuardError("в заморозке нет поля %r" % k)
    un = fz["universe"]
    if tuple(un.get("read", ())) != UNI_NAMES or un.get("decision") not in UNI_NAMES:
        raise GuardError("вселенные чтения %s / решающая %r ≠ %s / одна из них" % (un.get("read"), un.get("decision"), UNI_NAMES))
    for n in UNI_NAMES:
        if len(un.get("base", {}).get(n, {}).get("sha256", "")) != 64:
            raise GuardError("в заморозке нет sha256 базового списка %s" % n)
    if len(un.get("contracts_sha256", "")) != 64:
        raise GuardError("в заморозке нет sha256 contracts.csv базы")
    for c, v in fz["cells"].items():
        if "t24=12" not in v["keys"].split():
            raise GuardError("клетка %s без ключа t24=12 (§13.28.1 п. 1)" % c)
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


def check_tree_days(root, wins, last_day, allowed=None):
    """В деревьях окон нет суток позже даты точки (имена файлов `…-YYYY-MM-DD.zip` / `YYYY-MM-DD.csv`) и (если задан `allowed`) нет монет с файлами вне списка `all`; нет дерева — остановка."""
    late, n, alien = [], 0, []
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
                        if allowed is not None and sym not in allowed:
                            alien.append("%s/%s/%s" % (w.name, sub, sym))
                        if d(m.group(1)) > last_day:
                            late.append("%s/%s/%s" % (w.name, sub, name))
    if late:
        raise GuardError("в деревьях окон есть сутки позже %s (%d файлов), например: %s" % (last_day, len(late), late[:3]))
    if alien:
        raise GuardError("в деревьях окон есть монеты вне списка all точки (%d файлов), например: %s" % (len(alien), sorted(set(alien))[:3]))
    return n


# ---- вселенная точки --------------------------------------------------------------------------------------------------------------------

def check_universe(fz, udir, base_dir, point, prev_lists=None):
    """§13.28.1 п. 2 и п. 6 (3): списки вселенной точки. Возвращает {"lists": {имя: [монеты]}, "text": {имя: строка для --symbols}, "sha256": {имя: sha256 файла}, "contracts_sha256": …}.
    Базовые списки (775 / 539 / 236) сверяются с замороженными sha256 и должны входить в списки точки; all = crypto ⊔ tradfi; прирост (нет в базе) — первые сутки > 13.11 и ≤ D точки;
    предыдущая точка (`prev_lists`) входит в текущую."""
    un = fz["universe"]
    base = {}
    for n in UNI_NAMES:
        bp = os.path.join(base_dir, "symbols-%s.txt" % n)
        if not os.path.exists(bp):
            raise GuardError("нет базового списка %s (--base-dir)" % bp)
        if sha256_text(bp) != un["base"][n]["sha256"]:
            raise GuardError("базовый список %s: sha256 %s ≠ замороженному %s" % (bp, sha256_text(bp), un["base"][n]["sha256"]))
        base[n] = rr.read_symbols(bp)[1]
    cur, text, sha = {}, {}, {}
    for n in UNI_NAMES:
        cp = os.path.join(udir, "symbols-%s.txt" % n)
        if not os.path.exists(cp):
            raise GuardError("нет списка вселенной точки %s: готовится скриптом подготовки до точки" % cp)
        text[n], cur[n] = rr.read_symbols(cp)
        sha[n] = sha256_text(cp)
        if len(set(cur[n])) != len(cur[n]):
            raise GuardError("список %s: повторы монет" % cp)
        miss = sorted(set(base[n]) - set(cur[n]))
        if miss:
            raise GuardError("список %s не содержит базовых монет (%d), например: %s" % (n, len(miss), miss[:3]))
    if set(cur["all"]) != set(cur["crypto"]) | set(cur["tradfi"]) or set(cur["crypto"]) & set(cur["tradfi"]):
        raise GuardError("all ≠ crypto ⊔ tradfi (пересечение %s)" % sorted(set(cur["crypto"]) & set(cur["tradfi"]))[:3])
    cpath = os.path.join(udir, "contracts.csv")
    if not os.path.exists(cpath):
        raise GuardError("нет %s" % cpath)
    rows = list(csv.DictReader(open(cpath, encoding="utf-8")))
    if {r["symbol"] for r in rows} != set(cur["all"]) or len(rows) != len(cur["all"]):
        raise GuardError("contracts.csv и список all не совпадают по монетам")
    base_all, tradfi, last = set(base["all"]), set(cur["tradfi"]), point["d"]
    for r in rows:
        inb = r["symbol"] in base_all
        if int(r["in_base"]) != int(inb) or int(r["tradfi"]) != int(r["symbol"] in tradfi):
            raise GuardError("contracts.csv: %s — in_base / tradfi расходятся со списками" % r["symbol"])
        days = int(r["days_0107_1311"])
        if inb and days < 1:
            raise GuardError("contracts.csv: базовый %s — days_0107_1311 = %d < 1" % (r["symbol"], days))
        if not inb and (days != 0 or not (LISTING_AFTER < r["first_day"] <= r["last_day"] <= last)):
            raise GuardError("прирост %s: days_0107_1311 = %d (нужно 0), сутки %s…%s вне (%s; %s] — членство только по факту публикации после 13.11" % (
                r["symbol"], days, r["first_day"], r["last_day"], LISTING_AFTER, last))
    for n in UNI_NAMES:
        base_tradfi = set(base["tradfi"])
        if n == "tradfi" and not base_tradfi <= set(cur[n]):
            raise GuardError("класс TradFi базовых контрактов изменён")
        if n == "crypto" and set(base["crypto"]) - set(cur[n]):
            raise GuardError("класс crypto базовых контрактов изменён")
        if prev_lists is not None and set(prev_lists[n]) - set(cur[n]):
            raise GuardError("вселенная точки %s не содержит вселенной предыдущей точки (%s): %s" % (point["id"], n, sorted(set(prev_lists[n]) - set(cur[n]))[:3]))
    return {"lists": cur, "text": text, "sha256": sha, "contracts_sha256": sha256_text(cpath)}


@dataclasses.dataclass
class UPaths(rr.Paths):
    """Пути прогона одной вселенной: деревья и кэш окон общие, выходы — `out/<вселенная>/<окно>`."""
    uni: str = "all"

    def out(self, w):
        return self.j("out", self.uni, w)

    def sx(self, w):                                       # TK-638: сигнальные дампы и таблицы потока — по вселенным (потоки кандидатов вселенных разные)
        return self.j("sx", self.uni, w)

    def need_dir(self, w, need):
        return self.j("ftab", self.uni, w, need.vid)


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


def sha256_stream(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_manifest(root, wins):
    """§13.28.1 п. 6 (8): {(вид, монета, сутки): (sha256, размер)} по файлам деревьев окон точки (ссылка → её цель). Одни сутки в разных окнах — тот же файл (sha256), иначе остановка."""
    out, by_real = {}, {}
    for w in wins:
        for sub in ("aggtrades", "klines"):
            base = os.path.join(root, w.name, sub)
            for sym in sorted(os.listdir(base)):
                p = os.path.join(base, sym)
                if not os.path.isdir(p):
                    continue
                for name in sorted(os.listdir(p)):
                    m = _DATE.search(name)
                    if not m:
                        continue
                    real = os.path.realpath(os.path.join(p, name))
                    if real not in by_real:
                        by_real[real] = (sha256_stream(real), os.path.getsize(real))
                    key = (sub, sym, m.group(1))
                    if key in out and out[key] != by_real[real]:
                        raise GuardError("окна точки расходятся по файлу %s: %s ≠ %s" % (key, out[key][0][:12], by_real[real][0][:12]))
                    out[key] = by_real[real]
    return out


def write_manifest(path, man):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for (kind, sym, day), (sha, size) in sorted(man.items()):
            f.write("%s\t%s\t%s\t%s\t%d\n" % (kind, sym, day, sha, size))


def read_manifest(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        kind, sym, day, sha, size = line.rstrip("\n").split("\t")
        out[(kind, sym, day)] = (sha, int(size))
    return out


def manifest_guard(prev, cur):
    """Сутки, уже лежавшие в дереве предыдущей точки, не изменились (тот же sha256) и не пропали; новые файлы допустимы. Возвращает список различий."""
    out = []
    for k, v in sorted(prev.items()):
        if k not in cur:
            out.append("пропал файл %s" % (k,))
        elif cur[k] != v:
            out.append("изменён файл %s: sha256 %s → %s" % (k, v[0][:12], cur[k][0][:12]))
    return out


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
    """Пессимистичная граница фандинга B: по ставкам (`rf.funding_bound`, Σ|ставка|·$500), если заданы; иначе Σ модулей фандинга движка по сделкам."""
    if rates is not None:
        names = [r[0] for r in rows]
        t_in = np.array([r[1] for r in rows], dtype=np.int64)
        t_out = np.array([r[2] for r in rows], dtype=np.int64)
        return float(rf.funding_bound(names, t_in, t_out, rates).sum()), "ставки"
    return float(sum(abs(r[5]) for r in rows)), "модуль фандинга движка"


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


def sharpe_of(S):
    """Шарп описательно: среднее / σ (ddof = 1) суточного P&L (нулевые сутки входят) × √365."""
    sd = float(np.std(S, ddof=1)) if len(S) > 1 else 0.0
    return float(np.mean(S) / sd * np.sqrt(365.0)) if sd > 0 else float("nan")


# ---- кандидат вперёд `L_mk2` (TK-641, §13.38; условия критика C1–C3) -------------------------------------------------------------------

def candidate2_path(freeze_path):
    return os.path.join(os.path.dirname(os.path.abspath(freeze_path)), CAND2_NAME)


def mk2_keys(base_keys):
    if not base_keys.endswith(" t24=12") or "mk=" in base_keys:
        raise GuardError("ключи L_base %r: не кончаются на `t24=12` или уже есть `mk`" % base_keys)
    return base_keys[: -len(" t24=12")] + " mk=2 t24=12"


def load_candidate2(path, fz, freeze_path, cand, cand_path):
    """C1–C2: файл `tk641-frozen.json` против заморозки (`fz`; сверяются поля без α) и кандидата tk638 (`cand`, файл `cand_path`) до любых данных.
    Версия; sha256 tk626 и tk638 в файле = sha256 файлов; бинарь / вселенная / доли / точки / окна = заморозке; клетка `L_mk2` = `L_base` + `mk=2` (vid по ключам);
    α: набор = α tk638 ∪ {L_mk2}, |Σ − 0,05| ≤ 1e-12, S_base + L_mk2 = α(S_base) tk638, остальные α = tk638; α_k = α × доли у всех клеток."""
    if cand is None:
        raise GuardError("кандидат %s без %s: цепочка tk626 → tk638 → tk641 разорвана" % (CAND2_NAME, CAND_NAME))
    if not os.path.exists(path):
        raise GuardError("нет файла кандидата %s" % path)
    c = json.load(open(path, encoding="utf-8"))
    if c.get("version") != CAND2_VERSION:
        raise GuardError("версия кандидата 2 %r ≠ %r" % (c.get("version"), CAND2_VERSION))
    bf, pc = c.get("base_freeze", {}), c.get("prev_candidate", {})
    if bf.get("version") != VERSION or bf.get("sha256") != sha256_text(freeze_path):
        raise GuardError("кандидат 2 записан против другой заморозки: sha256 %s… ≠ файла %s %s…" % (str(bf.get("sha256"))[:12], freeze_path, sha256_text(freeze_path)[:12]))
    if pc.get("version") != CAND_VERSION or pc.get("sha256") != sha256_text(cand_path):
        raise GuardError("кандидат 2 записан против другого %s: sha256 %s… ≠ файла %s…" % (CAND_NAME, str(pc.get("sha256"))[:12], sha256_text(cand_path)[:12]))
    for k in ("binary", "universe", "point_shares", "points", "windows"):
        if c.get(k) != fz.get(k):
            raise GuardError("кандидат 2: поле %r расходится с заморозкой %s" % (k, os.path.basename(freeze_path)))
    if set(c.get("cells", {})) != {CAND2_CELL}:
        raise GuardError("кандидат 2: клетки %s ≠ [%s]" % (sorted(c.get("cells", {})), CAND2_CELL))
    cell, base = c["cells"][CAND2_CELL], fz["cells"].get("L_base")
    if base is None:
        raise GuardError("в заморозке нет клетки L_base")
    keys = mk2_keys(base["keys"])
    if cell.get("side") != "L" or cell.get("base_keys") != base["keys"] or cell.get("base_vid") != base["vid"] or cell.get("keys") != keys or cell.get("family") is not False:
        raise GuardError("кандидат 2: клетка %s ≠ L_base + `mk=2`" % CAND2_CELL)
    if rf.Req("L", keys, False).vid != cell.get("vid"):
        raise GuardError("кандидат 2: vid %s ≠ вычисленному %s" % (cell.get("vid"), rf.Req("L", keys, False).vid))
    a, a1 = c.get("alpha", {}), cand["alpha"]
    if set(a) != set(a1) | {CAND2_CELL}:
        raise GuardError("α кандидата 2 заданы для %s ≠ клеткам %s + %s" % (sorted(a), CAND_NAME, CAND2_CELL))
    if not _near(sum(a.values()), ALPHA_TOTAL):
        raise GuardError("сумма α кандидата 2 %.6f ≠ %.2f" % (sum(a.values()), ALPHA_TOTAL))
    if not _near(a["S_base"] + a[CAND2_CELL], a1["S_base"]) or a[CAND2_CELL] <= 0:
        raise GuardError("α: S_base %.6f + %s %.6f ≠ α(S_base) %s %.6f (кандидат берёт α только из доли S_base)" % (a["S_base"], CAND2_CELL, a[CAND2_CELL], CAND_NAME, a1["S_base"]))
    for k, v in a1.items():
        if k != "S_base" and not _near(a[k], v):
            raise GuardError("α(%s) кандидата 2 %.6f ≠ %s %.6f" % (k, a[k], CAND_NAME, v))
    ak = c.get("alpha_k", {})
    if set(ak) != set(a) or any(set(ak[k]) != set(fz["point_shares"]) or any(not _near(ak[k][p], a[k] * sh) for p, sh in fz["point_shares"].items()) for k in ak):
        raise GuardError("α по точкам кандидата 2 ≠ α × доли точек")
    return c


def apply_candidate2(fz, c2, read):
    """Заморозка с α tk641 (C1); при чтении `L_mk2` — клетка пакета точки (тот же движок, те же охраны префикса и швов)."""
    out = dict(fz)
    out["alpha"] = dict(c2["alpha"])
    if read:
        cell = c2["cells"][CAND2_CELL]
        out["cells"] = dict(fz["cells"])
        out["cells"][CAND2_CELL] = {"side": "L", "keys": cell["keys"], "vid": cell["vid"], "family": False}
    return out


def resolve_candidate2(a, fz, cand, repo):
    """(кандидат 2, {поля маркера}). Нет файла — `GuardError` всегда (α tk638 перекрыты; молчаливого возврата нет), кроме тестов прежнего поведения (`REQUIRE_CANDIDATE2 = False`).
    `--no-candidate2` — единственный обход: α tk641 применяются, `L_mk2` не читается."""
    explicit = getattr(a, "candidate2", "")
    path = explicit or candidate2_path(a.freeze)
    if not os.path.exists(path):
        if explicit or REQUIRE_CANDIDATE2:
            raise GuardError("нет файла кандидата %s: кандидат %s в силе, α S_base без него (%s) не применяются — читать нельзя (без чтения — `--no-candidate2`, файл всё равно нужен)" % (
                path, CAND2_CELL, cand["alpha"]["S_base"] if cand else "—"))
        return None, {}
    cand_path = getattr(a, "candidate", "") or candidate_path(a.freeze)
    c2 = load_candidate2(path, fz, a.freeze, cand, cand_path)
    commit, sha = check_freeze_committed(path, repo, getattr(a, "candidate2_commit", ""), getattr(a, "candidate2_sha256", ""))
    info = {"candidate2": CAND2_CELL, "candidate2_commit": commit, "candidate2_sha256": sha, "candidate2_read": not getattr(a, "no_candidate2", False)}
    if not info["candidate2_read"]:
        info["candidate2_note"] = "кандидат 2 не читан (--no-candidate2): α по кандидату 2 применены (S_base %s)" % c2["alpha"]["S_base"]
    return c2, info


def replay_mk2(a, runner=None, repo=REPO):
    """C3: `L_mk2` в выборке (wa / wb / wc, h775) замороженным бинарём; `FILES_641` побайтно как выходы G-626c (`--ref-dir/<окно>/<vid>/`). Сутки ≥ 14.11 не читает.
    Итог — `--out` (`gate-641c.json`); различие или нет файла — код 3."""
    fz = load_freeze(a.freeze)
    fcommit, fsha = check_freeze_committed(a.freeze, repo, a.freeze_commit, a.freeze_sha256)
    cpath = getattr(a, "candidate", "") or candidate_path(a.freeze)
    cand = load_candidate(cpath, fz, a.freeze)
    check_freeze_committed(cpath, repo, getattr(a, "candidate_commit", ""), getattr(a, "candidate_sha256", ""))
    c2, c2info = resolve_candidate2(a, fz, cand, repo)
    if c2 is None or not c2info["candidate2_read"]:
        raise GuardError("replay-mk2: нужен кандидат %s без --no-candidate2" % CAND2_NAME)
    md5, _sha = check_binary(a.bin, fz)
    cell = c2["cells"][CAND2_CELL]
    req = rf.Req("L", cell["keys"], False)
    if not a.ref_dir or not os.path.isdir(a.ref_dir):
        raise GuardError("--ref-dir: нет выходов G-626c (%r)" % a.ref_dir)
    wins = rf.WINDOWS
    os.makedirs(a.replay_root, exist_ok=True)
    for w in wins:
        link, src = os.path.join(a.replay_root, w.name), os.path.join(a.windows_root, w.name)
        if not os.path.lexists(link):
            os.symlink(src, link)
        elif os.path.realpath(link) != os.path.realpath(src):
            raise GuardError("%s указывает не на %s" % (link, src))
    ntree = check_tree_days(a.replay_root, wins, d(LISTING_AFTER))
    text = rr.read_symbols(a.symbols_file)[0]
    paths = UPaths(a.replay_root, uni="replay", tick_store=a.tick_store, sig_cache=a.sig_cache)
    rr.Engine(paths, runner or rr.SubprocessRunner(), text, a.bin, a.threads, None, windows=tuple(wins), prefix="m").launch([req])
    res = {"bin_md5": md5, "keys": cell["keys"], "vid": req.vid, "cell": CAND2_CELL, "candidate2_commit": c2info["candidate2_commit"], "candidate2_sha256": c2info["candidate2_sha256"],
           "freeze_commit": fcommit, "freeze_sha256": fsha, "tree_files": ntree, "ref_dir": a.ref_dir, "windows": {}}
    for w in wins:
        files = {}
        for f in FILES_641:
            x, y = os.path.join(paths.out(w.name), req.vid, f), os.path.join(a.ref_dir, w.name, req.vid, f)
            sx, sy = (sha256_stream(x) if os.path.exists(x) else None), (sha256_stream(y) if os.path.exists(y) else None)
            files[f] = {"sha256": sx, "ref_sha256": sy, "equal": sx is not None and sx == sy}
        res["windows"][w.name] = {"files": files, "equal": all(v["equal"] for v in files.values())}
    res["ok"] = all(v["equal"] for v in res["windows"].values())
    out = a.out or os.path.join(a.replay_root, "gate-641c.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("G-641c: %s (%s); %s" % ("ПРОЙДЕНЫ" if res["ok"] else "НЕ ПРОЙДЕНЫ", ", ".join("%s %s" % (n, "=" if v["equal"] else "≠") for n, v in res["windows"].items()), out))
    return 0 if res["ok"] else 3


# ---- кандидат вперёд `D_S` (TK-670, §13.70; условия критика C1–C3) ------------------------------------------------------------------------

def candidate3_path(freeze_path):
    return os.path.join(os.path.dirname(os.path.abspath(freeze_path)), CAND3_NAME)


def ds_keys(base_keys):
    if not base_keys.endswith(" t24=12") or "lx=" in base_keys:
        raise GuardError("ключи S_base %r: не кончаются на `t24=12` или уже есть `lx`" % base_keys)
    return base_keys[: -len(" t24=12")] + " lx=1 tf=60 t24=12"


def load_candidate3(path, fz, freeze_path, cand, cand_path, c2, c2_path):
    """C1–C2: файл `tk670-frozen.json` против заморозки (`fz`; сверяются поля без α), кандидатов tk638 / tk641 до любых данных.
    Версия; sha256 tk626, tk638, tk641 в файле = sha256 файлов; бинарь клеток / вселенная / доли / точки / окна = заморозке; `ds_binary` с md5 / sha256 / путём;
    клетка `D_S` = `S_base` + `lx=1 tf=60` (vid по ключам); α: набор = α tk641 ∪ {D_S}, |Σ − 0,05| ≤ 1e-12, α(D_S) снята РОВНО с одной из долей `S_mk1` / `L_base` (≥ 0,005 остаётся),
    `S_base` ≥ 0,020 (В-167) и не меняется, остальные α = tk641; α_k = α × доли у всех клеток."""
    if cand is None or c2 is None:
        raise GuardError("кандидат %s без %s и %s: цепочка tk626 → tk638 → tk641 → tk670 разорвана" % (CAND3_NAME, CAND_NAME, CAND2_NAME))
    if not os.path.exists(path):
        raise GuardError("нет файла кандидата %s" % path)
    c = json.load(open(path, encoding="utf-8"))
    if c.get("version") != CAND3_VERSION:
        raise GuardError("версия кандидата 3 %r ≠ %r" % (c.get("version"), CAND3_VERSION))
    for key, ver, p, what in (("base_freeze", VERSION, freeze_path, "заморозки"), ("prev_candidate", CAND_VERSION, cand_path, CAND_NAME), ("prev_candidate2", CAND2_VERSION, c2_path, CAND2_NAME)):
        x = c.get(key, {})
        if x.get("version") != ver or x.get("sha256") != sha256_text(p):
            raise GuardError("кандидат 3 записан против другого файла (%s): sha256 %s… ≠ файла %s %s…" % (what, str(x.get("sha256"))[:12], p, sha256_text(p)[:12]))
    for k in ("binary", "universe", "point_shares", "points", "windows"):
        if c.get(k) != fz.get(k):
            raise GuardError("кандидат 3: поле %r расходится с заморозкой %s" % (k, os.path.basename(freeze_path)))
    db = c.get("ds_binary", {})
    if len(db.get("md5", "")) != 32 or len(db.get("sha256", "")) != 64 or not db.get("path"):
        raise GuardError("в кандидате 3 нет md5 / sha256 / пути `ds_binary`")
    if set(c.get("cells", {})) != {CAND3_CELL}:
        raise GuardError("кандидат 3: клетки %s ≠ [%s]" % (sorted(c.get("cells", {})), CAND3_CELL))
    cell, base = c["cells"][CAND3_CELL], fz["cells"].get("S_base")
    if base is None:
        raise GuardError("в заморозке нет клетки S_base")
    keys = ds_keys(base["keys"])
    if cell.get("side") != "S" or cell.get("base_keys") != base["keys"] or cell.get("base_vid") != base["vid"] or cell.get("keys") != keys or cell.get("family") is not False:
        raise GuardError("кандидат 3: клетка %s ≠ S_base + `lx=1 tf=60`" % CAND3_CELL)
    if rf.Req("S", keys, False).vid != cell.get("vid"):
        raise GuardError("кандидат 3: vid %s ≠ вычисленному %s" % (cell.get("vid"), rf.Req("S", keys, False).vid))
    a, a2 = c.get("alpha", {}), c2["alpha"]
    if set(a) != set(a2) | {CAND3_CELL}:
        raise GuardError("α кандидата 3 заданы для %s ≠ клеткам %s + %s" % (sorted(a), CAND2_NAME, CAND3_CELL))
    if not _near(sum(a.values()), ALPHA_TOTAL):
        raise GuardError("сумма α кандидата 3 %.6f ≠ %.2f" % (sum(a.values()), ALPHA_TOTAL))
    if a[CAND3_CELL] <= 0:
        raise GuardError("α(%s) %.6f ≤ 0" % (CAND3_CELL, a[CAND3_CELL]))
    donors = [k for k in a2 if not _near(a[k], a2[k])]
    if len(donors) != 1 or donors[0] not in DS_ALPHA_DONORS or not _near(a[donors[0]] + a[CAND3_CELL], a2[donors[0]]) or a[donors[0]] < 0.005 - 1e-12:
        raise GuardError("α: %s берёт α ровно с одной из долей %s (остаётся ≥ 0,005, сумма сохраняется); изменились %s" % (CAND3_CELL, "/".join(DS_ALPHA_DONORS), donors))
    if not _near(a["S_base"], a2["S_base"]) or a["S_base"] < DS_ALPHA_FLOOR_S_BASE - 1e-12:
        raise GuardError("α(S_base) %.6f: пол %.3f (В-167) и без изменений против tk641 (%.6f)" % (a["S_base"], DS_ALPHA_FLOOR_S_BASE, a2["S_base"]))
    ak = c.get("alpha_k", {})
    if set(ak) != set(a) or any(set(ak[k]) != set(fz["point_shares"]) or any(not _near(ak[k][p], a[k] * sh) for p, sh in fz["point_shares"].items()) for k in ak):
        raise GuardError("α по точкам кандидата 3 ≠ α × доли точек")
    return c


def apply_candidate3(fz, c3):
    """Заморозка с α tk670 (C1). Клетка `D_S` в `cells` НЕ входит: пакет точки идёт замороженным бинарём без ключа `lx`, `D_S` — отдельный прогон `run_ds` бинарём `ds_binary`."""
    out = dict(fz)
    out["alpha"] = dict(c3["alpha"])
    return out


def check_ds_binary(path, c3):
    if not path or not os.path.exists(path):
        raise GuardError("--ds-bin: нет файла %r (нужен бинарь `D_S` кандидата 3)" % path)
    sha, md5 = rr.file_hashes(path)
    b = c3["ds_binary"]
    if md5 != b["md5"] or sha != b["sha256"]:
        raise GuardError("ds-бинарь %s: md5 %s / sha256 %s… ≠ замороженным %s / %s…" % (path, md5, sha[:12], b["md5"], b["sha256"][:12]))
    return md5, sha


def resolve_candidate3(a, fz, cand, c2, repo):
    """(кандидат 3, {поля маркера}). Нет файла — `GuardError` всегда (α tk641 перекрыты; молчаливого возврата нет), кроме тестов прежнего поведения (`REQUIRE_CANDIDATE3 = False`).
    `--no-candidate3` — единственный обход: α tk670 применяются, `D_S` не читается."""
    explicit = getattr(a, "candidate3", "")
    path = explicit or candidate3_path(a.freeze)
    if not os.path.exists(path):
        if explicit or REQUIRE_CANDIDATE3:
            raise GuardError("нет файла кандидата %s: кандидат %s в силе, α S_mk1 / L_base без него не применяются — читать нельзя (без чтения — `--no-candidate3`, файл всё равно нужен)" % (path, CAND3_CELL))
        return None, {}
    cand_path = getattr(a, "candidate", "") or candidate_path(a.freeze)
    c2_path = getattr(a, "candidate2", "") or candidate2_path(a.freeze)
    c3 = load_candidate3(path, fz, a.freeze, cand, cand_path, c2, c2_path)
    commit, sha = check_freeze_committed(path, repo, getattr(a, "candidate3_commit", ""), getattr(a, "candidate3_sha256", ""))
    info = {"candidate3": CAND3_CELL, "candidate3_commit": commit, "candidate3_sha256": sha, "candidate3_read": not getattr(a, "no_candidate3", False)}
    if not info["candidate3_read"]:
        info["candidate3_note"] = "кандидат 3 не читан (--no-candidate3): α по кандидату 3 применены (S_mk1 %s, L_base %s)" % (c3["alpha"]["S_mk1"], c3["alpha"]["L_base"])
    return c3, info


def ds_missing_pairs(pdir, wins, coins, lohi_of):
    """C2 `D_S`: пары (монета, сутки зачёта окна на точке) с файлом aggtrades суток, у которых нет файла klines ПРЕДЫДУЩИХ суток (уровень `D` рождается из вчерашних баров монеты;
    первые сутки листинга тоже считаются пропуском — консервативно) → (пропущено, всего). Только имена файлов."""
    miss = tot = 0
    for w in wins:
        lo, hi = lohi_of(w)
        for sym in coins:
            have = {}
            for sub in ("aggtrades", "klines"):
                have[sub] = set()
                p = os.path.join(pdir, w.name, sub, sym)
                if os.path.isdir(p):
                    for name in os.listdir(p):
                        m = _DATE.search(name)
                        if m:
                            have[sub].add(d(m.group(1)))
            day = lo
            while day <= hi:
                if day in have["aggtrades"]:
                    tot += 1
                    if day - datetime.timedelta(days=1) not in have["klines"]:
                        miss += 1
                day += datetime.timedelta(days=1)
    return miss, tot


def ds_meta(paths, w, vid):
    """Счётчики движка `D_S` в окне (`meta.json`, сторона шорт): сделок, сигналов, дней без файла; None — прогона окна нет."""
    p = os.path.join(paths.out(w.name), vid, "meta.json")
    if not os.path.exists(p):
        return None
    s = json.load(open(p, encoding="utf-8")).get(rf.ENGINE_SIDE["S"])
    return {"trades": int(s["trades"]), "signals_total": int(s["signals_total"]), "missing_days": int(s["missing_days"])} if s else None


def run_ds(paths, runner, symbols_text, bin_path, threads, wins, c3, coins, lohi_of):
    """`D_S` на одной вселенной (C2): доля пар (монета, сутки зачёта) без klines предыдущих суток ≤ 1 % → движок `ds_binary` с замороженными ключами → таблицы шорта окон.
    Любая `GuardError` здесь — клетка НЕ читается (причина в `info`), остальные клетки не страдают. → ({окно: таблица сделок} | None, info)."""
    req = rf.Req("S", c3["cells"][CAND3_CELL]["keys"], False)
    info = {"status": "skip", "reason": "", "missing_share": None, "missing_pairs": None, "pairs": None, "windows": {}}
    eng = rr.Engine(paths, runner, symbols_text, bin_path, threads, None, windows=tuple(wins), prefix="d")
    try:
        miss, tot = ds_missing_pairs(paths.root, wins, coins, lohi_of)
        info.update(missing_pairs=miss, pairs=tot, missing_share=(miss / tot if tot else 0.0))
        if info["missing_share"] > DS_MISSING_MAX_SHARE:
            raise TableSkip("доля пар (монета, сутки зачёта) без klines предыдущих суток %d / %d = %.2f %% > %.0f %%: уровень `D` без вчерашних баров, клетка не читается" % (
                miss, tot, 100 * info["missing_share"], 100 * DS_MISSING_MAX_SHARE))
        eng.launch([req])
        tabs = {w.name: rf.read_trades_csv(os.path.join(paths.out(w.name), req.vid, rf.ENGINE_SIDE["S"] + "-trades.csv")) for w in wins}
    except GuardError as e:
        info["reason"] = str(e)
        return None, info
    for w in wins:
        info["windows"][w.name] = ds_meta(paths, w, req.vid)
    info["status"] = "read"
    return tabs, info


def replay_ds(a, runner=None, repo=REPO):
    """C3: `D_S` в выборке (wa / wb / wc, h775) ТЕМ ЖЕ путём (`rr.Engine`, замороженные ключи, `ds_binary`); `FILES_670` побайтно как выходы TK-668 (`--ref-dir/<окно>/S_lx1/`).
    Сутки ≥ 14.11 не читает (деревья окон проверяются). Итог — `--out` (`gate-670c.json`); различие — код 3."""
    fz = load_freeze(a.freeze)
    fcommit, fsha = check_freeze_committed(a.freeze, repo, a.freeze_commit, a.freeze_sha256)
    cpath = getattr(a, "candidate", "") or candidate_path(a.freeze)
    cand = load_candidate(cpath, fz, a.freeze)
    check_freeze_committed(cpath, repo, getattr(a, "candidate_commit", ""), getattr(a, "candidate_sha256", ""))
    c2, _ = resolve_candidate2(a, fz, cand, repo)
    c3, c3info = resolve_candidate3(a, fz, cand, c2, repo)
    if c3 is None or not c3info["candidate3_read"]:
        raise GuardError("replay-ds: нужен кандидат %s без --no-candidate3" % CAND3_NAME)
    md5, _sha = check_ds_binary(a.ds_bin, c3)
    cell = c3["cells"][CAND3_CELL]
    req = rf.Req("S", cell["keys"], False)
    if not a.ref_dir or not os.path.isdir(a.ref_dir):
        raise GuardError("--ref-dir: нет выходов TK-668 (%r)" % a.ref_dir)
    wins = rf.WINDOWS
    os.makedirs(a.replay_root, exist_ok=True)
    for w in wins:
        link, src = os.path.join(a.replay_root, w.name), os.path.join(a.windows_root, w.name)
        if not os.path.lexists(link):
            os.symlink(src, link)
        elif os.path.realpath(link) != os.path.realpath(src):
            raise GuardError("%s указывает не на %s" % (link, src))
    ntree = check_tree_days(a.replay_root, wins, d(LISTING_AFTER))
    text = rr.read_symbols(a.symbols_file)[0]
    paths = UPaths(a.replay_root, uni="replay", tick_store=a.tick_store, sig_cache=a.sig_cache)
    rr.Engine(paths, runner or rr.SubprocessRunner(), text, a.ds_bin, a.threads, None, windows=tuple(wins), prefix="d").launch([req])
    res = {"ds_bin_md5": md5, "keys": cell["keys"], "vid": req.vid, "cell": CAND3_CELL, "candidate3_commit": c3info["candidate3_commit"], "candidate3_sha256": c3info["candidate3_sha256"],
           "freeze_commit": fcommit, "freeze_sha256": fsha, "tree_files": ntree, "ref_dir": a.ref_dir, "windows": {}}
    for w in wins:
        files = {}
        for f in FILES_670:
            x, y = os.path.join(paths.out(w.name), req.vid, f), os.path.join(a.ref_dir, w.name, DS_REF_SUB, f)
            sx, sy = (sha256_stream(x) if os.path.exists(x) else None), (sha256_stream(y) if os.path.exists(y) else None)
            files[f] = {"sha256": sx, "ref_sha256": sy, "equal": sx is not None and sx == sy}
        res["windows"][w.name] = {"files": files, "equal": all(v["equal"] for v in files.values())}
    res["ok"] = all(v["equal"] for v in res["windows"].values())
    out = a.out or os.path.join(a.replay_root, "gate-670c.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("G-670c: %s (%s); %s" % ("ПРОЙДЕНЫ" if res["ok"] else "НЕ ПРОЙДЕНЫ", ", ".join("%s %s" % (n, "=" if v["equal"] else "≠") for n, v in res["windows"].items()), out))
    return 0 if res["ok"] else 3


def joint_describe(rows_a, rows_b, to, margin=250.0):
    """Совместное чтение `S_base` (a) + `D_S` (b): ОПИСАТЕЛЬНО, без вердикта и без α (раздел «вместе с S_base» отчёта TK-668). Простая сумма суточных рядов зачётных строк; позиции не пересчитываются,
    лимит позиций и слоты не моделируются. n, Σ, $/сделку, шарп, макс. просадка $ (% маржи), суток до нового максимума, корреляция суточных P&L, пересечение сделок (та же монета; интервалы
    [вход, выход) пересекаются или входы в пределах ± 5 мин), по месяцам, депозит TK-664 (D1 = маржа × макс. позиций, D2 — мин. депозит с реализованным P&L; нет `tk664_funding_capital` — без депозита)."""
    ndays = (to - F0).days + 1
    Sa, _, _, _ = daily(rows_a, ndays)
    Sb, _, _, _ = daily(rows_b, ndays)
    S = Sa + Sb
    n = len(rows_a) + len(rows_b)
    cum = np.concatenate([[0.0], np.cumsum(S)])
    out = {"days": ndays, "n_a": len(rows_a), "n_b": len(rows_b), "n": n, "sum": float(S.sum()), "mean": float(S.sum() / n) if n else float("nan"), "sharpe": sharpe_of(S),
           "maxdd": float((np.maximum.accumulate(cum) - cum).max()), "sum_a": float(Sa.sum()), "sum_b": float(Sb.sum()), "sharpe_a": sharpe_of(Sa), "sharpe_b": sharpe_of(Sb)}
    out["maxdd_pct_margin"] = out["maxdd"] / margin * 100
    out["corr_daily"] = float(np.corrcoef(Sa, Sb)[0, 1]) if float(np.std(Sa)) > 0 and float(np.std(Sb)) > 0 else float("nan")
    try:
        import lib as _lib
        out["kpi"] = _lib.drawdown_kpi(S)
    except ImportError:
        out["kpi"] = None
    near = 5 * 60_000

    def paired(x, y):
        by = {}
        for r in y:
            by.setdefault(r[0], []).append(r)
        return [any((r[1] < q[2] and q[1] < r[2]) or abs(r[1] - q[1]) <= near for q in by.get(r[0], ())) for r in x]
    out["overlap"] = {"a_with_pair": int(sum(paired(rows_a, rows_b))), "b_with_pair": int(sum(paired(rows_b, rows_a))), "coins_a": len({r[0] for r in rows_a}), "coins_b": len({r[0] for r in rows_b}),
                      "coins_both": len({r[0] for r in rows_a} & {r[0] for r in rows_b})}
    mon = {}
    for tag, rows in (("a", rows_a), ("b", rows_b)):
        for r in rows:
            x = mon.setdefault(date_of(r[1]).strftime("%Y-%m"), {"n_a": 0, "sum_a": 0.0, "n_b": 0, "sum_b": 0.0})
            x["n_" + tag] += 1
            x["sum_" + tag] += r[3] - r[4]
    out["months"] = {m: mon[m] for m in sorted(mon)}
    try:
        import tk664_funding_capital as _t64
        allr = list(rows_a) + list(rows_b)
        t_in, t_out = np.array([r[1] for r in allr], dtype=np.int64), np.array([r[2] for r in allr], dtype=np.int64)
        sw = _t64.sweep(t_in, t_out, np.array([r[3] - r[4] for r in allr], dtype=float), t0_ms(F0), t0_ms(to) + DAY_MS)
        out["deposit"] = {"max_pos": sw["max"], "d1": sw["max"] * margin, "d2": sw["d2"]}
    except Exception as e:  # noqa: BLE001  — депозит TK-664 — справка к описательному чтению: его сбой не должен ронять прочтение точки после маркера
        out["deposit"] = {"note": "без депозита: %s: %s" % (type(e).__name__, e)}
    return out


def cell_point_stats(fz, cell, point, rows, entries_to, rates=None, nboot=NBOOT, uni=None):
    """Статистика клетки на точке. `uni` — вселенная: α_k только у решающей вселенной (`uni` не задан — решающая); у остальных — «описательно»."""
    ndays = (entries_to - F0).days + 1
    S, N, _, _ = daily(rows, ndays)
    cs, cn = coin_arrays(rows)
    n = int(N.sum())
    st = {"cell": cell, "uni": uni or fz["universe"]["decision"], "n": n, "days": ndays, "sum": float(S.sum()), "mean": float(S.sum() / n) if n else float("nan"), "sharpe": sharpe_of(S),
          "forced": sum(1 for r in rows if r[6] == "forced")}
    if n == 0:
        st.update({"p": 1.0, "alpha_k": 0.0, "mean_wo_days": float("nan"), "mean_wo_coins": float("nan"), "fund_ok": False, "worst_days": float("nan"),
                   "worst_coins": float("nan"), "fund_bound": 0.0, "fund_mode": "—"})
    else:
        o = rf.own_stat(S, N, nboot)
        B, mode = funding_bound_of(rows, rates)
        st.update({"p": o.p, "se": o.se, "lower": o.lower, "upper": o.upper, "mean_wo_days": mean_without(S, N, DROP), "mean_wo_coins": mean_without(cs, cn, DROP),
                   "worst_days": mean_without(S, N, DROP, best=False), "worst_coins": mean_without(cs, cn, DROP, best=False),
                   "fund_bound": B, "fund_mode": mode, "fund_ok": bool((S.sum() - B) / n > 0)})
    st["alpha_k"] = alpha_k(fz, cell, point["id"]) if cell in fz["alpha"] and st["uni"] == fz["universe"]["decision"] else 0.0
    st["decision"] = decide(point, point["id"] == fz["points"][-1]["id"], st)
    return st


# ---- кандидат вперёд `L_n24` (TK-638, §13.36 п. 13; условия критика C1–C3) ----------------------------------------------------------------

class TableSkip(GuardError):
    """Таблица `L_n24` не годится для чтения (C2): клетка на этой вселенной / точке не читается, остальные клетки считаются как обычно."""


def candidate_path(freeze_path):
    return os.path.join(os.path.dirname(os.path.abspath(freeze_path)), CAND_NAME)


def _near(a, b):
    return abs(a - b) <= 1e-12


def load_candidate(path, fz, freeze_path):
    """C1: проверяет файл кандидата против заморозки `tk626-frozen.json` (`fz`, прочитана из `freeze_path`) до любых данных. Кандидат — отдельный файл: α берётся ИЗ него.
    Версия; sha256 заморозки в кандидате = sha256 файла `freeze_path`; бинарь / вселенная / доли точек / точки / окна — равны заморозке; клетка `L_n24` = `L_base` + `fa=… fo=lo fv=0.5`;
    α: набор = α заморозки ∪ {L_n24}, сумма 0,05, S_base + L_n24 = α(S_base) заморозки, остальные α не меняются, α по точкам = α × доли."""
    if not os.path.exists(path):
        raise GuardError("нет файла кандидата %s" % path)
    c = json.load(open(path, encoding="utf-8"))
    if c.get("version") != CAND_VERSION:
        raise GuardError("версия кандидата %r ≠ %r" % (c.get("version"), CAND_VERSION))
    bf = c.get("base_freeze", {})
    if bf.get("version") != VERSION or bf.get("sha256") != sha256_text(freeze_path):
        raise GuardError("кандидат записан против другой заморозки: sha256 в кандидате %s… ≠ файла %s %s…" % (str(bf.get("sha256"))[:12], freeze_path, sha256_text(freeze_path)[:12]))
    for k in ("binary", "universe", "point_shares", "points", "windows"):
        if c.get(k) != fz.get(k):
            raise GuardError("кандидат: поле %r расходится с заморозкой %s" % (k, os.path.basename(freeze_path)))
    fb = c.get("feature_binary", {})
    if len(fb.get("md5", "")) != 32 or len(fb.get("sha256", "")) != 64:
        raise GuardError("в кандидате нет md5 / sha256 `alpha-feat`")
    flt = c.get("filter", {})
    if not isinstance(flt.get("N"), (int, float)) or isinstance(flt.get("N"), bool) or flt["N"] <= 0 or not flt.get("name"):
        raise GuardError("кандидат: фильтр без имени или без порога N")
    if set(c.get("cells", {})) != {CAND_CELL}:
        raise GuardError("кандидат: клетки %s ≠ [%s]" % (sorted(c.get("cells", {})), CAND_CELL))
    cell, base = c["cells"][CAND_CELL], fz["cells"].get("L_base")
    if base is None:
        raise GuardError("в заморозке нет клетки L_base")
    keys = base["keys"] + " fa=%s fo=lo fv=0.5" % flt["name"]
    if cell.get("side") != "L" or cell.get("base_keys") != base["keys"] or cell.get("base_vid") != base["vid"] or cell.get("keys") != keys or cell.get("family") is not False:
        raise GuardError("кандидат: клетка %s ≠ L_base + `fa=%s fo=lo fv=0.5`" % (CAND_CELL, flt["name"]))
    if rf.Req("L", keys, False).vid != cell.get("vid"):
        raise GuardError("кандидат: vid %s ≠ вычисленному %s" % (cell.get("vid"), rf.Req("L", keys, False).vid))
    a, a0 = c.get("alpha", {}), fz["alpha"]
    if set(a) != set(a0) | {CAND_CELL}:
        raise GuardError("α кандидата заданы для %s ≠ клеткам заморозки + %s" % (sorted(a), CAND_CELL))
    if not _near(sum(a.values()), ALPHA_TOTAL):
        raise GuardError("сумма α кандидата %.6f ≠ %.2f" % (sum(a.values()), ALPHA_TOTAL))
    if not _near(a["S_base"] + a[CAND_CELL], a0["S_base"]) or a[CAND_CELL] <= 0:
        raise GuardError("α: S_base %.6f + %s %.6f ≠ α(S_base) заморозки %.6f (кандидат берёт α только из доли S_base)" % (a["S_base"], CAND_CELL, a[CAND_CELL], a0["S_base"]))
    for k, v in a0.items():
        if k != "S_base" and not _near(a[k], v):
            raise GuardError("α(%s) кандидата %.6f ≠ заморозке %.6f" % (k, a[k], v))
    ak = c.get("alpha_k", {})
    if set(ak) != set(a) or any(set(ak[k]) != set(fz["point_shares"]) or any(not _near(ak[k][p], a[k] * sh) for p, sh in fz["point_shares"].items()) for k in ak):
        raise GuardError("α по точкам кандидата ≠ α × доли точек")
    return c


def apply_candidate(fz, cand):
    """Заморозка с α кандидата (C1). Клетка `L_n24` в `cells` НЕ входит: базовый пакет не должен взять её без таблицы `--ftab`."""
    out = dict(fz)
    out["alpha"] = dict(cand["alpha"])
    return out


def check_feat_binary(path, cand):
    if not path or not os.path.exists(path):
        raise GuardError("--feat-bin: нет файла %r (нужен `alpha-feat` кандидата)" % path)
    sha, md5 = rr.file_hashes(path)
    b = cand["feature_binary"]
    if md5 != b["md5"] or sha != b["sha256"]:
        raise GuardError("alpha-feat %s: md5 %s / sha256 %s… ≠ замороженным %s / %s…" % (path, md5, sha[:12], b["md5"], b["sha256"][:12]))
    return md5, sha


def resolve_candidate(a, fz, repo):
    """(кандидат, {поля маркера}): файл `--candidate`, иначе `tk638-frozen.json` рядом с `--freeze`. Кандидат в силе (α уже потрачены: S_base 0,025), поэтому нет файла — `GuardError`, молчаливого возврата к α tk626 нет.
    Файл найден — целиком под охраной (C1, коммит); `--no-candidate` — α по кандидату применяются, но `L_n24` не читается (маркер: «кандидат не читан»); иначе ещё и `alpha-feat` (md5 / sha256)."""
    explicit = getattr(a, "candidate", "")
    path = explicit or candidate_path(a.freeze)
    if not os.path.exists(path):
        if explicit or REQUIRE_CANDIDATE:
            raise GuardError("нет файла кандидата %s: кандидат %s в силе, α S_base без него (0,03) не применяются — читать нельзя (без чтения кандидата — `--no-candidate`, файл всё равно нужен)" % (path, CAND_CELL))
        return None, {}
    cand = load_candidate(path, fz, a.freeze)
    commit, sha = check_freeze_committed(path, repo, getattr(a, "candidate_commit", ""), getattr(a, "candidate_sha256", ""))
    info = {"candidate": CAND_CELL, "candidate_commit": commit, "candidate_sha256": sha, "candidate_read": False}
    if getattr(a, "no_candidate", False):
        info["candidate_note"] = "кандидат не читан (--no-candidate): α по кандидату применены (S_base %s)" % cand["alpha"]["S_base"]
        return cand, info
    fmd5, fsha = check_feat_binary(getattr(a, "feat_bin", ""), cand)
    info.update({"feat_bin_md5": fmd5, "feat_bin_sha256": fsha, "candidate_read": True})
    return cand, info


def n24_req(cand):
    return rf.Req("L", cand["cells"][CAND_CELL]["keys"], False)


def read_sigsx_keys(path):
    """(монеты, sig_ms) кандидатов потока из `long-sigsx.csv` дампа (по позициям: в заголовке бинаря нет `natr_e8`); порядок файла; дубль ключа — остановка."""
    syms, ms = [], []
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        head = next(rd, None)
        if not head or head[0] != "sym" or head[2] != "sig_ms":
            raise GuardError("сигнальный дамп %s: заголовок %r" % (path, head))
        for r in rd:
            if r:
                syms.append(r[0])
                ms.append(int(r[2]))
    if len(set(zip(syms, ms))) != len(syms):
        raise GuardError("сигнальный дамп %s: ключ (sym, sig_ms) не уникален" % path)
    return syms, np.array(ms, dtype=np.int64)


def read_counts(path):
    """Выход `alpha-feat tcount`: (монеты, sig_ms, n24, m24)."""
    syms, ms, n24, m24 = [], [], [], []
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        if next(rd, None) != ["sym", "sig_ms", "n60", "n24", "m60", "m24"]:
            raise GuardError("выход tcount %s: заголовок не `sym,sig_ms,n60,n24,m60,m24`" % path)
        for r in rd:
            if r:
                syms.append(r[0])
                ms.append(int(r[1]))
                n24.append(int(r[3]))
                m24.append(int(r[5]) != 0)
    return syms, np.array(ms, dtype=np.int64), np.array(n24, dtype=np.int64), np.array(m24, dtype=bool)


def build_n24_table(paths, runner, eng, w, need, cand, feat_bin, threads, tick_store, lohi):
    """Таблица `L_n24` одного окна: сигнальный дамп движка по потоку `L_base` → `alpha-feat tcount` → CSV `sym,sig_ms,<фильтр>` (0,0 — исключён: n24 < N и не m24; 1,0 — пропущен; m24 = 1 проходит).
    `lohi` — зачётные даты окна на точке: по ним считается доля кандидатов с m24 = 1. → информация окна."""
    vfile = os.path.join(paths.var, "sx-%s-%s.txt" % (w.name, need.vid))
    os.makedirs(paths.var, exist_ok=True)
    with open(vfile, "w", encoding="utf-8", newline="\n") as f:
        f.write("%s %s\n" % (need.vid, need.keys))
    cmd, env = eng.command(w, vfile, paths.sx(w.name), None, sigsx_only=True)
    log = os.path.join(paths.log, "sx-%s-%s-%s.log" % (paths.uni, w.name, need.vid))
    if runner.run(cmd, env, log) != 0:
        raise GuardError("движок (сигнальный дамп %s), окно %s: код возврата ≠ 0\n%s" % (paths.uni, w.name, rr.tail(log)))
    dump = os.path.join(paths.sx(w.name), need.vid, "long-sigsx.csv")
    if not os.path.exists(dump):
        raise GuardError("нет сигнального дампа %s" % dump)
    syms, ms = read_sigsx_keys(dump)
    d_ = paths.need_dir(w.name, need)
    os.makedirs(d_, exist_ok=True)
    eps, counts, tab = (os.path.join(d_, n) for n in ("eps.csv", "counts.csv", "ftab.csv"))
    with open(eps, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms\n" + "".join("%s,%d\n" % (s, m) for s, m in zip(syms, ms)))
    cmd = [feat_bin, "tcount", "--eps", eps, "--out", counts, "--base-date", w.t0, "--trades", paths.trades(w.name), "--threads", str(threads)]
    if tick_store:
        cmd += ["--store", tick_store]
    log = os.path.join(paths.log, "tcount-%s-%s-%s.log" % (paths.uni, w.name, need.vid))
    if runner.run(cmd, None, log) != 0:
        raise GuardError("alpha-feat tcount (%s), окно %s: код возврата ≠ 0\n%s" % (paths.uni, w.name, rr.tail(log)))
    csyms, cms, n24, m24 = read_counts(counts)
    if csyms != syms or not np.array_equal(cms, ms):
        raise TableSkip("окно %s: строк tcount %d ≠ строк входа (кандидатов %d) или ключи разошлись" % (w.name, len(csyms), len(syms)))
    col = np.where(~m24 & (n24 < cand["filter"]["N"]), 0.0, 1.0)
    with open(tab, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms,%s\n" % cand["filter"]["name"] + "".join("%s,%d,%.1f\n" % (s, m, v) for s, m, v in zip(syms, ms, col)))
    off = t0_ms(w.t0d)
    cr = np.array([lohi[0] <= date_of(int(m) + off) <= lohi[1] for m in ms], dtype=bool)
    return {"rows": len(syms), "credited": int(cr.sum()), "m24_credited": int((m24 & cr).sum()), "m24_all": int(m24.sum()), "excluded": int((col == 0.0).sum()),
            "credited_range": [lohi[0].isoformat(), lohi[1].isoformat()], "ftab_sha256": sha256_stream(tab), "counts_sha256": sha256_stream(counts)}


def n24_meta(paths, w, vid):
    """Счётчики фильтра движка окна (`meta.json`): кандидатов, отброшено, no_key, no_value; None — прогона окна нет."""
    p = os.path.join(paths.out(w.name), vid, "meta.json")
    if not os.path.exists(p):
        return None
    st = json.load(open(p, encoding="utf-8")).get(rf.ENGINE_SIDE["L"], {}).get("filter_[cands,dropped,no_key,no_value]")
    return {"cands": int(st[0]), "dropped": int(st[1]), "no_key": int(st[2]), "no_value": int(st[3])} if st else None


def run_n24(paths, runner, symbols_text, bin_path, threads, wins, cand, feat_bin, tick_store, lohi_of):
    """`L_n24` на одной вселенной (C2): таблицы окон → доля `m24 = 1` ≤ 1 % зачётных кандидатов точки → движок с `--ftab` → охрана счётчиков (`no_key = 0`, `cands` = строк, `dropped` = пересчёту).
    Любая `GuardError` здесь — клетка НЕ читается (причина в `info`), остальные клетки не страдают. → ({окно: таблица сделок} | None, info)."""
    req = n24_req(cand)
    need = rr.Engine.stream_need(req)
    info = {"status": "skip", "reason": "", "m24_share": None, "no_key": None, "windows": {}}
    eng = rr.Engine(paths, runner, symbols_text, bin_path, threads, None, ensure_tables=lambda needs: None, windows=tuple(wins), prefix="n")
    try:
        for w in wins:
            info["windows"][w.name] = build_n24_table(paths, runner, eng, w, need, cand, feat_bin, threads, tick_store, lohi_of(w))
        cr = sum(x["credited"] for x in info["windows"].values())
        m24 = sum(x["m24_credited"] for x in info["windows"].values())
        info["m24_share"] = m24 / cr if cr else 0.0
        if info["m24_share"] > M24_MAX_SHARE:
            raise TableSkip("доля кандидатов L_base с m24 = 1 среди зачётных %d / %d = %.2f %% > %.0f %%: сутки до сигнала отсутствуют, клетка не читается" % (
                m24, cr, 100 * info["m24_share"], 100 * M24_MAX_SHARE))
        eng.launch([req])
        tabs = {w.name: rf.read_trades_csv(os.path.join(paths.out(w.name), req.vid, "long-trades.csv")) for w in wins}
    except GuardError as e:
        info["reason"] = str(e)
        for w in wins:
            m = n24_meta(paths, w, req.vid)
            if m and w.name in info["windows"]:
                info["windows"][w.name]["engine"] = m
        nk = [x["engine"]["no_key"] for x in info["windows"].values() if "engine" in x]
        info["no_key"] = sum(nk) if nk else None
        return None, info
    for w in wins:
        info["windows"][w.name]["engine"] = n24_meta(paths, w, req.vid)
    info["status"] = "read"
    info["no_key"] = sum(x["engine"]["no_key"] for x in info["windows"].values())
    return tabs, info


FILES_638 = ("long-trades.csv", "long-daily.csv", "long-coinday.csv", "long-entryrej.csv")


def replay_n24(a, runner=None, repo=REPO):
    """C3: `L_n24` в выборке (wa / wb / wc, h775) ТЕМ ЖЕ путём (`run_n24`: дамп → tcount → таблица → движок `--ftab`); выходы `long-{trades,daily,coinday,entryrej}.csv` — побайтно как у G-638a (`--ref`).
    Читает сутки ≤ 13.11 (деревья окон проверяются). Итог — `--out` (`gate-638c.json`); различие или пропуск клетки — код 3."""
    fz = load_freeze(a.freeze)
    fcommit, fsha = check_freeze_committed(a.freeze, repo, a.freeze_commit, a.freeze_sha256)
    cand, cinfo = resolve_candidate(a, fz, repo)
    if cand is None or not cinfo["candidate_read"]:
        raise GuardError("replay-n24: нужен кандидат %s без --no-candidate" % CAND_NAME)
    md5, _sha = check_binary(a.bin, fz)
    if not a.ref or not os.path.exists(a.ref):
        raise GuardError("--ref: нет эталона G-638a (%r)" % a.ref)
    ref = json.load(open(a.ref, encoding="utf-8"))
    if ref.get("bin_md5") != md5 or ref.get("keys") != cand["cells"][CAND_CELL]["keys"] or not ref.get("ok"):
        raise GuardError("эталон G-638a: бинарь %s / ключи / итог не совпадают с кандидатом" % ref.get("bin_md5"))
    wins = rf.WINDOWS
    os.makedirs(a.replay_root, exist_ok=True)
    for w in wins:
        link, src = os.path.join(a.replay_root, w.name), os.path.join(a.windows_root, w.name)
        if not os.path.lexists(link):
            os.symlink(src, link)
        elif os.path.realpath(link) != os.path.realpath(src):
            raise GuardError("%s указывает не на %s" % (link, src))
    ntree = check_tree_days(a.replay_root, wins, d(LISTING_AFTER))
    text = rr.read_symbols(a.symbols_file)[0]
    paths = UPaths(a.replay_root, uni="replay", tick_store=a.tick_store, sig_cache=a.sig_cache)
    tabs, info = run_n24(paths, runner or rr.SubprocessRunner(), text, a.bin, a.threads, wins, cand, a.feat_bin, a.tick_store, lambda w: (w.lod, w.hid))
    res = {"bin_md5": md5, "feat_bin_md5": cinfo["feat_bin_md5"], "keys": cand["cells"][CAND_CELL]["keys"], "cell": CAND_CELL, "candidate_commit": cinfo["candidate_commit"],
           "candidate_sha256": cinfo["candidate_sha256"], "freeze_commit": fcommit, "freeze_sha256": fsha, "tree_files": ntree, "n24": info, "windows": {}, "ok": False}
    if tabs is not None:
        vid = n24_req(cand).vid
        for w in wins:
            files = {}
            for f in FILES_638:
                x, y = sha256_stream(os.path.join(paths.out(w.name), vid, f)), ref["windows"][w.name]["files"][f]["ref_sha256"]
                files[f] = {"sha256": x, "ref_sha256": y, "equal": x == y}
            res["windows"][w.name] = {"ftab_sha256": info["windows"][w.name]["ftab_sha256"], "files": files, "equal": all(v["equal"] for v in files.values())}
        res["ok"] = all(v["equal"] for v in res["windows"].values())
    out = a.out or os.path.join(a.replay_root, "gate-638c.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("G-638c: %s (%s); m24 %s; no_key %s; %s" % ("ПРОЙДЕНЫ" if res["ok"] else "НЕ ПРОЙДЕНЫ", ", ".join("%s %s" % (n, "=" if v["equal"] else "≠") for n, v in res["windows"].items()) or
                                                      "клетка не прочитана: " + info["reason"], info["m24_share"], info["no_key"], out))
    return 0 if res["ok"] else 3


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


def manifest_path(root, pid):
    return os.path.join(root, "files-%s.tsv" % pid)


def key_of(cell, uni):
    return "%s|%s" % (cell, uni)


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
    cand, cinfo = resolve_candidate(a, fz, repo)
    if cand is not None:
        fz = apply_candidate(fz, cand)                                   # C1: α из кандидата (S_base 0,025 + L_n24 0,005), не из tk626-frozen.json
    run_cand = cand is not None and cinfo["candidate_read"]
    c2, c2info = resolve_candidate2(a, fz, cand, repo)
    if c2 is not None:
        fz = apply_candidate2(fz, c2, c2info["candidate2_read"])         # TK-641 C1: α из tk641 (S_base 0,020 + L_mk2 0,005); `L_mk2` — обычная клетка пакета
    c3, c3info = resolve_candidate3(a, fz, cand, c2, repo)
    if c3 is not None:
        fz = apply_candidate3(fz, c3)                                    # TK-670 C1: α из tk670 (S_base 0,020 + D_S 0,005, доля S_mk1 / L_base); `D_S` — отдельный прогон `run_ds`
    run_ds_cell = c3 is not None and c3info["candidate3_read"]
    pt = get_point(fz, a.point)
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    check_point_date(pt, today)
    md5, sha = check_binary(a.bin, fz)
    if run_ds_cell:
        check_ds_binary(getattr(a, "ds_bin", ""), c3)                    # C1: бинарь `D_S` = замороженному ДО claim_read (иначе точка не «сгорит»)
    wins_all = {w.name: w for w in windows_of(fz)}
    wins_used = [wins_all[n] for n in pt["windows"]]
    D, to = d(pt["d"]), d(pt["entries_to"])
    pdir = os.path.join(a.root, pt["id"])
    prev = prev_point(fz, pt["id"])
    prev_rows, prev_lists, prev_man = None, None, None
    if prev is not None:
        pp = tables_path(a.root, prev["id"])
        if not os.path.exists(pp):
            raise GuardError("нет таблиц точки %s (%s): префикс-охрана невозможна, точки читаются по порядку" % (prev["id"], pp))
        saved = json.load(open(pp, encoding="utf-8"))
        prev_rows, prev_lists = {c: [tuple(r) for r in v] for c, v in saved["rows"].items()}, saved["universe"]
        mp = manifest_path(a.root, prev["id"])
        if not os.path.exists(mp):
            raise GuardError("нет манифеста файлов точки %s (%s)" % (prev["id"], mp))
        prev_man = read_manifest(mp)
    uni = check_universe(fz, a.universe_dir or os.path.join(pdir, "universe"), a.base_dir, pt, prev_lists)
    ntree = check_tree_days(pdir, wins_used, D, allowed=set(uni["lists"]["all"]))
    man = tree_manifest(pdir, wins_used)
    if prev_man is not None:
        bad = manifest_guard(prev_man, man)
        if bad:
            raise GuardError("данные между точками изменились (%d): %s" % (len(bad), bad[:3]))
    claim_read(a.root, pt["id"], {"point": pt["id"], "bin_md5": md5, "bin_sha256": sha, "freeze_commit": commit, "freeze_sha256": fsha, "tree_files": ntree,
                                 "universe_sha256": uni["sha256"], "universe_n": {n: len(v) for n, v in uni["lists"].items()}, "contracts_sha256": uni["contracts_sha256"],
                                 **cinfo, **c2info, **c3info, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    reqs = reqs_of(fz)
    tabs, byw, n24, ds_info = {}, {}, {}, {}
    rn = runner or rr.SubprocessRunner()
    for u in fz["universe"]["read"]:
        paths = UPaths(pdir, uni=u, tick_store=a.tick_store, sig_cache=a.sig_cache)
        eng = rr.Engine(paths, rn, uni["text"][u], a.bin, a.threads, None, windows=tuple(wins_used), prefix="f")
        eng.launch(list(reqs.values()))
        t, b = read_cell_tables(paths, reqs, wins_used)
        for c in reqs:
            tabs[key_of(c, u)], byw[key_of(c, u)] = t[c], b[c]
        if run_cand:                                                     # C2: любая охрана таблицы `L_n24` — клетка не читается, остальные считаются
            t24, n24[u] = run_n24(paths, rn, uni["text"][u], a.bin, a.threads, wins_used, cand, a.feat_bin, a.tick_store, lambda w: (max(w.lod, F0), min(w.hid, to)))
            if t24 is not None:
                tabs[key_of(CAND_CELL, u)] = t24
                byw[key_of(CAND_CELL, u)] = {w.name: rows_of(t24[w.name], w) for w in wins_used}
        if run_ds_cell:                                                  # C2 `D_S`: доля пар без klines предыдущих суток > 1 % или любая охрана — клетка не читается, остальные считаются
            tds, ds_info[u] = run_ds(paths, rn, uni["text"][u], a.ds_bin, a.threads, wins_used, c3, uni["lists"][u], lambda w: (max(w.lod, F0), min(w.hid, to)))
            if tds is not None:
                tabs[key_of(CAND3_CELL, u)] = tds
                byw[key_of(CAND3_CELL, u)] = {w.name: rows_of(tds[w.name], w) for w in wins_used}
    for k in byw:
        for w in wins_used:
            end_of_data_check(byw[k][w.name], w, D)
    seam = {k: seams_check(tabs[k], wins_used, to) for k in byw}
    bad = {k: x[:5] for k, x in seam.items() if x}
    if bad:
        raise GuardError("G3 швов окон: %s" % bad)
    rows = {k: splice_rows(byw[k], wins_used, to) for k in byw}
    pref, forced = {}, {}
    if prev_rows is not None:
        pref, forced = prefix_guard({c: v for c, v in prev_rows.items() if c in rows}, rows, d(prev["entries_to"]))        # только клетки, посчитанные сейчас (`L_n24` мог не читаться)
        badp = {c: x[:5] for c, x in pref.items() if x}
        if badp:
            raise GuardError("префикс-охрана точки %s против %s: %s" % (pt["id"], prev["id"], badp))
    os.makedirs(a.root, exist_ok=True)
    json.dump({"point": pt["id"], "entries_to": pt["entries_to"], "rows": rows, "universe": uni["lists"]}, open(tables_path(a.root, pt["id"]), "w", encoding="utf-8"))
    write_manifest(manifest_path(a.root, pt["id"]), man)
    rates = None
    if a.funding:
        months = sorted({(F0 + datetime.timedelta(days=i)).strftime("%Y-%m") for i in range((to - F0).days + 1)})
        rates = rf.load_funding_rates(a.funding, uni["lists"]["all"], months=tuple(months)) or None
    stats = {k: cell_point_stats(fz, k.split("|")[0], pt, rows[k], to, rates, a.nboot, uni=k.split("|")[1]) for k in rows}
    joint = None
    sk, dk = key_of("S_base", fz["universe"]["decision"]), key_of(CAND3_CELL, fz["universe"]["decision"])
    if sk in rows and dk in rows:                                        # совместное чтение с S_base — ОПИСАТЕЛЬНО, без вердикта и без α (раздел «вместе с S_base» отчёта TK-668)
        joint = joint_describe(rows[sk], rows[dk], to)
    res = {"point": pt, "freeze_commit": commit, "freeze_sha256": fsha, "bin_md5": md5, "stats": stats, "seams": {k: len(x) for k, x in seam.items()},
           "prefix": {"forced_prev": forced, "diffs": {c: len(x) for c, x in pref.items()}}, "n24": n24, "candidate": cinfo, "candidate2": c2info, "candidate3": c3info, "ds": ds_info, "joint": joint, "tree_files": ntree, "manifest_files": len(man),
           "universe": {"n": {n: len(v) for n, v in uni["lists"].items()}, "sha256": uni["sha256"]}}
    json.dump(res, open(os.path.join(a.root, "point-%s.json" % pt["id"]), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    if run_cand:
        json.dump(n24, open(os.path.join(a.root, "READ-%s.n24.json" % pt["id"]), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if run_ds_cell:
        json.dump(ds_info, open(os.path.join(a.root, "READ-%s.ds.json" % pt["id"]), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(os.path.join(a.root, "READ-%s.done" % pt["id"]), "w").close()
    print(render(res))
    return 0


def f2(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else ("%+." + str(nd) + "f") % x


def render(res):
    pt = res["point"]
    L = ["# TK-626 — точка %s: данные до %s, входы ≤ %s (окна %s); решающая вселенная — all, остальные — описательно" % (pt["id"], pt["d"], pt["entries_to"], ", ".join(pt["windows"])), "",
         "| клетка | вселенная | сделок | forced | ΣS $ | $/сделку | шарп | p | α точки | без 2 лучших суток | без 2 лучших монет | граница B $ (режим) | (ΣS − B)/N > 0 | решение |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, s in res["stats"].items():
        L.append("| %s | %s | %d | %d | %s | %s | %s | %.4f | %.4f | %s | %s | %s (%s) | %s | **%s** |" % (
            s["cell"], s["uni"], s["n"], s.get("forced", 0), f2(s["sum"]), f2(s["mean"], 3), f2(s.get("sharpe"), 2), s["p"], s["alpha_k"], f2(s["mean_wo_days"], 3), f2(s["mean_wo_coins"], 3),
            f2(s["fund_bound"]), s["fund_mode"], "да" if s["fund_ok"] else "нет", s["decision"]))
    L += ["", "Швы окон: различий %s; префикс-охрана: %s; forced точки-предшественницы: %s" % (res["seams"], res["prefix"]["diffs"] or "нет предшественницы", res["prefix"]["forced_prev"] or "—"),
          "Вселенная точки: %s; файлов в манифесте %d" % (res["universe"]["n"], res["manifest_files"])]
    if (res.get("candidate") or {}).get("candidate_read") is False:
        L.append("%s (кандидат TK-638): %s" % (CAND_CELL, res["candidate"]["candidate_note"]))
    if (res.get("candidate2") or {}).get("candidate2_read") is False:
        L.append("%s (кандидат TK-641): %s" % (CAND2_CELL, res["candidate2"]["candidate2_note"]))
    if (res.get("candidate3") or {}).get("candidate3_read") is False:
        L.append("%s (кандидат TK-670): %s" % (CAND3_CELL, res["candidate3"]["candidate3_note"]))
    for u, x in (res.get("ds") or {}).items():
        L.append("%s (кандидат TK-670), вселенная %s: %s; пар (монета, сутки) без klines предыдущих суток %s из %s%s%s" % (
            CAND3_CELL, u, "прочитана" if x["status"] == "read" else "НЕ ЧИТАЛАСЬ", x["missing_pairs"] if x["missing_pairs"] is not None else "—", x["pairs"] if x["pairs"] is not None else "—",
            "" if x["missing_share"] is None else " (%.3f %%)" % (100 * x["missing_share"]), "" if x["status"] == "read" else " — " + x["reason"]))
        if x["status"] == "read":
            L.append("%s, вселенная %s, счётчики движка по окнам (печатаются, в решение не входят): %s" % (CAND3_CELL, u, x["windows"]))
    j = res.get("joint")
    if j:
        L += ["", "Совместно `S_base` + `%s` (вселенная all, ОПИСАТЕЛЬНО — без вердикта и α; простая сумма суточных рядов, лимит позиций и слоты не моделируются):" % CAND3_CELL,
              "сделок %d (S_base %d + %s %d); Σ %s $ (S_base %s, %s %s); $/сделку %s; шарп %s (S_base %s, %s %s); просадка %s $ (%s %% маржи); корреляция суточных %s; сделок с парой в другой линии: S_base %d, %s %d" % (
                  j["n"], j["n_a"], CAND3_CELL, j["n_b"], f2(j["sum"]), f2(j["sum_a"]), CAND3_CELL, f2(j["sum_b"]), f2(j["mean"], 3), f2(j["sharpe"], 2), f2(j["sharpe_a"], 2), CAND3_CELL, f2(j["sharpe_b"], 2),
                  f2(j["maxdd"], 1), f2(j["maxdd_pct_margin"], 1), f2(j["corr_daily"], 3), j["overlap"]["a_with_pair"], CAND3_CELL, j["overlap"]["b_with_pair"])]
    for u, x in (res.get("n24") or {}).items():
        L.append("%s (кандидат TK-638), вселенная %s: %s; доля кандидатов L_base с m24 = 1 среди зачётных %s; no_key %s%s" % (
            CAND_CELL, u, "прочитана" if x["status"] == "read" else "НЕ ЧИТАЛАСЬ", "—" if x["m24_share"] is None else "%.3f %%" % (100 * x["m24_share"]), x["no_key"] if x["no_key"] is not None else "—",
            "" if x["status"] == "read" else " — " + x["reason"]))
    return "\n".join(L) + "\n"


def cmd_plan(a):
    fz = load_freeze(a.freeze)
    print("заморозка: бинарь md5 %s; семейство %s; α %s; доли %s; вселенные %s, решающая %s" % (
        fz["binary"]["md5"], [c for c, v in fz["cells"].items() if v.get("family")], fz["alpha"], fz["point_shares"], fz["universe"]["read"], fz["universe"]["decision"]))
    cp = getattr(a, "candidate", "") or candidate_path(a.freeze)
    if os.path.exists(cp):
        cand = load_candidate(cp, fz, a.freeze)
        fz = apply_candidate(fz, cand)
        print("кандидат %s (%s): α %s; клетка %s vid %s, фильтр %s ≥ %s" % (CAND_NAME, cand["version"], fz["alpha"], CAND_CELL, cand["cells"][CAND_CELL]["vid"], cand["filter"]["name"], cand["filter"]["N"]))
        cp2 = getattr(a, "candidate2", "") or candidate2_path(a.freeze)
        if os.path.exists(cp2):
            c2 = load_candidate2(cp2, fz, a.freeze, cand, cp)
            fz = apply_candidate2(fz, c2, True)
            print("кандидат %s (%s): α %s; клетка %s vid %s" % (CAND2_NAME, c2["version"], fz["alpha"], CAND2_CELL, c2["cells"][CAND2_CELL]["vid"]))
            cp3 = getattr(a, "candidate3", "") or candidate3_path(a.freeze)
            if os.path.exists(cp3):
                c3 = load_candidate3(cp3, fz, a.freeze, cand, cp, c2, cp2)
                fz = apply_candidate3(fz, c3)
                print("кандидат %s (%s): α %s; клетка %s vid %s, бинарь %s" % (CAND3_NAME, c3["version"], fz["alpha"], CAND3_CELL, c3["cells"][CAND3_CELL]["vid"], c3["ds_binary"]["md5"]))
            else:
                print("кандидата %s рядом с заморозкой нет — чтение точки остановится (кандидат в силе: α S_base 0,020, S_mk1 / L_base без него не применяются)" % CAND3_NAME)
        else:
            print("кандидата %s рядом с заморозкой нет — чтение точки остановится (кандидат в силе: α S_base 0,020)" % CAND2_NAME)
    else:
        print("кандидата %s рядом с заморозкой нет — чтение точки остановится (кандидат в силе: α S_base 0,025)" % CAND_NAME)
    for w in fz["windows"]:
        print("окно %s T0 %s зачёт %s…%s" % (w["name"], w["t0"], w["lo"], w["hi"]))
    for p in fz["points"]:
        mark = "прочитана" if os.path.exists(os.path.join(a.root, "READ-%s.started" % p["id"])) else "не читалась"
        print("точка %s: данные до %s, входы ≤ %s, окна %s, решение %s, α(S_base) %.4f — %s" % (
            p["id"], p["d"], p["entries_to"], ",".join(p["windows"]), "да" if p["decision"] else "нет (описательно)", alpha_k(fz, "S_base", p["id"]), mark))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "point", "replay-n24", "replay-mk2", "replay-ds"])
    ap.add_argument("point", nargs="?", default="")
    ap.add_argument("--freeze", default=FREEZE)
    ap.add_argument("--freeze-commit", default="")
    ap.add_argument("--freeze-sha256", default="")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--bin", default="")
    ap.add_argument("--base-dir", default=BASE_DIR)
    ap.add_argument("--universe-dir", default="")
    ap.add_argument("--threads", type=int, default=rr.THREADS)
    ap.add_argument("--tick-store", default="")
    ap.add_argument("--sig-cache", default="")
    ap.add_argument("--funding", default="")
    ap.add_argument("--nboot", type=int, default=NBOOT)
    ap.add_argument("--candidate", default="", help="файл кандидата (по умолчанию `%s` рядом с --freeze, если он есть)" % CAND_NAME)
    ap.add_argument("--no-candidate", action="store_true", help="L_n24 не читать (файл кандидата всё равно нужен: α S_base 0,025 уже потрачены); в маркере «кандидат не читан»")
    ap.add_argument("--candidate-commit", default="")
    ap.add_argument("--candidate-sha256", default="")
    ap.add_argument("--candidate2", default="", help="файл второго кандидата (по умолчанию `%s` рядом с --freeze)" % CAND2_NAME)
    ap.add_argument("--no-candidate2", action="store_true", help="L_mk2 не читать (файл всё равно нужен: α S_base 0,020 уже потрачены); в маркере «кандидат 2 не читан»")
    ap.add_argument("--candidate2-commit", default="")
    ap.add_argument("--candidate2-sha256", default="")
    ap.add_argument("--candidate3", default="", help="файл третьего кандидата (по умолчанию `%s` рядом с --freeze)" % CAND3_NAME)
    ap.add_argument("--no-candidate3", action="store_true", help="D_S не читать (файл всё равно нужен: α S_mk1 / L_base уже потрачены); в маркере «кандидат 3 не читан»")
    ap.add_argument("--candidate3-commit", default="")
    ap.add_argument("--candidate3-sha256", default="")
    ap.add_argument("--ds-bin", default="", help="бинарь `D_S` (md5 / sha256 — в `ds_binary` файла кандидата 3; ключ `lx` есть только в нём)")
    ap.add_argument("--ref-dir", default="/data/p42-tk626/gmk/out", help="replay-mk2: выходы G-626c `<окно>/<vid>/long-*`; replay-ds: выходы TK-668 `<окно>/S_lx1/short-*` (`/data/p42-tk668/e4/cells`)")
    ap.add_argument("--feat-bin", default="", help="`alpha-feat` кандидата (md5 / sha256 — в файле кандидата)")
    ap.add_argument("--replay-root", default="/data/p42-tk638/replay")
    ap.add_argument("--windows-root", default="/data/p42-tk621")
    ap.add_argument("--symbols-file", default="/data/p42-tk621/universe/symbols-all.txt")
    ap.add_argument("--ref", default="", help="G-638a (`gate-638a.json`): эталонные sha256 выходов")
    ap.add_argument("--out", default="", help="replay-n24: итог (по умолчанию <replay-root>/gate-638c.json; replay-ds — gate-670c.json)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            return cmd_plan(a)
        if a.cmd == "replay-n24":
            return replay_n24(a)
        if a.cmd == "replay-mk2":
            return replay_mk2(a)
        if a.cmd == "replay-ds":
            return replay_ds(a)
        if not a.point:
            raise GuardError("point: укажите L1…L5")
        return run_point(a)
    except GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
