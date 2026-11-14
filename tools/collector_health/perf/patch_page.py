import re, sys
p = sys.argv[1]
s = open(p, encoding='utf-8', newline='').read().replace('\r\n', '\n')
assert 'id="p-a"' not in s

CSS = r"""
html{overflow-y:scroll}
.top{justify-content:flex-start;align-items:center;gap:24px}
.top .t{margin-left:auto}
.tabs{display:flex;gap:26px;margin-left:18px}
.tg button,.tabs button{all:unset;cursor:pointer;color:var(--mut);padding:4px 0;border-bottom:2px solid transparent;display:inline-flex;flex-direction:column;align-items:center}
.tg button::after,.tabs button::after{content:attr(data-t);font-weight:600;height:0;overflow:hidden;visibility:hidden}
.tabs button{font-size:15px}
.tg button:hover,.tabs button:hover{color:var(--ink)}
.tg button.on,.tabs button.on{color:var(--ink);font-weight:600;border-bottom-color:var(--acc)}
[hidden]{display:none!important}
.flt{display:flex;gap:22px;margin:-6px 0 12px;font-size:14px;flex-wrap:wrap;height:30px}
.ctl{display:flex;gap:34px;margin:0 0 18px;font-size:14px;align-items:baseline;height:30px}
.ctl .g{display:flex;gap:18px;align-items:baseline}
.ctl .g>span{color:var(--mut);font-size:13px}
.ctl .cnt{display:inline-block;width:150px;color:var(--mut);font-size:12px;white-space:nowrap}
.ctl .upd{margin-left:auto;color:var(--mut);font-size:12px;white-space:nowrap}
table.a{border-collapse:collapse;width:100%;table-layout:fixed;font-variant-numeric:tabular-nums}
table.a th{font-weight:400;font-size:12px;line-height:1.25;color:var(--mut);text-align:right;vertical-align:bottom;height:38px;padding:0 14px 8px 0;cursor:pointer;white-space:normal}
table.a th.l,table.a td.l{text-align:left}
table.a th:hover,table.a th.on{color:var(--ink)}
.hl{position:relative;display:inline-block}
.hl .ar{position:absolute;left:100%;bottom:0;margin-left:3px;font-style:normal}
table.a td{padding:0 14px 0 0;height:42px;text-align:right;border-top:1px solid #ecebe5;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
table.a td.l{padding-right:8px}
table.a tr.r{cursor:pointer}
table.a tr.r:hover td{background:#f3f2ed}
table.a td.c{font-size:16px;font-weight:600}
table.a td.p{color:var(--mut);font-size:14px}
table.a td.none{color:var(--nolog)}
table.a tr.ch td{padding:6px 0 22px;height:auto;background:none!important;cursor:default;overflow:visible}
.cwrap{position:relative;height:300px}
.cwrap canvas{display:block;width:100%;height:300px}
.cwrap .ld{position:absolute;left:0;top:130px;width:100%;text-align:center;color:var(--mut);font-size:13px}
.lg{display:flex;gap:22px;font-size:12px;color:var(--mut);margin:0 0 4px;height:16px;white-space:nowrap}
.lg i{display:inline-block;width:18px;height:0;border-top:2px solid;vertical-align:middle;margin-right:6px}
.pend{color:var(--mut);font-size:14px;padding:30px 0}
table.a tr.sp td{padding:0;border:0;line-height:0;font-size:0;cursor:default}
"""
s = s.replace('</style>', CSS + '</style>', 1)

s = s.replace('<div class="top"><h1>Сбор данных</h1><div class="t" id="clock"></div></div>',
 '<div class="top"><h1>Сбор данных</h1><nav class="tabs"><button id="tab-a" data-t="Арбитраж" class="on">Арбитраж</button><button id="tab-h" data-t="Здоровье">Здоровье</button></nav><div class="t" id="clock"></div></div>', 1)
s = s.replace('<div class="cap">потеряно за час</div>', '<section id="p-h" hidden>\n<div class="cap">потеряно за час</div>', 1)
s = s.replace('<div class="axis" id="axis"></div></div>\n<script>', '<div class="axis" id="axis"></div></div>\n</section>\n\n<section id="p-a">\n<div class="flt tg" id="flt"></div>\n<div class="ctl tg" id="ctl"></div>\n<div class="pend" id="pend" hidden></div>\n<table class="a" id="tab"><colgroup id="cg"></colgroup><thead id="th"></thead><tbody id="tb"></tbody></table>\n</section>\n\n<script>', 1)

JS = open(sys.argv[2], encoding='utf-8').read()
h0 = 'async function load(){\n  try{'
assert h0 in s
s = s.replace(h0, "async function load(){\n  if(document.getElementById('p-h').hidden||document.hidden)return;\n  try{", 1)
h1 = 'function render(){\n  if(!D||!D.rows)return;'
assert h1 in s
s = s.replace(h1, "function render(){\n  if(!D||!D.rows||document.getElementById('p-h').hidden)return;", 1)
s = s.replace('</script>\n</body>', '</script>\n' + JS + '</body>', 1)
assert 'id="p-a"' in s and 'id="tab-a"' in s and 'window._arb' in s
open(p, 'w', encoding='utf-8', newline='').write(s)
