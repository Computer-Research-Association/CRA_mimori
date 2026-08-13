"""
facet_search가 4개 facet을 병렬로 검색하는지(순차 실행보다 훨씬 빠른지) +
facet별 결과가 올바르게 모이는지 확인. 실제 Qdrant 호출 없음
(search_relevant_chunks를 sleep 포함 fake로 대체).

실행: uv run python tests/test_facet_search_parallel.py
"""
import os
import sys
import threading
import time
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.rag_pipeline as rag_pipeline
from analysis.rag_pipeline import facet_search

_WORDS = ["사과바나나사과", "컴퓨터키보드모니터", "하늘구름비바람", "축구농구야구배구"]
_word_lock = threading.Lock()
_word_index = {"n": 0}


def _fake_search_relevant_chunks(keyword, dense_vec, sparse, **kwargs):
    time.sleep(0.15)  # 실제 Qdrant 왕복 시간을 흉내냄
    with _word_lock:
        word = _WORDS[_word_index["n"] % len(_WORDS)]
        _word_index["n"] += 1
    return [SimpleNamespace(
        id=uuid.uuid4().hex,
        score=1.0,
        payload={"text": f"{keyword} {word}", "title": "제목", "url": "https://example.com", "source": "tavily"},
    )]


def _facet_config():
    common = {"top_k": 5, "min_length": 30, "over_fetch_factor": 3, "min_dense_score": 0.3, "max_per_source": 2}
    return {
        "의미": {"question": "야르, 무슨 의미?", **common},
        "유행_이유": {"question": "야르, 왜 유행했나요?", **common},
        "사용법": {"question": "야르, 어떻게 사용하나요?", **common},
        "사용자층": {"question": "야르, 주로 누가 사용하나요?", **common},
    }


def test_4개_facet을_병렬로_검색해_각각_결과를_모은다():
    _word_index["n"] = 0
    original = rag_pipeline.search_relevant_chunks
    rag_pipeline.search_relevant_chunks = _fake_search_relevant_chunks
    try:
        facet_config = _facet_config()
        facet_vectors = {name: {"dense": [0.1], "sparse": {}} for name in facet_config}

        start = time.monotonic()
        merged_points, diagnostics = facet_search("야르", facet_config, facet_vectors=facet_vectors, is_relevant=True)
        elapsed = time.monotonic() - start

        assert elapsed < 0.35, f"4개를 병렬로 돌렸다면 0.35초 안에 끝나야 함(순차면 약 0.6초): {elapsed:.2f}s"
        assert set(diagnostics["facet_points"].keys()) == set(facet_config.keys()), diagnostics["facet_points"].keys()
        for name, points in diagnostics["facet_points"].items():
            assert len(points) == 1, (name, points)
        assert len(merged_points) >= 1, merged_points
    finally:
        rag_pipeline.search_relevant_chunks = original
    print(f"[OK] facet 4개 병렬 검색 완료 (elapsed={elapsed:.3f}s), facet별 결과 정상 취합")


if __name__ == "__main__":
    test_4개_facet을_병렬로_검색해_각각_결과를_모은다()
    print("\nALL PASS")
