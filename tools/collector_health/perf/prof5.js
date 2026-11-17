const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const ctx = await b.newContext({ viewport: { width: 1180, height: 800 } });
  const pg = await ctx.newPage();
  const cdp = await ctx.newCDPSession(pg);
  await pg.goto(process.argv[2]); await pg.waitForSelector('#tab tr.r'); await pg.waitForTimeout(800);
  await cdp.send('Profiler.enable'); await cdp.send('Profiler.setSamplingInterval', { interval: 200 }); await cdp.send('Profiler.start');
  const t = await pg.evaluate(async () => { const t0 = performance.now(); document.querySelector('#ctl button[data-m="out"]').click(); const j = performance.now() - t0; await new Promise(r => requestAnimationFrame(() => setTimeout(r, 0))); return [j, performance.now() - t0] });
  const { profile } = await cdp.send('Profiler.stop');
  const dt = profile.timeDeltas, self = {};
  const byId = Object.fromEntries(profile.nodes.map(n => [n.id, n]));
  profile.samples.forEach((id, i) => { const n = byId[id], k = n.callFrame.functionName + ' ' + (n.callFrame.url.split('/').pop() || '') + ':' + n.callFrame.lineNumber; self[k] = (self[k] || 0) + dt[i] / 1000 });
  console.log('click JS, кадр:', t.map(x => Math.round(x)));
  Object.entries(self).sort((a, b) => b[1] - a[1]).slice(0, 12).forEach(([k, v]) => console.log(v.toFixed(1).padStart(7), 'мс', k));
  await b.close();
})();
