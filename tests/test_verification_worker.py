"""새 근거가 기존 답변을 갱신할지 결정하는 보수적 워커 테스트."""
from datetime import datetime, timezone
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.verification_worker import run_once


class Collection:
    def __init__(self, docs): self.docs = {doc["_id"]: doc for doc in docs}
    def find_one_and_update(self, query, update, sort=None):
        for doc in self.docs.values():
            if doc.get("status") == query.get("status"):
                doc.update(update["$set"]); return {**doc}
        return None
    def update_one(self, query, update, upsert=False):
        doc = self.docs[query["_id"]]; doc.update(update["$set"])
    def update_many(self, query, update):
        for doc in self.docs.values():
            if doc.get("keyword") == query.get("keyword") and doc.get("status") == query.get("status"):
                doc.update(update["$set"])


def test_자료와_출처가_부족하면_기존_답변을_보존하고_변화없음으로_기록한다():
    queue = Collection([{"_id": "야르", "keyword": "야르", "status": "queued", "document_count": 1, "source_count": 1}])
    answers = Collection([{"_id": "a", "keyword": "야르", "status": "done", "result": {"result": "기존 답변"}}])
    assert run_once(queue, answers) is True
    assert answers.docs["a"]["status"] == "done"
    assert answers.docs["a"]["result"]["result"] == "기존 답변"
    assert answers.docs["a"]["verification"]["status"] == "unchanged"


def test_독립_새_자료가_충분하면_기존_답변을_재생성_큐로_보낸다():
    queue = Collection([{"_id": "야르", "keyword": "야르", "status": "queued", "document_count": 2, "source_count": 2}])
    answers = Collection([{"_id": "a", "keyword": "야르", "status": "done", "result": {"result": "기존 답변"}}])
    assert run_once(queue, answers) is True
    assert answers.docs["a"]["status"] == "queued"
    assert answers.docs["a"]["refresh_reason"] == "evidence"


if __name__ == "__main__":
    test_자료와_출처가_부족하면_기존_답변을_보존하고_변화없음으로_기록한다()
    test_독립_새_자료가_충분하면_기존_답변을_재생성_큐로_보낸다()
    print("ALL PASS")
