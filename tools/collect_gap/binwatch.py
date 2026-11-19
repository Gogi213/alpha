#!/usr/bin/env python3
"""Сторож сбросов соединений Binance после выкладки пул-фикса: события `ws_end` с `how=fin_without_close_frame` по шардам.

Идёт по `events-*.jsonl` каталога данных с момента --since (epoch с), печатает строку на каждый такой сброс: шард, возраст сессии, число сбросов шарда.
Возраст сессии — по `diag.age_s` события или по разности с событием `connected` той же сессии. Сброс сессии моложе --young-s у шарда, повторившийся
(≥ 2 разных случая за --window-s; сбросы обеих линий в пределах --same-s — один случай), на шардах из --watch — строка `TRIGGER`
(условие отката `rollback.sh collect-binance`, проверка перед выкладкой: book03 BTC, book05 ETH); прочие шарды печатаются как `young` / `old` для сведения.
Запуск: python3 binwatch.py --root /data/collect/binance/data --since <epoch> [--watch book03,book05] [--young-s 60] [--window-s 600] [--same-s 5] [--interval 2]
"""
import argparse, glob, json, os, time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--since', type=int, required=True)
    ap.add_argument('--young-s', type=int, default=60)
    ap.add_argument('--window-s', type=int, default=600)
    ap.add_argument('--same-s', type=float, default=5.0)
    ap.add_argument('--watch', default='book03,book05')
    ap.add_argument('--interval', type=float, default=2.0)
    a = ap.parse_args()
    watch = set(a.watch.split(','))
    pos, conn = {}, {}  # файл -> смещение; (шард, сессия) -> ts_ms события connected
    total, young = {}, {}  # шард -> число сбросов / метки времени (с) молодых сбросов
    while True:
        for f in sorted(glob.glob(os.path.join(a.root, 'events-*.jsonl'))):
            try:
                with open(f, 'rb') as fh:
                    fh.seek(pos.get(f, 0))
                    data = fh.read()
                    end = data.rfind(b'\n') + 1
                    pos[f] = pos.get(f, 0) + end
            except OSError:
                continue
            for line in data[:end].splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = (d.get('ts_ms') or 0) / 1000
                if ts < a.since:
                    continue
                k = (d.get('shard'), d.get('session'))
                if d.get('ev') == 'connected':
                    conn[k] = ts
                elif d.get('ev') == 'ws_end' and d.get('how') == 'fin_without_close_frame':
                    age = (d.get('diag') or {}).get('age_s')
                    if age is None and k in conn:
                        age = ts - conn[k]
                    sh = d.get('shard')
                    total[sh] = total.get(sh, 0) + 1
                    is_young = age is not None and age < a.young_s
                    if is_young and not (young.get(sh) and ts - young[sh][-1] <= a.same_s):
                        young.setdefault(sh, []).append(ts)
                    recent = [t for t in young.get(sh, []) if ts - t <= a.window_s]
                    flag = 'TRIGGER' if is_young and sh in watch and len(recent) >= 2 else ('young' if is_young else 'old')
                    print(f'{time.strftime("%H:%M:%S", time.gmtime(ts))}Z {flag} {sh} fin_without_close_frame age={None if age is None else round(age)}s '
                          f'shard_total={total[sh]} young_in_{a.window_s}s={len(recent)}', flush=True)
        time.sleep(a.interval)


if __name__ == '__main__':
    main()
