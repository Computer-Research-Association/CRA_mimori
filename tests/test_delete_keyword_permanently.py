"""
delete_keyword_permanently()가 llm_requests 컬렉션도 함께 정리하는지 확인.
실제 Mongo/Qdrant 없이 fake collection + monkeypatch로 검증.

실행: uv run python tests/test_delete_keyword_permanently.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.pipeline as pipeline


class _FakeCollection:
    def __init__(self):
        self.deleted_many_filters = []
        self.deleted_one_filters = []

    def delete_many(self, filter_):
        self.deleted_many_filters.append(filter_)

    def delete_one(self, filter_):
        self.deleted_one_filters.append(filter_)


class _FakeQdrantClient:
    def __init__(self):
        self.delete_calls = []

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)


def test_삭제시_llm_requests도_keyword_기준으로_지운다():
    fake_collections = {}

    def fake_get_collection(name=None):
        key = name or "memes"
        fake_collections.setdefault(key, _FakeCollection())
        return fake_collections[key]

    original_get_collection = pipeline.get_collection
    original_get_client = pipeline.get_client
    pipeline.get_collection = fake_get_collection
    pipeline.get_client = lambda: _FakeQdrantClient()
    try:
        pipeline.delete_keyword_permanently("야르")
        assert fake_collections["llm_requests"].deleted_many_filters == [{"keyword": "야르"}], \
            fake_collections["llm_requests"].deleted_many_filters
    finally:
        pipeline.get_collection = original_get_collection
        pipeline.get_client = original_get_client
    print("[OK] delete_keyword_permanently가 llm_requests도 keyword 기준으로 정리함")


if __name__ == "__main__":
    test_삭제시_llm_requests도_keyword_기준으로_지운다()
    print("\nALL PASS ✅")
