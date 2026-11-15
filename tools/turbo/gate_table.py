#!/usr/bin/env python3
"""G-022 Gate: markdown table of the ladder windows (`steps.sh` + `restpass.sh`, EXCH=gate): every <tag>/<tag>-n<N>-w<K>/sum.json + rest.json under the given dirs.

  gate_table.py <ladder dir> [<ladder dir> ...]
Columns: set, window, shards, fast dials, book lag p50 / p99 / max, late (> 2 s) all / after covered, stalls, drops, REST trades missing / extra / mismatch of N (after covered),
«symbol x second» cells without a recorded trade / of N, mark candles that moved without a record / of N, recorded marks outside the REST candles / judged, process CPU, host load (load1, PSI cpu avg10), book records / s.
"""
import glob
import json
import os
import re
import sys


def key(p):
    m = re.search(r"-n(\w+)-w(\d+)", p)
    n = m.group(1)
    return (os.path.basename(os.path.dirname(os.path.dirname(p))), 10**6 if n == "full" else int(n), int(m.group(2)))


def main():
    print("| лестница | набор | окно | шардов | быстрых (подкл.) | лаг книги p50 / p99 / max, мс | поздних >2 с (всего / после covered) | остановок | обрывов | REST пропущено / лишних / расх. (из) | клеток «символ × секунда» без сделки (из) | свеча mark сдвинулась без записи (из) | mark вне свечей (из оценённых) | CPU, % ядра | нагрузка хоста (load1 / PSI) | книга, зап/с | вердикт |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for d in sys.argv[1:]:
        for p in sorted(glob.glob(os.path.join(d, "*", "sum.json")), key=key):
            s = json.load(open(p))
            try:
                t = json.load(open(os.path.join(os.path.dirname(p), "rest.json")))["total"]
            except (OSError, ValueError, KeyError):
                t = {}
            tag, n, k = key(p)
            fs = "—" if s["fast_share"] is None else f"{s['fast_share'] * 100:.0f} % ({round(s['fast_share'] * s['dials'])}/{s['dials']})"
            rest = f"{t.get('missing_after_covered')} / {t.get('extra_in_file')} / {t.get('mismatch')} ({t.get('rest_after_covered')})" if t else "—"
            cells = f"{t.get('cells_missing_after_covered')} ({t.get('cells_rest_after_covered')})" if t else "—"
            mark = f"{t.get('mark_missed_after_covered')} ({t.get('mark_moving_after_covered')})" if t else "—"
            out = f"{t.get('mark_out_of_range')} ({t.get('mark_judged')})" if t else "—"
            print(f"| {tag} | {'весь пул' if n == 10**6 else n} | {k} | {s['shards']} | {fs} | {s['lag_p50']} / {s['lag_p99']} / {s['lag_max']} | {s['late']} / {s['late_after_covered']} | {s['stalls']} | {s['drops']} | {rest} | {cells} | {mark} | {out} | "
                  f"{s['cpu_pct']} | {s.get('host_load1')} / {s.get('psi_cpu10')} | {s.get('book_rps')} | {'чисто' if s['ok'] else 'ОТКАЗ: ' + '; '.join(s['reasons'])} |")


if __name__ == "__main__":
    main()
