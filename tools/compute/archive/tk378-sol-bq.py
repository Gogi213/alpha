#!/usr/bin/env python3
"""TK-378: Solana Token Transfers 15-21.10 через BigQuery одним проходом: все переводы в tx, где участвует хоть один наш mint.
Запуск на calc: /root/tk0364/bqenv/bin/python tk378-sol-bq.py MINTS.csv OUT.csv [--run]  (без --run только dry_run, байты в stdout)"""
import sys, csv
from google.cloud import bigquery
mints = [r['mint'] for r in csv.DictReader(open(sys.argv[1]))]
c = bigquery.Client.from_service_account_json("/root/tk0364/bq.json")
q = """SELECT block_timestamp, block_slot, tx_signature, source, destination, mint, value, decimals
FROM `bigquery-public-data.crypto_solana_mainnet_us.Token Transfers`
WHERE block_timestamp >= TIMESTAMP("2026-10-15") AND block_timestamp < TIMESTAMP("2026-10-22")
QUALIFY COUNTIF(mint IN UNNEST(@m)) OVER (PARTITION BY tx_signature) > 0"""
cfg = dict(query_parameters=[bigquery.ArrayQueryParameter("m", "STRING", mints)], use_query_cache=False)
d = c.query(q, job_config=bigquery.QueryJobConfig(dry_run=True, **cfg))
print("dry_run GB", round(d.total_bytes_processed / 1e9, 1), "mints", len(mints), flush=True)
def fetch(j):
    import csv as _c
    r = j.result(); n = 0
    with open(sys.argv[2], "w", newline="") as f:
        w = _c.writer(f); w.writerow([x.name for x in r.schema])
        for row in r:
            w.writerow(list(row.values())); n += 1
    print("billed GB", round(j.total_bytes_billed / 1e9, 1), "rows", n, flush=True)
if "--fetch" in sys.argv:
    fetch(c.get_job(sys.argv[sys.argv.index("--fetch") + 1], location="US")); sys.exit()
if "--run" in sys.argv:
    j = c.query(q, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=int(d.total_bytes_processed * 1.05), **cfg))
    print("job", j.job_id, flush=True)
    fetch(j)
