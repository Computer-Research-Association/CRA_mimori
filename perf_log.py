"""
perf_log.py
파이프라인 단계별 소요 시간 계측용 로깅 유틸리티.

기존 로직에는 손대지 않고 '언제 시작해서 언제 끝났고 몇 초 걸렸는지'만 기록하기 위한
모듈이다. 계측 자체가 병목을 만들면 안 되므로 하는 일은 monotonic 시계 읽기와
로그 한 줄 출력뿐이다.

사용법
------
    from perf_log import stage, accumulate, report_totals

    with stage("크롤", keyword="야르", source="dcinside"):
        ...                      # 시작/종료/소요시간이 자동으로 로깅됨

    with accumulate("임베딩:인코딩"):
        ...                      # 반복 호출되는 구간의 누적 시간만 합산(로그 미출력)

    report_totals()              # 누적 구간들의 합계를 표로 출력

환경변수
--------
    PERF_LOG=0  으로 두면 계측 로그를 끄고 오버헤드를 0에 가깝게 만든다(기본값 1).
"""

import os
import threading
import time
from contextlib import contextmanager

from logging_config import get_logger

logger = get_logger("perf")

_ENABLED = os.getenv("PERF_LOG", "1") != "0"

# 여러 스레드(크롤 워커)가 같은 구간을 동시에 지나므로 누적값은 락으로 보호한다.
_lock = threading.Lock()
_totals: dict[str, float] = {}
_counts: dict[str, int] = {}


def _label(name: str, extra: dict) -> str:
    """'크롤[keyword=야르 source=dcinside]' 형태의 라벨을 만든다."""
    if not extra:
        return name
    joined = " ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
    return f"{name}[{joined}]" if joined else name


@contextmanager
def stage(name: str, **extra):
    """한 단계의 시작/종료/소요시간을 로그로 남긴다.

    예외가 나도 소요시간은 남긴다 — 실패한 단계가 얼마나 시간을 먹고 죽었는지가
    성능 진단에서는 성공 케이스만큼 중요하다.
    """
    if not _ENABLED:
        yield
        return

    label = _label(name, extra)
    start = time.monotonic()
    logger.info("▶ 시작  %s", label)
    try:
        yield
    except Exception as e:
        elapsed = time.monotonic() - start
        _record(name, elapsed)
        logger.info("✖ 실패  %s — %.2fs (%s)", label, elapsed, type(e).__name__)
        raise
    else:
        elapsed = time.monotonic() - start
        _record(name, elapsed)
        logger.info("■ 완료  %s — %.2fs", label, elapsed)


@contextmanager
def accumulate(name: str):
    """반복 호출되는 짧은 구간의 시간을 '합산만' 한다(구간마다 로그를 찍지 않음).

    배치 인코딩처럼 수백 번 불리는 구간은 매번 로그를 남기면 로그가 계측을 압도한다.
    """
    if not _ENABLED:
        yield
        return

    start = time.monotonic()
    try:
        yield
    finally:
        _record(name, time.monotonic() - start)


def _record(name: str, elapsed: float) -> None:
    with _lock:
        _totals[name] = _totals.get(name, 0.0) + elapsed
        _counts[name] = _counts.get(name, 0) + 1


def snapshot() -> dict[str, tuple[float, int]]:
    """현재까지의 {구간명: (누적초, 호출횟수)} 사본."""
    with _lock:
        return {k: (v, _counts[k]) for k, v in _totals.items()}


def reset() -> None:
    with _lock:
        _totals.clear()
        _counts.clear()


def report_totals(title: str = "단계별 누적 소요 시간") -> None:
    """누적 구간을 오래 걸린 순으로 출력한다.

    주의: 병렬 구간(크롤 워커)의 '누적'은 벽시계 시간이 아니라 워커 시간의 합이다.
    벽시계 시간과 비교해야 병렬화 이득이 실제로 나고 있는지 알 수 있으므로 둘 다 본다.
    """
    data = snapshot()
    if not data:
        return

    width = max(len(k) for k in data)
    total = sum(v for v, _ in data.values())
    lines = ["", "=" * (width + 34), title, "=" * (width + 34)]
    for name, (secs, count) in sorted(data.items(), key=lambda kv: -kv[1][0]):
        share = (secs / total * 100) if total else 0.0
        lines.append(f"  {name:<{width}}  {secs:8.2f}s  x{count:<5d} {share:5.1f}%")
    lines.append("=" * (width + 34))
    logger.info("\n".join(lines))
