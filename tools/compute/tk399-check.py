#!/usr/bin/env python3
"""TK-399: сверка событийного BBO (`lob seconds --bbo-events`) с посекундным рядом тех же суток (проверка гейта на настоящих данных).
    tk399-check.py <seconds.csv> <bbo.csv> <сутки YYYY-MM-DD>
Состояние на метку T — последняя строка bbo с t_ms <= T (метки — от начала суток); mid2 = bid+ask при обеих сторонах, иначе прежнее
двустороннее (как `snap_of`: срез без стороны не применяется). Сверяются mid2 на (t_s+1)·1000 и mid2_100 на t_s·1000+100 по всем
86 400 секундам. Печатает одну строку `rows=… mism_end=… mism_100=… order=ok|BAD cross=…`; rc 0 — всё сошлось."""
import bisect, calendar, csv, sys, time

sec_f, bbo_f, day = sys.argv[1:4]
t0 = calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000
with open(bbo_f, newline="") as f:
    rd = csv.reader(f)
    assert next(rd) == ["t_ms", "bid", "ask", "bq", "aq"], "заголовок bbo"
    rows = [tuple(map(int, r)) for r in rd]
ts = [r[0] - t0 for r in rows]
order = all(a < b for a, b in zip(ts, ts[1:])) and (not ts or (0 <= ts[0] and ts[-1] < 86_400_000))
changed = all(rows[i][1:] != rows[i - 1][1:] for i in range(1, len(rows)))
cross = sum(1 for r in rows if r[1] > 0 and r[2] > 0 and r[1] >= r[2])
mid, last = [], 0
for r in rows:  # carry двустороннего mid2
    if r[1] > 0 and r[2] > 0:
        last = r[1] + r[2]
    mid.append(last)


def at(t):
    i = bisect.bisect_right(ts, t) - 1
    return mid[i] if i >= 0 else 0


mm_end = mm_100 = n = 0
with open(sec_f, newline="") as f:
    for r in csv.DictReader(f):
        s = int(r["t_s"]) - t0 // 1000  # t_s в seconds.csv — абсолютные секунды эпохи
        n += 1
        mm_end += at((s + 1) * 1000) != int(r["mid2"])
        mm_100 += at(s * 1000 + 100) != int(r["mid2_100"])
ok = n == 86400 and mm_end == 0 and mm_100 == 0 and order and changed
print(f"rows={len(rows)} secs={n} mism_end={mm_end} mism_100={mm_100} order={'ok' if order else 'BAD'} "
      f"dedup={'ok' if changed else 'BAD'} cross={cross}")
sys.exit(0 if ok else 1)
