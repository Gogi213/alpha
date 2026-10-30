#!/usr/bin/env python3
"""TK-460 (слой G плана TK-456, В-67): выжившие признаки смарт-мани -> docs/findings/smartmoney-survivors-2026-10-30.csv.
Только stdlib. Берёт таблицы вердиктов L2 (TK-451), B2 (TK-458), S (TK-459) и оставляет признаки с вердиктом «подтверждено» по единому
правилу §3 п.3 плана (Холм по K + знак в каждом году с данными), причём против ВСЕХ контролей строки (признак с несколькими строками-контролями
выживает, только если подтверждены все). Прогноза нет: определение (def.* из словаря B1), метка события, ссылка на файл событий, ключ события.
  python tools/g017db/sm_survivors.py [--root .] [--check]   (--check: пересобрать в память и сравнить с файлом на диске)"""
import argparse, collections, csv, gzip, hashlib, io, os, re, sys

F = "docs/findings/"
OUT = F + "smartmoney-survivors-2026-10-30.csv"
COLS = ["sm_id", "layer", "test", "row_label", "def_ids", "claim_ids", "event_label", "event_key", "events_file", "controls", "n_rows",
        "delta_min", "p_holm_max", "verdict", "verdict_file", "verdict_rows", "verdict_md5"]
OK = ("подтверждено", "подтверждено (активнее)")
# B2: группа в holm.csv -> (тест(ы) словаря B1, файл событий). Файлы событий проверяются на существование.
B2 = {"G1.1": ("G1.1", "bp-g1-events"), "G2": ("G2.1 G2.2 G2.3", "bp-g2-events"), "G3.1": ("G3.1", "bp-g3-events"),
      "G3.2/3.3": ("G3.2 G3.2/G3.3 G3.3", "bp-g32-events"), "G4.1": ("G4.1", "bp-g41-events"), "G4.3": ("G4.3", "bp-g4-events"),
      "G5": ("G5.1 G5.2", "bp-g5-events"), "G6.1/6.2": ("G6.1/G6.3 G6.2", "bp-g61-events"), "G6.3": ("G6.3 G6.3/G8.1", "bp-g63-out-events"),
      "G6.4": ("G6.1/G6.3", "bp-g6-events"), "G7": ("G7.1 G7.2", "bp-g7-events"), "G8.1": ("G8.1", "bp-g8-events"), "G4.2": ("G4.2", "bp-g42-events")}
CTRL_SUFFIX = re.compile(r" \| (C1|без фильтра)$")


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def rd(root, path):
    with open(os.path.join(root, path), encoding="utf-8", newline="") as f:
        return list(enumerate(csv.DictReader(f), 2))


def claims(root):
    """тест B2 -> (def.* ..., id утверждений bp ...), по реестру B1."""
    d = collections.defaultdict(lambda: (set(), []))
    for _, x in rd(root, F + "bp-claims-2026-10-30.csv"):
        d[x["тест_B2"]][0].update(x["идентификаторы_определений"].split())
        d[x["тест_B2"]][1].append(x["id"])
    return d


