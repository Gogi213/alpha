#!/usr/bin/env python3
"""G-017 БД признаков и гипотез (TK-449). Только stdlib. Отдельная от Летописи (tools/registry).
  python tools/g017db/g017db.py init|load|verify|queries [--db data/g017/g017.db] [--root .]
load — единственный путь пополнения: SPEC ниже = «какой csv → какая таблица»; новый признак/сигнал = новая строка SPEC
(или новый csv в уже описанном формате) и повторный load (идемпотентен: файл с тем же md5 пропускается, иной — заменяется).
verify — каждая ячейка каждого csv-источника сверяется с БД (md=csv=БД) + числа доклада TK-447 против запроса q1."""
import argparse, csv, glob, hashlib, os, re, sqlite3, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
F = "docs/findings/"
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]
FUND = ("fund_", "lsr_", "liq_usd", "liq_imb", "liq_to_")  # признаки tk447-fund.py


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def num(s):
    s = (s or "").strip()
    if s == "" or s.lower() in ("nan", "inf", "-inf"):
        return None
    try:
        return float(s)
    except ValueError:
        return s


def mon(s):  # 'aug','sep','m03' -> '2026-MM'
    s = s.strip()
    return {"aug": "2026-08", "sep": "2026-09"}.get(s) or (f"2026-{s[1:]}" if re.fullmatch(r"m\d\d", s) else s)


def conv(kind, s):
    if kind == "t":
        return s.strip()
    if kind == "month":
        return mon(s)
    v = num(s)
    return None if v is None else (int(round(v)) if kind == "i" else v)


# столбцы: (csv-колонка, колонка БД, тип i|r|t|month). Колонки csv, которых нет в файле, -> NULL.
SIG = [("сигнал", "signal", "t"), ("месяц", "month", "month"), ("D", "d", "i"), ("направление", "direction", "t"), ("X_q99", "x_q99", "r"),
       ("ячеек", "cells", "i"), ("ячеек_с_ходом", "cells_move", "i"), ("база", "base", "r"), ("срабатываний", "fires", "i"),
       ("попаданий", "hits", "i"), ("точность", "precision", "r"), ("полнота", "recall", "r"), ("подъём", "lift", "r"),
       ("срабат_в_сутки_на_монету", "fires_per_day_coin", "r"), ("монета_дней_попаданий", "coin_days_hit", "i"), ("база_подмн", "base_sub", "r"),
       ("подъём_подмн", "lift_sub", "r"), ("точн_lo", "prec_lo", "r"), ("точн_hi", "prec_hi", "r"), ("подъём_lo", "lift_lo", "r"),
       ("подъём_hi", "lift_hi", "r")]
CURVE = [("ось", "axis", "t"), ("сигнал", "signal", "t"), ("порог", "threshold", "r"), ("мес", "month", "month"), ("D", "d", "i"), ("q", "q", "r"),
         ("напр", "direction", "t"), ("ячеек", "cells", "i"), ("база", "base", "r"), ("срабат", "fires", "i"), ("попад", "hits", "i"),
         ("точность", "precision", "r"), ("полнота", "recall", "r"), ("подъём", "lift", "r"), ("подъём_lo", "lift_lo", "r"),
         ("подъём_hi", "lift_hi", "r"), ("срабат_в_сутки_на_монету", "fires_per_day_coin", "r")]
LIFT = [("месяц", "month", "t"), ("признак", "feature", "t"), ("D", "d", "i"), ("дециль", "bucket", "i"), ("от", "lo", "r"), ("до", "hi", "r"),
        ("ячеек", "cells", "i"), ("попаданий", "hits", "i"), ("покрытие", "coverage", "r"), ("база", "base", "r"), ("подъём", "lift", "r"),
        ("подъём_lo", "lift_lo", "r"), ("подъём_hi", "lift_hi", "r"), ("база_есть", "base_present", "r"),
        ("подъём_к_базе_есть", "lift_base_present", "r")]
GRP = [("месяц", "month", "t"), ("D", "d", "t"), ("группа", "grp", "t"), ("условных_событий", "cond_events", "i"), ("в_группе", "in_group", "i"),
       ("база_дамп", "base_dump", "r"), ("доля_дампа", "share_dump", "r"), ("подъём", "lift", "r"), ("подъём_lo", "lift_lo", "r"),
       ("подъём_hi", "lift_hi", "r"), ("кластеров", "clusters", "i"), ("полнота", "recall", "r"), ("срабат_сут_монету", "fires_per_day_coin", "r")]
HYP = [("id", "id", "t"), ("дата", "date", "t"), ("тикет", "ticket", "t"), ("вопрос", "question", "t"), ("события", "events", "t"),
       ("признаки", "features", "t"), ("вид_r", "kind_r", "t"), ("семейство_K", "family_k", "t"), ("накоплено_K", "accum_k", "t"),
       ("месяц_подтв", "month_confirmed", "t"), ("статус", "status", "t"), ("документ", "doc", "t")]

