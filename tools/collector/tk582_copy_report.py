#!/usr/bin/env python3
"""TK-582 v3: приёмка копии коллектора Binance (ветка с тёмным шардом) рядом с боевым (В-156). Только чтение.

Сравнивает те же шарды копии и боевого за общий отрезок времени (после прогрева), считает показатели приёмки из отчёта `docs/findings/binance-shard-stops-2026-11-17.md` §8:
  1. подключения за 300 с (скользящее окно, максимум) в остановках / эпизодах / всего: `connected` + `path_rejected_rtt` + отказ `connect …` (каждое — одно место бюджета);
  2. `path_slow_accepted` по причине (`dark` / `sole_source` / `uncovered` / без причины — бюджет выбран) и `ping_degraded` по причине (`silent_in_gap` / без причины — таймаут pong);
  3. паузы данных шарда (`gap_end`), шард-с, по окнам и по видам (book / trade / mark): копия против боевого на тех же шардах;
  4. сравнение записей по хешу (`alpha collect compare --from-ms --to-ms`, без rx) за последние `--compare-span-h` часов: книга и mark копии против шардов боевого, откуда взят пул (сделки не сравниваются: их символы лежат в разных шардах боевого);
  5. снимок health обоих процессов (бюджет, режим «стоянка»).
Окна W1… находятся по паузам ВСЕГО боевого процесса (все его шарды): доля шардов в паузе в слоте `--slot-s` ≥ `--stop-share` — окно «большое» (в нём остановки биржи и сильные эпизоды сети), ≥ `--episode-share` — «малое»;
слоты ближе `--merge-s` сливаются. Это разбор по доле шардов, а не по причине: деление на остановки S1–S3 и эпизоды сети E1–E7 отчёта (по сети и по часам) — только вручную через `--windows`. Пороги разбора — назначаемые (Судье), не измеренные: меняются флагами, значения печатаются в отчёте; `--windows файл.json` (`[["S1","2026-11-16T21:18:32Z","2026-11-16T21:21:57Z"], …]`) задаёт окна вручную.
Прогрев: первые `--warmup-s` с после старта копии (пачка стартовых подключений, режим «стоянка» не оценивается первые 2 мин) в разбор не входят.

Копия работает на пуле из символов нескольких шардов боевого (`tk582_copy_start.sh`): ее шарды СОСТАВОМ не совпадают с одноимёнными шардами боевого (копия режет пул по алфавиту, боевой — по нагрузке),
поэтому «те же символы» = набор шардов боевого, из которых взят пул (`--live-shards`; список печатает `tk582_copy_start.sh` и пишет в `layout.json`); сравнение по видам (book / trade / mark) — суммы по этим наборам.
usage: tk582_copy_report.py --live /data/collect/binance/data --copy /data/tk582v3/data --live-shards book00,book01,trade02,mark00 [--compare-live-shards book00,book01,mark00]
       [--from-ms T] [--to-ms T] [--json out.json] [--live-health файл] [--copy-health файл] [--compare-bin /data/tk582v3/alpha-tk582v3 [--compare-span-h 2] [--work /data/tk582v3/cmp]]
Запуск на srv — под `nice -n 19` (события шардов читаются целиком; сравнение записей читает сутки по 5 шардам и держит их в памяти — только через `systemd-run`)."""
import argparse
import bisect
import collections
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

EVS = ("connected", "path_rejected_rtt", "error", "connect_wait", "path_slow_accepted", "path_rejected", "path_ok", "ping_degraded", "path_switch", "path_upgrade", "rotated",
       "storm_hold", "storm_abort", "replace_capped", "path_reject_capped", "gap", "gap_end", "started", "stopped", "subscribed")
EV_RE = re.compile(r'"ev":"(%s)"' % "|".join(EVS))
ATTEMPT_KEYS = ("connected", "path_rejected_rtt", "connect_error")
BLOCK_KEYS = ("storm_hold", "replace_capped", "path_reject_capped")
SHOW = ("connected", "path_rejected_rtt", "connect_error", "connect_wait", "path_slow_accepted:dark", "path_slow_accepted:sole_source", "path_slow_accepted:uncovered", "path_slow_accepted:budget",
        "path_ok", "path_rejected", "ping_degraded:silent_in_gap", "ping_degraded:timeout", "path_switch", "path_upgrade", "rotated", "storm_hold", "storm_abort", "replace_capped", "path_reject_capped")


def kind_of(shard):
    return "trade" if shard.startswith("trade") else ("mark" if shard.startswith("mark") else "book")


