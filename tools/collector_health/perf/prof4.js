const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: 1180, height: 800 } });
  await pg.goto(process.argv[2]); await pg.waitForSelector('#tab tr.r'); await pg.waitForTimeout(800);
  const r = await pg.evaluate(async () => {
    const m = Object.fromEntries(performance.getEntriesByType('mark').map(e => [e.name, Math.round(e.startTime)]));
    const nav = performance.getEntriesByType('navigation')[0];
    const res = performance.getEntriesByType('resource').filter(e => e.name.includes('/api/')).map(e => [e.name.split('/api/')[1], Math.round(e.startTime), Math.round(e.responseEnd)]);
    const A = window._arb, o = { marks: m, domContentLoaded: Math.round(nav.domContentLoadedEventEnd), res };
    const T = (name, f) => { const t = performance.now(); f(); o[name] = +(performance.now() - t).toFixed(1) };
    T('out: head()', () => { A.st.mode = 'out'; A.head() });
    T('out: layout after head', () => document.body.offsetHeight);
    T('out: buildView', () => A.buildView());
    T('out: paint (tbody replace + layout)', () => A.paint());
    T('out: paint again', () => A.paint());
    T('med: head()', () => { A.st.mode = 'med'; A.head() });
    T('med: layout after head', () => document.body.offsetHeight);
    T('med: paint', () => { A.buildView(); A.paint() });
    T('out again: head+layout', () => { A.st.mode = 'out'; A.head(); document.body.offsetHeight });
    T('out again: paint', () => { A.buildView(); A.paint() });
    return o;
  });
  console.log(JSON.stringify(r, null, 1));
  await b.close();
})();