# (загрузчик, тикет, файл, таблица, столбцы, постоянные поля). В csv TK-447 колонки направления нет: метка — «сильный ход вперёд»
# (md TK-447), направление 'any'. Step1 TK-442 (pump-forward-signal-2026-10-28-{aug,sep}.csv) — подмножество step2-all, не грузится.
SPEC = [("signals", "TK-442", F + "pump-forward-signal-2026-10-28-step2-all.csv", "signals", SIG, {}),
        ("signals", "TK-443", F + "pump-forward-notes5-2026-10-28.csv", "signals", SIG, {}),
        ("signals", "TK-443", F + "pump-forward-notes5-v3v11-2026-10-28.csv", "signals", SIG, {}),
        ("signals", "TK-443", F + "pump-forward-notes5-v4-2026-10-28.csv", "signals", SIG, {}),
        ("signals", "TK-446", F + "pump-titration-curves-2026-10-28-all.csv", "signals", CURVE, {}),
        ("groups", "TK-443", F + "pump-forward-notes5-v10-2026-10-28.csv", "signal_groups", GRP, {}),
        ("lift", "TK-447", F + "pump-features-v2-2026-10-29-lift-all.csv", "feature_lift", LIFT, {"direction": "any"}),
        ("lift", "TK-447", F + "pump-features-v2-2026-10-29-fund-lift-2026-08.csv", "feature_lift", LIFT, {"direction": "any"}),
        ("lift", "TK-447", F + "pump-features-v2-2026-10-29-fund-lift-2026-09.csv", "feature_lift", LIFT, {"direction": "any"}),
        ("hypotheses", "TK-433", "docs/research/g017-hypotheses.csv", "hypotheses", HYP, {})]
CODE, FCODE = "tools/compute/tk447-features.py", "tools/compute/tk447-fund.py"
EVENT_GLOBS = [(F + "pump-v11-events-2026-*.csv", "events-v11", "TK-433"), (F + "pump-classes-2026-10-28-events.csv", "events-classes", "TK-435")]
NOW = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")


def groups_of(name):
    if name.startswith(("fund_", "lsr_")):
        return "деривативы Bybit/Gate"
    if name.startswith(FUND):
        return "ликвидации Gate"
    if name in ("liq_rank7d", "liq_share24h", "spot_fut_to7d_ln"):
        return "ликвидность"
    if name.startswith("ev90_"):
        return "история ходов"
    if name.startswith("m_"):
        return "рынок целиком"
    if name.startswith("btc_"):
        return "режим BTC"
    if name.startswith(("hod_", "dow_")):
        return "время"
    return "форма графика/волатильность"


def horizon_of(name):
    m = re.search(r"(\d+(?:m|h|d))(?![a-z])", name)
    return m.group(1) if m else "—"


def rule_of(name, root):
    path = FCODE if name.startswith(FUND) else CODE
    pat = re.compile(r"""["']%s["']""" % re.escape(name))
    for i, ln in enumerate(open(os.path.join(root, path), encoding="utf-8"), 1):
        if pat.search(ln):
            return f"{path}:{i}: {ln.strip()[:240]}", path
    return f"{path}: имя строится по шаблону в цикле (см. файл и docs/findings/pump-features-v2-2026-10-29.md)", path


def connect(db):
    os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)
    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init(c):
    c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    have = {r[0] for r in c.execute("SELECT version FROM schema_version")}
    for p in sorted(glob.glob(os.path.join(HERE, "migrations", "*.sql"))):
        v = int(os.path.basename(p).split("_")[0])
        if v not in have:
            c.executescript(open(p, encoding="utf-8").read())
            c.execute("INSERT INTO schema_version VALUES (?,?)", (v, NOW()))
    c.commit()


def read_rows(root, path, cols):
    """-> [(номер строки csv, {колонка БД: значение})]; 2 = первая строка данных."""
    out = []
    with open(os.path.join(root, path), encoding="utf-8", newline="") as f:
        for n, r in enumerate(csv.DictReader(f), 2):
            if None in r or None in r.values():
                raise SystemExit(f"{path} стр. {n}: число полей ≠ заголовку (незакавыченная запятая?)")
            out.append((n, {dc: conv(k, r[cc]) for cc, dc, k in cols if r.get(cc) is not None}))
    return out


