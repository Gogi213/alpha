"""Этап A (живой, вкладка «Арбитраж»): дочитывает файл book<N> с места, где остановился в прошлый раз, и пишет куски по символам.

Те же поля и то же снятие повторов, что в parse_book.py (проверено на макете). Состояние файла (сколько распакованных байт целых строк
уже прочитано, накопленное время, последняя цена по символу, номер куска) — в <state>/<ключ>.pkl; кусок пишется до состояния, поэтому
сбой между ними лишь перепишет те же файлы. Файл с меткой .ok (закрыт сборщиком) после дочитывания помечается done и больше не читается.
Кусок: <out>/<биржа>/<символ>.<метка>.<номер>.npz — e, rx, lag, bid, ask, bsz, asz (как в этапе A).
"""
import os
import pickle
import subprocess
import sys
from array import array
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import LATE_MS  # noqa: E402

DECOMP = ["zstd", "-dc"]   # в тестах — ["cat"]


def tag_of(day, path):
    return f"{day}-{Path(path).name.removesuffix('.tsv.zst')}"


def state_path(state_dir, ex, tag):
    return Path(state_dir, f"{ex}-{tag}.pkl")


def load_state(state_dir, ex, tag):
    p = state_path(state_dir, ex, tag)
    if p.exists():
        with open(p, "rb") as f:
            return pickle.load(f)
    return {"off": 0, "cum": 0, "last": {}, "seq": 0, "done": False, "chunks": []}


def save_state(state_dir, ex, tag, st):
    p = state_path(state_dir, ex, tag)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(st, f, protocol=4)
    os.replace(tmp, p)


def run(ex, day, path, want, state_dir, out_dir, keep_from_ms):
    """Дочитывает один файл. want — {имя символа в файле (bytes): имя для файла}. -> число сохранённых записей."""
    tag = tag_of(day, path)
    st = load_state(state_dir, ex, tag)
    if st["done"]:
        return 0
    closed = os.path.exists(path + ".ok")
    p1 = subprocess.Popen([*DECOMP, path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    if st["off"]:
        p2 = subprocess.Popen(["tail", "-c", f"+{st['off'] + 1}"], stdin=p1.stdout, stdout=subprocess.PIPE, bufsize=1 << 22)
        p1.stdout.close()
        src, procs = p2.stdout, (p2, p1)
    else:
        src, procs = p1.stdout, (p1,)
    off, cum, last = st["off"], st["cum"], st["last"]
    arrs, kept, rx_lo, rx_hi = {}, 0, None, 0
    for line in src:
        if not line.endswith(b"\n"):   # недописанная строка — дочитаем в следующий раз
            break
        off += len(line)
        x = line.split(b"\t")
        cum += int(x[1])
        if x[0] != b"b":
            continue
        s = x[4]
        if s not in want:
            continue
        k = (x[6], x[8])
        lag = int(x[2])
        if lag <= LATE_MS and last.get(s) == k:
            continue
        last[s] = k
        rx = cum // 1_000_000
        if rx < keep_from_ms:
            continue
        a = arrs.get(s)
        if a is None:
            a = arrs[s] = (array("q"), array("q"), array("i"), array("d"), array("d"), array("d"), array("d"))
        a[0].append(rx - lag)
        a[1].append(rx)
        a[2].append(lag)
        a[3].append(float(k[0]))
        a[4].append(float(k[1]))
        a[5].append(float(x[7]))
        a[6].append(float(x[9]))
        kept += 1
        rx_lo = rx if rx_lo is None else min(rx_lo, rx)
        rx_hi = max(rx_hi, rx)
    src.close()
    for pr in procs:
        pr.wait()
    if kept:
        st["seq"] += 1
        od = Path(out_dir, ex)
        od.mkdir(parents=True, exist_ok=True)
        for s, a in arrs.items():
            np.savez(od / f"{want[s]}.{tag}.{st['seq']}.npz", e=np.frombuffer(a[0], "i8"), rx=np.frombuffer(a[1], "i8"),
                     lag=np.frombuffer(a[2], "i4"), bid=np.frombuffer(a[3], "f8"), ask=np.frombuffer(a[4], "f8"),
                     bsz=np.frombuffer(a[5], "f8"), asz=np.frombuffer(a[6], "f8"))
        st["chunks"].append((st["seq"], rx_lo, rx_hi))
    st.update(off=off, cum=cum, last=last, done=closed)
    save_state(state_dir, ex, tag, st)
    return kept


def prune(state_dir, out_dir, ex, cutoff_ms):
    """Удаляет куски, целиком старше cutoff_ms (по времени приёма). -> число удалённых файлов."""
    od = Path(out_dir, ex)
    if not od.exists():
        return 0
    by = {}
    for n in os.listdir(od):
        if n.endswith(".npz"):
            sym_tag_seq = n[:-4].rsplit(".", 2)
            if len(sym_tag_seq) == 3:
                by.setdefault((sym_tag_seq[1], int(sym_tag_seq[2])), []).append(n)
    removed = 0
    for sp in Path(state_dir).glob(f"{ex}-*.pkl"):
        tag = sp.stem[len(ex) + 1:]
        with open(sp, "rb") as f:
            st = pickle.load(f)
        keep = []
        for ch in st["chunks"]:
            if ch[2] < cutoff_ms:
                for n in by.get((tag, ch[0]), []):
                    os.remove(od / n)
                    removed += 1
            else:
                keep.append(ch)
        if len(keep) != len(st["chunks"]):
            st["chunks"] = keep
            save_state(state_dir, ex, tag, st)
    return removed
