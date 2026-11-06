#!/usr/bin/env python3
"""TK-493 w5 F: запуск команды с замером пиковой памяти (замер сухого прогона и F8_RSS=1 в f8_run.sh).

  python3 f8_rss.py <файл метки> <команда ...>

Запускает команду как дочерний процесс, ждёт, пишет в файл метки `maxrss_kb=<N>` - пик резидентной памяти САМОГО БОЛЬШОГО дочернего процесса (ru_maxrss
RUSAGE_CHILDREN: максимум по потомкам, не сумма), и возвращает код возврата команды. Только stdlib, Linux.
"""
import resource, subprocess, sys


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    mark, cmd = sys.argv[1], sys.argv[2:]
    rc = subprocess.call(cmd)
    kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    with open(mark, "w", encoding="utf-8") as fh:
        fh.write("maxrss_kb=%d" % kb)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
