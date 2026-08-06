"""
cleaner.py
크롤링된 원문(content)의 노이즈를 제거해 clean_content를 생성.

제거/치환 대상:
  - HTML 태그 / 엔티티        : 일부 dcinside/natepann 댓글 데이터에 <img>, <div> 등
                              태그가 그대로 남아있는 경우가 있어 안전망으로 제거
  - URL                     : 의미 분석에 노이즈이므로 제거
  - 이메일 / 전화번호        : 개인정보 보호를 위해 마스킹 ([EMAIL], [PHONE])
  - 반복 문자 (ㅋㅋㅋㅋㅋㅋ 등) : 완전히 지우면 밈 특유의 어감(초성체/이모티콘)이
                              사라지므로, 삭제하지 않고 REPEAT_CHAR_LIMIT 개로 축약만 함
  - UI 상투어("이웃추가" 등)  : 본문 추출이 사이드바/위젯까지 긁어왔을 때 섞여 들어옴.
                              config.BOILERPLATE_PHRASES를 quality_test/signals.py의
                              boilerplate_hits()와 공유 — 탐지 기준과 제거 기준이
                              어긋나지 않도록 목록을 한 곳(config)에 둔다.
  - 중복 공백/개행           : 정규화

메타데이터(키워드, 출처, URL 등)는 이 함수가 건드리지 않음 — 호출부(pipeline.py)가
원본 content는 그대로 두고 clean_content만 별도 필드에 저장.
"""

import html
import re

from config.config_cilent import BOILERPLATE_PHRASES, REPEAT_CHAR_LIMIT

# 크롤러들은 대부분 bs4 get_text()로 이미 태그를 제거한 텍스트를 넘기지만,
# dcinside 댓글처럼 JSON 응답을 정규식으로만 정제하는 경로에서는 태그/엔티티가
# 남는 경우가 있었음(예: 크롤러 수정 이전에 수집된 낡은 데이터). 이중 안전망으로
# 여기서도 한 번 더 제거한다.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 한국 휴대폰(010-1234-5678 등) / 지역번호 유선전화를 느슨하게 포괄.
# 완벽한 전화번호 검증이 목적이 아니라, 본문에 노출된 개인 연락처를 최대한
# 걸러내는 것이 목적이므로 과탐(false positive)보다 누락 방지를 우선함.
_PHONE_RE = re.compile(r"(?:\+?82[-\s]?)?0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}")
_REPEAT_CHAR_RE = re.compile(r"(.)\1{" + str(REPEAT_CHAR_LIMIT) + r",}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """원문 텍스트를 정제해 반환. 빈 문자열/None이면 빈 문자열 반환."""
    if not text:
        return ""

    text = html.unescape(text)  # &nbsp;, &quot; 등 HTML 엔티티 복원
    text = _HTML_TAG_RE.sub(" ", text)  # 태그 제거. 단어가 붙지 않도록 공백으로 치환
    text = text.replace("\xa0", " ")  # non-breaking space -> 일반 공백
    text = _URL_RE.sub("", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _REPEAT_CHAR_RE.sub(lambda m: m.group(1) * REPEAT_CHAR_LIMIT, text)
    for phrase in BOILERPLATE_PHRASES:
        text = text.replace(phrase, " ")
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)

    # 각 줄의 앞뒤 공백 제거 (줄 자체는 유지 — 섹션 마커 등 줄 단위 구조 보존)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


if __name__ == "__main__":
    sample = (
        "ㅋㅋㅋㅋㅋㅋㅋㅋㅋ 완전 웃기다 010-1234-5678로 연락주세요\n\n\n"
        "https://example.com/abc 참고하시고 test@example.com 으로도 메일 주세요"
    )
    print(clean_text(sample))
