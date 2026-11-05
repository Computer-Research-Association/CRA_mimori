"""공개 키워드의 전체 출처 분석 캐시를 기존 작업 큐로 제한된 개수씩 채운다.

기본은 상태 조회다. 실제 생성: uv run python scripts/warm_analysis_cache.py --run
이미 완성된 답변은 덮어쓰지 않으며, 실패 작업 재시도는 --retry-failed로 명시한다.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import ReturnDocument

from analysis.pipeline import list_visible_keywords
from api.routes import _analyze_job_id
from config.config_cilent import BATCH_KEYWORDS_PATH, LLM_REQUESTS_COLLECTION, LLM_WORKER_CONCURRENCY
from DB.mongo_client import get_collection


def enqueue_keyword(collection, keyword, retry_failed=False):
    """웹과 같은 캐시 키를 사용하고, 다른 요청의 진행·완료 상태를 보존한다."""
    job_id = _analyze_job_id(keyword, None)
    now = datetime.now(timezone.utc)
    doc = collection.find_one_and_update(
        {'_id': job_id},
        {'$setOnInsert': {
            'keyword': keyword, 'sources': None, 'status': 'queued',
            'requested_at': now, 'started_at': None, 'completed_at': None,
            'error': None, 'result': None,
        }},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    if retry_failed and doc['status'] == 'failed':
        collection.update_one(
            {'_id': job_id, 'status': 'failed'},
            {'$set': {
                'status': 'queued', 'requested_at': now, 'started_at': None,
                'completed_at': None, 'error': None, 'result': None,
            }},
        )
    return job_id


def snapshot(collection, keywords):
    counts = {}
    items = []
    for keyword in keywords:
        doc = collection.find_one({'_id': _analyze_job_id(keyword, None)}) or {}
        status = doc.get('status', 'missing')
        counts[status] = counts.get(status, 0) + 1
        items.append({'keyword': keyword, 'status': status, 'error': doc.get('error')})
    return {'updated_at': datetime.now(timezone.utc).isoformat(),
            'total': len(keywords), 'counts': counts, 'items': items}


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def warm_cache(collection, keywords, report_path, retry_failed=False, poll_seconds=5, concurrency=1):
    """생성 슬롯만큼만 큐에 넣어 사용자 요청 뒤에 대량 작업이 쌓이지 않게 한다."""
    if concurrency < 1:
        raise ValueError('동시 생성 수는 1 이상이어야 합니다.')
    remaining = iter(keywords)
    pending = {}
    exhausted = False
    while pending or not exhausted:
        while not exhausted and len(pending) < concurrency:
            try:
                keyword = next(remaining)
            except StopIteration:
                exhausted = True
                break
            job_id = enqueue_keyword(collection, keyword, retry_failed=retry_failed)
            pending[job_id] = (keyword, time.monotonic())
        report = snapshot(collection, keywords)
        write_report(report_path, report)
        for job_id, (keyword, started) in list(pending.items()):
            doc = collection.find_one({'_id': job_id})
            # 삭제·숨김 등의 수동 운영과 충돌하지 않도록 사라진 작업은 다시 만들지 않는다.
            if doc is None or doc['status'] in {'done', 'failed'}:
                print(f"[캐시] {keyword}: {(doc or {}).get('status', 'removed')}", flush=True)
                del pending[job_id]
            elif time.monotonic() - started > 30 * 60:
                raise TimeoutError(f"'{keyword}' 작업이 30분 이상 대기 중입니다. 큐 상태를 확인하세요.")
        if pending:
            time.sleep(poll_seconds)
    report = snapshot(collection, keywords)
    write_report(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='상태 조회 대신 캐시 생성을 실행')
    parser.add_argument('--retry-failed', action='store_true', help='기존 실패 작업을 한 번 재시도')
    parser.add_argument('--concurrency', type=int, default=LLM_WORKER_CONCURRENCY, help='동시 사전 생성 요청 수')
    parser.add_argument('--report', type=Path, default=Path('/tmp/mimori-analysis-cache.json'))
    args = parser.parse_args()
    keywords = list_visible_keywords()
    # 매일 수집하는 키워드를 먼저 준비하고, 나머지 공개 보관 키워드를 이어서 처리한다.
    batch = Path(BATCH_KEYWORDS_PATH).read_text(encoding='utf-8')
    daily = {line.strip() for line in batch.splitlines() if line.strip() and not line.startswith('#')}
    keywords.sort(key=lambda keyword: (keyword not in daily, keyword))
    collection = get_collection(LLM_REQUESTS_COLLECTION)
    if not args.run:
        print(json.dumps(snapshot(collection, keywords), ensure_ascii=False, indent=2))
        return
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = warm_cache(collection, keywords, args.report, args.retry_failed, concurrency=args.concurrency)
        print(json.dumps(report['counts'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
