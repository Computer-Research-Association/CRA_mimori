"""
analysis.pipeline.merge_keyword() 단위 테스트. 실제 Mongo/Qdrant 없이
fake collection/client + monkeypatch로 검증.

실행: uv run python tests/test_merge_keyword.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.pipeline as pipeline


class _FakeCollection:
    def __init__(self, docs=None):
        self._docs = docs or []
        self.update_many_calls = []

    def update_many(self, filter_, update):
        self.update_many_calls.append((filter_, update))
        keyword = filter_.get("keyword")
        new_keyword = update["$set"]["keyword"]
        for doc in self._docs:
            if doc.get("keyword") == keyword:
                doc["keyword"] = new_keyword

    def update_one(self, filter_, update):
        # hide_keyword가 쓰는 $setOnInsert + upsert 흉내 — 이미 있으면 아무 것도 안 함.
        pass


class _FakeQdrantClient:
    def __init__(self):
        self.set_payload_calls = []

    def set_payload(self, **kwargs):
        self.set_payload_calls.append(kwargs)


def _setup(memes_docs=None):
    fake_collections = {}

    def fake_get_collection(name=None):
        key = name or "memes"
        fake_collections.setdefault(key, _FakeCollection(memes_docs if key == "memes" else None))
        return fake_collections[key]

    fake_client = _FakeQdrantClient()
    return fake_collections, fake_get_collection, fake_client


def test_source와_target이_같으면_ValueError():
    original_get_collection = pipeline.get_collection
    try:
        raised = False
        try:
            pipeline.merge_keyword("야르", "야르")
        except ValueError:
            raised = True
        assert raised, "같은 키워드로 병합하면 ValueError여야 함"
    finally:
        pipeline.get_collection = original_get_collection
    print("[OK] source == target -> ValueError")


def test_memes_cleaned_memes_llm_requests의_keyword를_target으로_바꾼다():
    fake_collections, fake_get_collection, fake_client = _setup(
        memes_docs=[{"keyword": "야호~"}, {"keyword": "야호~"}, {"keyword": "다른키워드"}]
    )
    original_get_collection = pipeline.get_collection
    original_get_client = pipeline.get_client
    original_hide = pipeline.hide_keyword
    hide_calls = []
    pipeline.get_collection = fake_get_collection
    pipeline.get_client = lambda: fake_client
    pipeline.hide_keyword = lambda kw: hide_calls.append(kw)
    try:
        result = pipeline.merge_keyword("야호~", "거제 야호~")
        assert result == {"source": "야호~", "target": "거제 야호~"}, result

        memes = fake_collections["memes"]
        assert memes.update_many_calls == [
            ({"keyword": "야호~"}, {"$set": {"keyword": "거제 야호~"}})
        ], memes.update_many_calls
        # fake의 update_many가 실제로 문서를 옮겨놨는지도 확인 — "다른키워드"는 안 건드림.
        assert [d["keyword"] for d in memes._docs] == ["거제 야호~", "거제 야호~", "다른키워드"], memes._docs

        cleaned = fake_collections["cleaned_memes"]
        assert cleaned.update_many_calls == [
            ({"keyword": "야호~"}, {"$set": {"keyword": "거제 야호~"}})
        ], cleaned.update_many_calls

        llm = fake_collections["llm_requests"]
        assert llm.update_many_calls == [
            ({"keyword": "야호~"}, {"$set": {"keyword": "거제 야호~"}})
        ], llm.update_many_calls

        # trend_scores는 일부러 안 건드린다 — (keyword, date) 조합 중복 방지.
        assert "trend_scores" not in fake_collections, "trend_scores는 병합 대상이 아니어야 함"

        assert hide_calls == ["야호~"], hide_calls
    finally:
        pipeline.get_collection = original_get_collection
        pipeline.get_client = original_get_client
        pipeline.hide_keyword = original_hide
    print("[OK] memes/cleaned_memes/llm_requests만 옮기고 trend_scores는 그대로, source는 숨김")


def test_Qdrant_payload도_target으로_바뀐다():
    fake_collections, fake_get_collection, fake_client = _setup()
    original_get_collection = pipeline.get_collection
    original_get_client = pipeline.get_client
    original_hide = pipeline.hide_keyword
    pipeline.get_collection = fake_get_collection
    pipeline.get_client = lambda: fake_client
    pipeline.hide_keyword = lambda kw: None
    try:
        pipeline.merge_keyword("야호~", "거제 야호~")
        assert len(fake_client.set_payload_calls) == 1, fake_client.set_payload_calls
        call = fake_client.set_payload_calls[0]
        assert call["payload"] == {"keyword": "거제 야호~"}, call
    finally:
        pipeline.get_collection = original_get_collection
        pipeline.get_client = original_get_client
        pipeline.hide_keyword = original_hide
    print("[OK] Qdrant payload.keyword도 target으로 갱신됨(필터: source)")


if __name__ == "__main__":
    test_source와_target이_같으면_ValueError()
    test_memes_cleaned_memes_llm_requests의_keyword를_target으로_바꾼다()
    test_Qdrant_payload도_target으로_바뀐다()
    print("\nALL PASS ✅")
