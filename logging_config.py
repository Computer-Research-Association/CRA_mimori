"""
logging_config.py
프로젝트 전체에서 공유하는 로거 팩토리.

get_logger(name) 을 호출하면:
  - 콘솔(stdout) 핸들러는 항상 붙는다.
  - CLOUDWATCH_ENABLED=true 이고 watchtower/boto3 가 설치돼 있으면
    AWS CloudWatch Logs 핸들러도 함께 붙는다.

CloudWatch 설정 환경변수:
  CLOUDWATCH_ENABLED  : "true" 이면 활성화 (기본값 "false")
  AWS_LOG_GROUP       : CloudWatch 로그 그룹 이름 (기본값 "/mimori")
  AWS_REGION          : AWS 리전 (기본값 "us-east-1", 버지니아)
  AWS_ACCESS_KEY_ID   : AWS 자격증명 (없으면 IAM 롤/인스턴스 프로파일 사용)
  AWS_SECRET_ACCESS_KEY
"""

import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv()

try:
    import boto3
    import watchtower
    _WATCHTOWER_AVAILABLE = True
except ImportError:
    _WATCHTOWER_AVAILABLE = False

_CONSOLE_FMT = logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_CW_FMT = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")

# 이미 초기화한 로거를 재사용하기 위한 캐시
_initialized: set[str] = set()


def get_logger(name: str) -> logging.Logger:
    """
    name 에 해당하는 로거를 반환한다.
    같은 name 으로 두 번 호출해도 핸들러가 중복 등록되지 않는다.
    """
    logger = logging.getLogger(name)

    if name in _initialized:
        return logger

    _initialized.add(name)
    logger.setLevel(logging.INFO)
    # 루트 로거로 전파하지 않아 중복 출력을 막는다.
    logger.propagate = False

    # ── 콘솔 핸들러 ──────────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_CONSOLE_FMT)
    logger.addHandler(console)

    # ── CloudWatch 핸들러 ────────────────────────────────────────────────────
    if not _WATCHTOWER_AVAILABLE:
        return logger
    if os.getenv("CLOUDWATCH_ENABLED", "false").lower() != "true":
        return logger

    try:
        cw_client = boto3.client(
            "logs",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
        cw_handler = watchtower.CloudWatchLogHandler(
            boto3_client=cw_client,
            log_group_name=os.getenv("AWS_LOG_GROUP", "/mimori"),
            log_stream_name=name,
            create_log_group=True,
        )
        cw_handler.setFormatter(_CW_FMT)
        logger.addHandler(cw_handler)
        logger.info("CloudWatch 핸들러 등록 완료 (그룹: %s, 스트림: %s)",
                    os.getenv("AWS_LOG_GROUP", "/mimori"), name)
    except Exception as exc:
        logger.warning("CloudWatch 핸들러 초기화 실패 — 콘솔 전용으로 동작: %s", exc)

    return logger
