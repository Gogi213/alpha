#!/usr/bin/env python3
"""Сводка «до / после» выкладки доработок по записи `watch.py`: окно «до» — 30 мин перед накатом, «после» — с 90-й секунды до +62 мин.

По окнам лага 60 с: p50 и p99 (медиана, минимум, максимум), p99 по видам (медиана), `queue_lag` p99 максимум, задержка шардов p99 максимум, ЦП (медиана), `rx_clamped` на 1 000 записей,
рост `sink_blocks_dropped` / `sink_errors`, `ring full`, переподключения и таймауты за час, `late_1h` по видам (последнее значение).
Запуск: python3 fusum.py --ex bybit --deploy-ts <epoch> [--dir /data/perf/followups-deploy]
"""
import argparse, json, os, statistics as st


def load(path):
    out = []
    for ln in open(path):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if 'err' not in r:
            out.append(r)
    return out


def med(v):
    v = [x for x in v if x is not None]
    return round(st.median(v), 1) if v else None


def mm(v):
    v = [x for x in v if x is not None]
    return (min(v), med(v), max(v)) if v else None


def summ(rs):
    if not rs:
        return None
    kinds = {}
    for r in rs:
        for k, v in (r.get('lag_in_kind') or {}).items():
            kinds.setdefault(k, []).append(v.get('p99'))
    rec = [r['totals'].get('records') for r in rs]
    rx = [r['totals'].get('rx_clamped') for r in rs]
    d = lambda k: (rs[-1]['totals'].get(k) or 0) - (rs[0]['totals'].get(k) or 0)  # noqa: E731
    return {
        'n': len(rs), 'p50 min/med/max': mm([(r.get('lag_in') or {}).get('p50') for r in rs]), 'p99 min/med/max': mm([(r.get('lag_in') or {}).get('p99') for r in rs]),
        'p99_by_kind_med': {k: med(v) for k, v in kinds.items()}, 'queue_p99_max': mm([(r.get('queue_lag') or {}).get('p99') for r in rs])[2] if rs else None,
        'shard_lag_p99_max': max([(r.get('shards') or {}).get('lag_p99_max') or 0 for r in rs]), 'cpu_med': med([r.get('cpu_cores') for r in rs]),
        'rx_clamped_per_1000': round((rx[-1] - rx[0]) / max(1, rec[-1] - rec[0]) * 1000, 2) if rec[-1] and rx[-1] is not None and rx[0] is not None else None,
        'sink_dropped': d('sink_blocks_dropped'), 'sink_errors': d('sink_errors'), 'ring_full': rs[-1].get('ring_full_total'),
        'late_1h_last': rs[-1].get('late_1h'), 'ws_last': rs[-1].get('ws'),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ex', required=True)
    ap.add_argument('--deploy-ts', type=int, required=True)
    ap.add_argument('--dir', default='/data/perf/followups-deploy')
    a = ap.parse_args()
    rs = load(os.path.join(a.dir, f'watch-{a.ex}.jsonl'))
    t = a.deploy_ts
    print(json.dumps({'ex': a.ex, 'before(-30m..-5s)': summ([r for r in rs if t - 1800 <= r['ts'] < t - 5]), 'after(+90s..+62m)': summ([r for r in rs if t + 90 <= r['ts'] <= t + 3720])}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
