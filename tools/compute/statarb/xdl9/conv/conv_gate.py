#!/usr/bin/env python3
"""TK-493, П-38 r2 §3.4 (стадия G1): Gate в универсуме C2 - манифест готовности, пары Gate по правилу R2' (то же, что у 8 бирж), классы, подсказка H4.

Чтение: только формирование (замыкания 01.10-14.10 из `asset_extract.py`, 20 160 минут; окно не читается), метаданные - instruments.csv.
  python conv_gate.py manifest [--root ~/xdl9] [--ready PATH]     проверка манифеста (валиден ли gate_ready, причины)
  python conv_gate.py pairs   [--root ~/xdl9] [--asset-gate DIR] --out-dir DIR     таблица пар Gate: asset_pairs_gate.csv (+meta.json), gate_contracts.csv, gate_classes.csv
  python conv_gate.py verify8 [--root ~/xdl9] [--n 600]             сверка этого кода с F5 на 8 биржах: статистики случайных строк asset_pairs.csv и роли main/dup (asset_map.csv)
  python conv_gate.py hints   [--root ~/xdl9] --pairs FILE --out FILE   членство подсказок видео со связками Gate (H4: TA Gate x Binance и др.)
Правило R2' (П-37 §4.2, r3.2): пара «одно» ⇔ тот же укрупнённый класс, n_ret >= 300 и S <= 0.06171426 (S = σ(ln c1 - ln c2) / σ(ln c1), c1 - нога с большим оборотом); «дубль внутри биржи»
S < 0.09835096; пороги не пересчитываются (измерены F5 по основному ярусу). Алгоритм ролей (кластеры внутри биржи) и таблицы повторяет asset_table.py без изменений.
Gate-специфика (назначено исполнителем G1 по заданию CEO, подтвердить): (1) пред-рынок (колонка `pre_listing` справочника Gate, первичное поле is_pre_market) - вне теста; (2) классы
commodity и other - вне (§3.4); (3) пары Gate с классом не «крипта» остаются в таблице (c1d2 по тому же правилу), но рынок «gate_tradfi» - вне основного ряда C2 (нет меток региона и
панелей `panels_us` для Gate); (4) контракты enable_decimal (v целое число контрактов) включены с пометкой в колонке gate_note.
"""
import argparse
import collections
import csv
import hashlib
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))          # asset_lib, asset_class, asset_cands (tools/compute/statarb/xdl9)
sys.path.insert(0, HERE)

MANIFEST_CHECKS = ("rows", "gaps", "units", "open_semantics", "funding", "classes")
EXCH8 = ["binance", "bybit", "okx", "bingx", "aster", "kraken", "lighter", "bitget"]      # порядок asset_lib.EXCH (индекс i в F5)
N_MIN = 14 * 1440
THR = dict(n_ret_min=300,                 # П-37 params n_ret_min (назначен F5)
           stab=0.06171426,               # П-37 params stab_threshold (середина зазора: 0.02507755 | 0.09835096)
           stab_hi=0.09835096,            # лучший S «разное» (нижняя граница «разное» и «дубля»)
           worst_same=0.02507755,
           mult_tol=0.02)                 # П-37 Н4: путь М при dev <= 0.02
GATE_NOTE_DECIMAL = "enable_decimal: v целое число контрактов (минуты с одними дробными сделками имеют v=0)"
OUT = "вне теста"


def safe_name(sym):
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in sym)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ манифест
def read_manifest(path):
    """Файл `gate_ready` -> словарь `ключ=значение` (строки без «=» и комментарии «#» пропускаются; ключ - до первого «=»)."""
    kv = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f.read().split("\n"):
            ln = ln.rstrip("\r")
            if not ln or ln.lstrip().startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            kv[k.strip()] = v.strip()
    return kv


