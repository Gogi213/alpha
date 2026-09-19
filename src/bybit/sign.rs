//! Подпись приватных запросов Bybit, HMAC-SHA256.
//!
//! Decision 12 (`PLAN.md`): ключи — только из окружения, никогда из файла
//! или аргумента. Формат подписи v5 подтверждён официальной документацией
//! Bybit (`bybit-exchange.github.io/docs/v5/guide`, раздел Authentication)
//! и собственным демо-репозиторием биржи
//! (`bybit-exchange/api-usage-examples`, `V5_demo/api_demo/Encryption_HMAC.py`):
//! `timestamp + api_key + recv_window + body`, HMAC-SHA256, hex в нижнем
//! регистре. Числового примера с реальным секретом Bybit не публикует —
//! страница документации прямо это подтверждает (значение секрета опущено
//! «for security reasons»), и её собственное демо использует один и тот же
//! плейсхолдер `"XXXXXXXXXX"` и для `api_key`, и для `secret_key`. Поэтому
//! известный ответ в тестах ниже — не скопированный из документации вектор,
//! а построенный нами по той же формуле и сверенный независимо через Python
//! `hmac`/`hashlib` и `openssl dgst -sha256 -hmac`: ни та, ни другая
//! реализация не этот крейт, так что сверка не тавтологична.

use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

/// Имена переменных окружения — единственный источник ключей (Decision 12).
/// Константы, а не только текст внутри `from_env`: тест «зонд отказывается
/// работать без ключей» (`probe.rs`) обязан проверять отказ на этих же
/// именах, а не на переписанной вручную строке, которая могла разойтись.
pub const API_KEY_VAR: &str = "BYBIT_API_KEY";
pub const API_SECRET_VAR: &str = "BYBIT_API_SECRET";

/// Ключи приватного API. `api_secret` не отдаётся наружу ничем, кроме
/// `sign` — и оттуда выходит уже как HMAC-дайджест, необратимый к секрету,
/// а не как сам секрет.
pub struct Credentials {
    api_key: String,
    api_secret: String,
}

/// `derive(Debug)` здесь не используется намеренно: он напечатал бы оба
/// поля как есть, и `api_secret` попал бы в любой `{:?}` — в панике, в её
/// логе, в `dbg!` посреди отладки. Ручная реализация — единственный способ
/// гарантировать, что секрет не всплывёт там, где никто не собирался его
/// логировать; тест `secret_never_appears_in_debug_output` проверяет это
/// свойство явно, а не доверяет автогенерации.
impl std::fmt::Debug for Credentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Credentials")
            .field("api_key", &"<redacted>")
            .field("api_secret", &"<redacted>")
            .finish()
    }
}

/// Ошибка чтения ключей. Несёт только имя переменной, никогда значение —
// значения тут просто нет, читать было нечего.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CredentialsError {
    Missing(&'static str),
    /// `new_from_slice` вернул отказ. Для SHA256 недостижимо (ключ любой
    /// длины), но тип у конструктора `Result`, и выдумывать панику вместо
    /// варианта значило бы вернуть долг, который этот тикет гасит.
    MacUnusable,
}

impl Credentials {
    /// Единственный конструктор боевого пути: ключи только из окружения
    /// (Decision 12), пользователь выставляет их сам. Пустая строка
    /// приравнена к отсутствующей — иначе `BYBIT_API_KEY=` без значения
    /// проходит здесь и падает уже на бирже, с гораздо менее понятной
    /// ошибкой, чем эта.
    pub fn from_env() -> Result<Self, CredentialsError> {
        Ok(Self {
            api_key: read_var(API_KEY_VAR)?,
            api_secret: read_var(API_SECRET_VAR)?,
        })
    }

    /// Только для тестов: `from_env` — единственный способ получить ключи
    /// в бою, а тестам нужен предсказуемый секрет без переменных окружения
    /// вообще, иначе тест «ключей нет» и тест «ключи есть» гонялись бы по
    /// одной и той же глобальной памяти процесса. `pub(crate)`, а не
    /// приватная: тот же риск и та же нужда — у `probe.rs`, `cfg(test)`
    /// действует на весь крейт при `cargo test`, так что видимость внутри
    /// крейта здесь ничего не открывает наружу.
    #[cfg(test)]
    pub(crate) fn for_test(api_key: &str, api_secret: &str) -> Self {
        Self {
            api_key: api_key.to_string(),
            api_secret: api_secret.to_string(),
        }
    }

