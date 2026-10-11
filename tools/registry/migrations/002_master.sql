-- Летопись-мастер (TK-187, В-225): слои плана — параметры, стеки, оси гипотез, правила и сито. Канон — docs/registry/{params,stacks,hyp_axes,rules,plan_cells,sieve}.jsonl.
create table params (
  name text primary key, unit text, resolution real,   -- разрешимость: меньше шага ось не дробится
  axis_type text check (axis_type in ('numeric','log','categorical','circular')),
  cfg_path text,                                       -- ключ конфига клетки cellhash: «stop», «set.min_age_s», «variant.axes.n»
  note text
);
create table stacks (stack text not null, version text not null, param text not null, value text, primary key (stack, version, param));
create table hyp_axes (
  hyp text not null, protocol text not null, version text, stack text, param text not null,
  lo text, hi text, points_f1 text, old_points text, note text,   -- points через «|»
  primary key (hyp, protocol, param)
);
create table rules (protocol text not null, version text not null, key text not null, value text, primary key (protocol, version, key));
create table plan_cells (   -- сетка фракции: что план выдал (хеш — из cellhash.cell_hash)
  protocol text not null, fraction integer not null, hyp text not null, cell_hash text not null, axes text, cfg text,
  primary key (protocol, fraction, hyp, cell_hash)
);
create table sieve (        -- ответ сита по клетке и стороне монет
  protocol text not null, fraction integer not null, hyp text not null, cell_hash text not null, side text not null,
  p real, trades integer, days integer, eligible integer, plateau integer, edge integer, rule_version text,
  primary key (protocol, fraction, hyp, cell_hash, side)
);
