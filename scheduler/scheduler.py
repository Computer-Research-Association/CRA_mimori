import os
import sys
import subprocess
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

SCHEDULER_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCHEDULER_DIR)
MAIN_PY = os.path.join(BASE_DIR, "main.py")
DB_PATH = os.path.join(SCHEDULER_DIR, "scheduler.db")
STATUS_PATH = os.path.join(SCHEDULER_DIR, "status.txt")


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
    else:
        log_status(f"크롤링 실패 (code={result.returncode})")

jobstores = {
    'default' : SQLAlchemyJobStore(url=f'sqlite:///{DB_PATH}')
}

scheduler = BackgroundScheduler(jobstores=jobstores,timezone='Asia/Seoul')

scheduler.add_job(
    crawlrun,
    CronTrigger(hour=14, minute=0),
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
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    scheduler.shutdown()
except Exception as e:
    log_status(f"스케줄러 시작 실패: {e}")
    raise