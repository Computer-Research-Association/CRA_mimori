"""
analyze()가 ChatNVIDIA를 생성할 때 max_completion_tokens=4096으로 호출하는지,
그리고 응답의 finish_reason(특히 "length" = 토큰 상한에 걸려 잘림)을 로그로
남기는지 확인. 실제 NVIDIA API 호출은 하지 않는다 — ChatNVIDIA 클래스 자체를
fake로 바꿔치기한다.

실행: uv run python tests/test_analyze_token_limit.py
"""
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.pipeline as pipeline


class _FakeResponse:
    def __init__(self, content="가짜 응답", finish_reason="stop"):
        self.content = content
        self.response_metadata = {"finish_reason": finish_reason} if finish_reason else {}


class _FakeStreamChunk:
    def __init__(self, content, finish_reason=None):
        self.content = content
        # langchain_nvidia_ai_endpoints는 스트림의 마지막 청크에만 finish_reason을
        # response_metadata에 채운다 — 중간 청크는 빈 dict로 흉내낸다.
        self.response_metadata = {"finish_reason": finish_reason} if finish_reason else {}


class _FakeChatNVIDIA:
    captured_kwargs = None
    response = _FakeResponse()
    stream_chunks = None

    def __init__(self, **kwargs):
        _FakeChatNVIDIA.captured_kwargs = kwargs

    def invoke(self, messages):
        return _FakeChatNVIDIA.response

    def stream(self, messages):
        return iter(_FakeChatNVIDIA.stream_chunks)


def _run_with_fake(fn):
    original = pipeline.ChatNVIDIA
    pipeline.ChatNVIDIA = _FakeChatNVIDIA
    try:
        return fn()
    finally:
        pipeline.ChatNVIDIA = original


def test_max_completion_tokens는_4096이다():
    _FakeChatNVIDIA.response = _FakeResponse()
    result = _run_with_fake(lambda: pipeline.analyze("테스트 프롬프트"))
    assert result == "가짜 응답", result
    assert _FakeChatNVIDIA.captured_kwargs["max_completion_tokens"] == 4096, _FakeChatNVIDIA.captured_kwargs
    print("[OK] max_completion_tokens=4096")


def test_temperature는_0이다():
    _FakeChatNVIDIA.response = _FakeResponse()
    _run_with_fake(lambda: pipeline.analyze("테스트 프롬프트"))
    assert _FakeChatNVIDIA.captured_kwargs["temperature"] == 0, _FakeChatNVIDIA.captured_kwargs
    print("[OK] temperature=0 (근거 기반 답변엔 결정적 디코딩)")


def test_finish_reason이_length면_경고_로그를_남긴다():
    """max_completion_tokens 상한에 걸려 잘린 경우(finish_reason=length)를
    조용히 넘기지 않고 눈에 띄게 로그로 남기는지 확인 — 이게 있어야 상한을
    올려야 하는지 실측 근거를 모을 수 있다."""
    _FakeChatNVIDIA.response = _FakeResponse(finish_reason="length")
    buf = io.StringIO()
    with redirect_stdout(buf):
        _run_with_fake(lambda: pipeline.analyze("테스트 프롬프트"))
    output = buf.getvalue()
    assert "finish_reason=length" in output, output
    assert "잘렸습니다" in output, output
    print("[OK] finish_reason=length -> 경고 로그")


def test_finish_reason이_stop이면_평범하게_로그를_남긴다():
    _FakeChatNVIDIA.response = _FakeResponse(finish_reason="stop")
    buf = io.StringIO()
    with redirect_stdout(buf):
        _run_with_fake(lambda: pipeline.analyze("테스트 프롬프트"))
    output = buf.getvalue()
    assert "finish_reason=stop" in output, output
    assert "잘렸습니다" not in output, output
    print("[OK] finish_reason=stop -> 경고 없이 평범한 로그")


def test_스트리밍_경로도_마지막_청크의_finish_reason을_기록한다():
    _FakeChatNVIDIA.stream_chunks = [
        _FakeStreamChunk("안"),
        _FakeStreamChunk("녕"),
        _FakeStreamChunk("", finish_reason="length"),
    ]
    chunks_seen = []
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = _run_with_fake(
            lambda: pipeline.analyze("테스트 프롬프트", on_chunk=chunks_seen.append)
        )
    assert result == "안녕", result
    assert chunks_seen == ["안", "안녕", "안녕"], chunks_seen
    assert "finish_reason=length" in buf.getvalue(), buf.getvalue()
    print("[OK] 스트리밍 경로 — 마지막 청크의 finish_reason만 기록됨")


if __name__ == "__main__":
    test_max_completion_tokens는_4096이다()
    test_temperature는_0이다()
    test_finish_reason이_length면_경고_로그를_남긴다()
    test_finish_reason이_stop이면_평범하게_로그를_남긴다()
    test_스트리밍_경로도_마지막_청크의_finish_reason을_기록한다()
    print("\nALL PASS ✅")
