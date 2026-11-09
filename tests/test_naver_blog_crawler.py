"""신규 수집원의 관련성·요약 출처·중복·실패 격리를 검증한다."""
from unittest.mock import Mock
import pytest
from pymongo.errors import DuplicateKeyError
from crawlers import naver_blog_crawler as crawler


def item(**changes):
    return {'title': '<b>럭키비키</b> 뜻', 'description': '럭키비키는 불리한 상황도 긍정적으로 해석하는 유행 표현으로 사용됩니다.',
            'link': 'https://blog.naver.com/example/123', 'postdate': '20261109', **changes}


def test_요약과_날짜와_출처를_보존한다():
    doc = crawler.build_document('럭키비키', item())
    assert doc['title'] == '럭키비키 뜻'
    assert doc['source'] == 'naver_blog'
    assert doc['content_kind'] == 'search_snippet'
    assert doc['published_date'].utcoffset().total_seconds() == 9 * 3600
    assert not doc['is_embedded']
    assert crawler.build_document('럭키비키', item())['_id'] == doc['_id']


@pytest.mark.parametrize('changes', [
    {'title':'다른 글', 'description':'키워드가 포함되지 않은 전혀 관계없는 내용으로 구성된 검색 요약입니다.'},
    {'description':'짧음'}, {'link':'javascript:alert(1)'},
])
def test_무관하거나_불완전한_결과는_저장하지_않는다(changes):
    assert crawler.build_document('럭키비키', item(**changes)) is None


def test_중복만_무시하고_DB장애는_전파한다(monkeypatch):
    monkeypatch.setattr(crawler, 'NAVER_BLOG_ENABLED', True)
    monkeypatch.setattr(crawler, 'NAVER_CLIENT_ID', 'test')
    monkeypatch.setattr(crawler, 'NAVER_CLIENT_SECRET', 'test')
    session = Mock()
    session.get.return_value.json.return_value = {'items':[item()]}
    wrapper = Mock()
    wrapper.__enter__ = Mock(return_value=session)
    wrapper.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(crawler.requests, 'Session', lambda: wrapper)
    collection = Mock()
    monkeypatch.setattr(crawler, 'get_collection', lambda: collection)
    collection.insert_one.side_effect = DuplicateKeyError('duplicate')
    assert crawler.crawl_naver_blog('럭키비키') == []
    collection.insert_one.side_effect = RuntimeError('DB 장애')
    with pytest.raises(RuntimeError, match='DB 장애'):
        crawler.crawl_naver_blog('럭키비키')
