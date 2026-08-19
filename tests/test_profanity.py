"""
analysis.profanity.soften_profanity() 단위 테스트 (외부 의존 없음).

실행: uv run python tests/test_profanity.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.profanity import soften_profanity


def test_실제_ㅈㄱㄴ_답변에서_욕설_어근이_마스킹된다():
    original = (
        "'ㅈㄱㄴ'은 '좆같은 놈'의 앞글자만 따서 만든 약어입니다. '좆같은 놈'은 "
        "\"정말 못생기고 비열한 사람\"이라는 뜻의 욕설이죠. 실제 글에서는 \"ㅈㄱㄴ인 놈들\", "
        "\"ㅈㄱㄴ) 민좆 강원 당원 투표...\"처럼 사람을 비하할 때 쓰이고 있습니다."
    )
    result = soften_profanity(original)
    assert "좆같은" not in result, result
    assert "[욕설]은 놈" in result, result
    assert "ㅈㄱㄴ" in result, "욕설이 아닌 부분은 그대로 남아야 함"
    print("[OK] 실제 ㅈㄱㄴ 답변 -> 욕설 어근만 [욕설]로 마스킹")


def test_한_문장에_여러번_나와도_전부_마스킹된다():
    result = soften_profanity("씨발 진짜 개새끼네 시발")
    assert result == "[욕설] 진짜 [욕설]네 [욕설]", result
    print("[OK] 여러 번 등장 -> 전부 마스킹")


def test_욕설이_없으면_그대로_반환한다():
    text = "이 밈은 2024년부터 유행했습니다."
    assert soften_profanity(text) == text
    print("[OK] 욕설 없음 -> 원문 그대로")


def test_None은_그대로_None을_반환한다():
    assert soften_profanity(None) is None
    print("[OK] None -> None")


def test_애매한_단어는_목록에_없어_오검열되지_않는다():
    """'미친'(감탄사), '새끼'(친근한 호칭), '걸레'(원래 뜻: 청소용 천) 등은 문맥
    없이 욕설로 단정하기 애매해서 일부러 목록에서 뺐다 — 이런 단어가 들어간
    정상적인 문장이 오검열되면 안 된다."""
    text = "와 이거 미친 존잘이다. 이 강아지 새끼 진짜 귀엽다. 걸레로 바닥을 닦았다."
    assert soften_profanity(text) == text, soften_profanity(text)
    print("[OK] 애매한 단어(미친/새끼/걸레)는 오검열 없이 통과")


def test_출처_제목에도_적용된다():
    title = "ㅈㄱㄴ) 민좆 강원 당원 투표 김민새가 1위 (50.30%)"
    result = soften_profanity(title)
    assert "민좆" not in result, result
    assert "[욕설]" in result, result
    print("[OK] 출처 제목도 동일 함수로 마스킹 가능")


if __name__ == "__main__":
    test_실제_ㅈㄱㄴ_답변에서_욕설_어근이_마스킹된다()
    test_한_문장에_여러번_나와도_전부_마스킹된다()
    test_욕설이_없으면_그대로_반환한다()
    test_None은_그대로_None을_반환한다()
    test_애매한_단어는_목록에_없어_오검열되지_않는다()
    test_출처_제목에도_적용된다()
    print("\nALL PASS ✅")