def manifest_valid(root, path=None, expect_open_label=None):
    """Манифест Gate валиден (П-38 §3.4), если: файл есть; шесть строк `имя=ok`; число символов; `instruments_sha256` равен sha256 справочника `<root>/inst_gate/gate.csv`;
    `panels_manifest_sha256` равен sha256 `<root>/gx/sha256-gate.txt`; число панелей `<root>/panels/gate/*.sap` равно числу символов; множитель оборота читается и > 0;
    метка семантики open (если задана ожидаемая) совпадает. -> (ok, [причины], info). info: mult, funding_dir, panels_dir, raw_root, symbols, open_label."""
    path = path or os.path.join(root, "gate_ready")
    why = []
    info = {}
    if not os.path.isfile(path):
        return False, ["нет файла манифеста %s" % path], info
    kv = read_manifest(path)
    for k in MANIFEST_CHECKS:
        if kv.get(k) != "ok":
            why.append("строка %s не ok (%s)" % (k, kv.get(k, "нет строки")))
    nsym = kv.get("symbols") or kv.get("nsym")
    try:
        n = int(nsym)
        info["symbols"] = n
    except (TypeError, ValueError):
        n = None
        why.append("нет числа символов (symbols)")
    ip = os.path.join(root, "inst_gate", "gate.csv")
    if not kv.get("instruments_sha256"):
        why.append("нет instruments_sha256")
    elif not os.path.isfile(ip):
        why.append("нет справочника %s" % ip)
    elif sha256_file(ip) != kv["instruments_sha256"]:
        why.append("instruments_sha256 не равен sha256 справочника (манифест устарел)")
    sp = os.path.join(root, "gx", "sha256-gate.txt")
    if not kv.get("panels_manifest_sha256"):
        why.append("нет panels_manifest_sha256")
    elif not os.path.isfile(sp):
        why.append("нет списка sha256 панелей %s" % sp)
    elif sha256_file(sp) != kv["panels_manifest_sha256"]:
        why.append("panels_manifest_sha256 не равен sha256 списка панелей (манифест устарел)")
    pd = os.path.join(root, "panels", "gate")
    if n is not None:
        have = len([x for x in os.listdir(pd) if x.endswith(".sap")]) if os.path.isdir(pd) else 0
        if have != n:
            why.append("панелей %s: %d, а символов в манифесте %d" % (pd, have, n))
    try:
        mult = float(kv.get("units_turnover_mult_on_file_v", ""))
        if not mult > 0:
            raise ValueError
        info["mult"] = mult
    except ValueError:
        why.append("нет множителя оборота units_turnover_mult_on_file_v > 0")
    info["open_label"] = kv.get("open_semantics_label")
    if expect_open_label and info["open_label"] != expect_open_label:
        why.append("метка семантики open в манифесте %r не равна принятой в коде %r" % (info["open_label"], expect_open_label))
    info["funding_dir"] = kv.get("funding_dir") or None
    info["panels_dir"] = pd
    info["raw_root"] = kv.get("raw_root") or None
    info["kv"] = kv
    return (not why), why, info


# ------------------------------------------------------------------------------------------------ загрузка контрактов
def _stem(fn):
    return fn[:-7] if fn.endswith(".csv.gz") else fn


def _tags(s):
    return [t for t in (s or "").split("|") if t]


def _num(x, default=float("nan")):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def open_f32(path):
    """Замыкания формирования float32 [n, N_MIN] (memmap); пустой файл (биржа без контрактов) - массив [0, N_MIN]."""
    if os.path.getsize(path) == 0:
        return np.zeros((0, N_MIN), dtype="<f4")
    return np.memmap(path, dtype="<f4", mode="r").reshape(-1, N_MIN)


class Store:
    """Контракты (строки как в asset_lib.load_contracts) и их замыкания формирования (memmap по биржам)."""

    def __init__(self):
        self.rows = []
        self.mm = {}                              # exchange -> memmap [n, N_MIN]

    def close_of(self, d):
        return self.mm[d["exchange"]][d["row"]]


