"""검색 직렬화, 생성 병렬화, 공유 락의 수명과 개별 실패 격리를 검증한다."""
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from scripts import llm_request_worker as worker


class Queue:
    def __init__(self, stop):
        self.docs = {str(i): {'_id': str(i), 'keyword': f'키워드{i}', 'status': 'queued',
                              'requested_at': datetime.now(timezone.utc)} for i in range(4)}
        self.lock = threading.Lock()
        self.stop = stop

    def find_one(self, query):
        with self.lock:
            return next((dict(d) for d in self.docs.values() if d['status'] == query['status']), None)

    def find_one_and_update(self, query, update, sort=None):
        with self.lock:
            for doc in self.docs.values():
                if doc['status'] == query['status']:
                    previous = dict(doc)
                    doc.update(update['$set'])
                    return previous

    def update_one(self, query, update):
        with self.lock:
            self.docs[query['_id']].update(update['$set'])
            if all(doc['status'] in {'done', 'failed'} for doc in self.docs.values()):
                self.stop.set()


def test_검색은하나씩_생성은3개씩_실행하고_실패를격리한다(monkeypatch):
    stop = threading.Event()
    queue = Queue(stop)
    barrier = threading.Barrier(3, timeout=3)
    guard = threading.Lock()
    counts = {'encoding': 0, 'max_encoding': 0, 'generating': 0, 'max_generating': 0, 'calls': 0, 'owns': False, 'renew': 0}
    def encode(config):
        with guard:
            counts['encoding'] += 1
            counts['max_encoding'] = max(counts['max_encoding'], counts['encoding'])
        time.sleep(0.01)
        with guard:
            counts['encoding'] -= 1
        return {}

    def generate(prompt, **kwargs):
        assert kwargs['require_complete'] is True
        with guard:
            counts['calls'] += 1
            number = counts['calls']
            counts['generating'] += 1
            counts['max_generating'] = max(counts['max_generating'], counts['generating'])
        try:
            if number <= 3:
                barrier.wait()
            if number == 2:
                raise ValueError('잘린 답변')
            return '완성된 답변입니다.'
        finally:
            with guard:
                counts['generating'] -= 1

    def acquire(owner):
        assert not counts['owns']
        counts['owns'] = True
        return True

    def release(owner):
        assert counts['owns'] and counts['generating'] == 0
        counts['owns'] = False

    monkeypatch.setattr(worker, 'get_collection', lambda name: queue)
    monkeypatch.setattr(worker, '_requeue_stale_running', lambda collection: None)
    monkeypatch.setattr(worker, 'acquire_heavy_job_lock', acquire)
    monkeypatch.setattr(worker, 'release_heavy_job_lock', release)
    monkeypatch.setattr(worker, 'renew_heavy_job_lock', lambda owner: counts.update(renew=counts['renew'] + 1))
    monkeypatch.setattr(worker, 'LLM_WORKER_POLL_INTERVAL_SECONDS', 0.005)
    monkeypatch.setattr(worker, 'default_facet_config', lambda keyword: {})
    monkeypatch.setattr(worker, 'encode_facets', encode)
    point = SimpleNamespace(payload={'url': 'https://example.com/post', 'text': '근거 본문', 'title': '제목'})
    monkeypatch.setattr(worker, 'facet_search', lambda *args, **kwargs: ([point], {}))
    monkeypatch.setattr(worker, '_trend_info', lambda keyword: ('', None))
    monkeypatch.setattr(worker, 'build_facet_prompt', lambda *args, **kwargs: '프롬프트')
    monkeypatch.setattr(worker, 'analyze', generate)
    thread = threading.Thread(target=worker.run_loop, kwargs={'concurrency': 3, 'stop_event': stop})
    thread.start()
    try:
        thread.join(timeout=5)
        assert not thread.is_alive(), '세 생성이 동시에 시작되지 않았습니다.'
        assert counts['max_encoding'] == 1
        assert counts['max_generating'] == 3
        assert counts['renew'] > 0
        assert not counts['owns']
        statuses = [doc['status'] for doc in queue.docs.values()]
        assert statuses.count('done') == 3
        assert statuses.count('failed') == 1
    finally:
        stop.set()
        thread.join(timeout=5)
