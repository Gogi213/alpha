-- TK-210 (В-228): класс «метод» (не гипотеза) — hypotheses.ext.class='method'; Г-01/02/03 + М-04 (спека spec-m04-2026-10-12.md)
update hypotheses set ext = json_set(coalesce(ext, '{}'), '$.class', 'method') where id in ('Г-01', 'Г-02', 'Г-03');
insert or ignore into hypotheses (id, family, protocol, title, ext) values
  ('М-04', 'размер', 'П-14', 'Размер на первом подходе: потитровать снижение и увеличение позиции на approach_index == 0',
   '{"class":"method","spec":"docs/research/spec-m04-2026-10-12.md","mechanism":"метод: масштаб лота k1 на первом подходе к стене, на остальных 1","source":"В-228"}');
