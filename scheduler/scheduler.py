import os
import sys
import subprocess
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta

SCHEDULER_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCHEDULER_DIR)
MAIN_PY = os.path.join(BASE_DIR, "main.py")
DB_PATH = os.path.join(SCHEDULER_DIR, "scheduler.db")
STATUS_PATH = os.path.join(SCHEDULER_DIR, "status.txt")
LAST_CRAWL_PATH = os.path.join(SCHEDULER_DIR, "last_crawl_at.txt")

KST_OFFSET = timedelta(hours=9)  # 한국은 DST가 없어 고정 오프셋으로 계산해도 안전


def log_status(message: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATUS_PATH, "a", encoding="utf-8") as f:
        f.write(f"{now} - {message}\n")

def heartbeat():
    log_status("working: true")

def crawlrun():
    log_status("크롤링 시작")
    result = subprocess.run([sys.executable, MAIN_PY])
    if result.returncode == 0:
        log_status("크롤링 성공")
        with open(LAST_CRAWL_PATH, "w", encoding="utf-8") as f:
            f.write(datetime.utcnow().isoformat())
    else:
        log_status(f"크롤링 실패 (code={result.returncode})")


def _last_crawl_boundary_utc(now_utc: datetime) -> datetime:
    """지금 시각 기준, 가장 최근에 지나간 KST 14:00 크론 시각(UTC로 환산)."""
    now_kst = now_utc + KST_OFFSET
    today_14_kst = now_kst.replace(hour=14, minute=0, second=0, microsecond=0)
    boundary_kst = today_14_kst if now_kst >= today_14_kst else today_14_kst - timedelta(days=1)
    return boundary_kst - KST_OFFSET


def needs_catchup() -> bool:
    """컨테이너가 꺼져 있어서 가장 최근 KST 14:00 크롤링 슬롯을 놓쳤는지 확인."""
    if not os.path.exists(LAST_CRAWL_PATH):
        return True
    with open(LAST_CRAWL_PATH, "r", encoding="utf-8") as f:
        try:
            last_crawl_utc = datetime.fromisoformat(f.read().strip())
        except ValueError:
            return True
    return last_crawl_utc < _last_crawl_boundary_utc(datetime.utcnow())

jobstores = {
    'default' : SQLAlchemyJobStore(url=f'sqlite:///{DB_PATH}')
}

scheduler = BackgroundScheduler(jobstores=jobstores,timezone='Asia/Seoul')

scheduler.add_job(
    crawlrun,
    CronTrigger(hour=14, minute=0, timezone='Asia/Seoul'),
    id='crawl_job',
    coalesce=True,
    misfire_grace_time=3600,
    replace_existing=True,
)

scheduler.add_job(
    heartbeat,
'interval',
    minutes=10,
    id='heartbeat_job',
    coalesce=True,
    misfire_grace_time=60,
    replace_existing=True,
)

print("스케줄러 시작!")

try:
    scheduler.start()
    log_status("스케줄러 정상 시작")
    if needs_catchup():
        log_status("캐치업: 마지막 크롤링 슬롯을 놓쳐 즉시 1회 실행")
        threading.Thread(target=crawlrun, daemon=True).start()
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    scheduler.shutdown()
except Exception as e:
    log_status(f"스케줄러 시작 실패: {e}")
    raise