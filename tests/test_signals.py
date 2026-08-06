"""
청크 품질 신호 계산 검증 (외부 의존 없음, 1초 미만).

스팸 패턴은 실제 수집 데이터에서 관찰된 것을 그대로 샘플로 쓴다. 중요한 건
'정상 한국어 문장에는 걸리지 않는 것'이다 — 특히 마침표가 들어간 평범한 문장.

실행:  uv run python tests/test_signals.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quality_test.signals import (
    boilerplate_hits,
    compute_signals,
    hangul_ratio,
    spam_hits,
)

# 실제 수집된 대출 스팸 댓글 (natepann "퇴근버스" 게시글에서 관찰)
REAL_SPAM = "⭐ 라. 인: King 365 z ⭐ 사/고-자 신/불/자 연/체/자 가능"


def test_실제_스팸을_잡는다():
    assert spam_hits(REAL_SPAM) >= 3, spam_hits(REAL_SPAM)
    print(f"[OK] 실제 스팸 검출: {spam_hits(REAL_SPAM)}건")


def test_정상_한국어_문장은_스팸이_아니다():
    samples = [
        "오늘 진짜 재밌었습니다. 그리고 내일도 갈 거예요",
        "야르~ 90년도에 유행하던거 아님?ㅋㅋ 꼰대만 아는 유행어인데",
        "이 단어가 한국 온라인 커뮤니티로 넘어오면서 감탄사로 변형된 것이죠.",
    ]
    for s in samples:
        assert spam_hits(s) == 0, f"오탐: {s!r} → {spam_hits(s)}"
    print("[OK] 정상 문장 오탐 없음 (마침표 포함 문장 포함)")


def test_UI_상투어를_잡는다():
    text = "본문 바로가기 이웃추가 공유하기 URL복사 신고하기 실제 내용은 여기부터"
    assert boilerplate_hits(text) >= 4, boilerplate_hits(text)
    assert boilerplate_hits("평범한 게시글 본문입니다") == 0
    print(f"[OK] UI 상투어 검출: {boilerplate_hits(text)}건")


def test_한글비율을_잰다():
    assert hangul_ratio("한글만있음") == 1.0
    assert hangul_ratio("abcdef") == 0.0
    assert abs(hangul_ratio("한글abc") - 0.4) < 0.01, hangul_ratio("한글abc")
    assert hangul_ratio("") == 0.0
    print("[OK] 한글 비율 계산")


def test_공백은_한글비율_분모에서_빠진다():
    # 공백을 세면 "짧은 한글 + 많은 공백"이 낮은 비율로 잘못 나온다
    assert hangul_ratio("한 글") == 1.0, hangul_ratio("한 글")
    print("[OK] 공백은 분모 제외")


def test_신호_묶음을_한번에_낸다():
    sig = compute_signals(text="야르 하는 중", title="야르 후기", keyword="야르")
    assert sig["char_count"] == len("야르 하는 중"), sig["char_count"]
    assert sig["match_position"] == "title", sig["match_position"]
    assert sig["match_count"] == 2, sig["match_count"]
    assert sig["spam_hits"] == 0 and sig["boilerplate_hits"] == 0, sig
    assert 0.0 <= sig["hangul_ratio"] <= 1.0, sig["hangul_ratio"]
    print("[OK] compute_signals 묶음 출력")


def test_스팸_청크의_신호가_전부_나쁘게_나온다():
    sig = compute_signals(text=f"야르\n\n[댓글]\n{REAL_SPAM}", title="퇴근버스", keyword="야르")
    assert sig["spam_hits"] >= 3, sig
    assert sig["match_position"] == "early", sig["match_position"]
    assert sig["match_count"] == 1, sig["match_count"]
    print("[OK] 스팸 청크 신호 조합")


if __name__ == "__main__":
    test_실제_스팸을_잡는다()
    test_정상_한국어_문장은_스팸이_아니다()
    test_UI_상투어를_잡는다()
    test_한글비율을_잰다()
    test_공백은_한글비율_분모에서_빠진다()
    test_신호_묶음을_한번에_낸다()
    test_스팸_청크의_신호가_전부_나쁘게_나온다()
    print("\nALL PASS ✅")
