"""
rag_main.py의 select_keyword()/ensure_keyword_ready() 검증 (네트워크/DB/모델 불필요).
issue #69 온디맨드 RAG 크롤에서 "몇 분 기다렸는데 답이 안 나온다"로 이어지던 경로들.

확인하는 것 — select_keyword:
  0. 임베딩된 키워드가 없어도 입력을 받는다(예전엔 여기서 종료해 온디맨드가 무용지물),
     번호 선택, 0번을 통한 숫자 키워드 입력, 목록 밖 텍스트 = 새 키워드

확인하는 것 — ensure_keyword_ready:
  1. 크롤 0건이면 False (기존 동작 유지)
  2. 크롤은 됐지만 임베딩 청크가 0개면 False — 전처리 관련성 필터/근접중복에 다 걸린 경우.
     여기서 True를 돌려주면 사용자는 "검색 결과가 없습니다"만 반복해서 보게 된다.
  3. 수집량이 MIN_COMMUNITY_DOCS_FOR_TAVILY 미만이면 Keywords.md에 추가하지 않는다
     (오타/일회성 질의가 영구히 매일 배치 크롤 대상이 되는 것 방지)
  4. 수집량이 충분하면 Keywords.md에 추가하고 True

실행:  uv run python tests/test_ondemand_ready.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
import rag_main
from config.config_cilent import MIN_COMMUNITY_DOCS_FOR_TAVILY

import embedding.pipeline
import preprocessing.pipeline


class _Stubbed:
    """ensure_keyword_ready가 함수 안에서 지연 import 하는 대상들을 스텁으로 바꾼다.
    지연 import는 sys.modules에서 속성을 꺼내므로 모듈 속성을 갈아끼우면 그대로 먹는다."""

    def __init__(self, crawl_result, embed_result):
        self.crawl_result = crawl_result
        self.embed_result = embed_result
        self.added = []
        self.originals = {}

    def __enter__(self):
        self.originals = {
            "crawl_keyword": main.crawl_keyword,
            "add_keyword_if_missing": main.add_keyword_if_missing,
            "preprocess_documents": preprocessing.pipeline.preprocess_documents,
            "embed_documents": embedding.pipeline.embed_documents,
        }

        def fake_crawl(keyword, on_source_done=None):
            if on_source_done is not None:
                for source, (count, status) in self.crawl_result.items():
                    on_source_done(keyword, source, count, status)
            return self.crawl_result

        def fake_add(keyword):
            self.added.append(keyword)
            return True

        main.crawl_keyword = fake_crawl
        main.add_keyword_if_missing = fake_add
        preprocessing.pipeline.preprocess_documents = lambda kw: []
        embedding.pipeline.embed_documents = lambda kw: self.embed_result
        return self

    def __exit__(self, *exc):
        main.crawl_keyword = self.originals["crawl_keyword"]
        main.add_keyword_if_missing = self.originals["add_keyword_if_missing"]
        preprocessing.pipeline.preprocess_documents = self.originals["preprocess_documents"]
        embedding.pipeline.embed_documents = self.originals["embed_documents"]


def test_크롤_0건이면_False():
    with _Stubbed({"dcinside": (0, "ok")}, {"documents": 0, "chunks": 0}) as s:
        ready = rag_main.ensure_keyword_ready("없는키워드")
    assert ready is False
    assert s.added == [], s.added
    print("[OK] ensure_keyword_ready: 크롤 0건이면 False, Keywords.md 미추가")


def test_크롤은_됐지만_청크_0개면_False():
    """관련성 필터/근접중복으로 임베딩이 하나도 안 남은 경우."""
    crawled = {"dcinside": (5, "ok")}
    with _Stubbed(crawled, {"documents": 0, "chunks": 0}) as s:
        ready = rag_main.ensure_keyword_ready("관련없는키워드")
    assert ready is False, "청크가 0개면 검색해도 근거가 없으므로 False여야 함"
    assert s.added == [], s.added
    print("[OK] ensure_keyword_ready: 크롤됐어도 청크 0개면 False")


def test_수집량_적으면_Keywords_추가안하고_True():
    """이번 질의에는 쓰되, 매일 도는 배치 대상으로는 편입하지 않는다."""
    crawled = {"dcinside": (2, "ok")}
    few_docs = MIN_COMMUNITY_DOCS_FOR_TAVILY - 1
    with _Stubbed(crawled, {"documents": few_docs, "chunks": 3}) as s:
        ready = rag_main.ensure_keyword_ready("오타키워드")
    assert ready is True, "청크가 있으면 이번 질의에는 답변 가능해야 함"
    assert s.added == [], f"수집량이 적으면 Keywords.md에 추가하면 안 됨: {s.added}"
    print("[OK] ensure_keyword_ready: 수집량 적으면 Keywords.md 미추가하되 True")


def test_수집량_충분하면_Keywords_추가하고_True():
    crawled = {"dcinside": (9, "ok")}
    with _Stubbed(crawled, {"documents": MIN_COMMUNITY_DOCS_FOR_TAVILY, "chunks": 20}) as s:
        ready = rag_main.ensure_keyword_ready("진짜신조어")
    assert ready is True
    assert s.added == ["진짜신조어"], s.added
    print("[OK] ensure_keyword_ready: 수집량 충분하면 Keywords.md 추가하고 True")


def test_select_keyword_빈_목록이어도_입력을_받는다():
    """예전엔 임베딩된 키워드가 없으면 입력 전에 종료해서 온디맨드를 쓸 수 없었다."""
    selected = rag_main.select_keyword([], ask=lambda _: "새키워드")
    assert selected == "새키워드", selected
    print("[OK] select_keyword: 빈 목록에서도 입력을 받아 온디맨드로 넘어감")


def test_select_keyword_번호는_목록에서_고른다():
    selected = rag_main.select_keyword(["야르", "아자스"], ask=lambda _: "2")
    assert selected == "아자스", selected
    print("[OK] select_keyword: 번호 입력은 목록에서 선택")


def test_select_keyword_0번은_숫자_키워드를_받는다():
    """'3' 같은 순수 숫자 신조어가 목록 번호에 먹히지 않게 하는 통로."""
    answers = iter(["0", "3"])
    selected = rag_main.select_keyword(["야르", "아자스", "김치"], ask=lambda _: next(answers))
    assert selected == "3", selected
    print("[OK] select_keyword: 0번으로 숫자 키워드도 새 키워드로 입력 가능")


def test_select_keyword_범위밖_입력은_새_키워드():
    selected = rag_main.select_keyword(["야르"], ask=lambda _: "싹싹김치")
    assert selected == "싹싹김치", selected
    print("[OK] select_keyword: 목록에 없는 텍스트는 새 키워드로 간주")


def test_select_keyword_빈_입력은_None():
    assert rag_main.select_keyword(["야르"], ask=lambda _: "   ") is None
    assert rag_main.select_keyword([], ask=lambda _: "") is None
    print("[OK] select_keyword: 빈 입력은 None(취소)")


if __name__ == "__main__":
    test_select_keyword_빈_목록이어도_입력을_받는다()
    test_select_keyword_번호는_목록에서_고른다()
    test_select_keyword_0번은_숫자_키워드를_받는다()
    test_select_keyword_범위밖_입력은_새_키워드()
    test_select_keyword_빈_입력은_None()
    test_크롤_0건이면_False()
    test_크롤은_됐지만_청크_0개면_False()
    test_수집량_적으면_Keywords_추가안하고_True()
    test_수집량_충분하면_Keywords_추가하고_True()
    print("\nALL PASS ✅")
