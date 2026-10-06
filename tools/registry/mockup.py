#!/usr/bin/env python3
"""Макет страницы реестра (TK-068): один HTML, данные из docs/registry/runs.jsonl (до 150 строк, богатые первыми). Только макет."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import registry as R
rows = R.load()
rows.sort(key=lambda r: (-5*("config" in r), -sum(k in r for k in ("binary_md5", "commit", "machine", "wall_s", "cpu_s", "data_pool", "period")), r["ts"]))
rows = sorted(rows[:150], key=lambda r: r["ts"], reverse=True)
for r in rows:
    r["outcome"] = r.get("outcome", "")[:400]
HTML = """<!doctype html><meta charset=utf-8><title>Реестр прогонов — макет</title>
<style>body{font:14px system-ui;margin:0;display:flex;height:100vh}#l{width:46%;overflow:auto;border-right:1px solid #ccc}#r{flex:1;padding:16px;overflow:auto}
header{padding:10px;background:#f4f4f4;position:sticky;top:0}input,select{margin:2px;padding:4px}.row{padding:6px 10px;border-bottom:1px solid #eee;cursor:pointer}.row:hover,.sel{background:#eef5ff}
.b{font-size:11px;padding:1px 6px;border-radius:8px;background:#ddd}.b.боевой{background:#cfe8cf}.b.неполно{background:#f7e3b5}.b.проба{background:#d6e4f7}.b.недействителен{background:#f3c6c6}
pre{background:#f4f4f4;padding:6px;white-space:pre-wrap;word-break:break-all;margin:2px 0}table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #ddd;padding:2px 6px;text-align:left}button{padding:3px 8px;margin:2px 0}dt{color:#666;font-size:12px;margin-top:8px}dd{margin:0}.m{color:#888;font-size:12px}</style>
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
function esc(x){return String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function cfg(r){const c=r.config;let h='<h4>Конфиг <span class="b '+(r.config_status&&r.config_status.startsWith('полный')?'боевой':'неполно')+'">'+esc(r.config_status||'конфиг неполон')+'</span></h4>';
if(!c)return h+'<p class=m>Конфиг не сохранён (запись из текста журнала/тикета). Восстановить можно только по коммиту и скриптам — см. карточку источника.</p>';
const rid='cmd'+r.id;h+='<dt>команда повтора</dt><dd><pre id="'+rid+'">'+esc(c.cmdline||'')+'</pre><button onclick="navigator.clipboard.writeText(document.getElementById(&quot;'+rid+'&quot;).textContent)">скопировать команду повтора</button></dd>';
if(c.cwd)h+='<dt>каталог запуска</dt><dd>'+esc(c.cwd)+'</dd>';
const env=Object.entries(c.env||{});h+='<dt>окружение ('+env.length+')</dt><dd><table>'+env.map(e=>'<tr><td>'+esc(e[0])+'</td><td>'+esc(e[1])+'</td></tr>').join('')+'</table></dd>';
const fl=Object.entries(c.files||{});h+='<dt>скрипты и списки клеток — копии по sha ('+fl.length+')</dt><dd><table>'+fl.map(e=>'<tr><td>'+esc(e[0])+'</td><td><a href="files/'+e[1].sha256+'">'+e[1].sha256.slice(0,12)+'</a></td><td>'+e[1].size+' Б</td></tr>').join('')+'</table></dd>';
const inp=Object.entries(c.inputs||{});h+='<dt>входные данные — sha ('+inp.length+')</dt><dd><table>'+inp.map(e=>'<tr><td>'+esc(e[0])+'</td><td>'+esc(e[1].sha256?e[1].sha256.slice(0,16):(e[1].note||'sha не снят'))+'</td><td>'+(e[1].size||'')+'</td></tr>').join('')+'</table></dd>';
const bn=Object.entries(c.binaries||{});h+='<dt>бинарники — md5</dt><dd><table>'+bn.map(e=>'<tr><td>'+esc(e[0])+'</td><td>'+esc(e[1].md5||e[1].note||'?')+'</td></tr>').join('')+'</table></dd>';
if(c.cells)h+='<dt>клетки</dt><dd>'+esc(c.cells)+'</dd>';
h+='<dt>конфиг стратегии по клеткам (В-129)</dt><dd><details><summary>развернуть таблицу клеток</summary><table class=cells><tr><th>клетка<th>семья<th>вход / стоп / тейк / дедлайн / выход по стене / задержка В-68 / очередь / h3-mode / σ / hold-step</tr>'+(c.strategy_rows||[['g92-N3-u1d3','g92','N=3;u=1/3;K=5 (пример из П-12; в боевых записях раскрывается из cells-файла)']]).map(x=>'<tr><td>'+esc(x[0])+'<td>'+esc(x[1])+'<td>'+esc(x[2])).join('')+'</table></details></dd>';
if(c.strategy_note)h+='<dd class=m>'+esc(c.strategy_note)+'</dd>';return h}
function card(r){document.getElementById('r').innerHTML='<h3>'+r.id+' <span class="b '+r.status+'">'+r.status+'</span></h3><dl>'+Object.keys(L).filter(k=>r[k]&&k!='config'&&k!='config_status').map(k=>'<dt>'+L[k]+'</dt><dd>'+esc(r[k])+'</dd>').join('')+cfg(r)+'</dl>'}
['q','fs','fk','fp'].forEach(i=>document.getElementById(i).oninput=draw);draw()</script>"""
out = os.path.join(R.ROOT, "docs", "registry", "mockup.html")
open(out, "w", encoding="utf-8").write(HTML.replace("__DATA__", json.dumps(rows, ensure_ascii=False).replace("</", "<\/")))
print(out, len(rows))
