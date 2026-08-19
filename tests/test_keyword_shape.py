"""
crawlers/keyword_shape.py의 classify_keyword_shape() 단위 테스트 (외부 의존 없음).

실행: uv run python tests/test_keyword_shape.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.keyword_shape import classify_keyword_shape


def test_짧은_신조어는_밈으로_판단된다():
    for kw in ["아이폰", "야르", "싹싹김치", "좋~다~", "할렐야루", "거제 야호~"]:
        is_meme, reason = classify_keyword_shape(kw)
        assert is_meme is True, (kw, reason)
        assert reason is None, (kw, reason)
    print("[OK] 기존 실제 등록 키워드들은 전부 밈으로 통과")


def test_긴_문장형_질문은_밈이_아니라고_판단된다():
    is_meme, reason = classify_keyword_shape("언어 치료사 1급 합격 방법")
    assert is_meme is False, reason
    assert "언어 치료사 1급 합격 방법" in reason
    assert "5" in reason  # 단어 5개
    print("[OK] 문장형 질문 -> 거부 + 이유 포함")


def test_경계값_단어_3개는_통과_4개는_거부():
    ok, _ = classify_keyword_shape("한 두 세")  # 3단어
    assert ok is True
    bad, reason = classify_keyword_shape("한 두 세 네")  # 4단어
    assert bad is False, reason
    print("[OK] 경계값(3단어 통과 / 4단어 거부) 확인")


if __name__ == "__main__":
    test_짧은_신조어는_밈으로_판단된다()
    test_긴_문장형_질문은_밈이_아니라고_판단된다()
    test_경계값_단어_3개는_통과_4개는_거부()
    print("\nALL PASS ✅")
