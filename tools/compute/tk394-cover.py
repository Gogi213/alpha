#!/usr/bin/env python3
"""TK-394 П-28 п.1 (Судья): покрытие BBO Gate фьюча по ВСЕМ монетам и суткам.
Запуск (calc): tk394-cover.py OUTDIR COIN... -> OUTDIR/COIN.json: по суткам trades, strict, win, доля корзин бид>=аск."""
import sys, os, json, importlib.util
import numpy as np
D = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('cand', os.path.join(D, 'tk394-cand.py')); C = importlib.util.module_from_spec(sp); sp.loader.exec_module(C)
if __name__ == '__main__':
    od = sys.argv[1]; os.makedirs(od, exist_ok=True)
    for c in sys.argv[2:]:
        try:
            a = C.bbo(c)
            ok = np.isfinite(a[:, 1]) & np.isfinite(a[:, 3])
            cross = float(np.mean(a[ok, 1] >= a[ok, 3])) if ok.any() else None
            tc = C.trade_check(c, a)
            json.dump(dict(coin=c, bins=int(len(a)), cross_frac=cross, trade_check=tc), open(f'{od}/{c}.json', 'w'))
        except Exception as e:
            json.dump(dict(coin=c, error=repr(e)), open(f'{od}/{c}.json', 'w'))
        print(c, flush=True)
