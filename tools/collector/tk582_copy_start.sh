#!/usr/bin/env bash
# TK-582 v3 (В-156): копия коллектора Binance (ветка «тёмный шард») рядом с боевым. Запускать на srv под root; боевое не трогает.
#   BIN=/data/tk582v3/alpha-tk582v3 [LIVE_SHARDS=book00,book01] [BUDGET=20] [D=/data/tk582v3] bash tools/collector/tk582_copy_start.sh [--dry]
# Делает:
#  1. символы шардов боевого LIVE_SHARDS (книга) берёт из головы их сегодняшних файлов (раскладка боевого после старта не менялась: health pool.added_total = 0);
#  2. пул копии = первые POOL_FIRST (по умолчанию 28) символов объединения по алфавиту → $D/pool.json (`--pool-file`: пересмотр пула выключен, набор фиксирован); копия режет пул по алфавиту на куски
#     по STREAMS_PER_CONN (14), поэтому её шарды составом не равны шардам боевого. Два самых нагруженных символа (BTCUSDT, ETHUSDT: у боевого на разных соединениях) обязаны попасть в РАЗНЫЕ куски, иначе
#     скрипт отказывает: на одном соединении копия получала 458 сообщ/с на линию вместо 468 + ~375 у боевого (измерено 17.11, первый запуск 10:34 с двумя кусками по 21) — потери данных тяжёлых символов портят сравнение.
#     $D/layout.json говорит, какие шарды боевого несут те же символы (книга — LIVE_SHARDS; сделки — шарды с хоть одним из символов; mark00) и что подставить в `tk582_copy_report.py --live-shards`;
#  3. $D/budget — лимит подключений копии за 300 с (`--budget-file`). Боевой запущен без `--budget-file` и берёт до 280 за 300 с (BINANCE_LIMIT); лимит Binance — 300 на IP, поэтому копия ≤ 20;
#  4. юнит systemd-run `tk582v3-copy` (Restart=on-failure, RestartSec=300 — перезапуск не складывается в одно окно бюджета с прежним процессом), те же флаги, что у боевого `collect-binance`
#     (`--lanes 2 --never-slow --lazy --reactor --worker-inline --reactor-window-us 5000 --rx-stamp kernel --no-pong-only-degrade`), свои `--root $D/data` и `--health $D/health.json`.
# Остановка копии: `systemctl stop tk582v3-copy` (один SIGTERM; данные и журналы остаются в $D/data).
set -euo pipefail
BIN="${BIN:?BIN — путь к бинарю копии, например /data/tk582v3/alpha-tk582v3}"
D="${D:-/data/tk582v3}"
LIVE="${LIVE:-/data/collect/binance/data}"
LIVE_SHARDS="${LIVE_SHARDS:-book00,book01}"
BUDGET="${BUDGET:-20}"
STREAMS_PER_CONN="${STREAMS_PER_CONN:-14}"
POOL_FIRST="${POOL_FIRST:-28}"
UNIT="${UNIT:-tk582v3-copy}"
DAY="$(date -u +%F)"
DRY=0; [ "${1:-}" = "--dry" ] && DRY=1
[ -x "$BIN" ] || { echo "нет бинаря $BIN"; exit 2; }
if systemctl is-active --quiet "$UNIT" 2>/dev/null; then echo "юнит $UNIT уже работает"; exit 3; fi
[ "$BUDGET" -le 20 ] || { echo "бюджет копии $BUDGET > 20: боевой без --budget-file (до 280), сумма не должна превышать 300"; exit 4; }
mkdir -p "$D/data"

python3 - "$LIVE" "$DAY" "$LIVE_SHARDS" "$D" "$STREAMS_PER_CONN" "$POOL_FIRST" <<'PY'
import glob, json, math, subprocess, sys
live, day, book_shards, d = sys.argv[1], sys.argv[2], sys.argv[3].split(","), sys.argv[4]
per, first = int(sys.argv[5]), int(sys.argv[6])

