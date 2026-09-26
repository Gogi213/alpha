#!/usr/bin/env python3
"""Правило «позиция занята» движка вне движка (T-31, П-02 §12, В-117).

`lob bounce-grid --busy-skip off` считает каждый сигнал своим кругом и пишет рядом с `rounds.csv` след шагов
`signals.csv`. Этот скрипт сначала отбрасывает сигналы по фильтру признаков (`--keep`), затем применяет правило
движка — по (символ, сутки, форма) в порядке `signal_index`: сигнал занят, если `t0 < idle_ns` прошлого принятого
шага; принятый `end_of_data` / `no_window` / остаток `ended` обрывает сутки (дальше сигналов нет, как `break` в
`drive_bounce_with`). Выход — то же дерево прогона (`<выход>/<сутки>/<набор>/rounds.csv`) из принятых исполненных
кругов в исходном порядке и формате: его читает `portfolio-sim.py` без правок. Фильтр «всё» (без `--keep`) обязан
дать тело `rounds.csv` прогона `--busy-skip on` байт в байт (гейт T-31).

    busy-replay.py <прогон off> <выход> [--keep фильтр.csv] [--sets a,b]

Фильтр — CSV с шапкой; ключ — пересечение его колонок с одним из видов:
  symbol,day_utc,signal_index[,form]      (номер сигнала из `signals.csv`)
  symbol,t0_ns,entry_px[,form]            (склейка с касаниями T-28 по времени взвода; entry_px — до 10 знаков)
Сигнал остаётся, если его ключ есть в фильтре. Итог по каждому набору — `<выход>/busy-replay.txt`.
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

KEYS = (("symbol", "day_utc", "signal_index"), ("symbol", "t0_ns", "entry_px"))


def px(v):
    return f"{float(v):.10f}" if v not in ("", None) else ""


def load_keep(path):
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(line for line in fh if not line.startswith("#")))
    cols = set(rows[0].keys()) if rows else set()
    for k in KEYS:
        if set(k) <= cols:
            key = k + (("form",) if "form" in cols else ())
            norm = (lambda r, key=key: tuple(px(r[c]) if c == "entry_px" else r[c].strip() for c in key))
            return key, {norm(r) for r in rows}, norm
    sys.exit(f"--keep {path}: нужны колонки {KEYS[0]} или {KEYS[1]} (есть {sorted(cols)})")


def read_body(path):
    """Строки-комментарии (#…), шапка и строки CSV — раздельно, как в файле."""
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
    comments = [l for l in lines if l.startswith("#")]
    body = [l for l in lines if not l.startswith("#")]
    return comments, body


def replay(sig_path, keep):
    """Ключи (symbol, day_utc, form, signal_index) принятых сигналов и счётчики."""
    _, body = read_body(sig_path)
    state = {}
    kept = set()
    n = defaultdict(int)
    for r in csv.DictReader(body):
        n["сигналов"] += 1
        if keep is not None:
            key, allowed, norm = keep
            if norm(r) not in allowed:
                n["фильтр"] += 1
                continue
        g = (r["symbol"], r["day_utc"], r["form"])
        idle, stopped = state.get(g, (None, False))
        t0 = int(r["t0_ns"])
        if stopped:
            n["обрыв суток"] += 1
            continue
        if idle is not None and t0 < idle:
            n["занято"] += 1
            continue
        kept.add(g + (r["signal_index"],))
        n["принято"] += 1
        if r["step"] in ("end_of_data", "no_window") or r["residual"] == "ended":
            state[g] = (idle, True)
        else:
            state[g] = (int(r["idle_ns"]), False)
    return kept, n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--keep")
    ap.add_argument("--sets", help="наборы через запятую (умолчание — все)")
    a = ap.parse_args()
    keep = load_keep(a.keep) if a.keep else None
    sets = set(a.sets.split(",")) if a.sets else None
    report = []
    total = defaultdict(int)
    found = 0
    for root, _, files in sorted(os.walk(a.src)):
        if "signals.csv" not in files:
            continue
        rel = os.path.relpath(root, a.src)
        if sets is not None and os.path.basename(root) not in sets:
            continue
        found += 1
        kept, n = replay(os.path.join(root, "signals.csv"), keep)
        comments, body = read_body(os.path.join(root, "rounds.csv"))
        if not comments or "busy_skip=off" not in comments[0]:
            sys.exit(f"{root}/rounds.csv: шапка без busy_skip=off — это не прогон T-31")
        head, rows = body[0], body[1:]
        out_rows = []
        for line, r in zip(rows, csv.DictReader([head] + rows)):
            if (r["symbol"], r["day_utc"], r["form"], r["signal_index"]) in kept:
                out_rows.append(line)
        dst = os.path.join(a.out, rel)
        os.makedirs(dst, exist_ok=True)
        note = f"# busy-replay: keep={a.keep or 'всё'} кругов {len(out_rows)} из {len(rows)}\n"
        with open(os.path.join(dst, "rounds.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.writelines(comments + [note, head] + out_rows)
        n["кругов"] = len(out_rows)
        for k, v in n.items():
            total[k] += v
        report.append(f"{rel}: " + ", ".join(f"{k} {v}" for k, v in n.items()))
    if not found:
        sys.exit(f"{a.src}: нет ни одного signals.csv — прогон не `--busy-skip off`")
    os.makedirs(a.out, exist_ok=True)
    summary = "итого: " + ", ".join(f"{k} {v}" for k, v in total.items())
    with open(os.path.join(a.out, "busy-replay.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(report + [summary]) + "\n")
    print(summary)


if __name__ == "__main__":
    main()