def key_of(e):
    ev = e["ev"]
    if ev == "ping_degraded":
        return "ping_degraded:" + e.get("why", "timeout")
    if ev == "path_slow_accepted":
        return "path_slow_accepted:" + e.get("why", "budget")
    if ev == "error":
        return "connect_error" if str(e.get("err", "")).startswith("connect ") else "error"
    return ev


def read_events(root, shard):
    out = []
    try:
        fh = open(os.path.join(root, "events-%s.jsonl" % shard), encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for ln in fh:
            if not EV_RE.search(ln):
                continue
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if "ts_ms" in e:
                out.append(e)
    return out


def last_ts(root, shard):
    """Последний замер шарда: хвост `stats-<шард>.jsonl` (строка раз в 10 с) — события шарда в тихое время редки и конец отрезка по ним не найти."""
    try:
        path = os.path.join(root, "stats-%s.jsonl" % shard)
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - 20000))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
        for ln in reversed(lines):
            try:
                return int(json.loads(ln)["ts_ms"])
            except (ValueError, KeyError):
                continue
    except OSError:
        pass
    return 0


def shards_of(root):
    return sorted(os.path.basename(p)[7:-6] for p in glob.glob(os.path.join(root, "events-*.jsonl")))


def pauses(evs, hi):
    """Паузы данных шарда [(начало_мс, конец_мс)] по `gap_end`; пауза без конца к концу отрезка — до `hi`."""
    spans, open_gap = [], None
    for e in evs:
        if e["ev"] == "gap":
            open_gap = e["ts_ms"] - int(e.get("gap_ms", 0))
        elif e["ev"] == "gap_end":
            st = int(e["gap_start_ms"])
            spans.append((st, int(e.get("gap_end_ms", st + int(e.get("gap_total_ms", 0))))))
            open_gap = None
        elif e["ev"] in ("started", "stopped"):
            open_gap = None
    if open_gap is not None:
        spans.append((open_gap, hi))
    spans.sort()
    merged = []
    for s, e in spans:   # паузы шарда соседних запусков и перекрывающиеся метки концов — одна пауза
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def clipped(spans, lo, hi, min_ms=0):
    tot, n = 0, 0
    for s, e in spans:
        s, e = max(s, lo), min(e, hi)
        if e - s > 0 and e - s >= min_ms:
            tot += e - s
            n += 1
    return tot, n


