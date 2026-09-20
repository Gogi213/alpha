import json, os, math, collections, statistics, datetime as dt

D = json.load(open(os.environ["TEMP"] + "/rounds.json", encoding="utf-8"))
POS = 1000.0  # $ на позицию
usd = lambda bps: bps / 10000 * POS

SERIES = [
    ("long", "Лонг от старой бид-стены", "#2a78d6", "#3987e5"),
    ("short", "Шорт от старой аск-стены", "#eb6834", "#d95926"),
    ("long_dip", "Лонг после просадки пула за 4 ч", "#1baf7a", "#199e70"),
]

def metrics(rows):
    net = [r[5] for r in rows]
    n = len(net)
    if n == 0:
        return None
    wins = [x for x in net if x > 0]; losses = [x for x in net if x <= 0]
    eq = 0.0; peak = 0.0; dd = 0.0; curve = []
    for x in net:
        eq += x; peak = max(peak, eq); dd = min(dd, eq - peak); curve.append(eq)
    byday = collections.OrderedDict()
    for r in rows:
        byday.setdefault(r[1], []).append(r[5])
    daily = [sum(v) for v in byday.values()]
    sharpe_d = (statistics.mean(daily) / statistics.pstdev(daily) * math.sqrt(365)) if len(daily) > 1 and statistics.pstdev(daily) > 0 else None
    sharpe_t = (statistics.mean(net) / statistics.pstdev(net)) if n > 1 and statistics.pstdev(net) > 0 else None
    gp = sum(wins); gl = -sum(losses)
    # 95 % t-интервал среднего по сделкам (допущение: сделки независимы) и по дням
    sd = statistics.pstdev(net) if n > 1 else 0.0
    ci_trade = (1.96 * sd / math.sqrt(n)) if n > 1 else None
    ci_day = (1.96 * statistics.pstdev(daily) / math.sqrt(len(daily))) if len(daily) > 1 else None
    return dict(ci_trade=ci_trade, ci_day=ci_day, daily=daily,n=n, wins=len(wins), winrate=len(wins) / n, avg_win=(gp / len(wins)) if wins else 0.0,
                avg_loss=(-gl / len(losses)) if losses else 0.0, pf=(gp / gl) if gl > 0 else float("inf"),
                total=sum(net), per_trade=sum(net) / n, dd=dd, sharpe_d=sharpe_d, sharpe_t=sharpe_t,
                byday=byday, curve=curve, reasons=collections.Counter(r[6] for r in rows))

M = {k: metrics(v) for k, v in D.items()}

def f_usd(b, sign=True):
    v = usd(b)
    return (f"{v:+,.0f}" if sign else f"{v:,.0f}").replace(",", " ") + " $"

def f_pct(x):
    return f"{100 * x:.0f} %"

def f_sh(x):
    return "—" if x is None else f"{x:.1f}"

# ---------- tables ----------
def total_table():
    h = "<tr><th>стратегия</th><th>сделок</th><th>винрейт</th><th>ср. плюс / минус</th><th>PF</th><th>итог</th><th>на сделку ± 95 %</th><th>в день ± 95 %</th><th>макс. просадка</th><th>Шарп по дням*</th><th>Шарп по сделкам</th><th>выходы</th></tr>"
    rows = []
    for key, name, c, cd in SERIES:
        m = M[key]
        dd_pct = m["dd"] / 10000 * 100
        rows.append(f"<tr><td><span class='sw' style='--c:{c};--cd:{cd}'></span>{name}</td><td>{m['n']}</td><td>{f_pct(m['winrate'])}</td>"
                    f"<td>{f_usd(m['avg_win'])} / {f_usd(m['avg_loss'])}</td><td>{m['pf']:.2f}</td><td class='{'pos' if m['total']>0 else 'neg'}'><b>{f_usd(m['total'])}</b></td>"
                    f"<td>{f_usd(m['per_trade'])} ± {f_usd(m['ci_trade'], False) if m['ci_trade'] is not None else '—'}{' <span class=mut>(задевает ноль)</span>' if m['ci_trade'] is not None and abs(m['per_trade']) <= m['ci_trade'] else ''}</td>"
                    f"<td>{f_usd(statistics.mean(m['daily']))} ± {f_usd(m['ci_day'], False) if m['ci_day'] is not None else '—'}{' <span class=mut>(задевает ноль)</span>' if m['ci_day'] is not None and abs(statistics.mean(m['daily'])) <= m['ci_day'] else ''}</td>"
                    f"<td>{f_usd(m['dd'])} ({dd_pct:+.1f} % позиции)</td><td>{f_sh(m['sharpe_d'])}</td><td>{f_sh(m['sharpe_t'])}</td>"
                    f"<td>{', '.join(f'{k} {v}' for k, v in m['reasons'].most_common())}</td></tr>")
    return "<table>" + h + "".join(rows) + "</table>"

