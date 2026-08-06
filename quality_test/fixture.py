"""
fixture.py
실제 수집 문서를 로컬 JSONL로 떠놓고 다시 읽는다.

이 패키지에서 DB를 만지는 유일한 파일이다. 나머지 모듈은 전부 파일과 문자열만
다루므로 EC2/Mongo 없이, 목(mock) 없이 테스트된다.

왜 fixture가 필요한가:
  크롤러가 계속 새 문서를 넣기 때문에 Mongo를 직접 읽으면 실행할 때마다 입력이
  달라진다. 그러면 "개선 전후"를 비교해도 무엇이 코드 변경 때문이고 무엇이 데이터
  변경 때문인지 구분할 수 없다. 한 번 떠놓고 그 위에서만 비교한다.

읽기만 한다. 이 파일은 절대 Mongo에 쓰지 않는다.
"""

import json
import os
from typing import Iterator

from config.config_cilent import DEFAULT_FIXTURE_NAME, FIXTURE_DIR
from DB.mongo_client import get_collection


def dump_fixture(limit: int, keyword: str | None = None, path: str | None = None) -> tuple[str, int]:
    """memes 컬렉션에서 문서를 읽어 JSONL로 저장. (저장경로, 문서수) 반환.

    datetime 등 JSON으로 직렬화되지 않는 값은 default=str로 문자열화한다 —
    fixture는 정제/청킹 입력으로만 쓰이고 그 단계는 content/title/keyword만 보므로
    날짜 타입이 문자열이 되어도 무해하다.
    """
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    path = path or os.path.join(FIXTURE_DIR, DEFAULT_FIXTURE_NAME)

    query = {}
    if keyword:
        query["keyword"] = keyword

    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for doc in get_collection().find(query).limit(limit):
            f.write(json.dumps(doc, ensure_ascii=False, default=str) + "\n")
            count += 1

    return path, count


def load_fixture(path: str | None = None) -> Iterator[dict]:
    """JSONL을 한 줄씩 읽어 문서 dict를 내놓는다.

    한 줄씩 읽는 이유: 파일이 커져도 메모리에 전부 올리지 않는다.
    """
    path = path or os.path.join(FIXTURE_DIR, DEFAULT_FIXTURE_NAME)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
