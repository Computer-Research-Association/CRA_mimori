"""트렌드 해석 입력이 누락일·0 분모·당일 부분 집계를 왜곡하지 않는지 검증한다."""
from datetime import date, timedelta
from trend.context import format_trend_observations
from analysis.rag_pipeline import build_facet_prompt


def trend(values):
    first = date(2026, 10, 1)
    return {'date': '2026-11-01', 'sources': ['naver'], 'naver_z': -0.68,
            'ratios': [{'date': (first + timedelta(days=i)).isoformat(), 'ratio': value} for i, value in enumerate(values)]}


def test_두_7일_평균은_판정_z와_별도로_설명한다():
    context = format_trend_observations(trend([20] * 7 + [10] * 7))
    assert '2026-10-08~2026-10-14 7일 평균 10.00' in context
    assert '직전 7일 평균 20.00' in context and '-50.0%' in context
    assert '상태를 직접 결정하는 값이 아니다' in context
    assert '카카오' not in context and '구글' not in context


def test_이전값_0이면_무한대_증가율을_만들지_않는다():
    context = format_trend_observations(trend([0] * 7 + [10] * 7))
    assert '증감률 계산 불가' in context
    assert 'inf' not in context


def test_누락일이_있으면_관측_14개를_연속_14일로_오해하지_않는다():
    data = trend([20] * 7 + [10] * 8)
    del data['ratios'][6]
    assert '직전 7일 평균' not in format_trend_observations(data)


def test_부분집계와_유효하지않은_관측을_사용하지_않는다():
    data = trend([10] * 14)
    data['ratios'] += [{'date': '2026-11-01', 'ratio': 999}, {'date': '2026-10-15', 'ratio': float('nan')}]
    context = format_trend_observations(data)
    assert '999' not in context and 'nan' not in context
    assert '관측일 14개' in context


def test_최고점과_마지막관측일을_명시적으로_구분한다():
    context = format_trend_observations(trend([3, 100, 98]))
    assert '첫 관측값 3.00 (2026-10-01)' in context
    assert '마지막 관측일 2026-10-03, 마지막 관측값 98.00' in context
    assert '구간 최고값 100.00 (2026-10-02)' in context


def test_실제_집계_설명이_분석_프롬프트에_포함된다():
    prompt = build_facet_prompt('영크크', [], trend_info='검증된 관측 기간 2026-10-01~2026-10-14')
    assert '검증된 관측 기간 2026-10-01~2026-10-14' in prompt
    assert '### 5. 트렌드 맥락' in prompt