def load_other(root, st):
    """8 бирж F5: строки asset_map.csv (ключ, класс, роль, метки) + idx (оборот, номер строки замыканий) -> st.rows."""
    amap = {(r["exchange"], safe_name(r["symbol"])): r for r in csv.DictReader(open(os.path.join(root, "asset_map.csv"), encoding="utf-8", newline=""))}
    for ex in EXCH8:
        idx = list(csv.DictReader(open(os.path.join(root, "asset", ex + ".idx.csv"), encoding="utf-8", newline="")))
        st.mm[ex] = open_f32(os.path.join(root, "asset", ex + ".f32"))
        assert st.mm[ex].shape[0] == len(idx), (ex, st.mm[ex].shape, len(idx))
        for r in idx:
            a = amap.get((ex, _stem(r["file"])))
            if a is None:
                raise SystemExit("нет в asset_map.csv: %s %s" % (ex, r["file"]))
            d = dict(a)
            d.update(exchange=ex, file=r["file"], n_valid=int(r["n_valid"]), turnover=_num(r["turnover"]), row=int(r["row"]), tags=_tags(a["tags"]),
                     leg_c1d2=int(a["leg_c1d2"] or 0), role0=a["role"], gate=False)
            d["keys_extra"] = [t[len("alias_extra_"):] for t in d["tags"] if t.startswith("alias_extra_")]
            d["zero_share"] = 1.0 - d["n_valid"] / N_MIN
            d["i"] = len(st.rows)
            st.rows.append(d)


def load_gate(root, asset_gate, st):
    """Gate: справочник `<root>/inst_gate/gate.csv` + замыкания формирования `<asset_gate>/gate.{f32,idx.csv}` (asset_extract.py --exchanges gate) -> st.rows; классификация F5."""
    import asset_lib as AL
    import asset_class as AC
    import asset_cands as AX
    inst = {safe_name(r["symbol"]): r for r in csv.DictReader(open(os.path.join(root, "inst_gate", "gate.csv"), encoding="utf-8", newline=""))}
    idx = list(csv.DictReader(open(os.path.join(asset_gate, "gate.idx.csv"), encoding="utf-8", newline="")))
    st.mm["gate"] = open_f32(os.path.join(asset_gate, "gate.f32"))
    assert st.mm["gate"].shape[0] == len(idx)
    AL.PRIVATE_BASES = AC.derive_private_bases(AL.load_inst_all(root))
    AL.RESOLUTION = AC.load_resolution()
    rows = []
    for r in idx:
        ins = inst.get(_stem(r["file"]))
        if ins is None:
            raise SystemExit("нет в inst_gate/gate.csv: %s" % r["file"])
        d = dict(ins)
        d.update(exchange="gate", file=r["file"], n_valid=int(r["n_valid"]), turnover=_num(r["turnover"]), row=int(r["row"]), gate=True)
        d["zero_share"] = 1.0 - d["n_valid"] / N_MIN
        rows.append(d)
    AX.build(rows, None)                                      # d["big"], out_kind, out_reason, pre, key, tags, keys_extra (единая функция F5)
    for d in rows:
        d["gate_note"] = GATE_NOTE_DECIMAL if "enable_decimal=true" in (d.get("note") or "") else ""
        if str(d.get("pre_listing") or "0") == "1" and d["big"] != OUT:                   # первичное поле Gate is_pre_market: в asset_class.py для Gate правила нет
            d["big"], d["out_kind"], d["out_reason"], d["pre"] = OUT, "pre_listing", "вне теста: пред-листинг, первичное поле (gate is_pre_market=true, колонка pre_listing)", 1
        if d.get("class") in ("commodity", "other") and d["big"] != OUT:                  # П-38 §3.4: класс не определён - вне
            d["big"], d["out_kind"], d["out_reason"] = OUT, "class_undefined", "вне теста: класс %s не определён (П-38 §3.4)" % d["class"]
    n0 = len(st.rows)
    for k, d in enumerate(rows):
        d["i"] = n0 + k
    st.rows.extend(rows)
    return rows