def detect_windows(spans_by_shard, lo, hi, a):
    """Окна по паузам всех шардов процесса: [(имя, класс S|E, начало_мс, конец_мс, макс. доля)]."""
    slot = a.slot_s * 1000
    n_slots = max(1, (hi - lo) // slot + 1)
    cover = [0] * n_slots   # шардов в паузе в слоте (шард считается раз, сколько бы пауз ни было в слоте)
    for spans in spans_by_shard.values():
        hit = bytearray(n_slots)
        for s, e in spans:
            s, e = max(s, lo), min(e, hi)
            if e <= s:
                continue
            for i in range((s - lo) // slot, (e - lo) // slot + 1):
                hit[i] = 1
        for i, h in enumerate(hit):
            cover[i] += h
    total = max(1, len(spans_by_shard))
    share = [c / total for c in cover]
    marks = [(i, "S" if x >= a.stop_share else "E") for i, x in enumerate(share) if x >= a.episode_share]
    wins = []
    for i, c in marks:
        t0 = lo + i * slot
        if wins and t0 - wins[-1][3] <= a.merge_s * 1000:
            w = wins[-1]
            w[3] = t0 + slot
            w[4] = max(w[4], share[i])
            if c == "S":
                w[1] = "S"
        else:
            wins.append(["", c, t0, t0 + slot, share[i]])
    for i, w in enumerate(wins, 1):
        w[0] = "W%d" % i
    return [tuple(w) for w in wins]


def parse_ts(s):
    return int(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def max_in(times, lo, hi, width=300_000):
    """Наибольшее число подключений в окне `width` мс, начинающемся в (lo − width, hi)."""
    best = 0
    i0 = bisect.bisect_right(times, lo - width)
    for i in range(i0, len(times)):
        t = times[i]
        if t >= hi:
            break
        best = max(best, bisect.bisect_left(times, t + width) - i)
    return best


class Set:
    """Процесс (боевой или копия) на выбранных шардах: события и паузы."""

    def __init__(self, root, shards):
        self.root, self.shards = root, shards
        self.ev = {s: read_events(root, s) for s in shards}
        self.att = sorted(e["ts_ms"] for evs in self.ev.values() for e in evs if key_of(e) in ATTEMPT_KEYS)

    def counts(self, lo, hi):
        c = collections.Counter()
        for evs in self.ev.values():
            for e in evs:
                if lo <= e["ts_ms"] < hi:
                    c[key_of(e)] += 1
        c["attempts"] = sum(c[k] for k in ATTEMPT_KEYS)
        c["blocks"] = sum(c[k] for k in BLOCK_KEYS)
        c["max300"] = max_in(self.att, lo, hi)
        return c

    def paused(self, lo, hi, a):
        tot, n, n4 = 0, 0, 0
        kinds = collections.defaultdict(lambda: [0, 0, 0])
        for s, evs in self.ev.items():
            t, k = clipped(pauses(evs, hi), lo, hi)
            _, k4 = clipped(pauses(evs, hi), lo, hi, a.min_pause_s * 1000)
            tot, n, n4 = tot + t, n + k, n4 + k4
            kinds[kind_of(s)][0] += t
            kinds[kind_of(s)][1] += k
            kinds[kind_of(s)][2] += 1
        return tot / 1000.0, n, n4, {k: (v[0] / 1000.0, v[1], v[2]) for k, v in kinds.items()}


def fmt_ms(t):
    return datetime.datetime.fromtimestamp(t / 1000, datetime.timezone.utc).strftime("%m-%d %H:%M:%S")


def health_snap(path):
    if not path or not os.path.exists(path):
        return None
    try:
        h = json.load(open(path))
    except (OSError, ValueError):
        return None
    sh = h.get("shards", [])
    return {"version": h.get("process", {}).get("version"), "connect_budget": h.get("connect_budget"), "storm": h.get("storm"), "shards": len(sh),
            "shards_connected_lt2": [s.get("shard") for s in sh if s.get("connected", 0) < 2], "ts_ms": h.get("ts_ms")}


def span_days(lo, hi):
    d0 = datetime.datetime.fromtimestamp(lo / 1000, datetime.timezone.utc).date()
    d1 = datetime.datetime.fromtimestamp(hi / 1000, datetime.timezone.utc).date()
    days, d = [], d0
    while d <= d1:
        a = int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        days.append((d.isoformat(), max(lo, a), min(hi, a + 86_400_000)))
        d += datetime.timedelta(days=1)
    return days


def run_compare(a, shards, live_shards, lo, hi):
    """Сравнение записей за последние `--compare-span-h` часов отрезка (память `compare` растёт с числом записей: 14 ГБ на 10 ч двух шардов книги боевого); файлы — дней, которые отрезок задевает."""
    c_hi = a.compare_to_ms or hi
    c_lo = a.compare_from_ms or max(lo, c_hi - int(a.compare_span_h * 3_600_000))
    work = a.work or "/tmp/tk582_cmp"
    kinds = a.compare_kinds.split(",")
    live_cmp = a.compare_live_shards.split(",") if a.compare_live_shards else [s for s in live_shards if kind_of(s) in kinds]
    days = [d[0] for d in span_days(c_lo, c_hi)]
    farm = {}
    for tag, root, want in (("a", a.live, lambda n: n in live_cmp), ("b", a.copy, lambda n: kind_of(n) in kinds)):
        base = os.path.join(work, tag)
        os.makedirs(base, exist_ok=True)
        for d in os.listdir(base):   # прежние связи прошлого запуска
            dd = os.path.join(base, d)
            for f in os.listdir(dd):
                os.unlink(os.path.join(dd, f))
        n = 0
        for day in days:
            dst = os.path.join(base, day)
            os.makedirs(dst, exist_ok=True)
            for f in sorted(glob.glob(os.path.join(root, day, "*.tsv.zst"))):
                if want(os.path.basename(f).split("-")[0]):
                    os.symlink(f, os.path.join(dst, os.path.basename(f)))
                    n += 1
        farm[tag] = n
    cmd = ["nice", "-n", "19", a.compare_bin, "collect", "compare", "--a", os.path.join(work, "a"), "--b", os.path.join(work, "b"), "--from-ms", str(c_lo), "--to-ms", str(c_hi)]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    return {"from_ms": c_lo, "to_ms": c_hi, "days": days, "live_shards": live_cmp, "kinds": kinds, "files_live": farm["a"], "files_copy": farm["b"], "rc": r.returncode, "sec": round(time.time() - t0),
            "out": r.stdout.strip()[-6000:], "err": r.stderr.strip()[-1000:]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", required=True, help="корень данных боевого (events-*.jsonl, <день>/…)")
    ap.add_argument("--copy", required=True, help="корень данных копии")
    ap.add_argument("--copy-shards", help="шарды копии через запятую (по умолчанию — все по файлам events-*.jsonl)")
    ap.add_argument("--live-shards", help="шарды боевого с теми же символами, что у копии (по умолчанию — одноимённые шардам копии)")
    ap.add_argument("--compare-live-shards", help="шарды боевого для сравнения записей (по умолчанию — book*/mark* из --live-shards)")
    ap.add_argument("--compare-kinds", default="book,mark", help="виды шардов копии в сравнении записей")
    ap.add_argument("--from-ms", type=int)
    ap.add_argument("--to-ms", type=int)
    ap.add_argument("--warmup-s", type=int, default=300)
    ap.add_argument("--slot-s", type=int, default=10)
    ap.add_argument("--stop-share", type=float, default=0.5)
    ap.add_argument("--episode-share", type=float, default=0.15)
    ap.add_argument("--merge-s", type=int, default=60)
    ap.add_argument("--min-pause-s", type=int, default=4, help="пауза короче правило не трогает (отчёт §7.1, п. 1)")
    ap.add_argument("--windows", help="окна вручную (json)")
    ap.add_argument("--live-health")
    ap.add_argument("--copy-health")
    ap.add_argument("--compare-bin")
    ap.add_argument("--compare-span-h", type=float, default=2.0, help="сравнение записей — за последние N часов отрезка (память compare ≈ 1,3 ГБ на час двух шардов книги боевого)")
    ap.add_argument("--compare-from-ms", type=int)
    ap.add_argument("--compare-to-ms", type=int)
    ap.add_argument("--work")
    ap.add_argument("--json")
    a = ap.parse_args()

    shards = a.copy_shards.split(",") if a.copy_shards else shards_of(a.copy)
    if not shards:
        sys.exit("в корне копии нет events-*.jsonl")
    live_shards = a.live_shards.split(",") if a.live_shards else shards
    copy = Set(a.copy, shards)
    live_same = Set(a.live, live_shards)
    live_all = Set(a.live, shards_of(a.live))
    starts = [e["ts_ms"] for evs in copy.ev.values() for e in evs if e["ev"] == "started"]
    ends = [max(last_ts(a.copy, s), evs[-1]["ts_ms"] if evs else 0) for s, evs in copy.ev.items()]
    if not starts:
        sys.exit("в журнале копии нет `started`")
    t_start = max(starts)
    lo = a.from_ms or t_start + a.warmup_s * 1000
    hi = a.to_ms or min(ends)
    # отрезок только пока копия работала без перезапусков не проверяется: перезапуски копии печатаются ниже (число `started`)
    restarts = len(starts) - len(shards) if len(starts) >= len(shards) else 0
    print("# TK-582 v3: приёмка копии Binance (шарды копии %s; шарды боевого с теми же символами %s)" % (",".join(shards), ",".join(live_shards)))
    print("отрезок: %s … %s UTC (%.1f ч), старт копии %s, прогрев %d с; перезапусков копии (лишних `started`): %d" % (fmt_ms(lo), fmt_ms(hi), (hi - lo) / 3.6e6, fmt_ms(t_start), a.warmup_s, restarts))
    if hi <= lo:
        sys.exit("отрезок пуст: копия работает меньше прогрева")
    print("параметры разбора окон (назначаемые): слот %d с, окно большое ≥ %.0f %% шардов боевого в паузе, малое ≥ %.0f %%, слияние ≤ %d с; пауза «длинная» ≥ %d с\n" % (a.slot_s, a.stop_share * 100, a.episode_share * 100, a.merge_s, a.min_pause_s))

    live_spans = {s: pauses(evs, hi) for s, evs in live_all.ev.items()}
    if a.windows:
        wins = [(n, n[0], parse_ts(x), parse_ts(y), 0.0) for n, x, y in json.load(open(a.windows))]
    else:
        wins = detect_windows(live_spans, lo, hi, a)
    res = {"span": [lo, hi], "shards": shards, "live_shards": live_shards, "windows": [], "params": vars(a)}

    def block(name, w_lo, w_hi, extra=""):
        row = {}
        print("## %s%s: %s … %s (%d с)" % (name, extra, fmt_ms(w_lo), fmt_ms(w_hi), (w_hi - w_lo) // 1000))
        cols = (("боевой, шарды с теми же символами", live_same), ("копия", copy), ("боевой, все шарды", live_all))
        cs = {n: s.counts(w_lo, w_hi) for n, s in cols}
        ps = {n: s.paused(w_lo, w_hi, a) for n, s in cols}
        print("| показатель | " + " | ".join(n for n, _ in cols) + " |")
        print("|---|" + "---|" * len(cols))
        print("| число шардов | " + " | ".join(str(len(s.shards)) for _, s in cols) + " |")
        print("| подключений за 300 с, максимум (места бюджета) | " + " | ".join(str(cs[n]["max300"]) for n, _ in cols) + " |")
        print("| попыток всего | " + " | ".join(str(cs[n]["attempts"]) for n, _ in cols) + " |")
        for k in SHOW:
            if any(cs[n][k] for n, _ in cols):
                print("| %s | " % k + " | ".join(str(cs[n][k]) for n, _ in cols) + " |")
        print("| пауз данных / из них ≥ %d с | " % a.min_pause_s + " | ".join("%d / %d" % (ps[n][1], ps[n][2]) for n, _ in cols) + " |")
        print("| пауза, шард-с | " + " | ".join("%.0f" % ps[n][0] for n, _ in cols) + " |")
        for kd in ("book", "trade", "mark"):
            if any(kd in ps[n][3] for n, _ in cols):
                print("| пауза %s, шард-с / число (шардов) | " % kd + " | ".join("%.0f / %d (%d)" % ps[n][3].get(kd, (0.0, 0, 0)) for n, _ in cols) + " |")
        lp, cp = ps["боевой, шарды с теми же символами"][0], ps["копия"][0]
        print("\nпауза копии / боевого на тех же символах: %s\n" % ("%.2f (%.0f / %.0f шард-с)" % (cp / lp, cp, lp) if lp > 0 else "боевой без пауз (копия %.0f шард-с)" % cp))
        row["counts"] = {n: dict(cs[n]) for n, _ in cols}
        row["paused"] = {n: {"shard_s": ps[n][0], "n": ps[n][1], "n_long": ps[n][2], "by_kind": ps[n][3]} for n, _ in cols}
        return row

    res["total"] = block("Весь отрезок", lo, hi)
    print("## Окна по паузам боевого (%d): %s\n" % (len(wins), ", ".join("%s %s…%s доля %.0f %%" % (n, fmt_ms(x)[6:], fmt_ms(y)[6:], s * 100) for n, _, x, y, s in wins) or "нет"))
    for n, c, x, y, s in wins:
        row = block("Окно %s" % n, x, y, " (%s, макс. доля шардов боевого в паузе %.0f %%)" % ("большое" if c == "S" else "малое", s * 100))
        row.update({"name": n, "class": c, "lo": x, "hi": y})
        res["windows"].append(row)
    other = [(lo, hi)]
    for _, _, x, y, _ in sorted(wins, key=lambda w: w[2]):
        other = [seg for o in other for seg in ((o[0], min(o[1], x)), (max(o[0], y), o[1])) if seg[1] > seg[0]]
    for nm, s in (("боевой, шарды с теми же символами", live_same), ("копия", copy)):
        tot = sum(s.paused(x, y, a)[0] for x, y in other)
        res.setdefault("outside_windows_shard_s", {})[nm] = tot
    print("вне окон, пауза шард-с: " + "; ".join("%s %.0f" % (k, v) for k, v in res["outside_windows_shard_s"].items()) + "\n")

    res["health"] = {"live": health_snap(a.live_health), "copy": health_snap(a.copy_health)}
    print("## Health (снимок)\n```json\n%s\n```\n" % json.dumps(res["health"], ensure_ascii=False, indent=1))
    subs = {}
    for nm, s in (("боевой", live_same), ("копия", copy)):
        subs[nm] = {sh: next((e.get("streams") for e in reversed(evs) if e["ev"] == "subscribed"), None) for sh, evs in s.ev.items()}
    res["streams_per_shard"] = subs
    print("## Потоков на шард (последний `subscribed`; состав символов должен совпадать)\n```json\n%s\n```\n" % json.dumps(subs, ensure_ascii=False))
    if a.compare_bin:
        res["compare"] = run_compare(a, shards, live_shards, lo, hi)
        c = res["compare"]
        print("## Сравнение записей (`alpha collect compare`, %s … %s UTC, дни %s, файлов боевого %d / копии %d, rc %d, %d с)" % (fmt_ms(c["from_ms"]), fmt_ms(c["to_ms"]), ",".join(c["days"]), c["files_live"], c["files_copy"], c["rc"], c["sec"]))
        print("```\n%s\n```\n%s" % (c["out"], c["err"]))
    if a.json:
        json.dump(res, open(a.json, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
