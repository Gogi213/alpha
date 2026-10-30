"""TK-454 §3: 87 постов про «депозит» -> тип (а доходность периода / б риск на сделку / в реклама-бирж. / г прочее), цитаты дословно.
Вход: data/sancho-posts.csv. Выход: docs/findings/sancho-deposit-claims-2026-10-30.csv. Классы по словам, без догадок о числах."""
import csv, re, sys
src = sys.argv[1] if len(sys.argv) > 1 else 'data/sancho-posts.csv'
out = sys.argv[2] if len(sys.argv) > 2 else 'docs/findings/sancho-deposit-claims-2026-10-30.csv'
P_RET = re.compile(r'[^\n.]{0,80}[+\-−–]\s?\d+(?:[.,]\d+)?\s?%[^\n.]{0,40}(?:от\s+депозит\w*|к\s+депозит\w*|депозит\w*)[^\n.]{0,60}', re.I)
P_RISK = re.compile(r'[^\n.]{0,60}риск\w*\s+(?:в\s+)?\d+(?:[.,]\d+)?\s?%[^\n.]{0,40}|[^\n.]{0,40}\d+(?:[.,]\d+)?\s?%\s+от\s+депозит\w*[^\n.]{0,40}', re.I)
P_AD = re.compile(r'бонус|скидк|регистрац|реферал|ссылк|вывод|usdt trc|bybit|binance|биржа', re.I)
rows = []
for x in csv.DictReader(open(src, encoding='utf-8')):
    t = x['text']
    if not re.search('депозит', t, re.I): continue
    ret = P_RET.search(t); risk = P_RISK.search(t) if not ret else None
    if ret: cls, q = 'а: доходность периода (заявлено)', ret.group(0).strip()
    elif re.search(r'риск\w*\s+\d', t, re.I) and re.search(r'\d\s?%\s+от\s+депозит', t, re.I): cls, q = 'б: риск на сделку', P_RISK.search(t).group(0).strip()
    elif P_AD.search(t) and 'депозит' in t.lower() and re.search(r'депозит\w*\s+(?:и|в)|первый депозит|пополн', t, re.I): cls, q = 'в: биржа/реклама без числа', ''
    else: cls, q = 'г: прочее (совет/образование/без числа)', ''
    rows.append(dict(post_id=x['id'], time_gmt4=x['time_gmt4'], tags=x['tags'], cls=cls, quote=q.replace('\n', ' ')))
with open(out, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, rows[0].keys()); w.writeheader(); w.writerows(rows)
from collections import Counter
print(len(rows), Counter(r['cls'] for r in rows))
for r in rows:
    if r['cls'][0] in 'аб': print(r['post_id'], r['time_gmt4'], r['cls'][0], '|', r['quote'][:150])
