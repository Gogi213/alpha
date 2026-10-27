# TK-414 ob200: срезы + признаки + сводка (на calc, под alsched; протокол docs/research/P-TK414-ob200-2026-10-27.md)
cd /data/tk0393/tk414
rm -f ob200/ob200.rc
mkdir -p ob200
python3 tk414-obmin.py /data/tk415/ob/bybit/bybit-linear ob200/min 15 > ob200/obmin.log 2>&1 || { echo $? > ob200/ob200.rc; exit 1; }
python3 tk414-obfeat.py flow/oifund-events.csv ob200/min ob200/obfeat-events.csv > ob200/obfeat.log 2>&1 && \
python3 tk414-obsum.py ob200/obfeat-events.csv flow/flowbybit2-legs.csv pump-dump-scenarios-legs-2026-10-27.csv > ob200/obsum.md 2> ob200/obsum.err
echo $? > ob200/ob200.rc
