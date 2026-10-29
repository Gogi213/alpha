"""TK-454: разбор выгрузки Telegram-канала SANCHO D.T. -> csv постов (id, время GMT+04, текст, фото, #-метки).
Запуск: python -I tools/sancho_posts_parse.py <каталог выгрузки> <выходной csv>. Время — из title (GMT+04:00 в самой выгрузке)."""
import csv, html, re, sys, os
src, out = sys.argv[1], sys.argv[2]
rows = []
for fn in sorted(os.listdir(src)):
    if not re.fullmatch(r"messages\d*\.html", fn):
        continue
    t = open(os.path.join(src, fn), encoding="utf-8").read()
    parts = re.split(r'<div class="message (?:default|service)[^"]*" id="message(-?\d+)">', t)
    for i in range(1, len(parts), 2):
        mid, body = parts[i], parts[i + 1]
        m = re.search(r'class="pull_right date details" title="([^"]+)"', body)
        tm = re.search(r'class="text">\s*(.*?)\s*</div>', body, re.S)
        raw = tm.group(1) if tm else ""
        txt = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", raw))).strip()
        photos = re.findall(r'class="photo_wrap[^"]*"\s+href="(photos/[^"]+)"', body)
        reply = re.search(r'message(\d+)"', body[:0])  # reply-ссылки ниже
        rep = re.search(r'In reply to[^<]*<a href="#go_to_message(\d+)"', body)
        tags = sorted(set(re.findall(r"#(\w+)", txt)))
        rows.append([fn, mid, m.group(1) if m else "", rep.group(1) if rep else "", "|".join(tags), "|".join(photos), txt.replace("\r", "")])
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["file", "id", "time_gmt4", "reply_to", "tags", "photos", "text"])
    w.writerows(rows)
print(len(rows), "posts")
