//! Турбо-ядро коллектора рынка по WebSocket (G-022, В-148): приём без обратного давления и подвисаний, формат файлов `docs/collector-ws-format.md`.
//!
//! Карта модулей (зависимости сверху вниз):
//!
//! ```text
//!   scan, codec        разбор плоского JSON без аллокаций; формат записи (Encoder/Decoder)
//!   exchange           контракт адаптера биржи; binance, bybit — адреса, подписка, пул, разбор кадра
//!   metrics, fresh     гистограммы лагов, глубины очередей, свежесть записей
//!   ring               очередь приёма → писатель (ограниченная, с глубиной и счётом ожиданий)
//!   writer, sink       писатель шарда (разбор, дубли, строки) → поток диска (zstd, файл суток, `.ok`)
//!   net, budget, path  connect с RTT, TLS, WebSocket; бюджет подключений; качество пути
//!   session, shard     одно соединение (поток ОС); линии, замена пути, смена входа, надзор
//!   health, runner     health.json; процесс, сигналы, остановка
//! ```
//!
//! Поток данных: сокет --кадр--> `session` (метка приёма, счётчики) --`ring`--> `writer` (`Parser` адаптера, дубли по ключу
//! u/a/E, `Encoder`) --блок--> `sink` (zstd, файл) ; метрики всех стадий — в `ShardMetrics` → `health`.

pub mod bench;
pub mod binance;
pub mod budget;
pub mod bybit;
pub mod cli;
pub mod codec;
pub mod events;
pub mod exchange;
pub mod fresh;
pub mod health;
pub mod metrics;
pub mod net;
pub mod path;
pub mod pool;
pub mod ring;
pub mod runner;
pub mod scan;
pub mod session;
pub mod shard;
pub mod sink;
pub mod synth;
pub mod writer;

#[cfg(test)]
mod tests;