def new_run(c, tk, loader, path, h, nrows, table):
    """-> run_id или None, если файл с тем же md5 уже загружен; иной md5 — старые строки удаляются."""
    old = c.execute("SELECT run_id, source_md5 FROM runs WHERE loader=? AND source_file=?", (loader, path)).fetchone()
    if old and old[1] == h:
        return None
    if old:
        c.execute(f"DELETE FROM {table} WHERE run_id=?", (old[0],))
        c.execute("DELETE FROM runs WHERE run_id=?", (old[0],))
    c.execute("INSERT INTO runs (ticket,loader,source_file,source_md5,rows,loaded_at) VALUES (?,?,?,?,?,?)", (tk, loader, path, h, nrows, NOW()))
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def load(c, root):
    for loader, tk, path, table, cols, const in SPEC:
        rows = read_rows(root, path, cols)
        rid = new_run(c, tk, loader, path, md5(os.path.join(root, path)), len(rows), table)
        if rid is None:
            continue
        for n, row in rows:
            row = {**row, **const, "run_id": rid}
            if table != "hypotheses":
                row.update(source_file=path, src_row=n)
            if table in ("signals", "signal_groups"):
                row["ticket"] = tk
            ks = list(row)
            c.execute(f"INSERT INTO {table} ({','.join(ks)}) VALUES ({','.join('?' * len(ks))})", [row[k] for k in ks])
    load_features(c, root)
    load_events(c, root)
    c.commit()


def load_features(c, root):
    names = sorted(r[0] for r in c.execute("SELECT DISTINCT feature FROM feature_lift"))
    for nm in names:
        rule, p = rule_of(nm, root)
        src = "Bybit фандинг" if nm.startswith("fund_") else "Gate contract_stats" if nm.startswith(FUND) else "куб свечей Bybit linear 1 м (TK-436/440)"
        c.execute("INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(feature) DO UPDATE SET grp=excluded.grp, "
                  "rule_text=excluded.rule_text, code_file=excluded.code_file, code_md5=excluded.code_md5, horizon=excluded.horizon, source=excluded.source",
                  (nm, groups_of(nm), rule, p, md5(os.path.join(root, p)), horizon_of(nm), src, "v2", "TK-447"))
    c.execute("DELETE FROM feature_coverage")
    cov = {(m, f): v for m, f, v in c.execute("SELECT month, feature, AVG(coverage) FROM feature_lift GROUP BY month, feature")}
    for nm in names:
        for m in MONTHS:
            v = cov.get((m, nm))
            ok = bool(v)  # нет строк lift или покрытие 0 (признак не определён ни в одной ячейке) = «нет данных»
            note = "" if ok else ("покрытие 0 (TK-447: lsr/ликвидации Gate — только сентябрь)" if v == 0 else
                                  "строк в feature_lift нет (фандинг — авг., сен.; lsr/ликвидации Gate — сен.; TK-447)")
            c.execute("INSERT INTO feature_coverage VALUES (?,?,?,?,?)", (nm, m, v, "ok" if ok else "нет данных", note))


def load_events(c, root):
    for pat, kind, tk in EVENT_GLOBS:
        for full in sorted(glob.glob(os.path.join(root, pat))):
            p = os.path.relpath(full, root).replace("\\", "/")
            n = sum(1 for _ in open(full, encoding="utf-8")) - 1
            rid = new_run(c, tk, "events", p, md5(full), n, "events_ref")
            if rid is not None:
                m = re.search(r"(2026-\d\d)", p)
                c.execute("INSERT INTO events_ref VALUES (?,?,?,?,?,?,?)", (p, kind, m.group(1) if m else None, n, md5(full), tk, rid))


# ---- запросы-примеры (docs/findings/g017-db-2026-10-29.md) ----
LA = "source_file LIKE '%lift-all%'"


def q1(c):
    """топ-10 признаков по подъёму верхнего дециля (D=360) в сент.; рядом медиана янв–июнь -> [(признак, янв–июнь, сент.)]."""
    out = []
    for f, s in c.execute(f"SELECT feature, lift FROM feature_lift WHERE month='2026-09' AND d=360 AND bucket=10 AND {LA} AND lift IS NOT NULL"):
        v = [r[0] for r in c.execute(f"SELECT lift FROM feature_lift WHERE feature=? AND d=360 AND bucket=10 AND month<'2026-07' AND {LA} "
                                     "AND lift IS NOT NULL", (f,))]
        if v:
            out.append((f, round(statistics.median(v), 1), round(s, 1)))
    return sorted(out, key=lambda t: -t[2])[:10]


def q2(c):
    """подтверждено в ≥7 из 9 мес.: верхний дециль, нижняя граница CI подъёма > 1 -> [(признак, D, месяцев)]."""
    return c.execute(f"SELECT feature, d, COUNT(*) m FROM feature_lift WHERE bucket=10 AND lift_lo>1 AND {LA} GROUP BY feature, d "
                     "HAVING m>=7 ORDER BY d, m DESC, feature LIMIT 15").fetchall()


def q3(c):
    """чего нет по месяцам -> [(признак, месяцев без данных, первый, последний)]."""
    return c.execute("SELECT feature, COUNT(*) m, MIN(month), MAX(month) FROM feature_coverage WHERE status='нет данных' "
                     "GROUP BY feature ORDER BY m DESC, feature").fetchall()


