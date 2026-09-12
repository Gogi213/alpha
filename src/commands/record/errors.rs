//! Ошибки записи: `RecordError` — один тип на весь `record` (и сбой
//! ввода-вывода, и отторгнутое событие), `StepViolation` — то, что
//! возвращает горячий детектор до упаковки в него. Отдельно от рекордера,
//! чтобы `gaps`/`paths`/`steps` зависели от типа ошибки, а не от `Recorder`.

use std::io;

// ---------------------------------------------------------------------------
// Ошибки. Один тип на весь модуль: и сбой ввода-вывода, и отторгнутое событие.
// ---------------------------------------------------------------------------

/// Отказ рекордера. Ни один вариант не паникует: процесс рассчитан на недели
/// без присмотра, и вырожденный вход обязан вернуться ошибкой с причиной.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecordError {
    /// Файл не открылся / не записался / не закрылся.
    Io(String),
    /// `gaps.csv` / `instruments.csv` не разобрались как CSV.
    Csv(String),
    /// `instruments.csv`: нет файла, нет символа, не число, неположительный шаг.
    Steps(String),
    /// Шаги из кода/REST, а не из файла: нулевой или отрицательный масштаб —
    /// дельты в тиках с таким масштабом молча неверны, поэтому отказ, а не
    /// запись (та же дисциплина, что `validate_header` в `binlog`, но здесь
    /// аргумент приходит из кода вызывающего, а не с диска — см. её doc).
    BadSteps { tick_e9: i64, step_e9: i64 },
    /// Кадр не записался / не сжался.
    Binlog(String),
    /// Живое событие до первого снапшота файла. Ошибка программирования
    /// вызывающего (порядок «снапшот первым» — контракт `Recorder`), а не
    /// порча данных: событие отбрасывается loudly, файл остаётся валидным
    /// (заголовок + ноль кадров), и следующий читатель скажет `MissingSnapshot`,
    /// а не прочитает обрезанные сутки как полные.
    NoSnapshot,
    /// Горячий детектор: цена не кратна сохранённому тику или размер — шагу.
    /// Первое же затронутое событие; ни книга, ни файл не тронуты.
    Step {
        price_e9: i64,
        qty_e9: i64,
        tick_e9: i64,
        step_e9: i64,
    },
    /// Разрыв `u` последовательности Bybit. Книга больше не доверена;
    /// соединение уже шлёт ресинк-подписку само (`bybit::conn`), рекордер
    /// только фиксирует строку в `gaps.csv` и ждёт свежий снапшот.
    SequenceGap { expected: u64, got: u64 },
    /// Книга пересеклась после применения. Та же реакция, что на разрыв:
    /// строка в `gaps.csv`, ожидание снапшота, без ротации файла.
    Crossed {
        best_bid_tick: i64,
        best_ask_tick: i64,
    },
    /// Исчерпаны номера частей суток. Практически недостижимо (часть — это
    /// ротация по смене шагов внутри одних суток), но молча перезаписать
    /// часть 1 было бы потерей данных, поэтому явная ошибка.
    TooManyParts { day: String },
    /// Строка суток — не `YYYY-MM-DD`.
    BadDay { day: String },
    /// Метка времени вне диапазона календаря при форматировании дня/`ts_utc`.
    BadTimestamp { ts_ns: i64 },
}

impl std::fmt::Display for RecordError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RecordError::Io(e) => write!(f, "ввод-вывод: {e}"),
            RecordError::Csv(e) => write!(f, "CSV: {e}"),
            RecordError::Steps(e) => write!(f, "шаги инструмента: {e}"),
            RecordError::BadSteps { tick_e9, step_e9 } => {
                write!(
                    f,
                    "шаги неположительны: tick_e9={tick_e9}, step_e9={step_e9}"
                )
            }
            RecordError::Binlog(e) => write!(f, "бинлог: {e}"),
            RecordError::NoSnapshot => {
                write!(f, "живое событие до первого снапшота файла")
            }
            RecordError::Step {
                price_e9,
                qty_e9,
                tick_e9,
                step_e9,
            } => write!(
                f,
                "цена {price_e9} не на тике {tick_e9} или размер {qty_e9} не на шаге {step_e9}"
            ),
            RecordError::SequenceGap { expected, got } => {
                write!(f, "разрыв u: ждали {expected}, пришло {got}")
            }
            RecordError::Crossed {
                best_bid_tick,
                best_ask_tick,
            } => write!(
                f,
                "книга пересеклась: бид {best_bid_tick} >= аск {best_ask_tick}"
            ),
            RecordError::TooManyParts { day } => {
                write!(f, "исчерпаны номера частей суток {day}")
            }
            RecordError::BadDay { day } => {
                write!(f, "сутки не разобрались как YYYY-MM-DD: {day}")
            }
            RecordError::BadTimestamp { ts_ns } => {
                write!(f, "метка {ts_ns} нс вне диапазона календаря")
            }
        }
    }
}

impl std::error::Error for RecordError {}

impl From<io::Error> for RecordError {
    fn from(e: io::Error) -> Self {
        RecordError::Io(e.to_string())
    }
}

impl From<csv::Error> for RecordError {
    fn from(e: csv::Error) -> Self {
        RecordError::Csv(e.to_string())
    }
}

impl From<crate::binlog::BinlogError> for RecordError {
    fn from(e: crate::binlog::BinlogError) -> Self {
        RecordError::Binlog(e.to_string())
    }
}

/// Нарушение шагов одной пары цена/размер — то, что возвращает горячий
/// детектор до упаковки в `RecordError`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StepViolation {
    pub price_e9: i64,
    pub qty_e9: i64,
    pub tick_e9: i64,
    pub step_e9: i64,
}

impl From<StepViolation> for RecordError {
    fn from(v: StepViolation) -> Self {
        RecordError::Step {
            price_e9: v.price_e9,
            qty_e9: v.qty_e9,
            tick_e9: v.tick_e9,
            step_e9: v.step_e9,
        }
    }
}