    /// Публичный ключ — единственное поле, уходящее в заголовок
    /// `X-BAPI-API-KEY` открытым текстом; так задумано протоколом Bybit,
    /// это не секрет в криптографическом смысле.
    pub fn api_key(&self) -> &str {
        &self.api_key
    }

    /// Подпись v5: `timestamp + api_key + recv_window + body` (см. doc
    /// модуля). HMAC-SHA256, hex в нижнем регистре — оба требования задаёт
    /// сама биржа, не наш выбор.
    pub fn sign(
        &self,
        timestamp_ms: i64,
        recv_window_ms: u32,
        body: &str,
    ) -> Result<String, CredentialsError> {
        let payload = format!("{timestamp_ms}{}{recv_window_ms}{body}", self.api_key);
        // Ключ HMAC-SHA256 годится любой длины (короткий паддится нулями до
        // блока по определению алгоритма), поэтому `new_from_slice` не
        // может отказать по значению секрета — только по причинам, которых
        // у sha256-семейства попросту нет.
        let mut mac = HmacSha256::new_from_slice(self.api_secret.as_bytes())
            .map_err(|_| CredentialsError::MacUnusable)?;
        mac.update(payload.as_bytes());
        Ok(hex_lower(&mac.finalize().into_bytes()))
    }

    /// Подпись `auth` для WebSocket (приватный стрим и WS trade): HMAC-SHA256
    /// от строки `GET/realtime{expires}` — формат задокументирован биржей
    /// (`docs/v5/ws/connect`, «Authentication»), `expires` — миллисекунды
    /// эпохи, после которых кадр не принимается.
    pub fn ws_auth_signature(&self, expires_ms: i64) -> Result<String, CredentialsError> {
        let mut mac = HmacSha256::new_from_slice(self.api_secret.as_bytes())
            .map_err(|_| CredentialsError::MacUnusable)?;
        mac.update(b"GET/realtime");
        mac.update(expires_ms.to_string().as_bytes());
        Ok(hex_lower(&mac.finalize().into_bytes()))
    }

    /// Та же подпись v5, без единой аллокации (таск 17, запрет 1 горячего
    /// пути). Не делегирует `sign` выше: там `format!` склеивает payload в
    /// одну `String`, а `hex_lower` возвращает `String` — обе аллоцируют, и
    /// именно от этого зависит `bybit::trade_ws::OrderSigner::sign_into`,
    /// раз `Credentials` — его боевая реализация (`impl OrderSigner for
    /// Credentials`, `trade_ws.rs`). Формула та же: HMAC кормится теми же
    /// четырьмя кусками по отдельности через несколько `update` — порядок
    /// байт на входе алгоритма от этого не меняется, что и доказывает
    /// `sign_into_matches_sign_for_the_same_inputs` ниже сверкой с `sign`.
    pub fn sign_into(
        &self,
        timestamp_ms: i64,
        recv_window_ms: u32,
        body: &str,
        out: &mut [u8; 64],
    ) -> Result<(), CredentialsError> {
        let mut mac = HmacSha256::new_from_slice(self.api_secret.as_bytes())
            .map_err(|_| CredentialsError::MacUnusable)?;
        let mut ts_buf = [0u8; 20];
        mac.update(write_i64(timestamp_ms, &mut ts_buf));
        mac.update(self.api_key.as_bytes());
        let mut rw_buf = [0u8; 10];
        mac.update(write_u32(recv_window_ms, &mut rw_buf));
        mac.update(body.as_bytes());
        hex_lower_into(&mac.finalize().into_bytes(), out);
        Ok(())
    }
}

fn read_var(name: &'static str) -> Result<String, CredentialsError> {
    match std::env::var(name) {
        Ok(v) if !v.is_empty() => Ok(v),
        _ => Err(CredentialsError::Missing(name)),
    }
}

// Ветвлением вместо таблицы: ниббл всегда < 16 по построению масок
// вызывающего (`b >> 4`, `b & 0x0f`), total без индексации и без паники.
fn nibble(n: u8) -> u8 {
    if n < 10 {
        b'0' + n
    } else {
        b'a' + (n - 10)
    }
}

