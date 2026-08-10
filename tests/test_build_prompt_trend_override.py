"""
build_prompt의 trend_info 파라미터 동작 검증.
trend_info를 명시적으로 주면 format_trend_context(실시간 조회)를 호출하면 안 된다 —
API 서버가 캐시된 트렌드를 넘길 때 뒷문으로 실시간 호출이 새 나가는 걸 막기 위함.

실행: uv run python tests/test_build_prompt_trend_override.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.pipeline as pipeline


def test_trend_info를_주면_실시간_조회를_안한다():
    calls = []

    def fake_format_trend_context(keyword, result=None):
        calls.append(keyword)
        return "실시간조회됨"

    original = pipeline.format_trend_context
    pipeline.format_trend_context = fake_format_trend_context
    try:
        prompt = pipeline.build_prompt("야르", ["청크1"], trend_info="캐시된트렌드")
        assert "캐시된트렌드" in prompt, prompt
        assert calls == [], f"trend_info를 줬는데도 실시간 조회가 호출됨: {calls}"
    finally:
        pipeline.format_trend_context = original
    print("[OK] trend_info 명시 시 실시간 조회 생략")


def test_trend_info_생략하면_기존처럼_실시간_조회한다():
    calls = []

    def fake_format_trend_context(keyword, result=None):
        calls.append(keyword)
        return "실시간조회됨"

    original = pipeline.format_trend_context
    pipeline.format_trend_context = fake_format_trend_context
    try:
        prompt = pipeline.build_prompt("야르", ["청크1"])
        assert "실시간조회됨" in prompt, prompt
        assert calls == ["야르"], calls
    finally:
        pipeline.format_trend_context = original
    print("[OK] trend_info 생략 시 하위호환(실시간 조회) 유지")


if __name__ == "__main__":
    test_trend_info를_주면_실시간_조회를_안한다()
    test_trend_info_생략하면_기존처럼_실시간_조회한다()
    print("\nALL PASS ✅")
