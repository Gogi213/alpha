// node shot_html.js <файл.html> <файл.png> [ширина] [высота] — снимок статичного макета (Edge headless, playwright из npx-кеша)
const { chromium } = require('C:/Users/Георгий/AppData/Local/npm-cache/_npx/9833c18b2d85bc59/node_modules/playwright');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const pg = await b.newPage({ viewport: { width: +(process.argv[4] || 1180), height: +(process.argv[5] || 900) } });
  await pg.goto('file:///' + process.argv[2].replace(/\\/g, '/'));
  await pg.waitForTimeout(300);
  await pg.screenshot({ path: process.argv[3], fullPage: true });
  await b.close();
})();
