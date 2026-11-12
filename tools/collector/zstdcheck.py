#!/usr/bin/env python3
"""G-022 (TK-527, дефект ядра): перечень закрытых файлов коллектора (рядом есть `.ok`), на которых `zstd -t` падает, и что в них читается. Только чтение: файлы не меняются и не переписываются (данные как есть).
  zstdcheck.py ROOT [ROOT …] [--out файл.tsv]
По каждому закрытому файлу: `zstd -t`; для упавших — строк читается (`decode` до обрыва), строк по `.ok`, последняя читаемая запись (тип/what), время приёма последней строки против времени изменения файла
(после дефекта последняя строка идёт в новом кадре с абсолютным dns: время приёма «уплывает» вперёд на ≈ эпоху). Итог — по корню и по суткам.
"""
import argparse
import glob
import io
import os
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wscore import codec  # noqa: E402


def readable_lines(path):
    """Строки файла до обрыва кадра (zstd-ошибка конца потока) — как их прочтёт читатель; неполная последняя строка отбрасывается."""
    import zstandard

    buf = io.BytesIO()
    with open(path, "rb") as f:
        r = zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True)
        while True:
            try:
                chunk = r.read(1 << 20)
            except zstandard.ZstdError:
                break
            if not chunk:
                break
            buf.write(chunk)
    data = buf.getvalue()
    return data[: data.rfind(b"\n") + 1].decode("utf-8", errors="replace").split("\n")[:-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args()
    rows, summary = [], Counter()
    for root in a.roots:
        for p in sorted(glob.glob(os.path.join(root, "20*", "*.tsv.zst"))):
            if not os.path.exists(p + ".ok"):
                continue  # открытый файл (пишется сейчас) — не закрытый
            day = os.path.basename(os.path.dirname(p))
            summary[(root, day, "closed")] += 1
            if subprocess.run(["zstd", "-t", "-q", p], capture_output=True).returncode == 0:
                continue
            summary[(root, day, "zstd_t_fail")] += 1
            lines = readable_lines(p)
            recs = list(codec.decode(lines))
            ok_rows = int(open(p + ".ok").read().strip())
            last = recs[-1] if recs else None
            last_kind = (last["t"] + (":" + last["what"] if last and last["t"] == "c" else "")) if last else "-"
            mtime_ms = int(os.path.getmtime(p) * 1000)
            wrong_rx = bool(last) and last["rx_ns"] // 1_000_000 > mtime_ms + 86_400_000
            summary[(root, day, "last_row_closed")] += last_kind == "c:closed"
            summary[(root, day, "last_rx_wrong")] += wrong_rx
            rows.append((root, day, os.path.basename(p), os.path.getsize(p), len(recs), ok_rows, last_kind, wrong_rx))
    head = "root\tday\tfile\tbytes\trows_readable\trows_ok\tlast_row\tlast_rx_wrong"
    out = [head] + ["\t".join(map(str, r)) for r in rows]
    if a.out:
        with open(a.out, "w") as f:
            f.write("\n".join(out) + "\n")
    print("\n".join(out))
    print()
    for (root, day, what), n in sorted(summary.items()):
        print(f"{root}\t{day}\t{what}\t{n}")


if __name__ == "__main__":
    main()