def event_defs(root, stem):
    p = os.path.join(root, F + stem + "-2026-10-30.csv.gz")
    assert os.path.exists(p), p
    seen = []
    with gzip.open(p, "rt", encoding="utf-8", newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            if i >= 20000:  # метка события — def_id первых строк файла (весь файл не читаем: файлы большие)
                break
            v = r.get("def_id", "")
            if v and v not in seen:
                seen.append(v)
            if len(seen) > 6:
                break
    return " ".join(seen), F + stem + "-2026-10-30.csv.gz"


def f6(x):
    return "" if x in ("", None) else f"{float(x):.6g}"


def build(root):
    out = []
    # --- L2 (TK-451): A5 в семействе Холма, оба вердикта подтверждено; X3 (hi/lo) — одна строка, только если подтверждены обе границы
    vf = F + "sancho-2026-10-30-layer2.csv"
    grp = collections.OrderedDict()
    for n, x in rd(root, vf):
        if x["claim"] == "A5" and x["family"] == "True":
            g = grp.setdefault((x["tf"], x["k"], x["dir"], re.sub(r"(hi|lo)$", "", x["oc"])), [])
            g.append((n, x))
    for (tf, k, d, oc), rows in grp.items():
        if not all(x["verdict"] == "подтверждено" and x["verdict_base"] == "подтверждено" for _, x in rows):
            continue
        # x3m2hi и x3m2lo — две границы одной строки X3: нужны обе
        if oc.startswith("x3") and sorted(x["oc"][-2:] for _, x in rows) != ["hi", "lo"]:
            continue
        side = "лой (L)" if d == "L" else "хай (H)"
        out.append(dict(sm_id="", layer="L2/TK-451", test="A5 свип→разворот",
                        row_label=f"свип {side}, ТФ {tf} мин, k={k}, исход {oc}", def_ids="def.swing_k def.sweep def.outcome def.horizon",
                        claim_ids="Санчо A5", event_label=f"sweep_k{k}:{d}", event_key="sym,dir,t (бар)",
                        events_file="calc:/data/tk0451/l1/l1-<SYM>-" + ("60" if tf == "60" else tf) + f"-{k}.csv.gz",
                        controls="C1 C2 (случайные уровни; то же без события)", n_rows=len(rows),
                        delta_min=min(min(float(x["delta_C1"]), float(x["delta_C2"])) for _, x in rows),
                        p_holm_max=max(float(x["p_holm_used"]) for _, x in rows), verdict="подтверждено", verdict_file=vf,
                        verdict_rows=" ".join(str(n) for n, _ in rows)))
    # --- B2 (TK-458): вердикт_K из holm.csv; признак = строка без суффикса контроля
    vf = F + "bp-b2-holm-2026-10-30.csv"
    cl = claims(root)
    grp = collections.OrderedDict()
    for n, x in rd(root, vf):
        grp.setdefault((x["группа"], CTRL_SUFFIX.sub("", x["строка"])), []).append((n, x))
    for (g, lab), rows in grp.items():
        if not all(x["вердикт_K"] in OK for _, x in rows):
            continue
        tests, stem = B2[g]
        defs, ids = set(), []
        for t in tests.split():
            defs |= cl[t][0]
            ids += cl[t][1]
        # тест, в котором лежит строка: G2/G3.x — префикс в подписи, иначе единственный/первый тест группы
        m = re.match(r"(G\d(?:\.\d)?)", lab)
        if m and m.group(1) in cl:
            defs, ids = set(cl[m.group(1)][0]), list(cl[m.group(1)][1])
        evd, ef = event_defs(root, stem)
        out.append(dict(sm_id="", layer="B2/TK-458", test=g, row_label=lab if re.match(r"G\d", lab) else f"{g} {lab}",
                        def_ids=" ".join(sorted(defs)), claim_ids=" ".join(ids), event_label=evd or lab,
                        event_key="coin,tf,ts_utc_ms" if evd else "по файлу", events_file=ef,
                        controls="C1 C2 (и «без фильтра» в G7)", n_rows=len(rows),
                        delta_min=min(float(x["dlt"]) for _, x in rows), p_holm_max=max(float(x["holm_m"]) for _, x in rows),
                        verdict="подтверждено", verdict_file=vf, verdict_rows=" ".join(str(n) for n, _ in rows)))
    # --- S (TK-459): строка × контроль (C1, «без фактора»); признак — строка, оба контроля подтверждены
    vf = F + "smartmoney-synthesis-2026-10-30.csv"
    grp = collections.OrderedDict()
    for n, x in rd(root, vf):
        grp.setdefault((x["сочетание"], x["строка"]), []).append((n, x))
    sdefs = {"S2": ("def.pd_zone def.sweep def.swing_k def.outcome", "sm_s2.py: нога k=2 до свипа, p>0,5 премиум", "sm_s2.premium_discount"),
             "S5": ("def.asia_range def.sweep def.session_window def.outcome", "sm_s5.py: снятие границы Азии в часе свипа",
                    "sm_s5.asia_boundary_taken")}
    for (comb, lab), rows in grp.items():
        if not all(x["вердикт"] == "подтверждено" for _, x in rows):
            continue
        s = lab.split()[0]
        defs, note, flag = sdefs[s]
        out.append(dict(sm_id="", layer="S/TK-459", test=comb, row_label=lab + " — " + note,
                        def_ids=defs, claim_ids="Санчо A5 + bp", event_label=flag, event_key="sym,dir,t (события A5 TK-451) + флаг",
                        events_file="calc:/data/tk0451/l1 (события A5) + флаг из tools/sm_" + s.lower() + ".py",
                        controls="C1; без фактора", n_rows=len(rows), delta_min=min(float(x["dlt"]) for _, x in rows),
                        p_holm_max=max(float(x["holm_gt0"]) for _, x in rows), verdict="подтверждено", verdict_file=vf,
                        verdict_rows=" ".join(str(n) for n, _ in rows)))
    cnt = collections.Counter()
    for r in out:
        pre = r["layer"].split("/")[0]
        cnt[pre] += 1
        r["sm_id"] = f"SM-{pre}-{cnt[pre]:02d}"
        r["verdict_md5"] = md5(os.path.join(root, r["verdict_file"]))
        r["delta_min"], r["p_holm_max"] = f6(r["delta_min"]), f6(r["p_holm_max"])
    return out


def text(rows):
    b = io.StringIO()
    w = csv.DictWriter(b, COLS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return b.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    rows = build(a.root)
    t = text(rows)
    p = os.path.join(a.root, OUT)
    if a.check:
        ok = os.path.exists(p) and open(p, encoding="utf-8", newline="").read() == t
        print(("OK  " if ok else "FAIL"), f"{OUT}: {len(rows)} признаков, пересборка = файл")
        sys.exit(0 if ok else 1)
    open(p, "w", encoding="utf-8", newline="").write(t)
    print(OUT, len(rows), dict(collections.Counter(r["layer"] for r in rows)))


if __name__ == "__main__":
    main()
