"""TK-071 п.3: рабочая нагрузка для пар A=A — W процессов, каждый PASSES раз считает crc32 по 64 МБ (L3/память-чувствительно)."""
import multiprocessing as mp, os, sys, zlib

W, PASSES = int(sys.argv[1]), int(sys.argv[2])


def work(i):
    buf = bytes(range(256)) * (64 << 12)
    c = 0
    for _ in range(PASSES):
        c = zlib.crc32(buf, c)
    return c


if __name__ == "__main__":
    with mp.Pool(W) as p:
        print(sum(p.map(work, range(W))) & 0xFFFFFFFF)
