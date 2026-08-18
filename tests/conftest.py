"""
conftest.py
pytest 전용 공통 fixture. 개별 테스트 파일은 여전히 `uv run python tests/test_x.py`로
독립 실행 가능해야 하므로(각 파일의 if __name__ 블록), 여기 있는 자동 적용
fixture는 pytest로 실행할 때만 동작한다 — 단독 실행 시에는 매번 새 프로세스라
어차피 전역 상태가 자연히 리셋된다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.rate_limit import reset as _reset_rate_limit


@pytest.fixture(autouse=True)
def _reset_api_rate_limit():
    """api/rate_limit.py의 카운터는 프로세스 전역이다. pytest는 모든 테스트
    파일을 한 프로세스 안에서 돌리므로, 리셋 없이 두면 한 테스트에서 쌓인
    crawl-request/analyze-request 호출 횟수가 다음 테스트로 새어나가 429로
    오탐할 수 있다 — 테스트마다 자동으로 비운다."""
    _reset_rate_limit()
    yield
