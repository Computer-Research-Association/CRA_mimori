"""
crawlers/youtube_crawler.py의 자막(트랜스크립트) 수집 단위 테스트.
실제 네트워크/API 키 없이 YouTubeTranscriptApi를 monkeypatch로 대체한다.

배경: 영상 설명/댓글만으로는 "이 밈 뜻이 뭔지 영상에서 설명한다"는 류의 영상에서
정작 실제 설명 내용이 빠져, 댓글창의 드립(농담)이 사실처럼 분석에 인용되는 문제가
있었다(실측 사례: '알잘딱깔센'). 공식 API로는 임의 영상의 캡션을 다운로드할 수
없어(OAuth 필요) 비공식 youtube-transcript-api로 보완한다.

실행: uv run python tests/test_youtube_transcript.py
"""
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crawlers.youtube_crawler as yc


@dataclass
class _FakeSnippet:
    text: str


class _FakeApi:
    """YouTubeTranscriptApi() 인스턴스 흉내 — .fetch(video_id, languages=...)만 지원."""

    def __init__(self, snippets=None, raise_exc=None):
        self._snippets = snippets or []
        self._raise_exc = raise_exc

    def fetch(self, video_id, languages=("en",)):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._snippets


def _patch_api(fake_instance):
    original = yc.YouTubeTranscriptApi
    yc.YouTubeTranscriptApi = lambda: fake_instance
    return original


def _restore_api(original):
    yc.YouTubeTranscriptApi = original


def test_자막이_있으면_텍스트로_합쳐진다():
    fake = _FakeApi(snippets=[_FakeSnippet("알잘딱깔센은"), _FakeSnippet("이런 뜻이에요")])
    original = _patch_api(fake)
    try:
        text = yc.fetch_transcript("abc123")
        assert text == "알잘딱깔센은 이런 뜻이에요", text
    finally:
        _restore_api(original)
    print("[OK] 자막 스니펫들이 공백으로 합쳐짐")


def test_자막_없는_영상은_빈_문자열을_반환한다():
    """자막 비활성화/미지원 등은 절대다수 케이스 — 예외를 삼키고 빈 문자열."""
    fake = _FakeApi(raise_exc=RuntimeError("no transcript"))
    original = _patch_api(fake)
    try:
        text = yc.fetch_transcript("abc123")
        assert text == "", text
    finally:
        _restore_api(original)
    print("[OK] 자막 없음/실패 시 예외 없이 빈 문자열 반환")


def test_긴_자막은_상한까지만_잘린다():
    long_text = "가" * (yc.YOUTUBE_MAX_TRANSCRIPT_CHARS + 500)
    fake = _FakeApi(snippets=[_FakeSnippet(long_text)])
    original = _patch_api(fake)
    try:
        text = yc.fetch_transcript("abc123")
        assert len(text) == yc.YOUTUBE_MAX_TRANSCRIPT_CHARS, len(text)
    finally:
        _restore_api(original)
    print("[OK] 자막이 YOUTUBE_MAX_TRANSCRIPT_CHARS까지만 잘림")


def _video():
    return {"video_id": "abc123", "title": "알잘딱깔센 뜻", "published_at": "2026-01-01T00:00:00Z"}


def test_자막이_있으면_설명과_댓글_사이에_자막_섹션이_들어간다():
    doc = yc.build_document(
        keyword="알잘딱깔센",
        video=_video(),
        description="영상 설명입니다",
        transcript="알아서 잘 딱 깔끔하고 센스있게 라는 뜻이에요",
        comments=[{"text": "ㅋㅋㅋ", "like_count": 1}],
    )
    body, _, comment_part = doc["content"].partition("\n\n[댓글]\n")
    assert "[자막]" in body, body
    assert "알아서 잘 딱 깔끔하고 센스있게" in body, body
    # 청커는 "[댓글]" 이전을 전부 본문으로 취급하므로, 자막이 댓글 쪽으로 새면 안 됨.
    assert "알아서 잘 딱 깔끔하고 센스있게" not in comment_part, comment_part
    print("[OK] 자막이 [자막] 섹션으로 설명 뒤·댓글 앞(본문)에 삽입됨")


def test_자막이_없으면_기존과_동일하게_자막_섹션이_생략된다():
    """자막 없는(절대다수) 영상에서 기존 동작(설명+댓글만)이 그대로 유지되는지 확인."""
    doc = yc.build_document(
        keyword="알잘딱깔센",
        video=_video(),
        description="영상 설명입니다",
        transcript="",
        comments=[{"text": "ㅋㅋㅋ", "like_count": 1}],
    )
    assert "[자막]" not in doc["content"], doc["content"]
    assert doc["content"] == "알잘딱깔센 뜻\n\n영상 설명입니다\n\n[댓글]\nㅋㅋㅋ", doc["content"]
    print("[OK] 자막이 없으면 [자막] 섹션 자체가 안 생기고 기존 포맷 그대로 유지됨")


if __name__ == "__main__":
    test_자막이_있으면_텍스트로_합쳐진다()
    test_자막_없는_영상은_빈_문자열을_반환한다()
    test_긴_자막은_상한까지만_잘린다()
    test_자막이_있으면_설명과_댓글_사이에_자막_섹션이_들어간다()
    test_자막이_없으면_기존과_동일하게_자막_섹션이_생략된다()
    print("\nALL PASS ✅")