def period_table():
    days = sorted({d for m in M.values() if m for d in m["byday"]})
    h = "<tr><th>период</th>" + "".join(f"<th>{name}</th>" for _, name, _, _ in SERIES) + "</tr>"
    rows = []
    for d in days:
        cells = []
        for key, *_ in SERIES:
            v = M[key]["byday"].get(d)
            cells.append(f"<td class='{'pos' if v and sum(v)>0 else 'neg'}'>{f_usd(sum(v))} <span class='mut'>({len(v)} сд, {f_pct(sum(1 for x in v if x>0)/len(v))})</span></td>" if v else "<td class='mut'>—</td>")
        rows.append(f"<tr><td>{d}</td>" + "".join(cells) + "</tr>")
    # month
    cells = []
    for key, *_ in SERIES:
        m = M[key]
        cells.append(f"<td class='{'pos' if m['total']>0 else 'neg'}'><b>{f_usd(m['total'])}</b> <span class='mut'>({m['n']} сд, {f_pct(m['winrate'])})</span></td>")
    rows.append("<tr class='tot'><td>2026-09 (месяц = все 3 дня записи)</td>" + "".join(cells) + "</tr>")
    return "<table>" + h + "".join(rows) + "</table>"

def coin_table(key, limit=None):
    by = collections.defaultdict(list)
    for r in D[key]:
        by[r[0]].append(r[5])
    items = sorted(by.items(), key=lambda kv: sum(kv[1]), reverse=True)
    if limit:
        items = items[:limit] + ([("…", [])] if len(items) > limit * 2 else []) + items[-limit:]
    h = "<tr><th>монета</th><th>сделок</th><th>винрейт</th><th>итог</th><th>на сделку</th></tr>"
    rows = []
    for s, v in items:
        if not v:
            rows.append("<tr><td colspan=5 class='mut'>…</td></tr>"); continue
        w = sum(1 for x in v if x > 0)
        rows.append(f"<tr><td>{s.replace('USDT','')}</td><td>{len(v)}</td><td>{f_pct(w/len(v))}</td><td class='{'pos' if sum(v)>0 else 'neg'}'>{f_usd(sum(v))}</td><td>{f_usd(sum(v)/len(v))}</td></tr>")
    return "<table>" + h + "".join(rows) + "</table>"

def direction_table():
    h = "<tr><th>направление (форма pct2-1to1-3600, семья a45)</th><th>сделок</th><th>винрейт</th><th>итог</th><th>на сделку</th><th>просадка</th><th>по дням</th></tr>"
    rows = []
    for key, name in (("long", "Лонг (бид-стены)"), ("short", "Шорт (аск-стены)")):
        m = M[key]
        rows.append(f"<tr><td>{name}</td><td>{m['n']}</td><td>{f_pct(m['winrate'])}</td><td class='{'pos' if m['total']>0 else 'neg'}'><b>{f_usd(m['total'])}</b></td><td>{f_usd(m['per_trade'])}</td><td>{f_usd(m['dd'])}</td>"
                    f"<td>{' / '.join(f_usd(sum(v)) for v in m['byday'].values())}</td></tr>")
    tot = M["long"]["total"] + M["short"]["total"]
    rows.append(f"<tr class='tot'><td>Обе стороны (сумма, как база до разделения)</td><td>{M['long']['n']+M['short']['n']}</td><td></td><td class='{'pos' if tot>0 else 'neg'}'><b>{f_usd(tot)}</b></td><td></td><td></td><td></td></tr>")
    return "<table>" + h + "".join(rows) + "</table>"

# ---------- chart data ----------
chart = {}
for key, name, c, cd in SERIES:
    pts = []
    eq = 0.0
    for r in D[key]:
        eq += usd(r[5])
        pts.append([r[3], round(eq, 2), r[0].replace("USDT", ""), round(usd(r[5]), 2), r[6]])
    chart[key] = dict(name=name, color=c, colorDark=cd, points=pts)
daily = {}
for key, *_ in SERIES:
    daily[key] = {d: round(usd(sum(v)), 1) for d, v in M[key]["byday"].items()}
