import threading

from pymongo import MongoClient
from config.config_cilent import MONGO_URI, MONGO_DB, MONGO_COLLECTION

# PyMongo 클라이언트 자체는 thread-safe(커넥션 풀 내장)지만, 이 lazy 초기화는
# 아니다. 락 없이 두 스레드가 `_client is None`을 동시에 통과하면 MongoClient 가
# 2개 생기고 하나는 leak(기본 풀 100 커넥션)된다 → 이중 검사 락으로 1개만 만든다.
_client = None
_client_lock = threading.Lock()

def get_db():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = MongoClient(MONGO_URI)
    return _client[MONGO_DB]

def get_collection(name: str = MONGO_COLLECTION):
    return get_db()[name]

def close():
    global _client
    if _client:
        _client.close()
        _client = None
