"""
chunker.py의 3단계 개선 검증: 본문/댓글 항상 분리(content_type) + 최소 길이 30.

외부 의존 없음. 실행:  uv run python tests/test_chunker.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from preprocessing.chunker import chunk_document
from config.config_cilent import MIN_CHUNK_CHARS, SPAM_HIT_THRESHOLD
from quality_test.signals import spam_hits


def _doc(**overrides):
    base = {
        "_id": "doc1",
        "source": "dcinside",
        "keyword": "야르",
        "url": "http://example.com",
        "title": "제목",
        "published_date": None,
        "crawled_at": None,
    }
    base.update(overrides)
    return base


def test_댓글_마커가_있으면_본문과_댓글이_content_type으로_나뉜다():
    body = "본문내용이 충분히 깁니다 " * 10
    comment = "댓글내용도 충분히 깁니다 " * 10
    doc = _doc(clean_content=body + "\n\n[댓글]\n" + comment)

    chunks = chunk_document(doc)
    assert chunks, "청크가 비어있음"

    body_chunks = [c for c in chunks if c["content_type"] == "body"]
    comment_chunks = [c for c in chunks if c["content_type"] == "comment"]
    assert body_chunks, "본문 청크가 없음"
    assert comment_chunks, "댓글 청크가 없음"
    for c in body_chunks:
        assert "댓글내용" not in c["text"], c
    for c in comment_chunks:
        assert "본문내용" not in c["text"], c
    print("[OK] 댓글 마커로 본문/댓글 content_type 분리")


def test_댓글_마커가_없으면_전부_본문이다():
    doc = _doc(clean_content="본문만 있는 문서입니다 " * 10)
    chunks = chunk_document(doc)
    assert chunks, "청크가 비어있음"
    assert all(c["content_type"] == "body" for c in chunks), chunks
    print("[OK] 마커 없으면 전부 body")


def test_최소_길이가_30자로_상향됐다():
    short_text = "본문이짧다" * 3  # 15자: 옛 기준(10)은 통과, 새 기준(30)은 탈락
    assert 10 <= len(short_text) < MIN_CHUNK_CHARS, len(short_text)

    doc = _doc(clean_content=short_text)
    chunks = chunk_document(doc)
    assert chunks == [], f"{MIN_CHUNK_CHARS}자 미만인데 청크가 생성됨: {chunks}"
    print("[OK] 최소 길이 상향 (10 -> {})".format(MIN_CHUNK_CHARS))


def test_나무위키_섹션_청크도_content_type_body를_가진다():
    text = "[[SECTION]] 첫 섹션\n" + ("섹션 본문 내용입니다 " * 10)
    doc = _doc(source="namuwiki", clean_content=text)
    chunks = chunk_document(doc)
    assert chunks, "청크가 비어있음"
    assert all(c["content_type"] == "body" for c in chunks), chunks
    print("[OK] 나무위키 섹션 청크는 전부 body")


def test_스팸_댓글_청크는_제거되고_정상_본문은_남는다():
    # quality_test/signals.py의 spam_hits()가 검출하는 것과 동일한 패턴
    # (장식기호 + 대출스팸 슬랭 + 한글/구분자 반복)을 댓글에 넣어 실제로
    # SPAM_HIT_THRESHOLD 이상 나오는지 전제부터 확인한다.
    body = "오늘 진짜 야르한 하루였다 정말 신났다 좋은 일이 많았다"
    spam_comment = "⭐ 급전 대출 카톡 문의 사/고-자 신/불/자 연체자 텔레그램 ⭐"
    assert spam_hits(spam_comment) >= SPAM_HIT_THRESHOLD, spam_hits(spam_comment)
    assert len(spam_comment) >= MIN_CHUNK_CHARS, len(spam_comment)  # 길이 필터 때문이 아님을 보장

    doc = _doc(clean_content=body + "\n\n[댓글]\n" + spam_comment)
    chunks = chunk_document(doc)

    assert all(c["content_type"] == "body" for c in chunks), (
        f"스팸 댓글 청크가 안 걸러짐: {chunks}"
    )
    assert any("야르한 하루" in c["text"] for c in chunks), "정상 본문까지 같이 사라짐"
    print("[OK] 스팸 댓글 청크 제거, 정상 본문은 보존")


def test_빈_본문은_빈_리스트를_반환한다():
    doc = _doc(clean_content="")
    assert chunk_document(doc) == []
    print("[OK] 빈 본문 방어")


if __name__ == "__main__":
    test_댓글_마커가_있으면_본문과_댓글이_content_type으로_나뉜다()
    test_댓글_마커가_없으면_전부_본문이다()
    test_최소_길이가_30자로_상향됐다()
    test_나무위키_섹션_청크도_content_type_body를_가진다()
    test_스팸_댓글_청크는_제거되고_정상_본문은_남는다()
    test_빈_본문은_빈_리스트를_반환한다()
    print("\nALL PASS ✅")
