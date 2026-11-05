"""NIM_MOCK_URL을 지정하면 실제 SDK HTTP 경로를 로컬 Postman 대역으로 검증한다."""
import os

import pytest
import requests

import analysis.pipeline as pipeline

MOCK_URL = os.getenv('NIM_MOCK_URL', '')
pytestmark = pytest.mark.skipif(not MOCK_URL, reason='로컬 NIM 대역 주소를 지정했을 때 실행')


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(pipeline, 'ANALYSIS_BASE_URL', MOCK_URL + '/v1')
    monkeypatch.setattr(pipeline, 'NIM_KEY', 'local-test-key')
    monkeypatch.setattr(pipeline.time, 'sleep', lambda seconds: None)
    def mode(value):
        response = requests.post(MOCK_URL + '/__control', json={'mode': value}, timeout=5)
        response.raise_for_status()
    return mode


def calls():
    return requests.get(MOCK_URL + '/__calls', timeout=5).json()['calls']


def test_실제_HTTP_스트림과_요약_설정이_전달된다(transport):
    transport('success')
    chunks = []
    text = pipeline.analyze('근거를 요약하세요', on_chunk=chunks.append)
    assert text == '근거에 있는 설명입니다. [자료 1]'
    assert chunks[-1] == text
    sent = calls()
    assert len(sent) == 1
    assert sent[0]['stream'] is True
    assert sent[0]['max_tokens'] == 4096
    assert sent[0]['temperature'] == 0.1
    assert sent[0].get('chat_template_kwargs') in (None, {})


def test_종료된모델_오류는_재시도하지_않는다(transport):
    transport('retired')
    with pytest.raises(Exception, match='410'):
        pipeline.analyze('분석', on_chunk=lambda text: None)
    assert len(calls()) == 1


def test_일시적503은_최대3회_후_실패한다(transport):
    transport('unavailable')
    with pytest.raises(Exception, match='503'):
        pipeline.analyze('분석', on_chunk=lambda text: None)
    assert len(calls()) == 3


def test_빈응답을_완성된_분석으로_반환하지_않는다(transport):
    transport('empty')
    with pytest.raises(ValueError, match='답변을 받지 못했습니다'):
        pipeline.analyze('분석', on_chunk=lambda text: None)
    assert len(calls()) == 1
