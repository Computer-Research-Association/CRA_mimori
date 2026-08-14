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
sys.path.insert(0, BASE_DIR)
from logging_config import get_logger

logger = get_logger("scheduler")
MAIN_PY = os.path.join(BASE_DIR, "main.py")
PREPROCESS_EMBED_PY = os.path.join(BASE_DIR, "preprocess_embed_main.py")
CRAWL_REQUEST_WORKER_PY = os.path.join(BASE_DIR, "scripts", "crawl_request_worker.py")
LLM_REQUEST_WORKER_PY = os.path.join(BASE_DIR, "scripts", "llm_request_worker.py")
DB_PATH = os.path.join(SCHEDULER_DIR, "scheduler.db")
STATUS_PATH = os.path.join(SCHEDULER_DIR, "status.txt")
LAST_CRAWL_PATH = os.path.join(SCHEDULER_DIR, "last_crawl_at.txt")
LAST_EMBED_PATH = os.path.join(SCHEDULER_DIR, "last_embed_at.txt")

KST_OFFSET = timedelta(hours=9)  # 한국은 DST가 없어 고정 오프셋으로 계산해도 안전


def log_status(message: str):
    """status.txt 파일에 기록 + logger(CloudWatch 포함)로 전송."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATUS_PATH, "a", encoding="utf-8") as f:
        f.write(f"{now} - {message}\n")
    logger.info(message)

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
        logger.error("크롤링 실패 (code=%d)", result.returncode)
        log_status(f"크롤링 실패 (code={result.returncode})")


def preprocess_embed_run():
    """전처리+임베딩을 별도 프로세스로 실행.

    크롤(하루 1회, KST 02:00)과 별개로 KST 04:30에 1회 돈다 — GPU 없는 CPU 인스턴스에서 임베딩을
    너무 자주 돌리면 CPU 크레딧 소진으로 같은 인스턴스의 mongo/qdrant까지 느려질
    수 있기 때문. 실패한 문서는 memes.is_embedded=False로 남아 다음 날 실행에서
    자동 재시도되므로, 여기서 별도 재시도 로직은 두지 않는다.
    """
    log_status("전처리+임베딩 시작")
    result = subprocess.run([sys.executable, PREPROCESS_EMBED_PY])
    if result.returncode == 0:
        log_status("전처리+임베딩 성공")
        with open(LAST_EMBED_PATH, "w", encoding="utf-8") as f:
            f.write(datetime.utcnow().isoformat())
    else:
        logger.error("전처리+임베딩 실패 (code=%d)", result.returncode)
        log_status(f"전처리+임베딩 실패 (code={result.returncode})")


def crawl_request_run():
    """온디맨드 키워드 수집 큐를 1회 확인해서, 있으면 하나 처리한다.
    큐가 비어있으면 crawl_request_worker.py 자체가 조용히 종료하므로 여기서
    성공 로그를 남기지 않는다(1분마다 빈 로그가 쌓이는 걸 피하려고) — 실패했을 때만 기록.

    서브프로세스를 띄우기 전에 가벼운 Mongo 조회로 처리할 게 있는지부터 확인한다 —
    crawl_request_worker.py는 embedding.pipeline(torch)을 모듈 최상단에서 임포트해
    빈 틱마다 매번 띄우면 1분 간격 대비 임포트 비용 비중이 상당하다. import는
    함수 안에서 해서 scheduler.py 자체를 무겁게 만들지 않는다.
    running까지 포함하는 이유: crawl_request_worker.py의 _requeue_stale_running은
    워커 프로세스 '안'에서 2시간 기준으로 도는데, queued만 보면 running에 멈춰있는
    고아 문서가 있어도 워커가 다시는 안 뜨게 되어 그 회수 로직 자체가 영영 실행되지
    않는다."""
    from DB.mongo_client import get_collection
    from config.config_cilent import CRAWL_REQUESTS_COLLECTION
    has_work = get_collection(CRAWL_REQUESTS_COLLECTION).find_one(
        {"status": {"$in": ["queued", "running"]}}
    )
    if has_work is None:
        return
    result = subprocess.run([sys.executable, CRAWL_REQUEST_WORKER_PY])
    if result.returncode != 0:
        logger.error("온디맨드 수집 워커 실패 (code=%d)", result.returncode)


_llm_worker_proc: subprocess.Popen | None = None


def llm_request_run():
    """상주 analyze 워커(llm_request_worker.py --loop)가 떠 있는지 확인하고,
    없는데 큐에 할 일이 있으면 새로 띄운다.

    예전엔 5초 틱마다 매번 새 서브프로세스를 띄웠는데, llm_request_worker.py가
    BGE-M3를 모듈 최상단에서 로드해(수십 초) 요청마다 콜드스타트를 반복하는
    게 체감 지연의 원인이었다(2026-08-14). 이제 워커는 --loop로 뜨면 큐가
    LLM_WORKER_IDLE_TIMEOUT_SECONDS만큼 빌 때까지 자기 안에서 계속 폴링하며
    모델을 재사용한다. 그래서 이 함수는 "이미 상주 워커가 있으면 아무것도
    안 함"이 기본 경로가 되고, 워커가 idle-timeout으로 스스로 종료했거나
    아직 한 번도 안 떴을 때만 Popen으로 새로 띄운다(블로킹 X — subprocess.run과
    달리 다음 5초 틱이 곧바로 다시 돌아 "이미 떠 있음" 체크를 반복할 수 있어야
    한다).

    서브프로세스를 새로 띄우기 전에 가벼운 Mongo 조회로 처리할 게 있는지부터
    확인한다 — llm_request_worker.py는 analysis.pipeline(langchain_nvidia_ai_endpoints)과
    embedding.encoder/analysis.rag_pipeline(torch, FlagEmbedding)을 모듈 최상단에서
    임포트해 임포트만 수십 초가 걸린다. import는 함수 안에서 해서 scheduler.py
    자체를 무겁게 만들지 않는다.
    running까지 포함하는 이유: _requeue_stale_running은 워커 프로세스 '안'에서
    도는데, 상주 워커가 죽으면 running 문서만 남고 queued는 없을 수 있어 queued만
    보면 그 요청이 영원히 회수되지 않는다."""
    global _llm_worker_proc

    if _llm_worker_proc is not None:
        if _llm_worker_proc.poll() is None:
            return  # 상주 워커가 이미 큐를 계속 비우고 있음
        if _llm_worker_proc.returncode != 0:
            logger.error("analyze 워커 비정상 종료 (code=%d)", _llm_worker_proc.returncode)
        _llm_worker_proc = None  # idle-timeout으로 스스로 종료했거나 죽음 — 재확인 필요

    from DB.mongo_client import get_collection
    from config.config_cilent import LLM_REQUESTS_COLLECTION
    has_work = get_collection(LLM_REQUESTS_COLLECTION).find_one(
        {"status": {"$in": ["queued", "running"]}}
    )
    if has_work is None:
        return
    _llm_worker_proc = subprocess.Popen([sys.executable, LLM_REQUEST_WORKER_PY, "--loop"])


CRAWL_HOUR = 2   # KST 02:00 — 크론(hour=2, minute=0)과 반드시 같은 값을 유지해야 함
CRAWL_MINUTE = 0


def _last_crawl_boundary_utc(now_utc: datetime) -> datetime:
    """지금 시각 기준, 가장 최근에 지나간 KST 02:00 크론 시각(UTC로 환산)."""
    now_kst = now_utc + KST_OFFSET
    boundary_kst = now_kst.replace(hour=CRAWL_HOUR, minute=CRAWL_MINUTE, second=0, microsecond=0)
    if now_kst < boundary_kst:
        boundary_kst -= timedelta(days=1)
    return boundary_kst - KST_OFFSET


def needs_catchup() -> bool:
    """컨테이너가 꺼져 있어서 가장 최근 크롤링 슬롯을 놓쳤는지 확인."""
    if not os.path.exists(LAST_CRAWL_PATH):
        return True
    with open(LAST_CRAWL_PATH, "r", encoding="utf-8") as f:
        try:
            last_crawl_utc = datetime.fromisoformat(f.read().strip())
        except ValueError:
            return True
    return last_crawl_utc < _last_crawl_boundary_utc(datetime.utcnow())


PREPROCESS_EMBED_HOUR = 4    # 크론(hour=4, minute=30)과 반드시 같은 값을 유지해야 함
PREPROCESS_EMBED_MINUTE = 30


def _last_embed_boundary_utc(now_utc: datetime) -> datetime:
    """지금 시각 기준, 가장 최근에 지나간 KST 04:30 크론 시각(UTC로 환산)."""
    now_kst = now_utc + KST_OFFSET
    boundary_kst = now_kst.replace(
        hour=PREPROCESS_EMBED_HOUR, minute=PREPROCESS_EMBED_MINUTE, second=0, microsecond=0
    )
    if now_kst < boundary_kst:
        boundary_kst -= timedelta(days=1)
    return boundary_kst - KST_OFFSET


def needs_embed_catchup() -> bool:
    """컨테이너가 꺼져 있어서(또는 방금 배포돼서) 가장 최근 전처리+임베딩 슬롯을 놓쳤는지 확인.
    crawl과 같은 캐치업 패턴 — 이걸로 재시작 즉시 실제 스케줄러 경로를 눈으로 검증할 수 있다."""
    if not os.path.exists(LAST_EMBED_PATH):
        return True
    with open(LAST_EMBED_PATH, "r", encoding="utf-8") as f:
        try:
            last_embed_utc = datetime.fromisoformat(f.read().strip())
        except ValueError:
            return True
    return last_embed_utc < _last_embed_boundary_utc(datetime.utcnow())

jobstores = {
    'default' : SQLAlchemyJobStore(url=f'sqlite:///{DB_PATH}')
}

scheduler = BackgroundScheduler(jobstores=jobstores,timezone='Asia/Seoul')

scheduler.add_job(
    crawlrun,
    CronTrigger(hour=CRAWL_HOUR, minute=CRAWL_MINUTE, timezone='Asia/Seoul'),
    id='crawl_job',
    coalesce=True,
    misfire_grace_time=1800,
    replace_existing=True,
)

scheduler.add_job(
    preprocess_embed_run,
    CronTrigger(hour=4, minute=30, timezone='Asia/Seoul'),
    id='preprocess_embed_job',
    coalesce=True,
    misfire_grace_time=1800,
    replace_existing=True,
)

# 사용자가 화면 앞에서 기다리는 큐라, 폴링 간격이 그대로 대기 시간에 얹힌다
# (1분이면 최대 60초, 평균 30초를 아무 일도 안 하고 버렸다). 빈 틱 비용은
# crawl_request_worker의 지연 임포트로 이미 낮아서 간격을 줄여도 부담이 없다.
# 여러 요청이 몰려도 동시 실행은 max_instances 기본값 1이 계속 막아준다 —
# 앞 작업이 안 끝났으면 다음 틱은 그냥 건너뛴다.
scheduler.add_job(
    crawl_request_run,
    'interval',
    seconds=15,
    id='crawl_request_job',
    coalesce=True,
    misfire_grace_time=60,
    replace_existing=True,
)

scheduler.add_job(
    llm_request_run,
    'interval',
    seconds=5,
    id='llm_request_job',
    coalesce=True,
    misfire_grace_time=30,
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
    if needs_embed_catchup():
        log_status("캐치업: 마지막 전처리+임베딩 슬롯을 놓쳐 즉시 1회 실행")
        threading.Thread(target=preprocess_embed_run, daemon=True).start()
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    scheduler.shutdown()
except Exception as e:
    log_status(f"스케줄러 시작 실패: {e}")
    raise