# ------------------------------------------------------------------------------------------------ статистики и роли
class Stats:
    def __init__(self, st):
        self.st = st
        self.cache = {}

    def of(self, a, b):
        """-> (c1, c2, статистики): c1 - нога с большим оборотом (при равенстве - с меньшим индексом), как в asset_pairs_stats.py."""
        import asset_lib as AL
        if a["turnover"] < b["turnover"] or (a["turnover"] == b["turnover"] and a["i"] > b["i"]):
            a, b = b, a
        key = (a["i"], b["i"])
        s = self.cache.get(key)
        if s is None:
            s = AL.pair_stats(self.st.close_of(a), self.st.close_of(b))
            # F5 пишет статистики в pairstats.csv с округлением (corr, dev, S - 6 знаков; mean_lr, sd_lr, sd_l1 - 8; med_ratio - 9 значащих) и читает обратно: решения принимаются по округлённым
            for k_, f_ in (("corr", "%.6f"), ("dev", "%.6f"), ("S", "%.6f"), ("mean_lr", "%.8f"), ("sd_lr", "%.8f"), ("sd_l1", "%.8f"), ("med_ratio", "%.9g")):
                s[k_] = float(f_ % s[k_])
            self.cache[key] = s
        return a, b, s


def is_one(s):
    return s is not None and s["n_ret"] >= THR["n_ret_min"] and s["S"] <= THR["stab"]


def is_dup(s):
    return s is not None and s["n_ret"] >= THR["n_ret_min"] and s["S"] < THR["stab_hi"]


def assign_roles(rows, stats):
    """Роли контрактов внутри биржи по F5 (asset_table.main): кластеры по убыванию оборота (HKD-ноги после USD-подобных), основной = первый, остальные - дубли.
    Меняет d['role'], d['reason'], d['asset_id'], d['leg_c1d2'], d['a1a2b1']."""
    import asset_cands as AX
    for d in rows:
        d["role"], d["reason"], d["asset_id"], d["main_i"] = "", "", "", -1
        d["dup_corr"] = d["dup_S"] = d["dup_ratio"] = float("nan")
        d["dup_n_ret"], d["dup_unverified"] = 0, 0
    groups = collections.defaultdict(list)
    for d in rows:
        if d["big"] == OUT:
            d["role"], d["reason"] = "excluded", d["out_reason"]
        elif d["n_valid"] == 0:
            d["role"], d["reason"] = "excluded", "нет валидных минут на формировании (n_valid=0)"
        else:
            groups[(d["exchange"], d["key"], d["big"])].append(d)
    for (ex, key, big), v in groups.items():
        v.sort(key=lambda d: (AX.is_hkd(d), -(d["turnover"] if d["turnover"] == d["turnover"] else 0), d["i"]))
        clusters = []
        for d in v:
            placed = False
            for m in clusters:
                _, _, s = stats.of(d, m)
                hkd = AX.is_hkd(d) and not AX.is_hkd(m)
                if is_dup(s) or (hkd and s["n_ret"] >= THR["n_ret_min"]):
                    d["role"], d["main_i"], d["asset_id"] = "dup", m["i"], m["asset_id"]
                    d["dup_corr"], d["dup_S"], d["dup_ratio"], d["dup_n_ret"] = s["corr"], s["S"], s["med_ratio"], s["n_ret"]
                    d["reason"] = AX.dup_reason(d, m) + ("" if is_dup(s) else " [по политике HKD r2 §4.2; S=%.3f выше порога дубля]" % s["S"])
                    placed = True
                    break
                elif s["n_ret"] < THR["n_ret_min"] and len(clusters) == 1:
                    d["role"], d["main_i"], d["asset_id"], d["dup_unverified"] = "dup", m["i"], m["asset_id"], 1
                    d["dup_corr"], d["dup_S"], d["dup_ratio"], d["dup_n_ret"] = s["corr"], s["S"], s["med_ratio"], s["n_ret"]
                    d["reason"] = AX.dup_reason(d, m) + " [не проверено измерением: n_ret=%d < %d]" % (s["n_ret"], THR["n_ret_min"])
                    placed = True
                    break
            if not placed:
                d["role"] = "main"
                d["asset_id"] = key + ("" if not clusters else "#%d" % (len(clusters) + 1)) + "|" + big
                if clusters:
                    d["reason"] = "второй актив с тем же именем на бирже (измерением не дубль основного)"
                clusters.append(d)
    for d in rows:
        d["leg_c1d2"] = 1 if (d["role"] == "main" or (d["role"] == "dup" and AX.is_hkd(d) and "нога в HKD" in d["reason"])) else 0
        d["a1a2b1"] = 1 if d["role"] == "main" else 0


