"""«Молчание ноги» по ВСЕМ записям `b` символа: из пофайловых промежутков hold_parse.py (<вход>/gaps/*.pkl) строит глобальные интервалы без единой записи символа дольше GAP_MS.

Интервал (p, n) — последовательные записи символа по E среди ВСЕХ файлов всех запусков; запись смены только размера тоже запись. Файл вкладывает «свободные» отрезки: (−∞, первая запись), его промежутки > 5 с, (последняя запись, +∞);
глобально свободен тот отрезок, который свободен во всех файлах, где символ есть (пересечение отрезков, счёт «покрытие = число файлов»). Нет записей символа — нет ни интервалов, ни эпизодов: «нет данных» не превращается в «молчит». Но хвост — от последней записи символа до конца записи биржи (последняя E любого символа биржи) — молчание: символ, выпавший из сбора (пул, перезапуск, обрыв подписки), оставляет в скане последнюю цену, и живая нога с другой биржи «расходится» с ней (v6, 17.11: так возникли 16 тыс. ложных эпизодов Gate 16.11 после перезапуска 02:11 UTC; REST-свечи биржи расхождения не показывают). Начало (до первой записи) не молчание — цены ещё нет.
Запуск: python3 hold_rawgaps.py <каталог с gaps/> <выход.pkl>; выход: {"gaps": {(биржа, символ): (p[], n[])}, "span": {биржа: (первая E, последняя E)}, "sym": {(биржа, символ): (записей, первая E, последняя E)}}.
"""
import glob
import pickle
import sys
from pathlib import Path

import numpy as np

GAP_MS = 5_000
INF = 1 << 60


def free_intervals(files, gap_ms=GAP_MS, tail_to=None):
    """files — список (первая E, последняя E, p[], n[]) файлов, где символ есть. -> (p[], n[]) интервалов без записей длиннее gap_ms; хвост после последней записи доходит до tail_to (конец записи биржи), если задан."""
    nf = len(files)
    st, en = [], []
    for first, last, gp, gn in files:
        st += [np.array([-INF, last], np.int64), gp]
        en += [np.array([first, INF], np.int64), gn]
    st, en = np.concatenate(st), np.concatenate(en)
    c = np.concatenate([st, en])
    dl = np.concatenate([np.ones(len(st), np.int64), -np.ones(len(en), np.int64)])
    o = np.argsort(c, kind="stable")
    c, dl = c[o], dl[o]
    uc, idx = np.unique(c, return_index=True)
    cnt = np.cumsum(np.add.reduceat(dl, idx))
    lo, hi = uc[:-1], uc[1:]
    if tail_to is not None:
        hi = np.where(hi >= INF, tail_to, hi)
    ok = (cnt[:-1] == nf) & (lo > -INF) & (hi < INF) & ((hi - lo) > gap_ms)
    return lo[ok], hi[ok]


def main(gdir, out):
    per = {}
    span, sym_info = {}, {}
    for f in sorted(glob.glob(str(Path(gdir, "*.pkl")))):
        ex = Path(f).name.split("-", 1)[0]
        for s, (n, first, last, gp, gn) in pickle.load(open(f, "rb")).items():
            per.setdefault((ex, s), []).append((first, last, gp, gn))
            a, b = span.get(ex, (first, last))
            span[ex] = (min(a, first), max(b, last))
            si = sym_info.get((ex, s))
            sym_info[(ex, s)] = (n, first, last) if si is None else (si[0] + n, min(si[1], first), max(si[2], last))
    gaps = {k: free_intervals(v, tail_to=span[k[0]][1]) for k, v in per.items()}
    pickle.dump({"gaps": gaps, "span": span, "sym": sym_info}, open(out, "wb"), protocol=4)
    print({"symbols": len(gaps), "with_gaps": sum(1 for v in gaps.values() if len(v[0])), "intervals": sum(len(v[0]) for v in gaps.values()), "span": span})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