def verify(c, root):
    bad = 0
    for loader, tk, path, table, cols, const in SPEC:
        run = c.execute("SELECT run_id, rows, source_md5 FROM runs WHERE loader=? AND source_file=?", (loader, path)).fetchone()
        if not run or run[2] != md5(os.path.join(root, path)):
            print("FAIL нет прогона / md5 источника изменён:", path)
            bad += 1
            continue
        rows = read_rows(root, path, cols)
        cur = c.execute(f"SELECT * FROM {table} WHERE run_id=? ORDER BY " + ("rowid" if table == "hypotheses" else "src_row"), (run[0],))
        names = [d[0] for d in cur.description]
        dbrows = [dict(zip(names, r)) for r in cur.fetchall()]
        if len(dbrows) != len(rows) or run[1] != len(rows):
            print(f"FAIL строк {path}: csv {len(rows)}, БД {len(dbrows)}")
            bad += 1
            continue
        diff = 0
        for (n, row), dr in zip(rows, dbrows):
            for k, v in {**row, **const}.items():
                if dr[k] != v:
                    diff += 1
                    if diff < 4:
                        print("  разл.", path, "стр.", n, k, repr(v), repr(dr[k]))
        print(("OK  " if not diff else "FAIL"), f"{path}: {len(rows)} строк, расхождений {diff}")
        bad += bool(diff)
    for pat, kind, tk in EVENT_GLOBS:
        for full in glob.glob(os.path.join(root, pat)):
            p = os.path.relpath(full, root).replace("\\", "/")
            r = c.execute("SELECT md5 FROM events_ref WHERE path=?", (p,)).fetchone()
            ok = bool(r) and r[0] == md5(full)
            print(("OK  " if ok else "FAIL"), "events_ref", p)
            bad += not ok
    nf = c.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    nc = c.execute("SELECT COUNT(*) FROM feature_coverage").fetchone()[0]
    ok = nf > 0 and nc == nf * len(MONTHS)
    print(("OK  " if ok else "FAIL"), f"features {nf}; feature_coverage {nc} = {nf}×{len(MONTHS)}")
    bad += not ok
    # md=БД: «подъём верхнего дециля, янв–июнь → сент.» из md TK-447 («имя 4,2→4,5», округление до 0,1) против медианы по БД, допуск 0,06
    md = open(os.path.join(root, F + "pump-features-v2-2026-10-29.md"), encoding="utf-8").read()
    for f in ("ret24h_bps", "dmin1d_bps", "dmin3d_bps", "path7d_bps"):
        v = [r[0] for r in c.execute(f"SELECT lift FROM feature_lift WHERE feature=? AND d=360 AND bucket=10 AND month<'2026-07' AND {LA}", (f,))]
        s9 = c.execute(f"SELECT lift FROM feature_lift WHERE feature=? AND month='2026-09' AND d=360 AND bucket=10 AND {LA}", (f,)).fetchone()
        m = re.search(r"(?:%s|%s)[^;]*?(\d,\d)→(\d,\d)" % (re.escape(f), re.escape(f.replace("_bps", ""))), md)
        db = (statistics.median(v), s9[0]) if v and s9 else None
        ok = bool(m and db) and all(abs(float(x.replace(",", ".")) - y) <= 0.06 for x, y in zip(m.groups(), db))
        print(("OK  " if ok else "FAIL"), f"q1 {f}: БД {db[0]:.4f}→{db[1]:.4f}; md TK-447 {m.group(1)}→{m.group(2)}" if m and db else f"q1 {f}: нет данных")
        bad += not ok
    print("ИТОГО:", "зелёный" if not bad else f"красный ({bad})")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["init", "load", "verify", "queries"])
    ap.add_argument("--db", default="data/g017/g017.db")
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    c = connect(a.db)
    init(c)
    if a.cmd == "load":
        load(c, a.root)
        for t in ("runs", "features", "feature_coverage", "feature_lift", "hypotheses", "signals", "signal_groups", "events_ref"):
            print(t, c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    elif a.cmd == "verify":
        sys.exit(1 if verify(c, a.root) else 0)
    elif a.cmd == "queries":
        for title, rows in (("Q1 топ-10 признаков по подъёму верх. дециля D=360: (признак, медиана янв–июнь, сент.)", q1(c)),
                            ("Q2 подтверждено (нижняя граница CI подъёма >1, верх. дециль) в ≥7 из 9 мес.: (признак, D, месяцев)", q2(c)),
                            ("Q3 чего нет по месяцам: (признак, месяцев без данных, первый, последний)", q3(c))):
            print("\n" + title)
            for r in rows:
                print("  ", r)


if __name__ == "__main__":
    main()
