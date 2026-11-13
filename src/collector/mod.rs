//! Турбо-ядро коллектора рынка по WebSocket (G-022, В-148): приём без обратного давления и подвисаний, формат файлов `docs/collector-ws-format.md`.
//!
//! Карта модулей (зависимости сверху вниз):
//!
//! ```text
//!   scan, codec        разбор плоского JSON без аллокаций; формат записи (Encoder/Decoder)
//!   exchange           контракт адаптера биржи; binance, bybit, okx, bingx — адреса, подписка, пул, разбор кадра
//!   gzip               разжатие кадров gzip (BingX) в потоке приёма, до метки лага и разбора
//!   metrics, fresh     гистограммы лагов, глубины очередей, свежесть записей
//!   ring               очередь приёма → писатель (ограниченная, с глубиной и счётом ожиданий)
//!   writer, sink       писатель шарда (разбор, дубли, строки) → поток диска (zstd, файл суток, `.ok`)
//!   net, budget, path  connect с RTT, TLS, WebSocket; бюджет подключений; качество пути
//!   session, shard     одно соединение (поток ОС); линии, замена пути, смена входа, надзор
//!   health, series     health.json; поминутный ряд на диске (`series-minutes.jsonl`)
//!   runner             процесс, сигналы, остановка
//! ```
//!
//! Поток данных: сокет --кадр--> `session` (метка приёма, счётчики) --`ring`--> `writer` (`Parser` адаптера, дубли по ключу
//! u/a/E, `Encoder`) --блок--> `sink` (zstd, файл) ; метрики всех стадий — в `ShardMetrics` → `health`.

pub mod bench;
pub mod binance;
pub mod bingx;
pub mod budget;
pub mod bybit;
pub mod cli;
pub mod codec;
pub mod events;
pub mod exchange;
pub mod fresh;
pub mod gzip;
pub mod hash;
pub mod health;
pub mod ladder;
pub mod metrics;
pub mod net;
pub mod okx;
pub mod path;
pub mod pool;
pub mod replay;
pub mod ring;
pub mod runner;
pub mod scan;
pub mod series;
pub mod session;
pub mod shard;
pub mod sink;
pub mod synth;
pub mod writer;

/// Версия сборки: `ALPHA_COMMIT` на этапе сборки (хеш коммита), иначе `unknown`.
pub const VERSION: &str = match option_env!("ALPHA_COMMIT") {
    Some(v) => v,
    None => "unknown",
};

#[cfg(test)]
mod tests;