coins_long = sorted(((s, round(usd(sum(v)), 1), len(v)) for s, v in
                     ((s, [r[5] for r in D["long"] if r[0] == s]) for s in {r[0] for r in D["long"]})), key=lambda x: x[1])

html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Эквити отскока</title>
<style>
:root{{--surface:#fcfcfb;--panel:#ffffff;--text:#0b0b0b;--text2:#52514e;--mut:#7a7975;--grid:#e6e5e1;--pos:#006300;--neg:#d03b3b;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--surface:#1a1a19;--panel:#232322;--text:#fff;--text2:#c3c2b7;--mut:#8a8984;--grid:#33332f;--pos:#0ca30c;--neg:#ec835a;--s1:#3987e5;--s2:#d95926;--s3:#199e70}}}}
:root[data-theme=dark]{{--surface:#1a1a19;--panel:#232322;--text:#fff;--text2:#c3c2b7;--mut:#8a8984;--grid:#33332f;--pos:#0ca30c;--neg:#ec835a;--s1:#3987e5;--s2:#d95926;--s3:#199e70}}
body{{margin:0;background:var(--surface);color:var(--text);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;padding:16px}}
main{{max-width:1100px;margin:0 auto}} h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}} .sub{{color:var(--text2);margin:0 0 16px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0}} th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}} th{{color:var(--text2);font-weight:600}}
td.pos{{color:var(--pos)}} td.neg{{color:var(--neg)}} .mut{{color:var(--mut)}} tr.tot td{{border-top:2px solid var(--grid);font-weight:600}}
.sw{{display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--c);margin-right:6px;vertical-align:middle}}
@media (prefers-color-scheme:dark){{.sw{{background:var(--cd)}}}}
.card{{background:var(--panel);border:1px solid var(--grid);border-radius:8px;padding:12px;margin:8px 0}}
svg{{width:100%;height:auto;display:block}} .legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--text2);margin:4px 0 8px}}
.tip{{position:fixed;pointer-events:none;background:var(--panel);border:1px solid var(--grid);border-radius:6px;padding:6px 8px;font-size:12px;display:none;box-shadow:0 2px 8px rgba(0,0,0,.15)}}
.note{{color:var(--text2);font-size:13px}} .wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>Отскок от старых стен — эквити и P&amp;L</h1>
<p class="sub">Бэктест `lob bounce-grid`, сутки 16–18.09.2026 UTC, 90 монет Bybit, семья a45 (стена ≥ $10k, стоит ≥ 45 мин), форма pct2-1to1-3600 (стоп 2 %, тейк 1:1, выход через час), позиция <b>$1000</b>, одна за раз, вход лимиткой, задержка измеренная (В-68), комиссии по ногам (В-63). Три дня — все рост рынка.</p>

<h2>Тотал</h2><div class="wrap">{total_table()}</div>
<p class="note">* Шарп по дням — годовой по трём дневным P&amp;L: при n = 3 это не оценка, а знак. Шарп по сделкам — mean/std одной сделки. Просадка — от пика накопленного P&amp;L, в $ на позицию $1000.</p>

<h2>Эквити (накопленный P&amp;L, $ на позицию $1000)</h2>
<div class="card"><div class="legend" id="lg"></div><svg id="eq" viewBox="0 0 1000 360" role="img" aria-label="Кривые эквити трёх стратегий по времени"></svg></div>

<h2>По периодам</h2><div class="wrap">{period_table()}</div>
<div class="card"><div class="legend">P&amp;L по дням, $ на позицию $1000</div><svg id="dl" viewBox="0 0 1000 220" role="img" aria-label="Дневной P&L по стратегиям"></svg></div>

<h2>По направлению</h2><div class="wrap">{direction_table()}</div>

<h2>По монетам — лонги (все)</h2><div class="wrap">{coin_table("long")}</div>
<div class="card"><div class="legend">Лонги: итог по монетам, $ на позицию $1000</div><svg id="cn" viewBox="0 0 1000 {40 + 18 * len(coins_long)}" role="img" aria-label="P&L по монетам, лонги"></svg></div>
<h2>По монетам — шорты (лучшие и худшие)</h2><div class="wrap">{coin_table("short", 6)}</div>
<h2>По монетам — лонги после просадки пула за 4 ч</h2><div class="wrap">{coin_table("long_dip")}</div>

