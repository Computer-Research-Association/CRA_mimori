"""
todayhumor_crawler.py의 순수 함수 검증 (외부 의존 없음, 네트워크 요청 없음).

실행:  uv run python tests/test_todayhumor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup

from crawlers.todayhumor_crawler import _normalize, _is_relevant, _parse_search_date, _parse_post


def test_정규화가_공백과_특수문자를_없앤다():
    assert _normalize("  야르~ 뜻!") == "야르뜻"
    print("[OK] 정규화 규칙")


def test_제목이나_본문에_키워드_있으면_관련있음():
    assert _is_relevant("야르", "야르한 아침이다", "본문") is True
    assert _is_relevant("야르", "제목", "오늘 야르한 기분") is True
    print("[OK] 제목/본문 부분 일치 판정")


def test_키워드가_전혀_없으면_비관련():
    assert _is_relevant("야르", "여행 후기", "괌 자유여행 정보") is False
    print("[OK] 미매치 판정")


def test_날짜_파싱():
    d = _parse_search_date("26/02/24 08:21")
    assert d is not None and d.year == 2026 and d.month == 2 and d.day == 24, d
    print("[OK] 실측 형식(YY/MM/DD HH:MM) 파싱")


def test_날짜_파싱_실패시_None():
    assert _parse_search_date("이상한형식") is None
    assert _parse_search_date("") is None
    print("[OK] 파싱 실패 시 None 방어")


def test_게시글_본문을_파싱한다():
    html = """
    <html><head><title>오늘의유머 - 럭키비키!!!!</title></head>
    <body><div class="viewContent">괜히 이 시간에 또 컵라면이 땡겨서 럭키비키</div></body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    title, body = _parse_post(soup)
    assert title == "럭키비키!!!!", title
    assert "럭키비키" in body, body
    print("[OK] 정상 게시글 제목/본문 파싱 (title 접두어 제거 확인)")


def test_이미지_전용_게시물은_본문이_빈문자열():
    # 실측: '유머자료' 게시판은 이미지만 있고 텍스트가 없는 경우가 흔함(11건 중 3건).
    html = """
    <html><head><title>오늘의유머 - 짤방</title></head>
    <body><div class="viewContent"><div class="upfile"><img src="//x.jpg"/></div></div></body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    title, body = _parse_post(soup)
    assert body == "", repr(body)
    print("[OK] 이미지 전용 게시물은 빈 본문 반환 (호출부에서 스킵 처리)")


def test_viewContent가_없으면_빈문자열():
    html = "<html><head><title>오늘의유머 - 삭제된글</title></head><body></body></html>"
    soup = BeautifulSoup(html, "lxml")
    title, body = _parse_post(soup)
    assert body == "", repr(body)
    print("[OK] viewContent 없는 경우 방어")


if __name__ == "__main__":
    test_정규화가_공백과_특수문자를_없앤다()
    test_제목이나_본문에_키워드_있으면_관련있음()
    test_키워드가_전혀_없으면_비관련()
    test_날짜_파싱()
    test_날짜_파싱_실패시_None()
    test_게시글_본문을_파싱한다()
    test_이미지_전용_게시물은_본문이_빈문자열()
    test_viewContent가_없으면_빈문자열()
    print("\nALL PASS ✅")
