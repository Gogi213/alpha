"""Этап A для сделок (режим «мейкер + тейкер-хедж»): дочитывает файлы trade<N> с места остановки, куски по символам.

Те же состояние и порядок записи, что в parse_inc.py (состояние — <state>/<биржа>-<метка>.pkl, кусок пишется до состояния).
Запись t (collector/wscore/codec.py): t dns lag ET sym da p q lf f m nq — берутся приём, лаг, цена p, количество q, флаг m.
Кусок: <out>/<биржа>/<символ>.<метка>.<номер>.npz — e (E, мс), et (E − T, мс: у Binance aggTrade E позже времени сделки T на 0…200 мс), rx, p, q, m (1 — покупатель мейкер).
Сделки пишутся все (повторы снимает писатель сборщика по ключу a).
"""
import os
import subprocess
import sys
from array import array
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import parse_inc  # noqa: E402
from parse_inc import load_state, save_state, tag_of  # noqa: E402


def consume(src, st, want, keep_from_ms, arrs):
    """Как parse_inc.consume, но для записей t. -> (сохранено, наименьший и наибольший приём)."""
    off, cum = st["off"], st["cum"]
    kept, rx_lo, rx_hi = 0, None, 0
    for line in src:
        if not line.endswith(b"\n"):
            break
        off += len(line)
        x = line.split(b"\t")
        cum += int(x[1])
        if x[0] != b"t":
            continue
        s = x[4]
        if s not in want:
            continue
        rx = cum // 1_000_000
        if rx < keep_from_ms:
            continue
        a = arrs.get(s)
        if a is None:
            a = arrs[s] = (array("q"), array("q"), array("d"), array("d"), array("b"), array("i"))
        a[0].append(rx - int(x[2]))
        a[1].append(rx)
        a[2].append(float(x[6]))
        a[3].append(float(x[7]))
        a[4].append(1 if x[10] == b"1" else 0)
        a[5].append(int(x[3]))
        kept += 1
        rx_lo = rx if rx_lo is None else min(rx_lo, rx)
        rx_hi = max(rx_hi, rx)
    st["off"], st["cum"] = off, cum
    return kept, rx_lo, rx_hi


def run(ex, day, path, want, state_dir, out_dir, keep_from_ms):
    """Дочитывает один файл сделок. want — {имя символа в файле (bytes): имя для файла}. -> число сохранённых записей."""
    tag = tag_of(day, path)
    st = load_state(state_dir, ex, tag)
    if st["done"]:
        return 0
    closed = os.path.exists(path + ".ok")
    p1 = subprocess.Popen([*parse_inc.DECOMP, path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    if st["off"]:
        p2 = subprocess.Popen(["tail", "-c", f"+{st['off'] + 1}"], stdin=p1.stdout, stdout=subprocess.PIPE, bufsize=1 << 22)
        p1.stdout.close()
        src, procs = p2.stdout, (p2, p1)
    else:
        src, procs = p1.stdout, (p1,)
    arrs = {}
    kept, rx_lo, rx_hi = consume(src, st, want, keep_from_ms, arrs)
    src.close()
    for pr in procs:
        pr.wait()
    if kept:
        st["seq"] += 1
        od = Path(out_dir, ex)
        od.mkdir(parents=True, exist_ok=True)
        for s, a in arrs.items():
            np.savez(od / f"{want[s]}.{tag}.{st['seq']}.npz", e=np.frombuffer(a[0], "i8"), rx=np.frombuffer(a[1], "i8"),
                     p=np.frombuffer(a[2], "f8"), q=np.frombuffer(a[3], "f8"), m=np.frombuffer(a[4], "i1"), et=np.frombuffer(a[5], "i4"))
        st["chunks"].append((st["seq"], rx_lo, rx_hi))
    st["done"] = closed
    save_state(state_dir, ex, tag, st)
    return kept


prune = parse_inc.prune