<p class="note">Источник: `b5/nightly-2026-09-19-base/&lt;набор&gt;/rounds.csv` на счётной машине (наборы a45-bid, a45-ask, a45-bid-p4h-neg), вердикты `study/bounce-verdict-nightly-2026-09-19-*.csv`. Сгенерировано 2026-09-20.</p>
<div class="tip" id="tip"></div>
</main>
<script>
const CH={json.dumps(chart, ensure_ascii=False)};
const DAILY={json.dumps(daily, ensure_ascii=False)};
const COINS={json.dumps(coins_long, ensure_ascii=False)};
const dark=matchMedia('(prefers-color-scheme:dark)').matches && document.documentElement.dataset.theme!=='light';
const col=k=>dark?CH[k].colorDark:CH[k].color;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const tip=document.getElementById('tip');
function showTip(e,html){{tip.innerHTML=html;tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';}}
function hideTip(){{tip.style.display='none';}}
const fmt=v=>(v>0?'+':'')+v.toFixed(0)+' $';
const dstr=ms=>{{const d=new Date(ms);return d.toISOString().slice(5,16).replace('T',' ');}};
// ---- equity
(function(){{
  const svg=document.getElementById('eq'),W=1000,H=360,L=56,R=16,T=16,B=36;
  const keys=Object.keys(CH); let xs=[],ys=[0];
  keys.forEach(k=>CH[k].points.forEach(p=>{{xs.push(p[0]);ys.push(p[1]);}}));
  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  const X=t=>L+(t-x0)/(x1-x0)*(W-L-R), Y=v=>T+(y1-v)/(y1-y0)*(H-T-B);
  let s='';
  const step=Math.pow(10,Math.floor(Math.log10((y1-y0)/4)))*(((y1-y0)/4)/Math.pow(10,Math.floor(Math.log10((y1-y0)/4)))>5?5:((y1-y0)/4)/Math.pow(10,Math.floor(Math.log10((y1-y0)/4)))>2?2:1);
  for(let v=Math.ceil(y0/step)*step;v<=y1;v+=step){{s+=`<line x1="${{L}}" x2="${{W-R}}" y1="${{Y(v)}}" y2="${{Y(v)}}" stroke="${{css('--grid')}}" stroke-width="1"/><text x="${{L-6}}" y="${{Y(v)+4}}" text-anchor="end" font-size="11" fill="${{css('--text2')}}">${{fmt(v)}}</text>`;}}
  s+=`<line x1="${{L}}" x2="${{W-R}}" y1="${{Y(0)}}" y2="${{Y(0)}}" stroke="${{css('--text2')}}" stroke-width="1"/>`;
  for(const d of ['2026-09-16','2026-09-17','2026-09-18','2026-09-19']){{const t=Date.parse(d+'T00:00:00Z'); if(t>=x0&&t<=x1) s+=`<line x1="${{X(t)}}" x2="${{X(t)}}" y1="${{T}}" y2="${{H-B}}" stroke="${{css('--grid')}}"/><text x="${{X(t)+4}}" y="${{H-B+16}}" font-size="11" fill="${{css('--text2')}}">${{d.slice(5)}}</text>`;}}
  keys.forEach(k=>{{const p=CH[k].points; let d='M'+X(x0)+','+Y(0); p.forEach(q=>{{d+=' L'+X(q[0])+','+Y(q[1]);}}); s+=`<path d="${{d}}" fill="none" stroke="${{col(k)}}" stroke-width="2" stroke-linejoin="round"/>`;
    const last=p[p.length-1]; s+=`<text x="${{Math.min(X(last[0])+6,W-R-90)}}" y="${{Y(last[1])+4}}" font-size="12" fill="${{css('--text')}}">${{fmt(last[1])}}</text>`;
    p.forEach(q=>{{s+=`<circle cx="${{X(q[0])}}" cy="${{Y(q[1])}}" r="7" fill="transparent" data-k="${{k}}" data-t="${{q[0]}}" data-e="${{q[1]}}" data-s="${{q[2]}}" data-n="${{q[3]}}" data-r="${{q[4]}}"/>`;}});
  }});
  svg.innerHTML=s;
  svg.addEventListener('mousemove',e=>{{const c=e.target.closest('circle');if(!c){{hideTip();return;}}showTip(e,`<b>${{CH[c.dataset.k].name}}</b><br>${{dstr(+c.dataset.t)}} UTC · ${{c.dataset.s}}<br>сделка ${{fmt(+c.dataset.n)}} (${{c.dataset.r}}) · накопл. ${{fmt(+c.dataset.e)}}`);}});
  svg.addEventListener('mouseleave',hideTip);
  document.getElementById('lg').innerHTML=keys.map(k=>`<span><span class="sw" style="background:${{col(k)}}"></span>${{CH[k].name}}</span>`).join('');
}})();
// ---- daily bars
(function(){{
  const svg=document.getElementById('dl'),W=1000,H=220,L=56,R=16,T=12,B=28;
  const keys=Object.keys(DAILY), days=[...new Set(keys.flatMap(k=>Object.keys(DAILY[k])))].sort();
  const vals=keys.flatMap(k=>Object.values(DAILY[k])); const y1=Math.max(0,...vals),y0=Math.min(0,...vals);
  const Y=v=>T+(y1-v)/(y1-y0)*(H-T-B); const gw=(W-L-R)/days.length, bw=(gw-24)/keys.length-2;
  let s=`<line x1="${{L}}" x2="${{W-R}}" y1="${{Y(0)}}" y2="${{Y(0)}}" stroke="${{css('--text2')}}"/>`;
  days.forEach((d,i)=>{{s+=`<text x="${{L+i*gw+gw/2}}" y="${{H-B+16}}" text-anchor="middle" font-size="11" fill="${{css('--text2')}}">${{d.slice(5)}}</text>`;
    keys.forEach((k,j)=>{{const v=DAILY[k][d]||0; const x=L+i*gw+12+j*(bw+2); const y=Math.min(Y(0),Y(v)); const h=Math.abs(Y(v)-Y(0));
      s+=`<rect x="${{x}}" y="${{y}}" width="${{bw}}" height="${{Math.max(h,1)}}" rx="3" fill="${{col(k)}}" data-k="${{k}}" data-d="${{d}}" data-v="${{v}}"/><text x="${{x+bw/2}}" y="${{v>=0?y-4:(y+h+12<H-B-2?y+h+12:y+12)}}" text-anchor="middle" font-size="11" fill="${{v>=0||y+h+12<H-B-2?css('--text'):'#fff'}}">${{fmt(v)}}</text>`;}});
  }});
  svg.innerHTML=s;
  svg.addEventListener('mousemove',e=>{{const r=e.target.closest('rect');if(!r){{hideTip();return;}}showTip(e,`<b>${{CH[r.dataset.k].name}}</b><br>${{r.dataset.d}}: ${{fmt(+r.dataset.v)}}`);}});
  svg.addEventListener('mouseleave',hideTip);
}})();
// ---- coins
(function(){{
  const svg=document.getElementById('cn'),W=1000,L=90,R=60,T=8,rowh=18; const H=T+rowh*COINS.length+24;
  const vals=COINS.map(c=>c[1]); const v0=Math.min(0,...vals),v1=Math.max(0,...vals); const X=v=>L+(v-v0)/(v1-v0)*(W-L-R);
  let s=`<line x1="${{X(0)}}" x2="${{X(0)}}" y1="${{T}}" y2="${{H-16}}" stroke="${{css('--text2')}}"/>`;
  [...COINS].reverse().forEach((c,i)=>{{const y=T+i*rowh; const x=Math.min(X(0),X(c[1])); const w=Math.abs(X(c[1])-X(0));
    s+=`<text x="${{L-6}}" y="${{y+13}}" text-anchor="end" font-size="11" fill="${{css('--text2')}}">${{c[0].replace('USDT','')}}</text><rect x="${{x}}" y="${{y+3}}" width="${{Math.max(w,1)}}" height="${{rowh-6}}" rx="3" fill="${{c[1]>=0?css('--s1'):css('--s2')}}"/><text x="${{c[1]>=0?X(c[1])+4:X(c[1])-4}}" y="${{y+13}}" text-anchor="${{c[1]>=0?'start':'end'}}" font-size="11" fill="${{css('--text')}}">${{fmt(c[1])}} · ${{c[2]}} сд</text>`;}});
  svg.innerHTML=s;
}})();
</script></body></html>"""
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "equity-2026-09-20.html")
open(out, "w", encoding="utf-8").write(html)
print(out, len(html))
for key, name, *_ in SERIES:
    m = M[key]
    print(f"{name}: n={m['n']} win={m['winrate']:.0%} total={usd(m['total']):+.0f}$ dd={usd(m['dd']):+.0f}$ sharpe_d={m['sharpe_d']} sharpe_t={m['sharpe_t']}")
