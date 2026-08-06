"""
키워드 매칭 단일 정의 검증 (외부 의존 없음, 1초 미만).

핵심은 개행 케이스다. 크롤러가 get_text(separator="\n")로 본문을 뽑기 때문에
"거제 야호~" 같은 공백 포함 키워드가 텍스트에서는 "거제\n야호"로 쪼개져 들어온다.
이걸 매칭하지 못하면 정상 수집된 문서가 검색에서 조용히 탈락한다.

실행:  uv run python tests/test_matching.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quality_test.matching import find_keyword, normalize


def test_개행으로_쪼개진_키워드도_매칭된다():
    # 크롤러가 인라인 요소 경계마다 개행을 넣어 실제로 이 형태가 만들어진다
    m = find_keyword("거제 야호~", title="", body="미나미가 외치는 거제\n야호 에서 야호는")
    assert m.matched, "개행이 낀 키워드가 매칭되지 않음 — 이게 지금의 버그"
    assert m.position == "early", m.position
    print("[OK] 개행으로 쪼개진 키워드 매칭")


def test_물결_개수가_달라도_매칭된다():
    # 키워드는 "좋~다~", 실제 게시글은 "좋다~~~" 처럼 물결 개수가 다르다
    m = find_keyword("좋~다~", title="", body="오늘 진짜 좋다~~~ 기분 최고")
    assert m.matched, "물결 개수 차이로 매칭 실패"
    print("[OK] 물결 개수 차이 흡수")


def test_제목_매치가_우선한다():
    m = find_keyword("야르", title="야르의 뜻", body="관련 없는 본문 내용")
    assert m.matched and m.position == "title", (m.matched, m.position)
    print("[OK] 제목 매치 우선")


def test_본문_후반_매치는_late다():
    body = "가" * 300 + "야르" + "나" * 10
    m = find_keyword("야르", title="", body=body)
    assert m.matched and m.position == "late", (m.matched, m.position)
    print("[OK] 본문 후반 매치는 late")


def test_어디에도_없으면_none이다():
    m = find_keyword("야르", title="여행 후기", body="괌 자유여행 정보입니다")
    assert not m.matched and m.position == "none" and m.count == 0, m
    print("[OK] 미매치는 none")


def test_등장_횟수를_센다():
    m = find_keyword("야르", title="야르", body="야르 야르 하는 야르")
    assert m.count == 4, m.count  # 제목 1 + 본문 3
    print("[OK] 등장 횟수 집계")


def test_빈_키워드는_매치되지_않는다():
    m = find_keyword("", title="아무거나", body="아무거나")
    assert not m.matched and m.count == 0, m
    print("[OK] 빈 키워드 방어")


def test_정규화가_대소문자와_공백류를_없앤다():
    assert normalize("  Hello\tWorld\n") == "helloworld"
    assert normalize("거제 야호~") == "거제야호"
    print("[OK] 정규화 규칙")


if __name__ == "__main__":
    test_개행으로_쪼개진_키워드도_매칭된다()
    test_물결_개수가_달라도_매칭된다()
    test_제목_매치가_우선한다()
    test_본문_후반_매치는_late다()
    test_어디에도_없으면_none이다()
    test_등장_횟수를_센다()
    test_빈_키워드는_매치되지_않는다()
    test_정규화가_대소문자와_공백류를_없앤다()
    print("\nALL PASS ✅")
