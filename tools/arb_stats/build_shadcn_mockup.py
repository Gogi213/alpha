"""Макет вкладки «Арбитраж» в стиле shadcn (тёмная тема) одним HTML-файлом на настоящих данных живой вкладки.

python build_shadcn_mockup.py <page-shadcn.html | page-dense.html> <arb.json> <chart.jsonl> <v3.json> <turnover.json> <fonts-dir> <выход.html> [подпись в <title>]
Страница та же, что пойдёт в бой (tools/collector_health/page-shadcn.html или page-dense.html); подменяется только fetch (/api/arb, /api/arb/chart, /api/v3) — данными снимка,
шрифты (/fonts/*.woff2 из страницы) вшиваются data-URI из <fonts-dir>. В строки таблицы добавляется v = [$ первой биржи, $ второй] за окно arb.json.
"""
import base64
import gzip
import json
import re
import sys
from pathlib import Path


def b64(b):
    return base64.b64encode(b).decode()


def main(page, arb, chart, v3, turn, fonts, out, label="макет shadcn"):
    A = json.loads(Path(arb).read_text(encoding="utf-8"))
    T = json.loads(Path(turn).read_text())
    coin = T["coin"]
    miss = 0
    for r in A["rows"]:
        c = coin.get(r["c"], {})
        va, vb = c.get(r["a"]), c.get(r["b"])
        if va is None or vb is None:
            miss += 1
        r["v"] = [round(va[0]) if va else 0, round(vb[0]) if vb else 0]
    A["vwin"] = T["win"]["page"]
    html = Path(page).read_text(encoding="utf-8")
    html = re.sub(r"/fonts/([\w.-]+\.woff2)", lambda m: "data:font/woff2;base64," + b64(Path(fonts, m.group(1)).read_bytes()), html)
    cz = gzip.compress(Path(chart).read_bytes(), 9)
    shim = """<script>
/* МАКЕТ: страница — копия боевой; fetch отдаёт снимок живой вкладки srv (arb.json + графики + здоровье), сети нет */
(function(){
const ARB=%s, V3=%s, CZ="%s";
let cp=null;
const chartText=()=>cp||(cp=(async()=>{const bin=Uint8Array.from(atob(CZ),c=>c.charCodeAt(0));return await new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'))).text()})());
const idx=new Map(ARB.rows.map(r=>[r.c+'|'+r.a+'|'+r.b,r]));
const J=(s,h)=>new Response(s,{headers:Object.assign({'content-type':'application/json'},h||{})});
window.fetch=async(u)=>{
  const url=new URL(u,'http://x.invalid/');
  if(url.pathname==='/api/arb')return J(JSON.stringify(ARB),{etag:'"mock"',date:new Date().toUTCString()});
  if(url.pathname==='/api/arb/chart'){
    const r=idx.get(url.searchParams.get('c')+'|'+url.searchParams.get('a')+'|'+url.searchParams.get('b'));
    if(!r)return J('{"error":"нет пары"}');
    const t=await chartText(),line=t.slice(r.o,r.o+r.l).trim();
    return J('{"gen":'+ARB.gen+',"t0":'+ARB.t0+',"t1":'+ARB.t1+',"slot":'+ARB.slot+','+line.slice(1));
  }
  if(url.pathname==='/api/v3')return J(JSON.stringify(V3));
  return new Response('',{status:404});
};
})();
</script>
""" % (json.dumps(A, ensure_ascii=False, separators=(",", ":")), Path(v3).read_text(encoding="utf-8").strip(), b64(cz))
    assert html.count("<script>") == 2
    html = html.replace("<script>", shim + "<script>", 1).replace("<title>Сбор данных</title>", "<title>Сбор данных — " + label + "</title>")
    Path(out).write_text(html, encoding="utf-8")
    print({"rows": len(A["rows"]), "rows_without_turnover": miss, "bytes": len(html), "chart_gz": len(cz)})


if __name__ == "__main__":
    main(*sys.argv[1:9])