/// Ручной hex вместо отдельного крейта: одно место использования на 32
/// байта — заводить ради него ещё одну зависимость дороже, чем эти десять
/// строк, а `Cargo.toml` в этом проходе не наш файл.
fn hex_lower(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(nibble(b >> 4) as char);
        out.push(nibble(b & 0x0f) as char);
    }
    out
}

/// `hex_lower` в предвыделенный буфер вместо `String` — единственная
/// разница с ним; `out.len() == bytes.len() * 2` для HMAC-SHA256 (32 байта
/// дайджеста → 64 hex-символа) гарантирует вызывающий типом `[u8; 64]`.
fn hex_lower_into(bytes: &[u8], out: &mut [u8; 64]) {
    debug_assert_eq!(bytes.len() * 2, out.len(), "дайджест не 32 байта");
    for (i, b) in bytes.iter().enumerate() {
        out[i * 2] = nibble(b >> 4);
        out[i * 2 + 1] = nibble(b & 0x0f);
    }
}

/// Десятичные ASCII-цифры `value` в `buf`, без аллокации; знак — только для
/// отрицательных (сюда не должны прилетать, но тип `timestamp_ms` — `i64`,
/// а не `u64`, и функция обязана быть тотальной, не паникующей на границе).
/// `i64::MIN..=i64::MAX` умещается в 20 байт буфера (19 цифр + знак).
fn write_i64(value: i64, buf: &mut [u8; 20]) -> &[u8] {
    if value == 0 {
        buf[0] = b'0';
        return &buf[..1];
    }
    let neg = value < 0;
    let mut n = value.unsigned_abs();
    let mut i = buf.len();
    while n > 0 {
        i -= 1;
        buf[i] = b'0' + (n % 10) as u8;
        n /= 10;
    }
    if neg {
        i -= 1;
        buf[i] = b'-';
    }
    &buf[i..]
}

/// Тот же приём для `recv_window_ms: u32` (10 цифр хватает на `u32::MAX`).
fn write_u32(value: u32, buf: &mut [u8; 10]) -> &[u8] {
    if value == 0 {
        buf[0] = b'0';
        return &buf[..1];
    }
    let mut n = value;
    let mut i = buf.len();
    while n > 0 {
        i -= 1;
        buf[i] = b'0' + (n % 10) as u8;
        n /= 10;
    }
    &buf[i..]
}

/// Синхронизация тестов, трогающих `BYBIT_API_KEY`/`BYBIT_API_SECRET`:
/// переменные окружения — общая память процесса, а `cargo test` гоняет
/// тесты параллельно потоками одного процесса. Без общего замка тест «ключ
/// есть» и тест «ключа нет» — из этого файла и из `probe.rs` — видят чужую
/// правку и мигают через раз.
#[cfg(test)]
static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// Общий помощник для обоих файлов, трогающих эти переменные: снимает
/// текущие значения (тест не вправе зависеть от того, что уже стоит в
/// окружении того, кто запускает `cargo test` — у живого трейдера ключи
/// вполне могут быть выставлены), очищает их на время `f`, потом
/// восстанавливает. Восстановление не переживёт `panic!` внутри `f`
/// (не оборачиваем в `catch_unwind` ради простоты) — это не проблема
/// корректности: следующий тест на этих переменных сам вызовет этот же
/// помощник и сам очистит перед собой.
#[cfg(test)]
pub(crate) fn with_cleared_env<T>(f: impl FnOnce() -> T) -> T {
    let _guard = ENV_LOCK.lock().unwrap();
    let saved = (
        std::env::var(API_KEY_VAR).ok(),
        std::env::var(API_SECRET_VAR).ok(),
    );
    std::env::remove_var(API_KEY_VAR);
    std::env::remove_var(API_SECRET_VAR);

    let result = f();

    match saved.0 {
        Some(v) => std::env::set_var(API_KEY_VAR, v),
        None => std::env::remove_var(API_KEY_VAR),
    }
    match saved.1 {
        Some(v) => std::env::set_var(API_SECRET_VAR, v),
        None => std::env::remove_var(API_SECRET_VAR),
    }
    result
}

#[cfg(test)]
mod tests;
