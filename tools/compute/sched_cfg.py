"""С-57 (TK-152, КТ-9): ВСЯ настраиваемая конфигурация планировщика alsched в одном месте.
Значение = умолчание в этом файле; переопределение — переменная окружения SCHED_* (как было). Выкладка — sched-deploy.sh вместе с alsched.py.
Не конфигурация, а интерфейс среды (остаются в своих модулях): GUARD_* — параметры шага guard.py, REG_*/SCHED_MANIFEST* — manifest.py."""
import os

DIR = os.environ.get("SCHED_DIR", "/data/sched")
NCPU, MEM_GB, TICK = 16, 56, int(os.environ.get("SCHED_TICK", "5"))
DEAD_S = int(os.environ.get("SCHED_DEAD_S", str(max(60, 10 * TICK))))
PREEMPT_S = int(os.environ.get("SCHED_PREEMPT_S", "600"))   # резерв не стартовал за это время — вытеснение заморозкой
DISK_SLOTS = int(os.environ.get("SCHED_DISK_SLOTS", "16"))   # заданий на диск; 16 = калибровка R1 06.10 (tk071-calib: P=16 на одном HDD, 51 ед/мин, 18 МБ/с, iowait 2 %, ЦП 92 % — упор в ЦП, не в диск); 0 = без лимита
FREEZE_PAT = (os.environ["SCHED_PAT"].split(",") if os.environ.get("SCHED_PAT")   # SCHED_PAT — только для smoke
              else ["tk0*", "t4*", "t5*", "run-*", "tk048-*"])   # как benchrun2: всё, кроме alpha-*
LEGACY_PAT = FREEZE_PAT
PACK = os.environ.get("SCHED_PACK", "claim")   # claim (как было) | fact: пускать из очереди по факту ЦП/памяти, заявка --cores — нижняя оценка (TK-071 v2, п.1)
FACT_BUSY = float(os.environ.get("SCHED_FACT_BUSY", "0.90"))      # старт сверх заявленных ядер, пока загрузка ЦП (EWMA 60 с) ниже этого
FACT_SETTLE_S = int(os.environ.get("SCHED_FACT_SETTLE_S", "30"))  # между стартами по факту: новая задача набирает ЦП не сразу
PROD_CAP_ISO = int(os.environ.get("SCHED_PROD_CAP_ISO", "8"))      # В-189 п.4 (Судья 09.10): заявленных ядер prod на время изолированного окна не больше N; сверх — заморозка новейших до конца окна
HOT_BUSY = 0.95                                                    # загрузка ЦП (EWMA 60 с): host 1,0 — цель, не перегруз; перегруз = загрузка высокая И конкуренция за ЦП (PSI)
PSI_CPU = float(os.environ.get("SCHED_PSI_CPU", "25"))             # PSI cpu some avg10, %: ниже — ядра заняты, но никто не ждёт; ВРЕМЕННО, значение подтвердить Судье по замеру синтетики
MEM_RAMP_S = int(os.environ.get("SCHED_MEM_RAMP_S", "180"))      # возраст, до которого задание «добирает» заявленную память
FACT_MEM_GAP_GB = float(os.environ.get("SCHED_FACT_MEM_GAP_GB", "2"))   # запас MemAvailable сверх заявки и недобранного идущими
