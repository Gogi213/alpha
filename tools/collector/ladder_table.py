"""ladder_table DIR — TK-533: таблица ступеней лестницы по файлам DIR/<вид>-<группа>-n<n>-r<повтор>.json (ladder.py): строка на (вид, группа, n), и точка излома по виду/группе.
Излом — первая ступень, где в любом повторе обрыв, либо lag_p50 > 2000 мс, либо тишина дольше половины окна (те же условия, что в ladder.sh). Окно «с паузой» — lag_max > STALL_MS: пауза пути/биржи, не предел соединения."""
import glob
import json
import os
import re
import statistics as st
import sys

STALL_MS = 1500


def load(d):
    rows = []
    for f in glob.glob(os.path.join(d, "*.json")):
        m = re.match(r"(\w+)-(\w+)-n(\d+)-r(\d+)\.json", os.path.basename(f))
        if m:
            r = json.load(open(f))
            r.update(kind=m.group(1), grp=m.group(2), n=int(m.group(3)), rep=int(m.group(4)))
            rows.append(r)
    return rows


def bad(r):
    return bool(r.get("error") or r["disconnects"] or (r.get("lag_p50") or 0) > 2000 or r.get("silent_s", 0) > r["dur"] / 2)


def main():
    rows = load(sys.argv[1])
    cell = {}
    for r in rows:
        cell.setdefault((r["kind"], r["grp"], r["n"]), []).append(r)
    print("| вид | группа | топиков | повторов | сообщ/с | КБ/с | лаг p50, мс | лаг p99, мс (мед. / макс.) | обрывов | окон с паузой > 1,5 с | CPU клиента, доля ядра |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    breaks = {}
    for (k, g, n), rs in sorted(cell.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])):
        cpu = [r["cpu_s"] / (r["dur"] + 5 + r.get("t_sub_done_s", 0)) for r in rs if r.get("cpu_s") is not None]
        p99 = [r["lag_p99"] for r in rs if r.get("lag_p99") is not None]
        stalls = sum(1 for r in rs if (r.get("lag_max") or 0) > STALL_MS)
        print(f"| {k} | {g} | {rs[0]['streams']} | {len(rs)} | {st.median(r['msg_s'] for r in rs):.0f} | {st.median(r['kb_s'] for r in rs):.0f} | {st.median(r['lag_p50'] for r in rs):.0f} "
              f"| {st.median(p99):.0f} / {max(p99):.0f} | {sum(r['disconnects'] for r in rs)} | {stalls} | {st.median(cpu):.2f} |")
        if any(bad(r) for r in rs):
            breaks.setdefault((k, g), n)
    print()
    for kg in sorted({(r["kind"], r["grp"]) for r in rows}):
        ns = sorted({r["n"] for r in rows if (r["kind"], r["grp"]) == kg})
        print(f"- {kg[0]}-{kg[1]}: ступеней {len(ns)} ({ns[0]}…{ns[-1]}), излом: {breaks[kg] if kg in breaks else 'не достигнут'}")


main()
