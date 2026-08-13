"""
analyze()가 ChatNVIDIA를 생성할 때 max_completion_tokens=4096으로 호출하는지 확인.
실제 NVIDIA API 호출은 하지 않는다 — ChatNVIDIA 클래스 자체를 fake로 바꿔치기한다.

실행: uv run python tests/test_analyze_token_limit.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.pipeline as pipeline


class _FakeResponse:
    content = "가짜 응답"


class _FakeChatNVIDIA:
    captured_kwargs = None

    def __init__(self, **kwargs):
        _FakeChatNVIDIA.captured_kwargs = kwargs

    def invoke(self, messages):
        return _FakeResponse()


def test_max_completion_tokens는_4096이다():
    original = pipeline.ChatNVIDIA
    pipeline.ChatNVIDIA = _FakeChatNVIDIA
    try:
        result = pipeline.analyze("테스트 프롬프트")
        assert result == "가짜 응답", result
        assert _FakeChatNVIDIA.captured_kwargs["max_completion_tokens"] == 4096, _FakeChatNVIDIA.captured_kwargs
    finally:
        pipeline.ChatNVIDIA = original
    print("[OK] max_completion_tokens=4096")


if __name__ == "__main__":
    test_max_completion_tokens는_4096이다()
    print("\nALL PASS")
