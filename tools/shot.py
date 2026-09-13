#!/usr/bin/env python3
"""Скриншот дашборда и проверка состояния плиток через Playwright.

    python tools/shot.py http://127.0.0.1:8765/index.html .tmp-shot/grid.png
    python tools/shot.py <url> <png> --width 1920 --height 1080 --wait 60

Печатает не только картинку, но и то, чего на картинке не видно: сколько
плиток в сетке, сколько графиков загрузилось, у каких монет ошибка и что
сказала консоль страницы. Только чтение: ни сервер, ни файлы дашборда
скрипт не трогает.

Ходить в браузер нужно вне песочницы DSH: Playwright поднимает драйвер
через именованные каналы, а они в песочнице запрещены.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from playwright.sync_api import sync_playwright

# Состояние плиток на странице: сколько всего, сколько загрузилось, у кого
# ошибка и сколько полосок/касаний в каждом файле. `CH` — карта состояния
# страницы (`dashboard_page.html`), объявлена в глобальной лексической
# области, поэтому из `evaluate` видна.
STATE_JS = """() => {
  const out = {tiles: 0, loaded: 0, loading: 0, err: [], bars: [], touches: [], draw: []};
  if (typeof CH === 'undefined') return out;
  out.tiles = CH.size;
  for (const [sym, st] of CH) {
    if (st.loaded) {
      out.loaded++;
      out.bars.push(sym + '=' + st.n);
      out.touches.push(sym + '=' + st.k);
      const d = st.draw;
      if (d) out.draw.push(sym + ': окон ' + Math.round(d.span/60000) + ' мин, полосок ' + d.bars +
        (d.capped ? ' из ' + d.inWin + ' (потолок)' : '') + ', стен ' + d.walls +
        ', касаний ' + d.touches + (d.tcapped ? ' из ' + d.tInWin + ' (потолок)' : '') +
        (d.clipped ? ', за ценой ' + d.clipped : '') +
        (d.profN ? ', живых в кадре ' + d.profN : ''));
    } else if (st.loading) {
      out.loading++;
    } else if (st.error) {
      out.err.push(sym + ': ' + st.error);
    }
  }
  return out;
}"""


def main() -> int:
    # Подсказки страницы содержат «×» и другие не-cp1251 символы: печатаем
    # в UTF-8, иначе вывод падает на консоли Windows.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--wait", type=float, default=60.0, help="секунд ждать загрузку плиток")
    ap.add_argument("--solo", help="развернуть одну монету на весь экран (например SOLUSDT)")
    ap.add_argument("--hover", help="навести курсор: 'fx,fy' — доли ширины/высоты плитки, и напечатать подсказку")
    ap.add_argument(
        "--text-only",
        action="store_true",
        help="не делать скриншот: только текстовый пробник рисунка (дешевле в разы)",
    )
    a = ap.parse_args()

    console: list[str] = []
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--hide-scrollbars"])
        page = browser.new_page(
            viewport={"width": a.width, "height": a.height}, device_scale_factor=1
        )
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.url, wait_until="load", timeout=60_000)
        state = None
        deadline = time.time() + a.wait
        while time.time() < deadline:
            try:
                state = page.evaluate(STATE_JS)
            except Exception as e:  # страница перезагрузилась — не повод падать
                errors.append(f"evaluate: {e}")
                state = None
            if state and state["tiles"] and state["loaded"] + len(state["err"]) >= state["tiles"]:
                break
            page.wait_for_timeout(1000)
        if state is None:
            state = {}
        tip = None
        if a.solo:
            page.evaluate("sym => { SOLO = sym; render(); }", a.solo)
            page.wait_for_timeout(2500)
        if a.hover:
            fx, fy = (float(v) for v in a.hover.split(","))
            box = page.locator("canvas").first.bounding_box()
            page.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
            page.wait_for_timeout(300)
            tip = page.evaluate(
                "() => { const t=document.querySelector('.tip'); return t && !t.hidden ? t.textContent : null; }"
            )
        page.screenshot(path=a.out) if not a.text_only else None
        browser.close()

    print(
        json.dumps(
            {
                "url": a.url,
                "tiles": state.get("tiles"),
                "loaded": state.get("loaded"),
                "loading": state.get("loading"),
                "ошибки плиток": state.get("err"),
                "полосок": state.get("bars"),
                "касаний": state.get("touches"),
                "рисунок": state.get("draw"),
                "ошибки страницы": errors,
                "консоль": console[-12:],
                "подсказка при наведении": tip,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    bad = bool(errors) or bool(state.get("err"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
