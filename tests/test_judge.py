"""
process_one()에 문서 단위 relevance judge가 붙었는지 검증 (외부 의존 없음).

핵심은 "문서 단위 판정"이다. 청크 하나하나에 키워드가 있는지가 아니라,
문서의 모든 청크를 이어붙인 텍스트에 키워드가 있는지로 판정하고, 그 결과를
모든 청크에 동일하게 붙인다. 그래서 키워드를 담지 않은 청크도 관련 문서에
속해 있으면 is_relevant=True가 된다.

실행:  uv run python tests/test_judge.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from preprocessing.pipeline import process_one


def _doc(**overrides):
    base = {
        "_id": "doc1",
        "source": "dcinside",
        "keyword": "야르",
        "url": "http://example.com",
        "title": "제목",
        "published_date": None,
        "crawled_at": None,
        "content": "본문 내용",
    }
    base.update(overrides)
    return base


def test_제목에_키워드가_있으면_모든_청크가_관련있음():
    _, chunks = process_one(_doc(title="야르 뜻 설명", content="아무 본문 내용도 충분히 길게 적어야 청크가 살아남는다"))
    assert chunks, "청크가 비어있음"
    for c in chunks:
        assert c["is_relevant"] is True, c
        assert c["relevance_position"] == "title", c
    print("[OK] 제목 매치 → 전체 청크 관련 처리")


def test_키워드가_전혀_없으면_모든_청크가_비관련():
    _, chunks = process_one(_doc(title="여행 후기", content="괌 자유여행 정보입니다 충분히 길게 적어야 청크가 살아남는다"))
    assert chunks, "청크가 비어있음"
    for c in chunks:
        assert c["is_relevant"] is False, c
        assert c["relevance_position"] == "none", c
        assert c["relevance_match_count"] == 0, c
    print("[OK] 미매치 → 전체 청크 비관련 처리")


def test_청크_경계와_무관하게_문서_단위로_판정한다():
    # 키워드가 등장하지 않는 500자 이상의 앞부분 때문에 최소 2개 청크로 쪼개지고,
    # 키워드는 뒷부분에만 등장한다. 첫 청크 자체에는 키워드 텍스트가 없어야 한다.
    # (동일 문자를 4번 이상 반복하면 cleaner가 3개로 축약하므로 문장을 다양하게 반복한다)
    filler = " ".join(f"문장{i}번째내용입니다" for i in range(80))
    long_text = filler + " 야르 " + "뒷부분내용도채운다 " * 5
    _, chunks = process_one(_doc(title="무관한 제목", content=long_text))
    assert len(chunks) >= 2, f"청크가 1개뿐이라 경계 테스트 불가: {len(chunks)}"

    first_chunk_text = chunks[0]["text"]
    assert "야르" not in first_chunk_text, "테스트 전제가 깨짐 — 첫 청크에 이미 키워드 포함"

    for c in chunks:
        assert c["is_relevant"] is True, (
            f"문서 전체에는 키워드가 있는데 청크 단위로만 판정해서 False가 됨: {c}"
        )
    print("[OK] 문서 단위 판정 (청크 경계 무관)")


def test_청크가_없는_문서는_에러_없이_통과한다():
    _, chunks = process_one(_doc(content=""))
    assert chunks == [], chunks
    print("[OK] 빈 본문 방어")


def test_키워드가_든_짧은_본문이_청킹필터에_걸려도_관련_판정된다():
    # 실사례(natepann '18.3 에스 하나더 성공'): 본문 "야호\n- dc official App"이
    # 30자 미만이라 청킹 단계에서 버려지고 댓글 청크만 살아남는다. judge가 살아남은
    # 청크만 이어붙여 판정하면 키워드가 사라져 비관련으로 오판된다 — 반드시
    # clean_content(청킹 이전 전문)로 판정해야 이 버그가 안 생긴다.
    short_body = "야호\n- dc official App"  # 22자, MIN_CHUNK_CHARS(30) 미만
    comment = "이거 노브 어지럽던데 이걸잡네 - dc App\n노브 비비고 앞에 노트 판정으로 비빔 - dc App"
    content = short_body + "\n\n[댓글]\n" + comment

    _, chunks = process_one(_doc(keyword="야호", title="제목", content=content))
    assert chunks, "청크가 비어있음"
    assert all(c["content_type"] == "comment" for c in chunks), (
        f"테스트 전제가 깨짐 — 본문 청크가 살아남음: {chunks}"
    )
    for c in chunks:
        assert c["is_relevant"] is True, (
            f"원문엔 키워드가 있는데 청킹 필터로 증거가 사라져 비관련 오판됨: {c}"
        )
    print("[OK] 청킹 필터로 사라진 본문의 키워드도 clean_content 기준으로 관련 판정됨")


if __name__ == "__main__":
    test_제목에_키워드가_있으면_모든_청크가_관련있음()
    test_키워드가_전혀_없으면_모든_청크가_비관련()
    test_청크_경계와_무관하게_문서_단위로_판정한다()
    test_청크가_없는_문서는_에러_없이_통과한다()
    test_키워드가_든_짧은_본문이_청킹필터에_걸려도_관련_판정된다()
    print("\nALL PASS ✅")
