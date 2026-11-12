#!/usr/bin/env python3
"""G-022 турбо: Python-ядро wscore на синтетическом источнике (`alpha collect synth`) — базовая линия потолка Python на том же потоке.
Не меняет tools/collector/: подменяет адрес биржи и пул в памяти процесса. Запуск (как у binance_ws.py):
  py_baseline.py --synth 127.0.0.1:9001 --pool-file /data/turbo-probe/synth-pool.json --root /data/turbo-probe/py-synth --health /data/turbo-probe/py-synth.health.json --kinds book --streams-per-conn 64 --lanes 1
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "collector"))


def main():
    args = sys.argv[1:]
    synth = args[args.index("--synth") + 1]
    pool = json.load(open(args[args.index("--pool-file") + 1]))["pool"]
    rest = []
    skip = 0
    for i, x in enumerate(args):
        if skip:
            skip -= 1
        elif x in ("--synth", "--pool-file"):
            skip = 1
        else:
            rest.append(x)
    import binance_ws as bw
    import wscore.session as ss

    bw.WS = f"ws://{synth}"
    ss.hostname = lambda url: None  # открытый ws:// — без TLS и server_hostname
    bw.Adapter.load_pool = lambda self, min_qv, top: (pool, [])
    sys.argv = ["binance_ws.py"] + rest
    sys.exit(bw.main())


if __name__ == "__main__":
    main()