def head(shard):
    fs = sorted(glob.glob("%s/%s/%s-*.tsv.zst" % (live, day, shard)))
    if not fs:
        sys.exit("нет сегодняшнего файла боевого для %s" % shard)
    p = subprocess.run("nice -n 19 ionice -c3 zstd -dc -q %s | head -c 60000000" % fs[-1], shell=True, capture_output=True)
    return p.stdout.split(b"\n")[:-1]   # последняя строка могла оборваться на границе головы

def syms(shard, typ):
    out = set()
    for ln in head(shard):
        f = ln.split(b"\t")
        if len(f) > 5 and f[0] == typ.encode():
            out.add(f[4].decode())
    return out

book = {s: syms(s, "b") for s in book_shards}
union = sorted(set().union(*book.values()))
pool = union[:first]
rates = json.load(open(live + "/pool-state.json"))["rates"]["book"]
n_chunks = math.ceil(len(pool) / per)
size = math.ceil(len(pool) / n_chunks)
chunks = [pool[i:i + size] for i in range(0, len(pool), size)]   # как `balanced_chunks` коллектора
top2 = sorted(pool, key=lambda s: -rates.get(s.lower(), 0))[:2]
where = {s: [i for i, c in enumerate(chunks) if s in c][0] for s in top2}
if where[top2[0]] == where[top2[1]]:
    sys.exit("два самых нагруженных символа %s на одном куске: %s — менять POOL_FIRST / STREAMS_PER_CONN" % (top2, where))
start_pool = set(json.load(open(sorted(glob.glob(live + "/pool-*-*.json"))[-1]))["pool"])
extra = [s for s in pool if s not in start_pool]
trade = []
for i in range(0, 64):
    sh = "trade%02d" % i
    if not glob.glob("%s/%s/%s-*.tsv.zst" % (live, day, sh)):
        break
    if syms(sh, "t") & set(pool):
        trade.append(sh)
json.dump({"pool": pool}, open(d + "/pool.json", "w"), ensure_ascii=False)
live_shards = list(book_shards) + trade + ["mark00"]
json.dump({"day": day, "symbols": len(pool), "streams_per_conn": per, "copy_book_chunks": [{"symbols": len(c), "rate_msgs_s": round(sum(rates.get(s.lower(), 0) for s in c))} for c in chunks], "top2": top2, "book_symbols": {k: len(v) for k, v in book.items()}, "live_book_shards": list(book_shards), "live_trade_shards": trade,
           "live_shards": live_shards, "not_in_start_pool": extra}, open(d + "/layout.json", "w"), ensure_ascii=False, indent=1)
print("символов в пуле копии: %d из %d (по шардам боевого: %s); куски книги копии (символов, сообщ/с по оценке боевого): %s; не из пула старта боевого: %s" % (len(pool), len(union), {k: len(v) for k, v in book.items()}, [(len(c), round(sum(rates.get(s.lower(), 0) for s in c))) for c in chunks], extra))
print("шарды боевого с теми же символами: %s" % ",".join(live_shards))
PY

echo "$BUDGET" > "$D/budget"
CMD=("$BIN" collect run --exchange binance --root "$D/data" --health "$D/health.json" --lanes 2 --never-slow --lazy --reactor --worker-inline --reactor-window-us 5000
     --rx-stamp kernel --no-pong-only-degrade --pool-file "$D/pool.json" --streams-per-conn "$STREAMS_PER_CONN" --budget-file "$D/budget")
RUN=(systemd-run --unit="$UNIT" --collect -p Restart=on-failure -p RestartSec=300 -p KillMode=control-group -p TimeoutStopSec=90 -p LimitNOFILE=1048576 -p CPUAccounting=yes -p MemoryAccounting=yes "${CMD[@]}")
echo "${RUN[*]}"
if [ "$DRY" = 1 ]; then echo "--dry: юнит не запущен"; exit 0; fi
"${RUN[@]}"
date -u +"запуск юнита $UNIT: %F %T UTC"
