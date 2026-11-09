#!/usr/bin/env python3
"""TK-516: перф-разбивка pq2bin l1 по корзинам. Вход — каталог с <источник>.txt от `tk516.sh prof` (perf report --sort symbol -n, self %, cycles:u).
`python tk516_buckets.py <каталог>` -> таблица «корзина × источник» в % циклов; символы вне порога 0,2 % — «прочее»."""
import re
import sys
from pathlib import Path

# порядок важен: первое совпадение
BUCKETS = [
    ("распаковка входа", r"ZSTD_decompress|ZSTD_buildFSE|HUF_read|HUF_decompress|FSE_decompress|zlib_rs.*(inflate|crc32|adler)|flate2|Decompress"),
    ("сжим выхода", r"ZSTD_compress|ZSTD_encode|ZSTD_seqToCodes|HIST_|HUF_compress|FSE_|ZSTD_entropy|ZSTD_buildCTable|ZSTD_count|ZSTD_"),
    ("декод parquet/arrow", r"parquet|arrow|byte_array|offset_buffer|RleDecoder|BitReader|bit_pack|ColhE3get|ColdE3get|Col.*get"),
    ("разбор чисел/строк", r"parse_f64|fastjson|from_utf8|parse_line|Cur|round$"),
    ("чтение строк (read_until)", r"read_until|buf_read|BufReader|memchr"),
    ("Book (книга)", r"Side|Book|set_side|hashbrown|BTree|select_nth|sort_unstable"),
    ("L1Enc (кодировщик)", r"L1Enc|push_row|publish"),
    ("инлайн-цикл строк (разбор+emit)", r"rust_begin_short_backtrace"),
    ("libc/ядро (без символов)", r"^0x[0-9a-f]+$|malloc|memcpy|memmove|\[k\]"),
]


def bucket(sym: str) -> str:
    for name, rx in BUCKETS:
        if re.search(rx, sym):
            return name
    return "прочее"


def main() -> int:
    d = Path(sys.argv[1])
    srcs = ["bn", "okx", "bybit", "gate"]
    tab: dict[str, dict[str, float]] = {}
    for s in srcs:
        total = 0.0
        for line in (d / f"{s}.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*([\d.]+)%\s+\d+\s+\[[.k]\]\s+(\S+)", line)
            if not m:
                continue
            pct, sym = float(m.group(1)), m.group(2)
            if line.split()[2] == "[k]":
                sym = "[k] " + sym
            tab.setdefault(bucket(sym), {}).setdefault(s, 0.0)
            tab[bucket(sym)][s] += pct
            total += pct
        tab.setdefault("прочее", {}).setdefault(s, 0.0)
        tab["прочее"][s] += max(0.0, 100.0 - total)
    print(f"{'корзина':36}" + "".join(f"{s:>8}" for s in srcs))
    for name in [b[0] for b in BUCKETS] + ["прочее"]:
        if name in tab:
            print(f"{name:36}" + "".join(f"{tab[name].get(s, 0.0):8.1f}" for s in srcs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
