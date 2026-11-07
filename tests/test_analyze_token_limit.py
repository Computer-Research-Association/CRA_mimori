"""
analyze()가 상세 답변용 출력 상한 16384로 ChatNVIDIA를 호출하는지,
그리고 응답의 finish_reason(특히 "length" = 토큰 상한에 걸려 잘림)을 확인해
불완전한 답변을 거르는지 검증. 실제 NVIDIA API 호출은 하지 않는다 — ChatNVIDIA 클래스 자체를
fake로 바꿔치기한다.

실행: uv run python tests/test_analyze_token_limit.py
"""
import io
import pytest
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
        # 종료 청크의 finish_reason 뒤에도 메타데이터 없는 사용량 청크가 올 수 있다.
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


def test_상세_트렌드_설명을_위한_출력상한은_16384이다():
    _FakeChatNVIDIA.response = _FakeResponse()
    result = _run_with_fake(lambda: pipeline.analyze("테스트 프롬프트"))
    assert result == "가짜 응답", result
    assert _FakeChatNVIDIA.captured_kwargs["max_completion_tokens"] == 16384
    print("[OK] max_completion_tokens=16384")


def test_temperature와_타임아웃은_검증된_설정을_따른다():
    _FakeChatNVIDIA.response = _FakeResponse()
    _run_with_fake(lambda: pipeline.analyze("테스트 프롬프트"))
    assert _FakeChatNVIDIA.captured_kwargs["temperature"] == pipeline.ANALYSIS_TEMPERATURE
    assert _FakeChatNVIDIA.captured_kwargs["timeout"] == 120


def test_기본모델에는_추론끄기_옵션을_전달한다():
    _FakeChatNVIDIA.response = _FakeResponse()
    _run_with_fake(lambda: pipeline.analyze("테스트 프롬프트"))
    assert _FakeChatNVIDIA.captured_kwargs["model"] == "nvidia/nemotron-3.5-super-vl-120b-a12b"
    assert _FakeChatNVIDIA.captured_kwargs["model_kwargs"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_다른모델에_기본모델의_추론옵션을_섞지않는다():
    _FakeChatNVIDIA.response = _FakeResponse()
    _run_with_fake(lambda: pipeline.analyze("테스트 프롬프트", model="다른-모델"))
    assert _FakeChatNVIDIA.captured_kwargs["model_kwargs"] == {}


def test_finish_reason이_length면_경고_로그를_남긴다():
    """길이 제한은 경고하고 불완전한 답변을 반환하지 않는다."""
    _FakeChatNVIDIA.response = _FakeResponse(finish_reason="length")
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(pipeline.IncompleteAnalysisError):
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


def test_사용량_청크가_뒤따라도_잘린_답변을_완료로_반환하지_않는다():
    _FakeChatNVIDIA.stream_chunks = [
        _FakeStreamChunk("중간에 끊긴 답변"),
        _FakeStreamChunk("", finish_reason="length"),
        _FakeStreamChunk(""),
    ]
    with pytest.raises(pipeline.IncompleteAnalysisError):
        _run_with_fake(lambda: pipeline.analyze("테스트", on_chunk=lambda text: None))


def test_사용량_청크가_뒤따르는_정상_답변은_보존한다():
    _FakeChatNVIDIA.stream_chunks = [
        _FakeStreamChunk("완성된 답변입니다."),
        _FakeStreamChunk("", finish_reason="stop"),
        _FakeStreamChunk(""),
    ]
    assert _run_with_fake(lambda: pipeline.analyze("테스트", on_chunk=lambda text: None)) == "완성된 답변입니다."


if __name__ == "__main__":
    test_상세_트렌드_설명을_위한_출력상한은_16384이다()
    test_temperature와_타임아웃은_검증된_설정을_따른다()
    test_finish_reason이_length면_경고_로그를_남긴다()
    test_finish_reason이_stop이면_평범하게_로그를_남긴다()
    test_사용량_청크가_뒤따라도_잘린_답변을_완료로_반환하지_않는다()
    print("\nALL PASS ✅")
