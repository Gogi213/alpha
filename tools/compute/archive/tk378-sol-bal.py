#!/usr/bin/env python3
"""TK-378 шаг 3: Solana Transactions (pre/post token balances) за сутки 17.10 и 20.10 одним проходом, только tx с нашим mint.
Запуск на calc: /root/tk0364/bqenv/bin/python tk378-sol-bal.py MINTS.csv OUT.jsonl [--run]  (без --run — только dry_run)"""
import sys, csv, json
from google.cloud import bigquery
mints = [r['mint'] for r in csv.DictReader(open(sys.argv[1]))]
c = bigquery.Client.from_service_account_json("/root/tk0364/bq.json")
q = """SELECT signature, block_timestamp, block_slot, index, fee,
 ARRAY(SELECT AS STRUCT mint, owner, amount FROM UNNEST(pre_token_balances)) pre,
 ARRAY(SELECT AS STRUCT mint, owner, amount FROM UNNEST(post_token_balances)) post
FROM `bigquery-public-data.crypto_solana_mainnet_us.Transactions`
WHERE ((block_timestamp >= TIMESTAMP("2026-10-17") AND block_timestamp < TIMESTAMP("2026-10-18"))
    OR (block_timestamp >= TIMESTAMP("2026-10-20") AND block_timestamp < TIMESTAMP("2026-10-21")))
  AND (EXISTS(SELECT 1 FROM UNNEST(post_token_balances) b WHERE b.mint IN UNNEST(@m))
    OR EXISTS(SELECT 1 FROM UNNEST(pre_token_balances) b WHERE b.mint IN UNNEST(@m)))"""
cfg = dict(query_parameters=[bigquery.ArrayQueryParameter("m", "STRING", mints)], use_query_cache=False)
d = c.query(q, job_config=bigquery.QueryJobConfig(dry_run=True, **cfg))
gb = d.total_bytes_processed / 1e9
print("dry_run GB", round(gb, 1), flush=True)
if "--run" in sys.argv:
    assert gb < 450, "бюджет"
    j = c.query(q, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=int(d.total_bytes_processed * 1.05), **cfg))
    print("job", j.job_id, flush=True)
    n = 0
    with open(sys.argv[2], "w") as f:
        for row in j.result():
            r = dict(row.items()); r["block_timestamp"] = r["block_timestamp"].isoformat(); r["fee"] = str(r["fee"])
            for k in ("pre", "post"):
                r[k] = [{"mint": x["mint"], "owner": x["owner"], "amount": str(x["amount"])} for x in r[k]]
            f.write(json.dumps(r) + "\n"); n += 1
    print("billed GB", round(j.total_bytes_billed / 1e9, 1), "rows", n, flush=True)
