"""분석 근거의 분량·출처 번호와 인용 검증을 관리한다. 사실 여부 판정기는 아니다."""
import copy
import math
import re
from urllib.parse import unquote, urlsplit

from config.config_cilent import (
    ANALYSIS_MAX_CONTEXT_CHARS, ANALYSIS_MAX_SOURCE_CHARS, ANALYSIS_MAX_SOURCES,
    ANALYSIS_DISTINCT_EXPRESSIONS,
)
from quality_test.matching import normalize

_CITATION = re.compile(r"\[자료\s*(\d+)\]")
_TREND_SECTION = re.compile(
    r'(?m)^(?:#{1,6}[ \t]*)?(?:\*\*)?5[.)][ \t]*(?:\*\*)?트렌드[ \t]*맥락'
    r'(?:\*\*)?[ \t]*[:：]?[ \t]*(?:\n|$)?'
)


def source_identity(url):
    """모바일·인코딩 표현만 다른 동일 게시물을 여러 근거로 세지 않는다."""
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if host == 'm.blog.naver.com':
        host = 'blog.naver.com'
    path = unquote(parsed.path)
    if host.endswith('.tistory.com') and path.startswith('/m/'):
        path = path[2:]
    return host, path.rstrip('/'), parsed.query


def compose_analysis(text, trend):
    """모델의 상세 트렌드 설명을 보존하고, 누락과 집계값 불일치를 거른다."""
    if not trend or not trend.get('date'):
        return text.strip()
    parts = _TREND_SECTION.split(text, maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        raise ValueError('트렌드 맥락 설명이 완성되지 않았습니다.')
    explanation = parts[1]
    expected_date = tuple(int(part) for part in str(trend['date']).split('-'))
    mentioned_dates = {
        tuple(int(part) for part in match)
        for match in re.findall(r'(\d{4})\s*(?:[-./]|년)\s*(\d{1,2})\s*(?:[-./]|월)\s*(\d{1,2})', explanation)
    }
    if expected_date not in mentioned_dates or trend.get('status', '') not in explanation:
        raise ValueError('트렌드 설명의 집계일·상태가 실제 판정과 일치하지 않습니다.')
    scores = [trend.get(key) for key in ('final_z', 'z_score', 'naver_z', 'kakao_z', 'google_z')]
    scores = [value for value in scores if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)]
    for match in re.finditer(r'(?<![A-Za-z])z(?:[-_ ]*score|값|점수)?\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)', explanation.replace('**', ''), re.I):
        literal = match.group(1)
        decimals = len(literal.split('.')[1]) if '.' in literal else 0
        tolerance = 0.5 * 10 ** -decimals + 1e-8
        if not any(abs(float(literal) - value) <= tolerance for value in scores):
            raise ValueError('트렌드 설명의 z-score가 실제 집계값과 일치하지 않습니다.')
    return text.strip()


def prepare_evidence(points, clean_url, keyword=None):
    """검색 순서를 유지하며 같은 URL을 묶고 근거 본문 분량을 제한한다.

    개별 청크에 없는 내용을 만들지 않고, 원래 point와 payload는 수정하지 않는다.
    """
    selected = []
    by_url = {}
    total = 0
    for point in points:
        title = normalize(point.payload.get('title') or '')
        if any(normalize(term) in title for term in ANALYSIS_DISTINCT_EXPRESSIONS.get(keyword, ())):
            continue
        url = clean_url(point.payload.get('url'))
        if not url.startswith(('https://', 'http://')):
            continue
        text = (point.payload.get('text') or '').strip()
        if not text:
            continue
        identity = source_identity(url)
        existing = by_url.get(identity)
        if existing is None:
            if len(selected) >= ANALYSIS_MAX_SOURCES:
                continue
            existing = copy.copy(point)
            existing.payload = {**point.payload, 'url': url, 'text': ''}
            by_url[identity] = existing
            selected.append(existing)
        previous = existing.payload['text']
        if text in previous:
            continue
        separator = '\n\n' if previous else ''
        remaining = min(ANALYSIS_MAX_SOURCE_CHARS - len(previous), ANALYSIS_MAX_CONTEXT_CHARS - total)
        addition = (separator + text)[:max(0, remaining)]
        existing.payload['text'] += addition
        total += len(addition)
        if total >= ANALYSIS_MAX_CONTEXT_CHARS:
            break
    return [point for point in selected if point.payload['text']]


def citation_warning(text, source_count):
    """자료 번호의 존재·범위만 확인한다. 인용한 자료가 주장을 뒷받침하는지는 별도 검토한다."""
    ids = [int(match) for match in _CITATION.findall(text or '')]
    return not ids or any(n < 1 or n > source_count for n in ids)


def link_citations(text, source_count):
    """실제로 제공한 자료 번호만 화면의 출처 목록에 연결한다."""
    if text is None:
        return None
    def replace(match):
        number = int(match.group(1))
        if not 1 <= number <= source_count:
            return '[출처 확인 필요]'
        return f'[자료 {number}](#source-{number})'
    return _CITATION.sub(replace, text)


_SECTION_TITLE = re.compile(
    r'(?m)^(?:#{1,6}\s*)?(?:\*\*)?([1-4])[.)]\s*(?:\*\*)?'
    r'(의미|유행\s*이유|사용법|확인된\s*사용\s*맥락)(?:\*\*)?\s*[:：]?[ \t]*\n?'
)
_SECTION_NAMES = ['의미', '유행 이유', '사용법', '확인된 사용 맥락']


def normalize_analysis_sections(text):
    """네 기본 항목과 선택적 트렌드 해석의 완성을 확인하고 제목을 통일한다."""
    parts = _TREND_SECTION.split(text, maxsplit=1)
    body = parts[0].strip()
    matches = list(_SECTION_TITLE.finditer(body))
    if [m.group(1) for m in matches] != ['1', '2', '3', '4']:
        raise ValueError('분석 항목이 완성되지 않았습니다. 완성된 답변이 필요합니다.')
    sections = []
    for index, match in enumerate(matches):
        content = body[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(body)].strip()
        ending = _CITATION.sub('', content).rstrip(' \n\t*`\"\'’”)]')
        if not ending or not ending.endswith(('.', '!', '?', '다', '요', '죠', '음', '함', '됨', '임')):
            raise ValueError('분석 문장이 중간에 끊겼습니다. 완성된 답변이 필요합니다.')
        sections.append(f'### {index + 1}. {_SECTION_NAMES[index]}\n\n{content}')
    if len(parts) == 2:
        content = parts[1].strip()
        ending = _CITATION.sub('', content).rstrip(' \n\t*`\"\'’”)]')
        if not ending or not ending.endswith(('.', '!', '?', '다', '요', '죠', '음', '함', '됨', '임')):
            raise ValueError('트렌드 설명이 중간에 끊겼습니다. 완성된 답변이 필요합니다.')
        sections.append('### 5. 트렌드 맥락\n\n' + content)
    return '\n\n'.join(sections)
