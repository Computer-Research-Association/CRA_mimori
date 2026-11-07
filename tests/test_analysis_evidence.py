"""제한된 근거가 원문을 훼손하지 않고 실제 출처 번호로 연결되는지 확인한다."""
from types import SimpleNamespace
import pytest

from analysis.evidence import prepare_evidence, citation_warning, link_citations
from analysis.rag_pipeline import clean_source_url, build_facet_prompt
from analysis.evidence import normalize_analysis_sections


def test_굵은제목과_인라인제목을_분리해_읽기쉽게_표시한다():
    body = '**1. 의미**\n설명입니다 [자료 1].\n\n2. **유행 이유**: 확인할 수 없음.\n\n### 3. 사용법\n예시입니다.\n\n**4. 확인된 사용 맥락**\n자료만으로 확인할 수 없음.'
    result = normalize_analysis_sections(body)
    assert result.startswith('### 1. 의미\n\n설명입니다')
    assert '### 2. 유행 이유\n\n확인할 수 없음.' in result
    assert result.count('### ') == 4


def test_네번째항목이_그외에는으로_잘린_실제형태를_거른다():
    body = '**1. 의미**\n설명입니다 [자료 1].\n\n**2. 유행 이유**\n설명입니다.\n\n**3. 사용법**\n설명입니다.\n\n**4. 확인된 사용 맥락**\n게임이 끝나갈 때였습니다 [자료 1]. 그 외에는'
    with pytest.raises(ValueError, match='중간에 끊겼습니다'):
        normalize_analysis_sections(body)


def test_항목이_누락된_답변을_완성으로_저장하지_않는다():
    with pytest.raises(ValueError, match='항목이 완성되지 않았습니다'):
        normalize_analysis_sections('### 1. 의미\n\n설명입니다.')


def point(url, text):
    return SimpleNamespace(payload={'url': url, 'text': text, 'title': '제목', 'source': 'tavily'})


def test_중복출처를_합치되_원본을_수정하지_않는다():
    original = point('https://example.com/1', '원문 하나')
    selected = prepare_evidence([original, point('https://example.com/1', '다른 청크'), original], clean_source_url)
    assert len(selected) == 1
    assert selected[0].payload['text'] == '원문 하나\n\n다른 청크'
    assert original.payload['text'] == '원문 하나'


def test_근거_본문_분량과_출처수에_상한을_두고_깨진링크는_제외한다():
    points = [point(f'https://example.com/{i}', '가' * 6000) for i in range(12)]
    points.insert(0, point('/goto?url=invalid', '가짜 출처'))
    selected = prepare_evidence(points, clean_source_url)
    assert len(selected) <= 6
    assert all(len(p.payload['text']) <= 1800 for p in selected)
    assert sum(len(p.payload['text']) for p in selected) <= 8000
    assert all(p.payload['url'].startswith('https://') for p in selected)


def test_프롬프트_번호와_출처목록_순서가_일치한다():
    points = [point('https://example.com/1', '첫 근거'), point('https://example.com/2', '두번째 근거')]
    prompt = build_facet_prompt('야르', points, trend_info='')
    assert '[자료 1] [제목: 제목 / https://example.com/1' in prompt
    assert '[자료 2] [제목: 제목 / https://example.com/2' in prompt
    assert '<수집자료>' in prompt and '</수집자료>' in prompt


def test_없는_자료번호는_링크를_만들지_않고_경고한다():
    assert link_citations('설명 [자료 1], 주장 [자료 99]', 2) == '설명 [자료 1](#source-1), 주장 [출처 확인 필요]'
    assert citation_warning('주장 [자료 99]', 2)
    assert citation_warning('출처 없는 설명', 2)
    assert not citation_warning('설명 [자료 1] [자료 2]', 2)
    assert link_citations(None, 0) is None


def test_현재밈과_별개표현이_명시된_자료를_섞지_않는다():
    current = point('https://example.com/current', '게임에서 요이~라고 외친다는 경험담')
    current.payload['title'] = '요이 말투가 뭐임?'
    historical = point('https://example.com/historical', '일본어 준비 출발 신호를 설명')
    historical.payload['title'] = '요이땅! 뜻'
    selected = prepare_evidence([historical, current], clean_source_url, keyword='요이')
    assert len(selected) == 1 and selected[0].payload['url'] == current.payload['url']
    # 사용자가 정확히 별개 표현을 검색한 경우까지 전역 차단하면 안 된다.
    assert len(prepare_evidence([historical], clean_source_url, keyword='요이 땅')) == 1


def test_동일_블로그_모바일주소와_인코딩주소를_중복근거로_세지_않는다():
    points = [point('https://a.tistory.com/entry/%ED%82%B9', '원문'), point('https://a.tistory.com/m/entry/킹', '원문')]
    assert len(prepare_evidence(points, clean_source_url)) == 1
    points = [point('https://blog.naver.com/a/123', '원문'), point('https://m.blog.naver.com/a/123', '원문')]
    assert len(prepare_evidence(points, clean_source_url)) == 1


def test_상세_트렌드_해석을_고정문구로_덮어쓰지_않는다():
    from analysis.evidence import compose_analysis
    text = '1. 의미: 설명 [자료 1]\n\n### 5. 트렌드 맥락\n\n2026-11-04 집계 기준 감소(z-score -0.90)입니다.\n\n최근 검색 관심도의 하락과 과거 유행 기록은 양립할 수 있습니다.'
    result = compose_analysis(text, {'date': '2026-11-04', 'status': '감소', 'final_z': -0.9})
    assert result == text


def test_모델이_집계값을_바꾸면_거부한다():
    from analysis.evidence import compose_analysis
    trend = {'date': '2026-11-04', 'status': '감소', 'final_z': -0.9}
    for explanation in ['오늘 핫함, z=99.', '2026-11-04 감소, z=99.']:
        with pytest.raises(ValueError):
            compose_analysis('### 5. 트렌드 맥락\n\n' + explanation, trend)


def test_집계일을_한글로_표기해도_동일한_날짜로_인식한다():
    from analysis.evidence import compose_analysis
    trend = {'date': '2026-11-06', 'status': '감소', 'final_z': -0.6767}
    text = '### 5. 트렌드 맥락\n\n2026년 11월 6일 기준 감소이며 종합 z-score는 -0.68입니다.'
    assert compose_analysis(text, trend) == text
    with pytest.raises(ValueError, match='집계일'):
        compose_analysis(text.replace('11월 6일', '11월 5일'), trend)


def test_트렌드_항목도_정규화하고_잘린_답변은_거른다():
    body = '\n\n'.join(f'### {i}. {name}\n\n설명입니다.' for i, name in enumerate(['의미', '유행 이유', '사용법', '확인된 사용 맥락'], 1))
    result = normalize_analysis_sections(body + '\n\n**5. 트렌드 맥락**\n\n자세한 해석입니다.')
    assert result.endswith('### 5. 트렌드 맥락\n\n자세한 해석입니다.')
    with pytest.raises(ValueError, match='트렌드 설명'):
        normalize_analysis_sections(body + '\n\n### 5. 트렌드 맥락\n\n그 외에는')
