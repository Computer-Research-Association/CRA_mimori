"""
preprocessing/cleaner.py의 UI 상투어 실제 제거 검증 (외부 의존 없음).

quality_test/signals.py는 BOILERPLATE_PHRASES 개수를 세기만 하고(1단계 범위),
실제로 지우는 코드는 없었다. 이번엔 config.BOILERPLATE_PHRASES를 그대로 재사용해
cleaner가 실제로 제거하도록 만든다 — 탐지 목록과 제거 목록이 같은 곳(config)을
봐야 어긋나지 않는다.

실행:  uv run python tests/test_cleaner.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from preprocessing.cleaner import clean_text
from quality_test.signals import boilerplate_hits


def test_UI_상투어가_본문에서_제거된다():
    text = "이웃추가 진짜 재밌는 글이었어요 공유하기"
    cleaned = clean_text(text)
    assert "이웃추가" not in cleaned, cleaned
    assert "공유하기" not in cleaned, cleaned
    assert "진짜 재밌는 글이었어요" in cleaned, cleaned
    print("[OK] UI 상투어 제거, 실제 내용은 보존")


def test_제거_후_boilerplate_hits가_0이_된다():
    # 제거 목록과 탐지 목록이 같은 config 상수를 쓰므로, 제거 후엔 반드시 0이어야 한다.
    text = "본문 바로가기 dc official App 진짜 좋은 글 이웃추가 구독하기"
    assert boilerplate_hits(text) > 0, "테스트 전제가 깨짐 — 제거 전엔 검출돼야 함"
    cleaned = clean_text(text)
    assert boilerplate_hits(cleaned) == 0, f"제거 후에도 검출됨: {cleaned!r}"
    print("[OK] 제거 후 boilerplate_hits == 0 (탐지/제거 기준 일치)")


def test_상투어가_없으면_아무것도_안_바뀐다():
    text = "오늘 진짜 야르한 하루였다"
    assert clean_text(text) == text
    print("[OK] 상투어 없는 정상 텍스트는 그대로 유지")


def test_제거_후_중복_공백이_정리된다():
    # "이웃추가"를 지우면 그 자리에 공백이 남을 수 있는데, 기존 중복공백 정리
    # 로직이 이미 있으므로 추가 처리 없이도 정리돼야 한다.
    text = "재밌다 이웃추가 정말"
    cleaned = clean_text(text)
    assert "  " not in cleaned, repr(cleaned)
    print("[OK] 상투어 제거 후 중복 공백 없음")


def test_다음카페_UI_상투어도_제거된다():
    # 실사례(2026-08-04, tavily/cafe.daum.net): 로그인/카페정보/검색옵션 같은
    # 다음 카페 전용 UI가 실제 게시글·댓글 내용과 뒤섞여 들어왔다.
    text = (
        "카페정보\n\n카페 프로필 이미지\n\n카페 가입하기\n\n검색\n\n### 카페 전체 메뉴\n\n"
        "쌰갈 이라는 말 여단오? 에서 나온 말임????\n\n"
        "검색이 허용된 게시물입니다.\n\n게시글 본문내용\n\n"
        "쌰갈 이렇게 하시는거 본거같았슨 ㅋㅋ\n\n"
        "검색 옵션 선택상자\n\n댓글내용선택됨\n\n서비스 약관/정책 | 권리침해신고 | 이용약관 | 카페 고객센터 | 검색비공개 요청"
    )
    cleaned = clean_text(text)
    for phrase in (
        "카페정보", "카페 프로필 이미지", "카페 가입하기", "카페 전체 메뉴",
        "검색이 허용된 게시물입니다", "게시글 본문내용", "검색 옵션 선택상자",
        "댓글내용선택됨", "서비스 약관/정책", "권리침해신고", "카페 고객센터", "검색비공개 요청",
    ):
        assert phrase not in cleaned, f"{phrase!r} 안 지워짐: {cleaned!r}"
    assert "쌰갈 이라는 말 여단오" in cleaned, cleaned
    assert "쌰갈 이렇게 하시는거 본거같았슨" in cleaned, cleaned
    print("[OK] 다음 카페 UI 상투어 제거, 게시글/댓글 내용은 보존")


if __name__ == "__main__":
    test_UI_상투어가_본문에서_제거된다()
    test_제거_후_boilerplate_hits가_0이_된다()
    test_상투어가_없으면_아무것도_안_바뀐다()
    test_제거_후_중복_공백이_정리된다()
    test_다음카페_UI_상투어도_제거된다()
    print("\nALL PASS ✅")
