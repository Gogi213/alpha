#!/usr/bin/env python3
"""Мост ПК ↔ сервер табло: запускается по ssh из collect.py и живёт, пока жив ssh (одно соединение на всё).

    bridge.py [смещение]
stdin  — по строке JSON {"view2": …, "built_at": …} раз в 5 с → атомарно в /data/board/status.json;
stdout — новые строки /data/board/answers.jsonl начиная со смещения (байты): {"off": <смещение после строки>, id, key, …};
         ПК обрабатывает и помнит `off`. Нет данных на stdin > 90 с (ПК пропал) — выходит.
"""
from __future__ import annotations

import json
import os
import select
import sys
import time
from pathlib import Path

DATA = Path(os.environ.get("BOARD_DATA", "/data/board"))
STATUS = DATA / "status.json"
ANSWERS = DATA / "answers.jsonl"
IDLE_EXIT_S = 90
MAX_LINE = 4 << 20


def save_status(line: bytes) -> None:
    try:
        d = json.loads(line)
    except ValueError:
        return
    if not isinstance(d, dict) or not isinstance(d.get("view2"), dict):
        return
    tmp = STATUS.with_name(STATUS.name + ".tmp")
    tmp.write_bytes(line.strip() + b"\n")
    os.replace(tmp, STATUS)


def send_answers(off: int, out) -> int:
    try:
        size = ANSWERS.stat().st_size
    except OSError:
        return off
    if off > size:  # файл заменили — читаем сначала (повторы безвредны: ask.py не отвечает дважды)
        off = 0
    if size == off:
        return off
    with open(ANSWERS, "rb") as f:
        f.seek(off)
        chunk = f.read(size - off)
    end = chunk.rfind(b"\n")
    if end < 0:
        return off
    pos = off
    for ln in chunk[: end + 1].split(b"\n")[:-1]:
        pos += len(ln) + 1
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            r["off"] = pos
            out.write(json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n")
    out.flush()
    return pos


def main() -> int:
    off = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    DATA.mkdir(parents=True, exist_ok=True)
    fd, out = sys.stdin.fileno(), sys.stdout.buffer
    buf, last_in = b"", time.time()
    while True:
        if select.select([fd], [], [], 1.0)[0]:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                return 0
            last_in = time.time()
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                save_status(line)
            if len(buf) > MAX_LINE:
                buf = b""
        elif time.time() - last_in > IDLE_EXIT_S:
            return 0
        try:
            off = send_answers(off, out)
        except BrokenPipeError:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
