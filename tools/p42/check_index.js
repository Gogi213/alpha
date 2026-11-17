// П-42 §13.16: проверка индекса сделок (index.html из charts.py) в headless Chrome — ТОЛЬКО на srv / в Софии, не на ПК владельца.
// Запуск (София): PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome node check_index.js file:///<путь>/index.html
// Что проверяет: фильтр «Метка» (число строк по каждому значению), клик по строке открывает окно с загруженным графиком,
// каждая сделка набора (в т. ч. строки «исчезла» со ссылкой на прежний набор) — картинка графика загружается (naturalWidth > 0).
const { chromium } = require(process.env.PW || "playwright-core");
(async () => {
  const url = process.argv[2];
  const b = await chromium.launch({ executablePath: process.env.CHROME, args: ["--no-sandbox", "--allow-file-access-from-files"], headless: true });
  const p = await b.newPage({ viewport: { width: 1700, height: 900 } });
  const errs = [];
  p.on("pageerror", (e) => errs.push(String(e)));
  await p.goto(url);
  await p.waitForSelector("#tb tr");
  const out = { url, marks: {}, click: [], errs };
  const hasMark = await p.evaluate(() => !!document.getElementById("f_mark"));
  const marks = hasMark ? ["cur", "common", "new", "gone", "all"] : [""];
  for (const v of marks) {
    if (hasMark) await p.selectOption("#f_mark", v);
    const info = await p.evaluate(() => ({ rows: document.querySelectorAll("#tb tr").length, cnt: document.getElementById("cnt").textContent }));
    out.marks[v || "-"] = info;
    for (const which of ["first", "last"]) {
      if (!info.rows) continue;
      await p.locator("#tb tr").nth(which === "first" ? 0 : info.rows - 1).click();
      await p.waitForFunction(() => { const im = document.getElementById("m_i"); return im && im.complete && im.naturalWidth > 0; }, null, { timeout: 15000 }).catch(() => {});
      await p.waitForFunction(() => { const z = document.getElementById("m_z"); const pz = document.getElementById("pz"); return !pz || pz.style.display === "none" || (z.complete && z.naturalWidth > 0); }, null, { timeout: 15000 }).catch(() => {});
      const r = await p.evaluate(() => { const im = document.getElementById("m_i"), z = document.getElementById("m_z"), pz = document.getElementById("pz"), zv = !!pz && pz.style.display !== "none"; return { src: decodeURIComponent(im.getAttribute("src") || ""), w: im.naturalWidth, zoom_w: zv ? z.naturalWidth : null, title: document.getElementById("m_t").textContent }; });
      const on = await p.evaluate(() => document.getElementById("modal").classList.contains("on") && document.getElementById("m_s").textContent.length > 10);
      out.click.push({ mark: v || "-", which, ...r, ok: r.w > 0 && on && (r.zoom_w === null || r.zoom_w > 0) });
      await p.keyboard.press("Escape");
    }
  }
  if (hasMark) {
    // каждая строка «исчезли» и «появились»: окно открывается, график загружен, подпись с пометкой
    for (const v of ["gone", "new"]) {
      await p.selectOption("#f_mark", v);
      const n = await p.locator("#tb tr").count();
      let good = 0;
      const bad = [];
      for (let i = 0; i < n; i++) {
        await p.locator("#tb tr").nth(i).click();
        await p.waitForFunction(() => { const im = document.getElementById("m_i"); return im && im.complete && im.naturalWidth > 0; }, null, { timeout: 15000 }).catch(() => {});
        const r = await p.evaluate((want) => { const im = document.getElementById("m_i"); const s = document.getElementById("m_s").textContent; return im.naturalWidth > 0 && document.getElementById("modal").classList.contains("on") && s.includes(want); }, v === "gone" ? "ИСЧЕЗЛА" : "появилась");
        r ? good++ : bad.push(i);
        await p.keyboard.press("Escape");
      }
      out["rows_" + v] = { n, good, bad };
    }
  }
  const all = await p.evaluate(async () => {
    const rows = D.flatMap((d) => [{ id: d.id, src: d.img || "charts/" + d.id + ".png", mark: d.mark || "" }].concat(d.zoom ? [{ id: d.id + " (zoom)", src: d.zoom, mark: "zoom" }] : []));
    const res = { total: rows.length, ok: 0, bad: [] };
    const load = (r) => new Promise((ok) => { const im = new Image(); im.onload = () => ok(im.naturalWidth > 0); im.onerror = () => ok(false); im.src = r.src; });
    for (let i = 0; i < rows.length; i += 16) {
      const part = rows.slice(i, i + 16);
      const got = await Promise.all(part.map(load));
      got.forEach((g, k) => (g ? res.ok++ : res.bad.push(part[k].id)));
    }
    res.gone = rows.filter((r) => r.mark === "исчезла").length;
    res.zoom = rows.filter((r) => r.mark === "zoom").length;
    return res;
  });
  out.links = all;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
  const badRows = ["rows_gone", "rows_new"].some((k) => out[k] && out[k].bad.length);
  process.exit(all.bad.length || out.click.some((c) => !c.ok) || errs.length || badRows ? 1 : 0);
})();
