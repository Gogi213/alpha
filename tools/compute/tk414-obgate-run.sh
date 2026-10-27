# TK-414 стакан Gate фьючерсов: срезы + признаки + сводка (на calc, под alsched; протокол docs/research/P-TK414-ob-gate-2026-10-27.md)
cd /data/tk0393/tk414
rm -f obgate/obgate.rc
mkdir -p obgate
SYMS=$(awk -F, 'NR>1&&$3=="futures_usdt"{print $4}' flow/flowgate2-events.csv | sort -u | paste -sd,)
python3 tk414-obmin-gate.py /data/tk415/ob/gate/gate-fut obgate/min 15 "$SYMS" > obgate/obmin.log 2>&1 || { echo $? > obgate/obgate.rc; exit 1; }
python3 tk414-obfeat.py flow/flowgate2-events.csv obgate/min obgate/obfeat-events.csv > obgate/obfeat.log 2>&1 && \
python3 tk414-obsum.py obgate/obfeat-events.csv flow/flowgate2-legs.csv pump-dump-scenarios-legs-2026-10-27.csv "Gate futures_usdt 22 инстр., сентябрь 2026" futures_usdt > obgate/obsum.md 2> obgate/obsum.err
echo $? > obgate/obgate.rc
