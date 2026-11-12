#!/usr/bin/env python3
"""G-022 turbo: markdown table of the step windows (`steps.sh`): every <tag>/<tag>-n<N>-w<K>/sum.json under the given dirs.

  stepstable.py <dir with sum.json windows> [<dir> ...]
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
    print("| набор | символов | окно | шардов | быстрых подкл. | лаг книги p50 / p99 / max, мс | поздних >2 с | обрывов (`error`) | покрытие mark после covered | REST пропущено после covered | шард-с без сессии | CPU, % ядра | замен пути / улучшений / отложено | вердикт |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for d in sys.argv[1:]:
        for p in sorted(glob.glob(os.path.join(d, "*", "sum.json")), key=key):
            s = json.load(open(p))
            tag, n, k = key(p)
            fs = "—" if s["fast_share"] is None else f"{s['fast_share'] * 100:.0f} % ({round(s['fast_share'] * s['dials'])}/{s['dials']})"
            mc = "—" if s["mark_cov"] is None else f"{s['mark_cov'] * 100:.3f} %"
            print(f"| {tag} | {'весь пул' if n == 10**6 else n} | {k} | {s['shards']} | {fs} | {s['lag_p50']} / {s['lag_p99']} / {s['lag_max']} | {s['late']} | {s['drops']} | {mc} | {s['rest_missing_after_covered']} из {s['rest']} | "
                  f"{s['uncovered_shard_s']} | {s['cpu_pct']} | {s['path_switch']} / {s['path_upgrade']} / {s['replace_deferred']} | {'чисто' if s['ok'] else 'ОТКАЗ: ' + '; '.join(s['reasons'])} |")


if __name__ == "__main__":
    main()