def candidate_pairs(st, gate_only=True):
    """Пары-кандидаты по ключу актива внутри групп (ключ и дополнительные ключи алиасов): контракты не «вне теста» с n_valid > 0; при gate_only - пары, где есть контракт Gate."""
    g = collections.defaultdict(list)
    for d in st.rows:
        if d["big"] != OUT and d["n_valid"] > 0:
            g[d["key"]].append(d)
            for k2 in d.get("keys_extra", []):
                g[k2].append(d)
    seen = set()
    out = []
    for key in sorted(g):
        v = g[key]
        for x in range(len(v)):
            for y in range(x + 1, len(v)):
                a, b = v[x], v[y]
                if gate_only and not (a.get("gate") or b.get("gate")):
                    continue
                k = (min(a["i"], b["i"]), max(a["i"], b["i"]))
                if k in seen:
                    continue
                seen.add(k)
                out.append((key, a, b))
    return out


PAIR_COLS = ["key", "scope", "big1", "big2", "class_match", "ex1", "sym1", "role1", "ex2", "sym2", "role2", "quote1", "quote2", "turnover1", "turnover2", "zero1", "zero2",
             "n_common", "zero_common", "n_ret", "corr", "med_ratio", "k", "dev", "path", "S", "sd_lr", "sd_l1", "verdict", "in_calib_gap", "c1d2", "why", "source"]
SRC_PREFIX = ("alias_", "fx_pair", "cross_quote", "suffix_", "kraken_", "lighter_", "quote_BTC")


def pair_row(key, a, b, stats):
    """Строка таблицы пар (поля и правила asset_table.main)."""
    a, b, s = stats.of(a, b)
    scope = "между биржами" if a["exchange"] != b["exchange"] else "внутри биржи"
    class_match = a["big"] == b["big"]
    if not class_match:
        if s["n_ret"] < THR["n_ret_min"]:
            verdict = "класс не совпал (связь не измерена: мало общих доходностей)"
        else:
            verdict = "класс не совпал (связь есть)" if s["S"] <= THR["stab"] else "класс не совпал (связи нет)"
    elif s["n_ret"] < THR["n_ret_min"]:
        verdict = "нет данных (мало общих доходностей)"
    else:
        verdict = "одно" if s["S"] <= THR["stab"] else ("пограничное (S между серединой и лучшим «разное»)" if s["S"] < THR["stab_hi"] else "разное")
    path = "М" if s["dev"] <= THR["mult_tol"] else "К"
    usable = (scope == "между биржами" and verdict == "одно" and bool(a["leg_c1d2"]) and bool(b["leg_c1d2"]))
    why = ""
    if scope == "между биржами" and verdict == "одно" and not usable:
        if a["role"] == "dup" or b["role"] == "dup":
            why = "нога - дубль: " + "; ".join(x for x in (
                "%s:%s (%s)" % (a["exchange"], a["symbol"], a["reason"]) if a["role"] == "dup" else "",
                "%s:%s (%s)" % (b["exchange"], b["symbol"], b["reason"]) if b["role"] == "dup" else "") if x)
    tg = [t for t in a["tags"] + b["tags"] if t.startswith(SRC_PREFIX)]
    src = "имя" if not tg else "имя+словарь: " + ",".join(sorted(set(tg)))
    in_gap = THR["worst_same"] < s["S"] < THR["stab_hi"]
    return dict(key=key, scope=scope, big1=a["big"], big2=b["big"], class_match=int(class_match), ex1=a["exchange"], sym1=a["symbol"], role1=a["role"], ex2=b["exchange"],
                sym2=b["symbol"], role2=b["role"], quote1=a["quote"], quote2=b["quote"], turnover1=a["turnover"], turnover2=b["turnover"], zero1=a["zero_share"], zero2=b["zero_share"],
                n_common=s["n_common"], zero_common=1.0 - s["n_common"] / N_MIN, n_ret=s["n_ret"], corr=s["corr"], med_ratio=s["med_ratio"], k=s["k"], dev=s["dev"], path=path, S=s["S"],
                sd_lr=s["sd_lr"], sd_l1=s["sd_l1"], verdict=verdict, in_calib_gap=int(in_gap), c1d2=int(usable), why=why, source=src)


