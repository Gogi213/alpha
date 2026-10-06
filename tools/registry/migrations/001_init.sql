-- Реестр прогонов alpha, схема v1 (TK-068). Расширение — новой миграцией NNN_*.sql или ключами в ext/params (JSON).
create table hypotheses (
  id text primary key,            -- «Г-85», «R1-07»
  family text, protocol text, title text,
  ext text                        -- JSON: что угодно ещё
);
create table runs (
  id text primary key, ts text not null, ticket text, protocol text, kind text, what text,
  machine text, wall_s real, cpu_s real, result_path text, outcome text, judge text,
  status text, status_why text, source text, note text,
  cmdline text, config_status text,
  config text,                    -- JSON манифеста снимка: env, files{sha}, inputs{sha}, binaries{md5}, git
  ext text
);
create index runs_ts on runs(ts);
create index runs_ticket on runs(ticket);
create table run_hypotheses (run_id text not null references runs(id), hyp_id text not null, primary key (run_id, hyp_id));
create table run_data (
  run_id text primary key references runs(id),
  pool text, pool_sha text, verdict_sha text, epochs text, format text,
  period_from text, period_to text, period text, coins text, coins_sha text
);
create table run_binaries (run_id text not null references runs(id), md5 text, git_commit text, flags text);
create index run_binaries_md5 on run_binaries(md5);
create table cells (
  id text primary key,            -- sha256 канонического JSON параметров (одинаковая клетка = одна строка)
  logic_version text,             -- версия логики стратегии / семейства (расширение под R1/R2/…)
  form text, set_name text,       -- как в файле --cells: «<форма> <набор>»
  entry_bps real, stop_bps real, take_bps real, deadline_s real, wall_exit text, set_form text,
  latency_ms real, queue text, h3_mode text, sigma real, hold_step text,
  params text                     -- JSON: все остальные параметры клетки по ключам
);
create table run_cells (run_id text not null references runs(id), cell_id text not null references cells(id), hyp_id text, primary key (run_id, cell_id));
create table results (
  run_id text not null references runs(id), cell_id text not null references cells(id), month text not null,
  trades integer, pnl_usd real, kpi text, max_dd real, ext text,
  primary key (run_id, cell_id, month)
);
create table verdicts (
  id text primary key, run_id text references runs(id), hyp_id text, judge text, verdict text,
  review_path text, ts text, note text
);
