"""
inspect.py
run에서 나쁜 청크 실물을 보여준다.

숫자만 보면 착각한다. 상위 N개를 눈으로 읽어봐야 "왜 나쁜지"를 알고 다음에
뭘 고칠지 정할 수 있다.

정렬 기준은 '위반한 규칙 개수'다. 점수를 합성하지 않고 위반 목록을 그대로
보여주므로, 왜 위에 올라왔는지가 설명된다.
"""

import json
import os

from config.config_cilent import (
    DOMESTIC_SOURCES,
    MIN_CHUNK_CHARS,
    MIN_HANGUL_RATIO,
    RUNS_DIR,
    SPAM_HIT_THRESHOLD,
)


def violations(signals: dict, source: str) -> list[str]:
    """이 청크가 위반한 '확실히 나쁨' 규칙 이름 목록."""
    found = []
    if signals.get("char_count", 0) < MIN_CHUNK_CHARS:
        found.append(f"짧음(<{MIN_CHUNK_CHARS})")
    if signals.get("match_position") == "none":
        found.append("키워드없음")
    if signals.get("spam_hits", 0) >= SPAM_HIT_THRESHOLD:
        found.append(f"스팸({signals['spam_hits']})")
    if source in DOMESTIC_SOURCES and signals.get("hangul_ratio", 1.0) < MIN_HANGUL_RATIO:
        found.append(f"한글비율({signals.get('hangul_ratio')})")
    if signals.get("match_position") == "late":
        found.append("후반매치")
    if signals.get("boilerplate_hits", 0) > 0:
        found.append(f"UI상투어({signals['boilerplate_hits']})")
    return found


def print_worst(run_name: str, keyword: str | None = None, n: int = 10) -> None:
    """위반 규칙이 많은 순으로 상위 n개 청크를 출력."""
    path = os.path.join(RUNS_DIR, run_name, "chunks.jsonl")

    scored = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            if keyword and chunk.get("keyword") != keyword:
                continue
            found = violations(chunk.get("signals", {}), chunk.get("source") or "")
            if found:
                scored.append((len(found), found, chunk))

    scored.sort(key=lambda item: -item[0])

    label = f"{run_name}" + (f" / keyword={keyword}" if keyword else "")
    print(f"\n=== 나쁜 청크 상위 {n}개 — {label} (위반 있는 청크 {len(scored):,}개) ===")
    for rank, (count, found, chunk) in enumerate(scored[:n], 1):
        sig = chunk.get("signals", {})
        print(f"\n[{rank}] 위반 {count}건: {', '.join(found)}")
        print(f"    source={chunk.get('source')}  len={sig.get('char_count')}  "
              f"match={sig.get('match_position')}({sig.get('match_count')}회)")
        print(f"    title={(chunk.get('title') or '')[:50]!r}")
        preview = (chunk.get("text") or "").replace("\n", " ")[:150]
        print(f"    text={preview!r}")
    print()
