#!/usr/bin/env python3
"""TK-489: таблица «прежнее/новое» по D2 TK-479 (funding-pp / funding-ps) после E-010 из логов calc.
  remeasure_table.py table <id заявки перемера> [<id старого лога, по умолчанию 1102012237287>]   markdown-таблица
  remeasure_table.py check <id заявки перемера>   rc 0, если: digest funding-pp новый != digest cross новый (240/240, ядро),
                                                  сделки нового == прежнего по каждому варианту, обе стороны не пусты.
Лог берётся по ssh с calc (/data/sched/logs/<id>.log); `--file <путь>` вместо id — локальный файл."""
import re
import statistics
import subprocess
import sys

SSH = ["ssh", "-i", "C:/Users/Георгий/.ssh/id_rsa", "-o", "UserKnownHostsFile=C:/Users/Георгий/.ssh/known_hosts",
       "-o", "ConnectTimeout=15", "root@89.163.242.211"]
OLD_ID = "1102012237287"
BENCH = re.compile(
    r"bench kind=(\w+) engine=(\w+) threads=(\d+) window=(\d+) refresh=(\d+): заданий (\d+) из (\d+), минут (\d+), "
    r"пропусков (\d+), сделок (\d+), счёт ([\d.]+) с \(загрузка ([\d.]+) с, ставок фандинга (\d+)\), "
    r"([\d.e+]+) строк/с, ([\d.]+) пар/с")
KINDS = {"FundingPp": "funding-pp", "FundingPs": "funding-ps", "Cross": "cross"}


def fetch(job):
    out = subprocess.run(SSH + [f"cat /data/sched/logs/{job}.log"], capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        sys.exit(f"лог {job}: ssh rc {out.returncode}")
    return out.stdout.splitlines()


def parse(lines):
    """[{who, rep, kind, engine, threads, window, refresh, jobs, minutes, trades, secs, rates, rps, pps, digest, line}]"""
    recs, who, rep = [], "", 0
    for i, ln in enumerate(lines, 1):
        m = re.match(r"-- повтор (\d+) (PREV|NEW)\b", ln)
        if m:
            rep, who = int(m[1]), m[2]
            continue
        if ln.startswith("-- ") and not ln.startswith("-- повтор"):
            who, rep = "OLD", 0
            continue
        m = BENCH.search(ln)
        if m:
            g = m.groups()
            recs.append(dict(who=who or "OLD", rep=rep, kind=KINDS.get(g[0], g[0]), engine=g[1].lower(), threads=int(g[2]),
                             window=int(g[3]), refresh=int(g[4]), jobs=int(g[5]), minutes=int(g[7]), trades=int(g[9]),
                             secs=float(g[10]), rates=int(g[12]), rps=float(g[13]), pps=float(g[14]), digest=None, line=i))
            continue
        m = re.match(r"digest ([0-9a-f]{16})", ln)
        if m and recs:
            recs[-1]["digest"] = m[1]
    return recs


def key(r):
    return (r["kind"], r["engine"], r["threads"], r["window"], r["refresh"])


def group(recs, who, kinds=("funding-pp", "funding-ps")):
    g = {}
    for r in recs:
        if r["who"] == who and r["kind"] in kinds:
            g.setdefault(key(r), []).append(r)
    return g


def med(rs, f):
    return statistics.median(r[f] for r in rs)


def label(k):
    kind, eng, th, w, rf = k
    mode = "reference" if eng == "reference" else ("катится" if rf > 10**9 else f"пересчёт {rf}")
    return f"{kind}, {mode}, окно {w}, {th} пот."


def cmd_table(new_id, old_id):
    new, old = parse(fetch(new_id)), parse(fetch(old_id))
    gp, gn, go = group(new, "PREV"), group(new, "NEW"), group(old, "OLD")
    print(f"| вариант | строк/с в отчёте TK-479 (лог {old_id}:строка) | сделок, ставок (отчёт) | прежний, эта заявка | новый, эта заявка "
          f"| сделок прежний/новый | ставок новый | новый/прежний |")
    print("|---|---|---|---|---|---|---|---|")
    for k in sorted(gn):
        o = go.get(k, [None])[0]
        p, n = gp.get(k, []), gn[k]
        ostr = f"{o['rps']:.3e} (:{o['line']})" if o else "—"
        osd = f"{o['trades']}, {o['rates']}" if o else "—"
        pstr = f"{med(p, 'rps'):.3e}" if p else "—"
        ratio = f"{med(n, 'rps') / med(p, 'rps'):.3f}" if p else "—"
        ptr = f"{p[0]['trades']}" if p else "—"
        print(f"| {label(k)} | {ostr} | {osd} | {pstr} | {med(n, 'rps'):.3e} | {ptr} / {n[0]['trades']} | {n[0]['rates']} | {ratio} |")
    return 0


def cmd_check(new_id):
    new = parse(fetch(new_id))
    ok = True
    gp, gn = group(new, "PREV"), group(new, "NEW")
    cross = [r for r in new if r["who"] == "NEW" and r["kind"] == "cross" and r["digest"]]
    pp = [r for r in new if r["who"] == "NEW" and r["kind"] == "funding-pp" and r["engine"] == "kernel"
          and r["window"] == 240 and r["refresh"] == 240 and r["threads"] == 15 and r["digest"]]
    if not cross or not pp:
        print(f"ОТКАЗ: нет digest cross ({len(cross)}) или funding-pp ({len(pp)})")
        return 1
    same = pp[0]["digest"] == cross[0]["digest"]
    print(f"digest funding-pp {pp[0]['digest']} {'==' if same else '!='} cross {cross[0]['digest']} — "
          + ("ОТКАЗ: фандинг не начисляется" if same else "фандинг начисляется"))
    ok &= not same
    if len(gn) < 10 or len(gp) < 10:
        print(f"ОТКАЗ: вариантов новых {len(gn)}, прежних {len(gp)} (нужно по 10)")
        ok = False
    for k, rs in sorted(gn.items()):
        trades_new = {r["trades"] for r in rs}
        trades_prev = {r["trades"] for r in gp.get(k, [])}
        if trades_new != trades_prev or len(trades_new) != 1:
            print(f"РАЗНИЦА сделок {label(k)}: прежний {sorted(trades_prev)}, новый {sorted(trades_new)}")
            ok = False
    print("сделки z-правила новые == прежним по всем вариантам" if ok else "ИТОГ: есть расхождения")
    return 0 if ok else 1


def main(argv):
    args = [a for a in argv[1:] if a != "--file"]
    if len(args) >= 2 and args[0] == "table":
        return cmd_table(args[1], args[2] if len(args) > 2 else OLD_ID)
    if len(args) >= 2 and args[0] == "check":
        return cmd_check(args[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
