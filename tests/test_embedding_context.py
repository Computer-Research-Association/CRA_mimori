"""
embedding/pipeline.py의 나무위키 섹션 컨텍스트 프리펜딩 검증 (외부 의존 없음).
BGE-M3 모델을 안 띄운다 — _encoding_text()는 순수 문자열 조합 함수라 인코딩
직전 텍스트만 확인하면 된다(실제 인코딩은 test_reembed.py처럼 무겁고 이 로직과
무관).

실행:  uv run python tests/test_embedding_context.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embedding.pipeline import _encoding_text


def test_section_title이_있으면_제목과_섹션제목을_앞에_붙인다():
    # 실사례: 나무위키 '유래' 섹션이 "1990년대부터 사용되기 시작했다"처럼
    # 대상을 생략하고 시작 — 제목/섹션제목 없이 인코딩하면 문맥이 사라진다.
    chunk = {"title": "이순신", "section_title": "유래", "text": "1990년대부터 사용되기 시작했다."}
    encoded = _encoding_text(chunk)
    assert encoded.startswith("이순신 - 유래"), encoded
    assert "1990년대부터 사용되기 시작했다." in encoded, encoded
    print("[OK] section_title 있으면 제목-섹션제목 프리픽스 추가")


def test_section_title이_없으면_원문_그대로():
    # 커뮤니티 소스는 section_title이 없음 — 제목이 본문과 겹치는 경우가 많아
    # 검증되지 않은 범위이므로 손 안 댐.
    chunk = {"title": "럭키비키!!!!", "section_title": None, "text": "괜히 컵라면이 땡겨서 럭키비키"}
    assert _encoding_text(chunk) == chunk["text"]
    print("[OK] section_title 없으면 원문 그대로 인코딩")


def test_section_title_키_자체가_없어도_안전하다():
    chunk = {"title": "제목", "text": "본문"}
    assert _encoding_text(chunk) == "본문"
    print("[OK] section_title 키 부재 시에도 방어")


def test_title이_없어도_안전하다():
    chunk = {"section_title": "개요", "text": "본문 내용"}
    encoded = _encoding_text(chunk)
    assert "개요" in encoded and "본문 내용" in encoded, encoded
    print("[OK] title 없어도 section_title만으로 프리픽스 구성")


if __name__ == "__main__":
    test_section_title이_있으면_제목과_섹션제목을_앞에_붙인다()
    test_section_title이_없으면_원문_그대로()
    test_section_title_키_자체가_없어도_안전하다()
    test_title이_없어도_안전하다()
    print("\nALL PASS ✅")
