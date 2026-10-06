#!/usr/bin/env python3
"""Макет страницы реестра (TK-068): один HTML, данные из docs/registry/runs.jsonl (до 150 строк, богатые первыми). Только макет."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import registry as R
rows = R.load()
rows.sort(key=lambda r: (-sum(k in r for k in ("binary_md5", "commit", "machine", "wall_s", "cpu_s", "data_pool", "period")), r["ts"]))
rows = sorted(rows[:150], key=lambda r: r["ts"], reverse=True)
for r in rows:
    r["outcome"] = r.get("outcome", "")[:400]
HTML = """<!doctype html><meta charset=utf-8><title>Реестр прогонов — макет</title>
<style>body{font:14px system-ui;margin:0;display:flex;height:100vh}#l{width:46%;overflow:auto;border-right:1px solid #ccc}#r{flex:1;padding:16px;overflow:auto}
header{padding:10px;background:#f4f4f4;position:sticky;top:0}input,select{margin:2px;padding:4px}.row{padding:6px 10px;border-bottom:1px solid #eee;cursor:pointer}.row:hover,.sel{background:#eef5ff}
.b{font-size:11px;padding:1px 6px;border-radius:8px;background:#ddd}.b.боевой{background:#cfe8cf}.b.неполно{background:#f7e3b5}.b.проба{background:#d6e4f7}.b.недействителен{background:#f3c6c6}
dt{color:#666;font-size:12px;margin-top:8px}dd{margin:0}.m{color:#888;font-size:12px}</style>
<div id=l><header><b>Реестр прогонов</b> <span class=m id=cnt></span><br>
<input id=q placeholder="поиск: Г-45, P-10, TK-049, v171b, md5…" size=34>
<select id=fs><option value="">статус</option><option>боевой</option><option>проба</option><option>недействителен</option><option>неполно</option></select>
<select id=fk><option value="">вид</option><option>research</option><option>speed</option><option>data</option><option>other</option></select>
<input id=fp placeholder="период / данные" size=14></header><div id=list></div></div>
<div id=r><i>Выберите прогон слева — карточка: что, на чём (данные, период, монеты, брак-гейт), чем (бинарник md5, коммит, флаги), где (машина, время, ЦП), результат (путь, итог), проверка Судьи, статус.</i></div>
<script>const D=__DATA__;const L={ts:'когда',ticket:'тикет',protocol:'протокол',kind:'вид',what:'что',hyp:'гипотезы',data_pool:'данные: пул',period:'период',coins:'монеты',gate:'брак-гейт',epochs:'эпохи/формат',binary_md5:'бинарник md5',commit:'коммит',flags:'флаги',machine:'машина',wall_s:'стена, с',cpu_s:'ЦП, с',result_path:'где результат',outcome:'итог',judge:'проверка Судьи',status:'статус',status_why:'почему',source:'источник записи',note:'заметка'};
function draw(){const q=document.getElementById('q').value.toLowerCase().split(/\s+/).filter(Boolean),s=fs.value,k=fk.value,p=fp.value.toLowerCase();
const x=D.filter(r=>{const t=JSON.stringify(r).toLowerCase();return q.every(w=>t.includes(w))&&(!s||r.status==s)&&(!k||r.kind==k)&&(!p||((r.period||'')+(r.data_pool||'')).toLowerCase().includes(p))});
cnt.textContent=x.length+' из '+D.length+' (в макете)';list.innerHTML='';x.forEach(r=>{const d=document.createElement('div');d.className='row';d.innerHTML='<span class="b '+r.status+'">'+r.status+'</span> <b>'+(r.ticket||r.protocol||'')+'</b> <span class=m>'+r.ts.slice(0,16)+'</span><br>'+r.what.slice(0,110).replace(/</g,'&lt;');d.onclick=()=>{document.querySelectorAll('.sel').forEach(e=>e.classList.remove('sel'));d.classList.add('sel');card(r)};list.appendChild(d)})}
function card(r){document.getElementById('r').innerHTML='<h3>'+r.id+' <span class="b '+r.status+'">'+r.status+'</span></h3><dl>'+Object.keys(L).filter(k=>r[k]).map(k=>'<dt>'+L[k]+'</dt><dd>'+String(r[k]).replace(/</g,'&lt;')+'</dd>').join('')+'</dl>'}
['q','fs','fk','fp'].forEach(i=>document.getElementById(i).oninput=draw);draw()</script>"""
out = os.path.join(R.ROOT, "docs", "registry", "mockup.html")
open(out, "w", encoding="utf-8").write(HTML.replace("__DATA__", json.dumps(rows, ensure_ascii=False).replace("</", "<\/")))
print(out, len(rows))