def fmt_pair_row(r):
    rr = dict(r)
    for k in ("zero1", "zero2", "zero_common", "corr", "dev", "S", "sd_lr", "sd_l1"):
        rr[k] = "%.5f" % rr[k] if rr[k] == rr[k] else ""
    for k in ("turnover1", "turnover2", "med_ratio"):
        rr[k] = "%.6g" % rr[k] if rr[k] == rr[k] else ""
    return rr


def write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


# ------------------------------------------------------------------------------------------------ команда pairs
def cmd_pairs(a):
    root = os.path.expanduser(a.root)
    asset_gate = os.path.expanduser(a.asset_gate) if a.asset_gate else os.path.join(root, "gx", "asset_gate")
    st = Store()
    load_other(root, st)
    gate_rows = load_gate(root, asset_gate, st)
    stats = Stats(st)
    assign_roles(gate_rows, stats)
    pairs = []
    for key, x, y in candidate_pairs(st, gate_only=True):
        r = pair_row(key, x, y, stats)
        notes = [d["gate_note"] for d in (x, y) if d.get("gate") and d.get("gate_note")]
        r["gate_note"] = "; ".join(sorted(set(notes)))
        pairs.append(r)
    pairs.sort(key=lambda r: (r["key"], r["scope"], r["ex1"], r["ex2"], r["sym1"], r["sym2"]))
    os.makedirs(a.out_dir, exist_ok=True)
    pp = os.path.join(a.out_dir, "asset_pairs_gate.csv")
    write_csv(pp, PAIR_COLS + ["gate_note"], [fmt_pair_row(r) for r in pairs])
    # контракты Gate
    ccols = ["symbol", "base", "class", "class_src", "big", "key", "tags", "status", "quanto", "pre_listing", "enable_decimal", "n_valid", "zero_share", "turnover", "role", "reason", "leg_c1d2", "out_kind"]
    crow = []
    for d in gate_rows:
        crow.append(dict(symbol=d["symbol"], base=d["base"], **{"class": d["class"]}, class_src=d["class_src"], big=d["big"], key=d["key"], tags="|".join(d["tags"]), status=d["status"],
                         quanto=d["contract_size"], pre_listing=d.get("pre_listing", ""), enable_decimal=int(bool(d["gate_note"])), n_valid=d["n_valid"], zero_share="%.4f" % d["zero_share"],
                         turnover="%.6g" % d["turnover"] if d["turnover"] == d["turnover"] else "", role=d["role"], reason=d["reason"], leg_c1d2=d["leg_c1d2"], out_kind=d.get("out_kind", "")))
    write_csv(os.path.join(a.out_dir, "gate_contracts.csv"), ccols, crow)
    # сводка по классам и ячейкам
    byc = collections.Counter()
    usable = collections.Counter()
    cells = collections.Counter()
    for r in pairs:
        byc[(r["scope"], r["verdict"].split(" (")[0])] += 1
        if r["c1d2"]:
            usable[r["big1"]] += 1
            ea, eb = sorted([r["ex1"], r["ex2"]])
            cells["%s~%s/%s" % (ea, eb, r["big1"])] += 1
    summary = dict(
        gate_contracts=len(gate_rows), gate_by_class=dict(collections.Counter(d["class"] for d in gate_rows)),
        gate_big=dict(collections.Counter(d["big"] for d in gate_rows)), gate_roles=dict(collections.Counter(d["role"] for d in gate_rows)),
        gate_out_kinds=dict(collections.Counter(d.get("out_kind", "") for d in gate_rows if d["big"] == OUT)),
        pairs_rows=len(pairs), pairs_by_scope_verdict={"%s|%s" % k: v for k, v in sorted(byc.items())},
        c1d2_pairs_by_class=dict(usable), c1d2_pairs_total=int(sum(usable.values())), c1d2_cells={k: v for k, v in sorted(cells.items())},
        thresholds=THR, inputs=dict(
            asset_pairs_sha256=sha256_file(os.path.join(root, "asset_pairs.csv")), asset_map_sha256=sha256_file(os.path.join(root, "asset_map.csv")),
            gate_instruments_sha256=sha256_file(os.path.join(root, "inst_gate", "gate.csv")), gate_idx_sha256=sha256_file(os.path.join(asset_gate, "gate.idx.csv")),
            gate_closes_sha256=sha256_file(os.path.join(asset_gate, "gate.f32"))),
        pairs_sha256=sha256_file(pp))
    with open(os.path.join(a.out_dir, "asset_pairs_gate.meta.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(json.dumps({k: summary[k] for k in ("gate_contracts", "gate_by_class", "gate_big", "gate_roles", "gate_out_kinds", "pairs_rows", "c1d2_pairs_by_class", "c1d2_pairs_total")}, ensure_ascii=False, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------ команда verify8
def cmd_verify8(a):
    """Сверка кода пар с F5: (1) статистики случайных строк asset_pairs.csv воспроизводятся с точностью записи; (2) роли main/dup всех контрактов 8 бирж равны asset_map.csv;
    (3) классификация (класс, ключ, метки) контрактов двух бирж по справочникам равна asset_map.csv."""
    import asset_lib as AL
    import asset_class as AC
    import asset_cands as AX
    root = os.path.expanduser(a.root)
    st = Store()
    load_other(root, st)
    stats = Stats(st)
    byk = {(d["exchange"], d["symbol"]): d for d in st.rows}
    allrows = list(csv.DictReader(open(os.path.join(root, "asset_pairs.csv"), encoding="utf-8", newline="")))
    rng = np.random.default_rng(37)
    pick = rng.permutation(len(allrows))[:a.n]
    bad, cmpd = [], 0
    for ix in pick:
        r = allrows[int(ix)]
        d1, d2 = byk.get((r["ex1"], r["sym1"])), byk.get((r["ex2"], r["sym2"]))
        if d1 is None or d2 is None:
            bad.append(("нет контракта", r["ex1"], r["sym1"], r["ex2"], r["sym2"]))
            continue
        c1, c2, s = stats.of(d1, d2)
        mine = fmt_pair_row(dict(n_common=s["n_common"], zero_common=1.0 - s["n_common"] / N_MIN, n_ret=s["n_ret"], corr=s["corr"], med_ratio=s["med_ratio"], k=s["k"], dev=s["dev"], S=s["S"],
                                 sd_lr=s["sd_lr"], sd_l1=s["sd_l1"], turnover1=c1["turnover"], turnover2=c2["turnover"], zero1=c1["zero_share"], zero2=c2["zero_share"]))
        cmpd += 1
        if (c1["exchange"], c1["symbol"]) != (r["ex1"], r["sym1"]):
            bad.append(("ориентация", r["ex1"], r["sym1"], r["ex2"], r["sym2"]))
            continue
        for k in ("n_common", "n_ret", "corr", "med_ratio", "k", "dev", "S", "sd_lr", "sd_l1", "zero_common", "turnover1", "turnover2", "zero1", "zero2"):
            if str(mine[k]) != str(r[k]):
                bad.append((k, str(mine[k]), str(r[k]), r["ex1"], r["sym1"], r["ex2"], r["sym2"]))
                break
    print("verify8 (1) строк сверено %d, расхождений %d %s" % (cmpd, len(bad), bad[:5]))
    # (2) роли по всем 8 биржам
    for d in st.rows:
        d["out_reason"] = d["reason"]
    assign_roles(st.rows, stats)
    diff = []
    amap = {(r["exchange"], safe_name(r["symbol"])): r for r in csv.DictReader(open(os.path.join(root, "asset_map.csv"), encoding="utf-8", newline=""))}
    for d in st.rows:
        r = amap[(d["exchange"], _stem(d["file"]))]
        if (d["role"], str(d["leg_c1d2"]), str(d["a1a2b1"])) != (r["role"], r["leg_c1d2"], r["a1a2b1"]) or d["asset_id"] != r["asset_id"]:
            diff.append((d["exchange"], d["symbol"], d["role"], r["role"], d["asset_id"], r["asset_id"]))
    print("verify8 (2) контрактов %d, расхождений ролей/leg_c1d2/asset_id %d %s" % (len(st.rows), len(diff), diff[:5]))
    # (3) классификация двух бирж из справочников
    AL.PRIVATE_BASES = AC.derive_private_bases(AL.load_inst_all(root))
    AL.RESOLUTION = AC.load_resolution()
    cdiff = []
    n3 = 0
    for ex in ("bybit", "kraken", "bitget"):
        inst = {safe_name(r["symbol"]): r for r in csv.DictReader(open(os.path.join(root, "inst", ex + ".csv"), encoding="utf-8", newline=""))}
        rows = []
        for r in csv.DictReader(open(os.path.join(root, "asset", ex + ".idx.csv"), encoding="utf-8", newline="")):
            d = dict(inst[_stem(r["file"])])
            d["symbol0"] = d["symbol"]
            rows.append(d)
        AX.build(rows, None)
        for d in rows:
            r = amap[(ex, safe_name(d["symbol"]))]
            n3 += 1
            if (d["big"], d["key"], "|".join(d["tags"])) != (r["big"], r["key"], r["tags"]):
                cdiff.append((ex, d["symbol"], d["big"], r["big"], d["key"], r["key"]))
    print("verify8 (3) контрактов %d, расхождений класса/ключа/меток %d %s" % (n3, len(cdiff), cdiff[:5]))
    return 0 if not (bad or diff or cdiff) else 1


# ------------------------------------------------------------------------------------------------ команда hints
HINT_ASSETS = [("H2", "BRETT"), ("H3", "LAB"), ("H4", "TA"), ("H6", "LPT"), ("H1", "MINIMAX"), ("H1", "ZHIPU"), ("H1", "TENCENT"), ("H1", "LENOVO"), ("H1", "XIAOMI"), ("H1", "SHEIN")]


def cmd_hints(a):
    """Членство подсказок видео со связками Gate: строки таблицы пар Gate по ключам активов подсказок. H4 в протоколе §3.4: TA Gate x Binance."""
    rows = list(csv.DictReader(open(a.pairs, encoding="utf-8", newline="")))
    out = []
    for tag, asset in HINT_ASSETS:
        got = [r for r in rows if r["key"].upper() == asset and r["scope"] == "между биржами"]
        if not got:
            out.append(dict(hint=tag, asset=asset, ex1="", sym1="", ex2="", sym2="", verdict="нет контракта Gate с ключом этого актива в таблице пар", n_ret="", S="", c1d2="", big1="", in_universe_c2="", market=""))
            continue
        for r in got:
            main = r["c1d2"] == "1" and r["big1"] == "крипта"
            out.append(dict(hint=tag, asset=asset, ex1=r["ex1"], sym1=r["sym1"], ex2=r["ex2"], sym2=r["sym2"], verdict=r["verdict"], n_ret=r["n_ret"], S=r["S"], c1d2=r["c1d2"], big1=r["big1"],
                            in_universe_c2=int(main), market=("24x7" if main else ("gate_tradfi" if r["c1d2"] == "1" else "вне (c1d2=0)"))))
    cols = ["hint", "asset", "ex1", "sym1", "ex2", "sym2", "verdict", "n_ret", "S", "c1d2", "big1", "in_universe_c2", "market"]
    write_csv(a.out, cols, out)
    for r in out:
        if r["asset"] == "TA":
            print(r)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["manifest", "pairs", "verify8", "hints"])
    ap.add_argument("--root", default="~/xdl9")
    ap.add_argument("--ready", default=None)
    ap.add_argument("--asset-gate", default=None)
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--pairs", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "manifest":
        ok, why, info = manifest_valid(os.path.expanduser(a.root), a.ready and os.path.expanduser(a.ready), expect_open_label="first_trade")
        print(json.dumps(dict(valid=ok, reasons=why, info={k: v for k, v in info.items() if k != "kv"}), ensure_ascii=False, indent=1))
        return 0 if ok else 1
    return {"pairs": cmd_pairs, "verify8": cmd_verify8, "hints": cmd_hints}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
