"""
funnel.py
파이프라인 단계별 입력/출력/사유별 드롭 수를 누적하고 직렬화한다.

지금은 각 단계가 print로 요약을 뱉고 끝나서, 어제 숫자를 알 방법도 없고
"몇 개를 왜 버렸는지"도 남지 않는다. 그래서 "고쳤더니 나아졌나"에 답할 수 없다.

만드는 dict는 하나이고 쓰는 곳만 다르다 — 개발 중에는 파일(dump), 운영에서는
Mongo(추후). 로직이 갈라지지 않으므로 "로컬에선 되는데 서버에선 다른 숫자" 사고가 없다.

청커 내부 드롭(chunker.py:81의 10자 미만 제거)은 여기 안 잡힌다. chunk_document()가
개수를 반환하지 않기 때문이며, 시그니처 변경은 1단계 범위 밖이다. 대신 살아남은
청크의 신호 분포(report.py)로 본다.
"""

import json
import os


class Funnel:
    """단계 이름 → {in, out, dropped:{사유: 수}} 누적기."""

    def __init__(self, run: str, fixture: str):
        self.run = run
        self.fixture = fixture
        self._stages: dict[str, dict] = {}

    def _stage(self, name: str) -> dict:
        return self._stages.setdefault(name, {"in": 0, "out": 0, "dropped": {}})

    def inc_in(self, stage: str, n: int = 1) -> None:
        self._stage(stage)["in"] += n

    def inc_out(self, stage: str, n: int = 1) -> None:
        self._stage(stage)["out"] += n

    def drop(self, stage: str, reason: str, n: int = 1) -> None:
        dropped = self._stage(stage)["dropped"]
        dropped[reason] = dropped.get(reason, 0) + n

    def to_dict(self) -> dict:
        return {"run": self.run, "fixture": self.fixture, "stages": self._stages}

    def dump(self, path: str) -> None:
        """JSON으로 저장. 상위 디렉터리가 없으면 만든다."""
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


def load_funnel(path: str) -> dict:
    """dump()로 저장한 파일을 읽어 dict로 반환."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
