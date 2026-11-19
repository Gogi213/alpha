#!/bin/bash
# G4: ошибка метки приёма на локальном TLS-источнике с воспроизведением захвата Binance book (исходные интервалы, время отправки в поле B).
# Запуск на srv: [EXCH=binance|bybit|okx|bingx] stamp_test.sh <захват capture-*.tsv> <секунд> <бинарь> <метка|флаги>...   (по одной конфигурации на аргумент «метка|флаги»; все идут одновременно)
set -u
CAP=$1; SECS=$2; BIN=$3; shift 3
O=/data/perf/st; mkdir -p $O
[ -f $O/ca.pem ] || {
  cd $O
  openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -subj "/CN=alpha-test-ca" -days 3 -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=keyCertSign,cRLSign" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -keyout srv.key -out srv.csr -subj "/CN=localhost" 2>/dev/null
  printf "subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n" > ext.cnf
  openssl x509 -req -in srv.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out srv.pem -days 3 -extfile ext.cnf 2>/dev/null
}
EXCH=${EXCH:-binance}
case $EXCH in
  binance) POOL=/data/collect/binance/data/pool-$(date -u +%F).json; SH=book00 ;;
  bybit) POOL=/data/turbo-probe/soak-bybit/data/pool-$(date -u +%F).json; SH=book00 ;;
  okx) POOL=/data/collect/okx/data/pool-$(date -u +%F).json; SH=book00 ;;
  bingx) POOL=/data/collect/bingx/data/pool-$(date -u +%F).json; SH=book00 ;;
esac
TAG=$(date -u +%H%M%S)
port=19100
for spec in "$@"; do
  label=${spec%%|*}; flags=${spec#*|}
  R=$O/$TAG-$label; mkdir -p $R/data
  python3 /data/perf/tlsws_replay.py $port $CAP $O/srv.pem $O/srv.key $SECS $EXCH > $R/server.txt 2>&1 &
  for i in $(seq 60); do ss -ltn | grep -q ":$port " && break; sleep 0.5; done
  ALPHA_TLS_EXTRA_ROOT=$O/ca.pem systemd-run --unit=st-$label-$TAG --collect -E ALPHA_TLS_EXTRA_ROOT=$O/ca.pem -E ALPHA_TAP_PORTS=19100,19101,19102,19103,19104,19105,19106,19107 $BIN collect run --exchange $EXCH --root $R/data --health $R/health.json \
    --pool-file $POOL --only-shards $SH --lanes 2 --tls-ws localhost:$port --connect-budget 20 --duration-s $((SECS + 15)) $flags > $R/unit.txt 2>&1
  port=$((port + 1))
done
sleep $((SECS + 25))
for spec in "$@"; do
  label=${spec%%|*}
  R=$O/$TAG-$label
  echo "== $label"
  $BIN collect stamperr --dir $R/data
  python3 -c "
import json
h = json.load(open('$R/health.json'))
print('  reactor', json.dumps(h.get('reactor')))
print('  cpu', h['process']['cpu_cores'], 'queue_lag', h['backpressure']['queue_lag_ms'])
" 2>/dev/null
done
echo "каталог: $O/$TAG-*"
