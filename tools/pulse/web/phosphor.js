/* Общее ядро трёх живых страниц веб-табло: опрос status.json, плашка «нет связи / сводка устарела», вопросы с ответом
   кнопками (подтверждение; вариант по умолчанию — одна кнопка «Ок»), общие кирпичи вёрстки. Каждая страница
   (сейчас одна: dispetcher) вызывает Board.start({... render(v, ui) → HTML}) и рисует только своё.
   Данные — view2 (tools/pulse/VIEW2.md); ответ — POST answer {id, key} на этот же сервер. Идентификаторы тикетов
   на экран не выводятся: только названия и слова по-людски. */
(function () {
  "use strict";
  const B = (window.Board = {});
  const PERIOD = 5000;

  const E = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const arr = (x) => (Array.isArray(x) ? x : []);
  const ST = { done: "ГОТОВО", run: "ДЕЛАЕТСЯ", review: "ПРОВЕРЯЕТСЯ", repair: "ЧИНИТСЯ", wait: "ЖДЁТ ВАС", todo: "ВПЕРЕДИ", bad: "ПРОБЛЕМА" };
  const STM = { done: "сделано", run: "идёт сейчас", review: "судья проверяет результат", repair: "нашли замечание, правят", wait: "ваш ход", todo: "в очереди", bad: "застряло" };
  const TAGS0 = { pc: { tag: "ПК", name: "этот ПК" }, vps: { tag: "VPS", name: "VPS София" }, calc: { tag: "СЧЁТ", name: "сервер счёта" }, col: { tag: "КОЛ", name: "коллектор" }, you: { tag: "ВЫ", name: "вы" } };

  let V = {};
  let cfg = null, view = null, loaded = false, netFail = false, ageAt = 0, fetchedAt = 0, lastJson = "", timer = null, ph = "green";
  const ui = { open: new Set(), sel: null, pending: null, answered: {}, busy: null, focus: null };
  B.ui = ui;
  B.E = E;
  B.arr = arr;

  // ---------- мелкие кирпичи ----------
  const sk = (s) => (ST[s] ? s : "todo");
  const tagOf = (id) => Object.assign({}, TAGS0[id] || { tag: String(id || "?").toUpperCase(), name: String(id || "?") }, (V.tags || {})[id] || {});
  const mt = (id) => (id ? `<span class="mt${id === "you" ? " you" : ""}">[${E(tagOf(id).tag)}]</span>` : "");
  const mn = (id) => `${mt(id)} ${E(tagOf(id).name)}`;
  const tok = (s) => `<span class="st s-${sk(s)}">[${ST[sk(s)]}]</span>`;
  const fmtMin = (m) => { m = Math.round(m); return m < 1 ? "меньше минуты" : m < 60 ? m + " мин" : Math.floor(m / 60) + " ч " + String(m % 60).padStart(2, "0") + " мин"; };
  const procs = () => arr(V.processes);
  const proc = (id) => procs().find((p) => p.id === id);
  const qOf = (id) => arr(V.questions).find((q) => q.id === id);
  const lamp = (s) => `<i class="lamp l-${E(s || "off")}" aria-hidden="true"></i>`;
  const forTxt = (p) => (p.for && p.for.text ? `для: ${E(p.for.text)}${p.for.on ? " " + mt(p.for.on) : ""}` : "");
  const flow = (p) => arr(p.flow).map((f) => `${E(f.text)} ${mt(f.on)}`).join(" → ");
  const whoOn = (s) => (s.who === "вы" ? "вы" : `${E(s.who)} на ${mt(s.on)}`);
  const tm = (s) => (s.finished ? `${s.started}–${s.finished}` : s.started ? `с ${s.started}` : "");
  const sdet = (s) => {
    const a = [];
    if (s.detail) a.push(E(s.detail));
    if (s.state === "run" && s.pct != null) a.push(s.pct + " %");
    if (s.eta_min) a.push("ещё ~" + fmtMin(s.eta_min));
    return a.join(" · ");
  };
  const waitTxt = (s) => { const q = s.question && qOf(s.question); return q ? `ждёт вас ${fmtMin(q.wait_min || 0)} · вопрос ${E(q.n)}` : "ждёт вас"; };
  const pinfo = (p) => {
    const a = [];
    if (p.steps_total) a.push(`шаг ${p.step_now || 0} из ${p.steps_total}`);
    if (p.state === "run" && p.eta_min) a.push(`ещё ~${fmtMin(p.eta_min)}`);
    if (p.state === "wait" && p.wait_min != null) a.push(`ждёт вас ${fmtMin(p.wait_min)}`);
    if (p.state === "bad" && p.wait_min != null) a.push(`висит ${fmtMin(p.wait_min)}`);
    if (p.state === "done") a.push("готово");
    return a.join(" · ");
  };
  const mProg = (p) => arr(p.steps).filter((s) => s.state === "done").length;
  const seg = (ps, label) => {
    const list = ps.filter((p) => arr(p.steps).length);
    const all = list.flatMap((p) => p.steps);
    const done = all.filter((s) => s.state === "done").length;
    if (!list.length) return `<span class="seg empty" role="img" aria-label="шагов нет"><span class="g" style="--n:1"><i></i></span></span>`;
    return `<span class="seg" role="img" aria-label="${E(label || "готово")} ${done} из ${all.length} шагов">${list.map((p) => `<span class="g" style="--n:${p.steps.length}">${p.steps.map((s) => `<i class="s-${sk(s.state)}"></i>`).join("")}</span>`).join("")}</span>`;
  };
  const bar = (pct, n) => {
    n = n || 14; const on = Math.round((pct / 100) * n); let h = "";
    for (let i = 0; i < n; i++) h += `<b${i < on ? ' class="on"' : ""}></b>`;
    return `<span class="bar" role="img" aria-label="${pct} %">${h}</span>`;
  };
  const meter = (l, v) => `<div class="mt-row"><span class="dim">${l}</span>${bar(v, 10)}<span>${v} %</span></div>`;

  // группы процессов по волнам (VIEW2.md → waves[]); процессов вне волн — последней группой без названия
  const groups = () => {
    const byId = {}; procs().forEach((p) => (byId[p.id] = p));
    const seen = new Set(), out = [];
    arr(V.waves).forEach((w) => {
      const ps = arr(w.procs).map((id) => byId[id]).filter(Boolean);
      ps.forEach((p) => seen.add(p.id));
      if (ps.length) out.push({ n: w.n, label: w.label || "", state: w.state || "todo", procs: ps });
    });
    const rest = procs().filter((p) => !seen.has(p.id));
    if (rest.length) out.push({ n: null, label: out.length ? "ПРОЧЕЕ" : "", state: "todo", procs: rest });
    return out;
  };
  // шаги процесса по волнам шага (параллельные — в одной группе)
  const stepWaves = (p) => {
    const m = new Map();
    arr(p.steps).forEach((s, i) => { const w = s.wave != null ? s.wave : i + 1; if (!m.has(w)) m.set(w, []); m.get(w).push(s); });
    return [...m.entries()].sort((a, b) => a[0] - b[0]).map((e) => ({ wave: e[0], steps: e[1] }));
  };
  const hasParallel = (p) => stepWaves(p).some((w) => w.steps.length > 1);

  const counts = () => {
    const c = V.counters || {};
    return ["done", "run", "review", "repair", "wait", "bad", "todo"].filter((k) => c[k] || k === "done").map((k) => `<span>${tok(k)} <b>${c[k] || 0}</b></span>`).join("");
  };
  const headline = () => {
    const h = V.headline || {};
    const t = E(String(h.text || "").toUpperCase());
    if (!t) return "";
    return h.state === "bad" ? `<span class="st s-bad big">${t}</span>` : h.state === "wait" ? `<span class="inv big">${t}</span>` : `<span class="hot big plain">${t}</span>`;
  };
  // общая полоса, «осталось» вилкой, «прошло»
  const progress = () => {
    const pr = V.progress;
    const ps = procs().filter((p) => p.wave != null);
    const strip = seg(ps, "готово");
    if (!pr) return { pct: null, left: `<span class="dim">процессов с планом пока нет</span>`, strip, right: "" };
    const eta = pr.eta && pr.eta.lo_min != null ? `<b class="hot">${pr.eta.lo_min}–${pr.eta.hi_min} мин</b>${pr.eta.measured ? ` <span class="dim">по ${pr.eta.measured} замерам</span>` : ""}` : `<span class="dim">пока не оценить: мало замеров</span>`;
    return {
      pct: pr.pct,
      left: `готово <b class="hot">${pr.pct} %</b> · <b>${Math.floor(pr.done)}</b> из ${pr.total} шагов`,
      strip,
      right: `осталось ${eta}${pr.spent_min != null ? ` · <span class="dim">прошло ${fmtMin(pr.spent_min)}</span>` : ""}`,
    };
  };
  const legend = () => `<div class="legend">${Object.keys(ST).map((k) => `<span>${tok(k)} <span class="dim">${STM[k]}</span></span>`).join("")}</div>`;
  const legendPip = () => `<div class="legend">${Object.keys(ST).map((k) => `<span><i class="pip s-${k}"></i>${ST[k].toLowerCase()} <span class="dim">· ${STM[k]}</span></span>`).join("")}</div>`;
  const feedWho = (f) => `${arr(f.on).map(mt).join(" ")}${f.to ? " → " + mt(f.to) : ""}`;

  // ---------- вопрос владельцу ----------
  const optLabel = (q, key) => { const o = arr(q.options).find((x) => String(x.key).toLowerCase() === String(key).toLowerCase()); return o ? o.label : String(key); };
  const qHTML = (q) => {
    const p = proc(q.process);
    const done = ui.answered[q.id] || (q.web_answer ? optLabel(q, q.web_answer) : null);
    let h = `<div class="q" data-q="${E(q.id)}"><div class="qh"><span class="inv qn">ВОПРОС ${E(q.n)}</span><span class="dim">${q.from ? E(q.from) : ""}${q.on ? " на " + mt(q.on) : ""}${p ? " · «" + E(p.title) + "»" : ""}${q.wait_min != null ? " · ждёт " + fmtMin(q.wait_min) : ""}</span></div><p class="qt">${E(q.text)}</p>`;
    if (done) return h + `<div class="ans"><span class="st s-done">[ОТВЕТ ЗАПИСАН]</span> «${E(done)}» <span class="dim">ПК заберёт его в течение минуты</span></div></div>`;
    const def = q.default != null && q.default !== "" ? String(q.default) : null;
    const pend = ui.pending && ui.pending.q === q.id ? String(ui.pending.k) : null;
    const busy = ui.busy === q.id ? " disabled" : "";
    h += `<div class="opts" role="group" aria-label="Варианты ответа на вопрос ${E(q.n)}">` +
      arr(q.options).map((o) => `<button type="button" class="opt" data-act="pick" data-q="${E(q.id)}" data-k="${E(o.key)}" aria-pressed="${String(o.key) === pend}"${busy}><span class="ol">${E(o.label)}</span>${o.effect ? `<span class="ef">${E(o.effect)}</span>` : ""}${def !== null && String(o.key) === def ? '<span class="df">предлагаю</span>' : ""}</button>`).join("") + "</div>";
    if (def !== null && arr(q.options).some((o) => String(o.key) === def)) {
      h += `<div class="okrow"><button type="button" class="okb" data-act="ok" data-q="${E(q.id)}" data-k="${E(def)}"${busy}>Ок</button><span class="dim">ответить предложенным: «${E(optLabel(q, def))}»</span></div>`;
    }
    if (pend !== null) {
      h += `<div class="confirm" role="alert"><span>Ответить «${E(optLabel(q, pend))}»?</span><button type="button" class="cbtn" data-act="confirm"${busy}>Да, ответить</button><button type="button" class="cbtn no" data-act="cancel"${busy}>Нет</button></div>`;
    }
    return h + "</div>";
  };
  const questions = () => (arr(V.questions).length ? arr(V.questions).map(qHTML).join("") : `<div class="none">вопросов к вам нет</div>`);

  Object.assign(B, { ST, STM, sk, tagOf, mt, mn, tok, fmtMin, procs, proc, qOf, lamp, forTxt, flow, whoOn, tm, sdet, waitTxt, pinfo, mProg, seg, bar, meter, groups, stepWaves, hasParallel, counts, headline, progress, legend, legendPip, feedWho, qHTML, questions });
  B.view = () => V;

  // ---------- ответ ----------
  const toast = (text, err) => {
    const t = document.getElementById("toast"); t.textContent = text; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 4500);
  };
  async function send(qid, key) {
    const q = qOf(qid); const label = q ? optLabel(q, key) : key;
    ui.busy = qid; render();
    try {
      const res = await fetch("answer", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: qid, key }) });
      const j = await res.json().catch(() => ({}));
      if (res.ok && j.ok) { ui.answered[qid] = j.label || label; toast("Ответ записан"); }
      else if (res.status === 409) { ui.answered[qid] = label; toast("Ответ на этот вопрос уже принят"); }
      else toast("Ответ не записан: " + (j.error || "ошибка " + res.status), true);
    } catch (e) { toast("Ответ не записан: нет связи с сервером", true); }
    ui.busy = null; ui.pending = null; render(); poll();
  }

  // ---------- отрисовка ----------
  const actKey = (n) => (n && n.dataset && n.dataset.act ? n.dataset.act + "|" + (n.dataset.q || n.dataset.id || "") + "|" + (n.dataset.k || "") : null);
  function render() {
    if (!loaded) return;
    const was = actKey(document.activeElement);
    view.innerHTML = cfg.render(V, ui);
    let target = null;
    if (ui.focus) { target = view.querySelector(ui.focus); ui.focus = null; }
    else if (was) target = [...view.querySelectorAll("[data-act]")].find((n) => actKey(n) === was);
    if (target) target.focus({ preventScroll: true });
    const n = arr(V.questions).filter((q) => !ui.answered[q.id] && !q.web_answer).length;
    if (cfg.after) cfg.after();
    document.title = (n ? "(" + n + ") " : "") + cfg.title;
    document.querySelector(".mon").style.setProperty("--rollh", Math.max(600, Math.round(view.getBoundingClientRect().height * 1)));
  }
  const defaultOpen = () => {
    const order = ["wait", "bad", "repair", "review", "run"];
    for (const s of order) { const p = procs().find((x) => x.state === s); if (p) return p.id; }
    return procs().length ? procs()[0].id : null;
  };
  B.defaultOpen = defaultOpen;

  const fmtAge = (s) => (s < 90 ? Math.round(s) + " с" : fmtMin(s / 60));
  function paintLink() {
    const banner = document.getElementById("banner"), lnk = document.getElementById("lnk"), stt = document.getElementById("stt"), led = document.querySelector(".led");
    let msg = null, short = "связь есть", ok = true;
    if (!loaded) { short = netFail ? "НЕТ СВЯЗИ" : "загрузка"; ok = false; if (netFail) msg = ["НЕТ СВЯЗИ", "сервер табло не отвечает, сводки ещё нет"]; }
    else {
      const age = ageAt + (Date.now() - fetchedAt) / 1000, tick = Number(V.tick_s) || 5, limit = 2 * tick + 5;
      if (netFail) { short = "НЕТ СВЯЗИ"; ok = false; msg = ["НЕТ СВЯЗИ", "сервер табло не отвечает, показана последняя сводка"]; }
      else if (age > limit) { short = "СВОДКА УСТАРЕЛА"; ok = false; msg = ["СВОДКА УСТАРЕЛА " + fmtAge(age), "ПК не передаёт данные, показана последняя сводка"]; }
    }
    banner.hidden = !msg;
    if (msg) banner.innerHTML = `<span>${E(msg[0])}</span><span>${E(msg[1])}</span>`;
    lnk.textContent = short;
    if (loaded) stt.textContent = "сводка " + (V.time || "--:--");
    led.classList.toggle("lit", ok);
  }

  async function poll() {
    clearTimeout(timer);
    const ctl = new AbortController(), to = setTimeout(() => ctl.abort(), 8000);
    try {
      const res = await fetch("status.json", { cache: "no-store", signal: ctl.signal });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const d = await res.json();
      if (!d || !d.view2) throw new Error("нет view2");
      V = d.view2; netFail = false; ageAt = Number(d.age_s) || 0; fetchedAt = Date.now();
      if (!loaded) { loaded = true; const id = defaultOpen(); if (id) { ui.open.add(id); ui.sel = id; } lastJson = ""; }
      const copy = Object.assign({}, V); delete copy.built_ts;
      const js = JSON.stringify(copy);
      if (js !== lastJson) { lastJson = js; render(); }   // без лишней перерисовки: нажатие и фокус не пропадают
    } catch (e) { netFail = true; }
    finally { clearTimeout(to); paintLink(); schedule(PERIOD); }
  }
  const schedule = (ms) => { clearTimeout(timer); if (!document.hidden) timer = setTimeout(poll, ms); };

  // ---------- запуск ----------
  B.start = function (c) {
    cfg = c;
    const PH = { green: ["P1", "#33ff66", "Зелёный люминофор"], amber: ["P3", "#ffb000", "Янтарный люминофор"], ice: ["Лёд", "#9fe7ff", "Ледяной люминофор"] };
    try { const s = localStorage.getItem("board-ph-" + c.id); if (PH[s]) ph = s; else ph = c.ph; } catch (e) { ph = c.ph; }
    document.body.insertAdjacentHTML("afterbegin",
      `<div class="mon ${E(c.cls)}" data-ph="${ph}">
        <div class="screen"><div class="crt">
          <div class="banner" id="banner" role="alert" hidden></div>
          <div class="term"><div class="inner" id="view"><div class="dim">Загружаю сводку…</div></div></div>
          <div class="status"><span class="tg">ТАБЛО</span><span class="wide">${E(c.path)}</span><span class="fill"></span><span class="wide" id="phl"></span><span id="lnk">загрузка</span><span class="wide" id="stt"></span></div>
        </div><i class="ov grain"></i><i class="ov scan"></i><i class="ov roll"></i>${c.extra || ""}<i class="ov vignette"></i><i class="ov glass"></i></div>
        <div class="strip"><div class="brand"><i class="led"></i><span class="brand-name">Alpha</span><span class="brand-model">${E(c.model)}</span></div><span class="fill"></span>
          <div class="dials" role="group" aria-label="Люминофор"><span class="dial-label">Люминофор</span>${Object.keys(PH).map((k) => `<button type="button" class="key" data-phk="${k}" aria-pressed="${k === ph}" aria-label="${PH[k][2]}" style="--c:${PH[k][1]}"><i class="lamp-k"></i>${PH[k][0]}</button>`).join("")}</div></div></div>
      <div class="toast" id="toast" hidden role="status"></div>`);
    view = document.getElementById("view");
    const mon = document.querySelector(".mon");
    const setPh = (k) => { ph = k; mon.dataset.ph = k; document.getElementById("phl").textContent = "[" + PH[k][0] + "]"; mon.querySelectorAll("[data-phk]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.phk === k))); try { localStorage.setItem("board-ph-" + c.id, k); } catch (e) { /* нет хранилища — не страшно */ } };
    setPh(ph);
    mon.querySelectorAll("[data-phk]").forEach((b) => b.addEventListener("click", () => setPh(b.dataset.phk)));
    view.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]"); if (!b || b.disabled) return;
      const a = b.dataset.act;
      if (a === "pick") { ui.pending = { q: b.dataset.q, k: b.dataset.k }; ui.focus = '[data-act="confirm"]'; render(); }
      else if (a === "cancel") { ui.pending = null; render(); }
      else if (a === "confirm") { if (ui.pending) send(ui.pending.q, ui.pending.k); }
      else if (a === "ok") send(b.dataset.q, b.dataset.k);
      else if (a === "toggle") { const id = b.dataset.id; if (ui.open.has(id)) ui.open.delete(id); else ui.open.add(id); render(); }
      else if (a === "select") { ui.sel = b.dataset.id; render(); }
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); else clearTimeout(timer); });
    setInterval(paintLink, 1000);
    paintLink();
    poll();
  };
})();
