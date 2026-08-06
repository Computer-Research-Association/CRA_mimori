"""
crawlers/base.py의 merge_dedup_by_url() 검증 (외부 의존 없음).

정렬 하나만으로 검색하면 "아직 인기를 못 얻은 최신 글"이 계속 순위 밖으로 밀리는
편향이 생긴다(인기순은 추천이 쌓일 시간이 필요해서 갓 올라온 글은 못 낌).
natepann/dcinside가 이제 두 정렬(예: 인기+최신)로 나눠 수집한 뒤 이 함수로
합치는데, 같은 글이 두 정렬 모두에서 나올 수 있어 URL 기준 중복 제거가 필요하다.

실행:  uv run python tests/test_crawl_merge.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.base import merge_dedup_by_url

_D1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_D2 = datetime(2026, 2, 1, tzinfo=timezone.utc)
_D3 = datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_겹치지_않으면_전부_합쳐진다():
    a = [("https://a.com/1", _D1)]
    b = [("https://a.com/2", _D2)]
    merged = merge_dedup_by_url(a, b)
    assert merged == [("https://a.com/1", _D1), ("https://a.com/2", _D2)], merged
    print("[OK] 겹치지 않으면 전부 합쳐짐")


def test_같은_URL이_두_정렬에_다_나오면_한번만_남는다():
    a = [("https://a.com/1", _D1), ("https://a.com/2", _D2)]
    b = [("https://a.com/2", _D2), ("https://a.com/3", _D3)]
    merged = merge_dedup_by_url(a, b)
    urls = [u for u, _ in merged]
    assert urls == ["https://a.com/1", "https://a.com/2", "https://a.com/3"], urls
    assert len(merged) == 3, merged
    print("[OK] 중복 URL은 한 번만 남음 (먼저 나온 정렬 결과 유지)")


def test_빈_목록도_안전하게_처리된다():
    assert merge_dedup_by_url([], []) == []
    assert merge_dedup_by_url([("https://a.com/1", _D1)], []) == [("https://a.com/1", _D1)]
    print("[OK] 빈 목록 방어")


def test_세개_이상의_정렬도_합칠_수_있다():
    a = [("https://a.com/1", _D1)]
    b = [("https://a.com/2", _D2)]
    c = [("https://a.com/1", _D1), ("https://a.com/3", _D3)]
    merged = merge_dedup_by_url(a, b, c)
    urls = [u for u, _ in merged]
    assert urls == ["https://a.com/1", "https://a.com/2", "https://a.com/3"], urls
    print("[OK] 3개 이상 정렬도 병합 가능")


if __name__ == "__main__":
    test_겹치지_않으면_전부_합쳐진다()
    test_같은_URL이_두_정렬에_다_나오면_한번만_남는다()
    test_빈_목록도_안전하게_처리된다()
    test_세개_이상의_정렬도_합칠_수_있다()
    print("\nALL PASS ✅")
