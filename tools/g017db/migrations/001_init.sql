-- G-017: БД признаков и гипотез пампов (TK-449). Отдельная от Летописи (tools/registry).
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
-- (7) кто положил строки: загрузчик × файл-источник
CREATE TABLE runs (
  run_id INTEGER PRIMARY KEY, ticket TEXT NOT NULL, loader TEXT NOT NULL, source_file TEXT NOT NULL,
  source_md5 TEXT NOT NULL, rows INTEGER NOT NULL, loaded_at TEXT NOT NULL, UNIQUE (loader, source_file));
-- (1) каталог признаков
CREATE TABLE features (
  feature TEXT PRIMARY KEY, grp TEXT NOT NULL, rule_text TEXT NOT NULL, code_file TEXT NOT NULL, code_md5 TEXT NOT NULL,
  horizon TEXT NOT NULL, source TEXT NOT NULL, version TEXT NOT NULL, ticket TEXT NOT NULL, run_id INTEGER REFERENCES runs);
-- (2) признак × месяц: покрытие и «нет данных»
CREATE TABLE feature_coverage (
  feature TEXT NOT NULL REFERENCES features, month TEXT NOT NULL, coverage REAL, status TEXT NOT NULL CHECK (status IN ('ok','нет данных')),
  note TEXT NOT NULL, PRIMARY KEY (feature, month));
-- (3) польза признака: месяц × D × дециль/корзина × направление
CREATE TABLE feature_lift (
  id INTEGER PRIMARY KEY, month TEXT NOT NULL, feature TEXT NOT NULL, d INTEGER NOT NULL, direction TEXT NOT NULL,
  bucket INTEGER NOT NULL, lo REAL, hi REAL, cells INTEGER, hits INTEGER, coverage REAL, base REAL, lift REAL, lift_lo REAL, lift_hi REAL,
  base_present REAL, lift_base_present REAL, source_file TEXT NOT NULL, src_row INTEGER NOT NULL, run_id INTEGER NOT NULL REFERENCES runs,
  UNIQUE (month, feature, d, direction, bucket, source_file));
CREATE INDEX feature_lift_f ON feature_lift (feature, d, month);
-- (4) реестр гипотез (источник — docs/research/g017-hypotheses.csv)
CREATE TABLE hypotheses (
  id TEXT PRIMARY KEY, date TEXT, ticket TEXT, question TEXT, events TEXT, features TEXT, kind_r TEXT, family_k TEXT,
  accum_k TEXT, month_confirmed TEXT, status TEXT, doc TEXT, run_id INTEGER NOT NULL REFERENCES runs);
-- (5) результаты сигналов: TK-442 (vr&qr), TK-443 (V1…V11, q15…), TK-446 (кривые A1–A4)
CREATE TABLE signals (
  id INTEGER PRIMARY KEY, ticket TEXT NOT NULL, source_file TEXT NOT NULL, src_row INTEGER NOT NULL, axis TEXT, signal TEXT NOT NULL,
  threshold REAL, month TEXT NOT NULL, d INTEGER NOT NULL, q REAL, direction TEXT NOT NULL, x_q99 REAL, cells INTEGER, cells_move INTEGER,
  base REAL, fires INTEGER, hits INTEGER, precision REAL, recall REAL, lift REAL, lift_lo REAL, lift_hi REAL, prec_lo REAL, prec_hi REAL,
  fires_per_day_coin REAL, coin_days_hit INTEGER, base_sub REAL, lift_sub REAL, run_id INTEGER NOT NULL REFERENCES runs);
CREATE INDEX signals_s ON signals (ticket, signal, d, month);
-- (5б) группы фаз/дампа после пампа (TK-443 v10)
CREATE TABLE signal_groups (
  id INTEGER PRIMARY KEY, ticket TEXT NOT NULL, source_file TEXT NOT NULL, src_row INTEGER NOT NULL, month TEXT NOT NULL, d TEXT NOT NULL, -- D мин или 'все'
  grp TEXT NOT NULL, cond_events INTEGER, in_group INTEGER, base_dump REAL, share_dump REAL, lift REAL, lift_lo REAL, lift_hi REAL,
  clusters INTEGER, recall REAL, fires_per_day_coin REAL, run_id INTEGER NOT NULL REFERENCES runs);
-- (6) ссылки на файлы событий (не копии)
CREATE TABLE events_ref (
  path TEXT PRIMARY KEY, kind TEXT NOT NULL, month TEXT, rows INTEGER NOT NULL, md5 TEXT NOT NULL, ticket TEXT NOT NULL, run_id INTEGER NOT NULL REFERENCES runs);
