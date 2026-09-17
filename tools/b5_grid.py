#!/usr/bin/env python3
"""Прогон предрегистрированной сетки форм отскока (B5, В-58).

Одна команда на пару «форма × символ»: `lob backtest --touches` с покруговым
дампом `--trades-out`. Сам вердикт считает `lob bounce-verdict` по дампам —
этот скрипт только гоняет сетку и складывает артефакты в `data/b5/<метка>/`:

    trades/<форма>/<СИМВОЛ>.csv   покруговые дампы — вход вердикта
    summary/<форма>/<СИМВОЛ>.csv  сводка прогона (оси В-44)
    pnl/<форма>/<СИМВОЛ>.csv      кривая PnL
    logs/<форма>/<СИМВОЛ>.log     stdout+stderr прогона
    manifest.txt                  шаблон команды и список пар

Лот каждого символа считает сам бэктест: `--order-qty-from-pool` —
`order_size_22a` (Decision 22а) от полей пула `instruments.csv` сессии и цены
последнего касания реплея. Так оба набора данных меряются одной меркой: у
замороженного пула на 100 монет `order_size_e9` в журнале `candidates.csv`
нет (тот отбор туда не писал), а подставлять шаг книги вместо лота площадки
значило бы мерить другую сделку. RTT — assumed 20 мс (В-37).

Готовые прогоны не переделываются (возобновление после падения); `--force`
перегоняет всё заново.
"""

import argparse
import csv
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Предрегистрированная сетка В-58 (B1): стоп × дедлайн × досрочный выход.
STOP_MODES = ["before", "at", "behind"]
DEADLINE_SECS = [60, 600, 3600, 7200]
EARLY_EXIT_SECS = [None, 1, 2, 3]

# В-37: RTT не измерена на живом исполнении, значение назначено решением.
MEDIAN_RTT_NS = 20_000_000
P95_RTT_NS = 20_000_000

H3_MODE = "floor"


def form_name(stop: str, deadline: int, early) -> str:
    return f"{stop}-{deadline}-{'off' if early is None else early}"


def forms():
    for stop in STOP_MODES:
        for deadline in DEADLINE_SECS:
            for early in EARLY_EXIT_SECS:
                yield form_name(stop, deadline, early), stop, deadline, early


def symbols_of(root: Path, symbols_file: Path) -> list:
    if symbols_file:
        return [
            s.strip()
            for s in symbols_file.read_text(encoding="utf-8").split()
            if s.strip()
        ]
    pool = root / "instruments.csv"
    if not pool.exists():
        sys.exit(f"нет {pool}: список символов и лот из пула взять негде")
    with pool.open(newline="", encoding="utf-8") as f:
        return [row["symbol"] for row in csv.DictReader(f) if row.get("symbol")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", required=True)
    ap.add_argument("--root", required=True, help="каталог сессии с <СИМВОЛ>-<день>.binlog")
    ap.add_argument("--label", required=True, help="имя набора данных (local-20260912, server-2026-09-16)")
    ap.add_argument("--out", default="data/b5", help="корень артефактов (data/b5)")
    ap.add_argument("--symbols-file", default=None, help="список символов (по одному в строке)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="первые N символов (0 — все)")
    ap.add_argument("--force", action="store_true", help="перегнать готовые пары заново")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        sys.exit(f"нет каталога {root}")
    if not (root / "instruments.csv").exists():
        sys.exit(f"нет {root / 'instruments.csv'}: лот из пула взять негде (--order-qty-from-pool)")
    out = Path(args.out) / args.label
    symbols = symbols_of(root, Path(args.symbols_file) if args.symbols_file else None)
    if args.limit:
        symbols = symbols[: args.limit]
    grid = list(forms())

    for sub in ("trades", "summary", "pnl", "logs"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    pairs = []
    for form, stop, deadline, early in grid:
        for symbol in symbols:
            trades = out / "trades" / form / f"{symbol}.csv"
            if trades.exists() and trades.stat().st_size > 0 and not args.force:
                continue
            pairs.append((form, stop, deadline, early, symbol))

    def command(form, stop, deadline, early, symbol):
        cmd = [
            args.binary, "lob", "backtest", "--touches",
            "--session-root", str(root), "--symbol", symbol,
            "--median-rtt-ns", str(MEDIAN_RTT_NS), "--p95-rtt-ns", str(P95_RTT_NS),
            "--order-qty-from-pool",
            "--h3-mode", H3_MODE,
            "--stop-mode", stop, "--deadline-secs", str(deadline),
        ]
        if early is not None:
            cmd += ["--early-exit-secs", str(early)]
        cmd += [
            "--trades-out", str(out / "trades" / form / f"{symbol}.csv"),
            "--out", str(out / "summary" / form / f"{symbol}.csv"),
            "--pnl-out", str(out / "pnl" / form / f"{symbol}.csv"),
        ]
        return cmd

    manifest = out / "manifest.txt"
    with manifest.open("w", encoding="utf-8") as f:
        f.write(f"# binary: {args.binary}\n# root: {root}\n# label: {args.label}\n")
        f.write(f"# форм {len(grid)}, символов {len(symbols)}, пар в этом запуске {len(pairs)}\n")
        f.write(f"# RTT assumed {MEDIAN_RTT_NS} нс (В-37), H3={H3_MODE}, "
                f"лот — order_size_22a от пула и цены последнего касания\n")
        for form, stop, deadline, early, symbol in pairs:
            f.write(" ".join(command(form, stop, deadline, early, symbol)) + "\n")

    print(f"b5-grid: {args.label}: форм {len(grid)}, символов {len(symbols)}, "
          f"к прогону {len(pairs)} пар, рабочих {args.workers}", flush=True)
    if not pairs:
        print("b5-grid: всё уже прогнано (--force перегоняет)", flush=True)
        return 0

    failed = []

    def run(pair):
        form, stop, deadline, early, symbol = pair
        log = out / "logs" / form / f"{symbol}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as lf:
            proc = subprocess.run(command(form, stop, deadline, early, symbol),
                                  stdout=lf, stderr=subprocess.STDOUT)
        return pair, proc.returncode

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, p) for p in pairs]
        for fut in as_completed(futures):
            pair, code = fut.result()
            done += 1
            if code != 0:
                failed.append(pair)
                print(f"  [{done}/{len(pairs)}] ОТКАЗ {pair[0]} {pair[4]}: exit {code}", flush=True)
            elif done % 25 == 0 or done == len(pairs):
                print(f"  [{done}/{len(pairs)}] готово", flush=True)

    print(f"b5-grid: {args.label}: готово {len(pairs) - len(failed)}, отказов {len(failed)}", flush=True)
    for form, _, _, _, symbol in failed[:20]:
        print(f"  отказ: {form} {symbol}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
