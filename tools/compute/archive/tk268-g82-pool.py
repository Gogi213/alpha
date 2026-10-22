#!/usr/bin/env python3
"""TK-268 ш.2: пул сигналов R1 с колонками *_60s_bp — сверка со старым пулом TK-064 и сборка нового пула символьными ссылками.

  tk268-g82-pool.py --old /data/tk064/pool --new /data/tk0268/g82/main/signals --pool115 /data/tk0115/pool --out /data/tk0268/pool60 [--dry]

Источник суток: новый проход (--new) -> пул TK-115 (--pool115, там колонки 60s уже есть) -> иначе сутки остаются как в старом пуле (без колонок 60s).
Сверка по каждым суткам нового источника: ключ (symbol, day_utc, signal_index) — множества ключей старого пула (signals + signals-retry) и нового равны;
старые колонки побайтно равны; rounds.csv — те же строки. Любое расхождение — сутки не берутся из нового источника (остаются старые) и печатаются.
Выход: <out>/m-<мес>/{signals/<сутки> -> источник, lvl -> старый lvl}; старый пул не меняется.
"""
import argparse, csv, os, sys

SET = "t-bid-btc4h-q1"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep"]


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.reader(l for l in f if not l.startswith("#"))
        head = next(r, None)
        return head, list(r)


def old_rows(old, mon, day):
    """signals + signals-retry старого пула: {ключ: строка}, первым — signals; заголовок; rounds-строки."""
    rows, head, rnd = {}, None, []
    for sub in ("signals", "signals-retry"):
        p = os.path.join(old, f"m-{mon}", sub, day, SET)
        if os.path.isfile(os.path.join(p, "signals.csv")):
            h, rs = read(os.path.join(p, "signals.csv"))
            head = head or h
            si, di, ni = h.index("symbol"), h.index("day_utc"), h.index("signal_index")
            for r in rs:
                rows.setdefault((r[si], r[di], r[ni]), dict(zip(h, r)))
        if os.path.isfile(os.path.join(p, "rounds.csv")):
            rnd += read(os.path.join(p, "rounds.csv"))[1]
    return rows, head, rnd


def check(old, mon, day, src):
    """None — сутки сходятся; иначе текст расхождения."""
    orows, ohead, ornd = old_rows(old, mon, day)
    p = os.path.join(src, day, SET)
    if not os.path.isfile(os.path.join(p, "signals.csv")):
        return "нет signals.csv"
    nh, nr = read(os.path.join(p, "signals.csv"))
    si, di, ni = nh.index("symbol"), nh.index("day_utc"), nh.index("signal_index")
    new = {(r[si], r[di], r[ni]): dict(zip(nh, r)) for r in nr}
    if set(new) != set(orows):
        return f"ключи: старых {len(orows)} новых {len(new)}; только старых {len(set(orows) - set(new))}, только новых {len(set(new) - set(orows))}"
    miss = [c for c in (ohead or []) if c not in nh]
    if miss:
        return f"пропали колонки {miss[:3]}"
    bad = sum(1 for k, o in orows.items() if any(new[k][c] != o[c] for c in ohead))
    if bad:
        return f"старые колонки различаются в {bad} строках"
    rp = os.path.join(p, "rounds.csv")
    nrnd = read(rp)[1] if os.path.isfile(rp) else []
    if sorted(map(tuple, nrnd)) != sorted(map(tuple, ornd)):
        return f"rounds: старых {len(ornd)} новых {len(nrnd)} (строки различаются)"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--pool115", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    tot = {"new": 0, "p115": 0, "none": 0, "bad": 0}
    for mon in MONTHS:
        days = set()
        for sub in ("signals", "signals-retry"):
            base = os.path.join(a.old, f"m-{mon}", sub)
            days |= set(os.listdir(base)) if os.path.isdir(base) else set()
        days = sorted(d for d in days if os.path.isfile(os.path.join(a.old, f"m-{mon}", "signals", d, SET, "signals.csv"))
                      or os.path.isfile(os.path.join(a.old, f"m-{mon}", "signals-retry", d, SET, "signals.csv")))
        out = os.path.join(a.out, f"m-{mon}")
        if not a.dry:
            os.makedirs(os.path.join(out, "signals"), exist_ok=True)
            lvl = os.path.join(out, "lvl")
            if not os.path.lexists(lvl):
                os.symlink(os.path.join(a.old, f"m-{mon}", "lvl"), lvl)
        cnt = {"new": 0, "p115": 0, "none": 0, "bad": 0}
        for d in days:
            pick = None
            for kind, src in (("new", a.new), ("p115", os.path.join(a.pool115, f"m-{mon}", "signals"))):
                if os.path.isdir(os.path.join(src, d)):
                    why = check(a.old, mon, d, src)
                    if why is None:
                        pick = (kind, os.path.join(src, d))
                        break
                    cnt["bad"] += 1
                    print(f"РАСХОЖДЕНИЕ {mon} {d} [{kind}]: {why}")
            if pick is None:
                # сутки как в старом пуле (без колонок 60s): ссылка на старый signals (+ retry, если там)
                for sub in ("signals", "signals-retry"):
                    s = os.path.join(a.old, f"m-{mon}", sub, d)
                    if os.path.isdir(s) and not a.dry:
                        dst = os.path.join(out, "signals" if sub == "signals" else "signals-retry")
                        os.makedirs(dst, exist_ok=True)
                        if not os.path.lexists(os.path.join(dst, d)):
                            os.symlink(s, os.path.join(dst, d))
                cnt["none"] += 1
                continue
            cnt[pick[0]] += 1
            if not a.dry:
                dst = os.path.join(out, "signals", d)
                if not os.path.lexists(dst):
                    os.symlink(pick[1], dst)
        print(f"{mon}: суток {len(days)}; из нового прохода {cnt['new']}, из пула TK-115 {cnt['p115']}, без колонок 60s {cnt['none']}, расхождений {cnt['bad']}")
        for k in tot:
            tot[k] += cnt[k]
    print("итого", tot)
    sys.exit(1 if tot["bad"] else 0)


main()
