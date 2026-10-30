"""TK-454: сводка вердиктов по docs/findings/sancho-trades-p1-*.csv (дубли trade_id: берём строку с t0_source=б, иначе первую)."""
import csv, collections, sys
src = sys.argv[1] if len(sys.argv) > 1 else 'docs/findings/sancho-trades-p1-2026-10-30.csv'
rows = list(csv.DictReader(open(src, encoding='utf-8')))
best = {}
for r in rows:
    k = r['trade_id']
    if k not in best or (r['t0_source'] == 'б' and best[k]['t0_source'] != 'б'):
        best[k] = r
c = collections.Counter(r['match'].split(':')[0] + (':' + r['match'].split(':')[1].strip() if r['match'].startswith('нельзя') else '') for r in best.values())
print(len(rows), 'строк,', len(best), 'сделок')
for k, v in c.most_common(): print(v, k)
