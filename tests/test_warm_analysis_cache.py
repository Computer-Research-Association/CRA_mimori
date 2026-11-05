"""사전 생성이 기존 답변·진행 작업을 덮지 않고 한 건씩 큐에 넣는지 확인한다."""
from copy import deepcopy

from api.routes import _analyze_job_id
from scripts import warm_analysis_cache as warm


class Collection:
    def __init__(self, docs=()):
        self.docs = {doc['_id']: deepcopy(doc) for doc in docs}
        self.inserted = []

    def find_one(self, query):
        return deepcopy(self.docs.get(query['_id']))

    def find_one_and_update(self, query, update, **kwargs):
        key = query['_id']
        if key not in self.docs:
            self.docs[key] = {'_id': key, **deepcopy(update['$setOnInsert'])}
            self.inserted.append(key)
        return self.find_one(query)

    def update_one(self, query, update):
        doc = self.docs[query['_id']]
        if doc['status'] == query['status']:
            doc.update(deepcopy(update['$set']))


def test_웹기본검색과_같은키로_한번만_큐에_넣는다():
    collection = Collection()
    key = warm.enqueue_keyword(collection, '야르')
    assert key == _analyze_job_id('야르', None)
    warm.enqueue_keyword(collection, '야르')
    assert collection.inserted == [key]
    assert collection.docs[key]['sources'] is None


def test_완료결과와_진행작업은_덮어쓰지_않는다():
    for status in ['done', 'running', 'queued']:
        original = {'_id': _analyze_job_id('야르', None), 'keyword': '야르',
                    'status': status, 'result': {'result': '기존 내용'}, 'requested_at': '이전 시각'}
        collection = Collection([original])
        warm.enqueue_keyword(collection, '야르', retry_failed=True)
        assert collection.docs[original['_id']] == original


def test_실패는_명시했을때만_재시도한다():
    key = _analyze_job_id('야르', None)
    collection = Collection([{'_id': key, 'status': 'failed', 'error': '오류'}])
    warm.enqueue_keyword(collection, '야르')
    assert collection.docs[key]['status'] == 'failed'
    warm.enqueue_keyword(collection, '야르', retry_failed=True)
    assert collection.docs[key]['status'] == 'queued'
    assert collection.docs[key]['error'] is None


def test_첫작업이_끝나기전에는_다음작업을_큐에_넣지_않는다(tmp_path, monkeypatch):
    collection = Collection()
    def finish_one(seconds):
        queued = [doc for doc in collection.docs.values() if doc['status'] == 'queued']
        assert len(queued) == 1
        queued[0]['status'] = 'done'
    monkeypatch.setattr(warm.time, 'sleep', finish_one)
    report = warm.warm_cache(collection, ['야르', '요이'], tmp_path / 'progress.json')
    assert report['counts'] == {'done': 2}
    assert len(collection.inserted) == 2


def test_병렬창_크기까지만_큐에_넣고_빈자리를_채운다(tmp_path, monkeypatch):
    collection = Collection()
    windows = []
    def finish_one(seconds):
        queued = [doc for doc in collection.docs.values() if doc['status'] == 'queued']
        windows.append(len(queued))
        assert 1 <= len(queued) <= 3
        queued[0]['status'] = 'done'
    monkeypatch.setattr(warm.time, 'sleep', finish_one)
    report = warm.warm_cache(collection, ['야르', '요이', '킹받네', '밤티'], tmp_path / 'progress.json', concurrency=3)
    assert report['counts'] == {'done': 4}
    assert windows[0] == 3
