"""Макет режима «мейкер + хедж» вкладки «Арбитраж»: та же страница (стили page.html), таблица в новом режиме с настоящими числами счёта.

Запуск: python build_maker_mockup.py <summary.json> <cfg> <page.html> <выход.html> [строк]
cfg — ключ настройки из summary.json, например "(0, 300, 'tt')". Макет статичный (для показа и снимка), не боевая страница.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

NAMES = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "bingx": "BingX"}
MINUS = "−"


def num(v, d=1, plus=False):
    if v is None:
        return "—"
    s = f"{abs(v):.{d}f}".replace(".", ",")
    if round(v, d) < 0:
        return MINUS + s
    return ("+" + s) if plus and round(v, d) > 0 else s


def dur(s):
    if s is None:
        return "—"
    if s < 1:
        return f"{round(s * 1000)} мс"
    if s < 60:
        return num(s, 1) + " с"
    if s < 3600:
        return num(s / 60, 1) + " мин"
    return num(s / 3600, 1) + " ч"


def usd(v):
    return (MINUS if v < 0 else "") + f"{abs(v):.2f}".replace(".", ",")


def main(summ_p, cfg, page_p, out_p, nrows=20):
    d = json.load(open(summ_p, encoding="utf-8"))
    css = re.search(r"<style>(.*?)</style>", open(page_p, encoding="utf-8").read(), re.S).group(1)
    top = d["top"][cfg][:int(nrows)]
    t1 = datetime.fromtimestamp(d["t1"] / 1000, timezone(timedelta(hours=4)))
    cols = [("l", "монета", 9), ("l", "пара бирж<br>мейкер → хедж", 15), ("", "проскальзыв.<br>хеджа, bps", 9), ("", "входов<br>за 1 ч", 7), ("", "входов<br>за 4 ч", 7),
            ("", "входов<br>за 6 ч", 7), ("", "входов<br>за 1 сут", 8), ("", "удержание,<br>медиана", 9), ("", "после комиссий,<br>bps", 10), ("on", "$ в сутки ↓<br>(круг 100 USDT)", 11)]
    cg = "".join(f'<col style="width:{w}%">' for _, _, w in cols)
    th = "".join(f'<th class="{c}">{h}</th>' for c, h, _ in cols)
    rows = []
    for r in top:
        f = r["fills"]
        rows.append(f'<tr class="r"><td class="l c">{r["coin"]}</td><td class="l p">{NAMES[r["M"]]} → {NAMES[r["H"]]}</td>'
                    f'<td>{num(r["slip"][0], 1)}</td><td>{f[0]}</td><td>{f[1]}</td><td>{f[2]}</td><td>{f[3]}</td>'
                    f'<td>{dur(r["hold_s"][0])}</td><td>{num(r["net"][0], 1, True)}</td><td>{usd(r["usd_day"])}</td></tr>')
    m = d["model"]
    th_i, lat, var = eval(cfg)
    foot = (f"Модель: исполнение — сделка строго за ценой; хедж тейкером через {lat} мс; котировка обновляется через {m['D_MS']} мс после сдвига цены хеджа; "
            f"вход ≥ {num(m['THETAS'][th_i], 1)} bps к центру, закрытие {'мейкером' if var == 'mt' else 'тейкером'}.")
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Сбор данных — макет</title><style>{css}
.tg.ctl .mode{{margin-left:0}}</style></head><body>
<div class="top"><h1>Сбор данных</h1><nav class="tabs"><button class="on" data-t="Арбитраж">Арбитраж</button><button data-t="Здоровье">Здоровье</button></nav><div class="t">{t1:%d.%m} · {t1:%H:%M}</div></div>
<section><div class="flt tg"><button class="on" data-t="Все">Все</button><button data-t="Binance → BingX">Binance → BingX</button><button data-t="Binance → Bybit">Binance → Bybit</button><button data-t="Binance → OKX">Binance → OKX</button><button data-t="Bybit → BingX">Bybit → BingX</button><button data-t="Bybit → OKX">Bybit → OKX</button><button data-t="OKX → BingX">OKX → BingX</button></div>
<div class="ctl tg"><div class="g"><span>режим</span><button data-t="тейкер-тейкер">тейкер-тейкер</button><button class="on" data-t="мейкер + хедж">мейкер + хедж</button></div>
<div class="g"><span>счёт</span><button class="on" data-t="медиана">медиана</button><button data-t="выбросы">выбросы</button></div><div class="g"><button data-t="скрыть пустые">скрыть пустые</button><span class="cnt">пустых: 0 из {d["pairs_total"]}</span></div><span class="upd">обновлено {t1:%H:%M}</span></div>
<table class="a"><colgroup>{cg}</colgroup><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table>
<div class="pend" style="padding:14px 0 0;font-size:12px">{foot}</div></section></body></html>"""
    open(out_p, "w", encoding="utf-8").write(html)
    print(len(top))


if __name__ == "__main__":
    main(*sys.argv[1:6])
