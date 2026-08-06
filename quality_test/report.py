"""
report.py
run을 집계해 표로 보여주고, 두 run을 나란히 비교한다.

라벨 없이도 볼 수 있는 것만 다룬다 — 통과율, 드롭 사유, 신호 분포.
"좋다"는 못 말해도 "어제보다 나빠졌다"는 확실히 말한다.

청커 내부 드롭은 funnel에 없으므로(chunk_document가 개수를 안 돌려줌),
살아남은 청크의 signals를 집계해서 본다. 3단계(최소 길이 10→30)에서 필요한
숫자가 정확히 이것이다.
"""

import json
import os
import unicodedata

from config.config_cilent import (
    DOMESTIC_SOURCES,
    MIN_CHUNK_CHARS,
    MIN_HANGUL_RATIO,
    RUNS_DIR,
    SPAM_HIT_THRESHOLD,
)
from quality_test.funnel import load_funnel


def _iter_chunks(run_name: str):
    path = os.path.join(RUNS_DIR, run_name, "chunks.jsonl")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def summarize(run_name: str) -> dict:
    """run 하나를 집계해 지표 dict로 반환."""
    funnel = load_funnel(os.path.join(RUNS_DIR, run_name, "funnel.json"))

    total = 0
    lengths = []
    short = spam = no_keyword = late = low_hangul = boilerplate = 0
    per_source: dict[str, int] = {}

    for chunk in _iter_chunks(run_name):
        sig = chunk.get("signals", {})
        source = chunk.get("source") or "unknown"
        total += 1
        lengths.append(sig.get("char_count", 0))
        per_source[source] = per_source.get(source, 0) + 1

        if sig.get("char_count", 0) < MIN_CHUNK_CHARS:
            short += 1
        if sig.get("spam_hits", 0) >= SPAM_HIT_THRESHOLD:
            spam += 1
        if sig.get("match_position") == "none":
            no_keyword += 1
        if sig.get("match_position") == "late":
            late += 1
        if source in DOMESTIC_SOURCES and sig.get("hangul_ratio", 1.0) < MIN_HANGUL_RATIO:
            low_hangul += 1
        if sig.get("boilerplate_hits", 0) > 0:
            boilerplate += 1

    return {
        "run": run_name,
        "docs_in": funnel["stages"].get("clean", {}).get("in", 0),
        "docs_out": funnel["stages"].get("clean", {}).get("out", 0),
        "docs_dropped": funnel["stages"].get("clean", {}).get("dropped", {}),
        "chunks": total,
        "median_length": _median(lengths),
        "short": short,
        "spam": spam,
        "no_keyword": no_keyword,
        "late": late,
        "low_hangul": low_hangul,
        "boilerplate": boilerplate,
        "per_source": per_source,
    }


_LABEL_WIDTH = 22  # 화면상 표시 폭(칸 수). 문자 개수가 아니다 — 아래 참고.


def _visual_width(s: str) -> int:
    """터미널에 실제로 찍히는 폭. 한글/전각 문자는 2칸을 차지하므로 len(s)로
    맞추면(파이썬은 코드포인트 1개=1로 셈) 라벨마다 한글 비중이 달라 표가
    삐뚤어진다. east_asian_width로 폭을 보정해야 열이 맞는다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, width: int = _LABEL_WIDTH) -> str:
    return s + " " * max(0, width - _visual_width(s))


def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):5.1f}%" if total else "    -"


def _delta_width(col_width: int) -> int:
    """Δ 칸의 폭. 부호(+)까지 포함해 헤더의 'Δ'와 실제 값이 같은 위치에 오도록
    _row와 헤더 양쪽에서 이 함수로 폭을 맞춘다."""
    return max(7, col_width - 1) + 1


def _row(label: str, a, b, total_a: int, total_b: int | None, col_width: int = 8) -> str:
    """b가 None이면 단일 run 표시, 아니면 비교 표시.

    col_width: run 이름 컬럼의 폭. run 이름이 8자보다 길면(예: "after_chunking")
    숫자 컬럼도 그만큼 넓혀야 헤더와 어긋나지 않는다.
    """
    if b is None:
        return f"  {_pad(label)} {a:>8,} {_pct(a, total_a):>8}"
    delta = b - a
    sign = "+" if delta > 0 else ""
    signed = f"{sign}{delta:,}"
    return f"  {_pad(label)} {a:>{col_width},} {b:>{col_width},}  {signed:>{_delta_width(col_width)}}"


def print_report(run_a: str, run_b: str | None = None) -> None:
    """run 하나를 표로 출력하거나, 두 run을 비교 출력한다."""
    a = summarize(run_a)
    b = summarize(run_b) if run_b else None

    if b is None:
        print(f"\n=== {a['run']} ===")
        print(f"  {_pad('문서 (입력 → 통과)')} {a['docs_in']:>8,} → {a['docs_out']:,}")
        if a["docs_dropped"]:
            for reason, n in a["docs_dropped"].items():
                print(f"    └ {_pad(reason, 18)} {n:>8,}")
        print(f"  {_pad('청크')} {a['chunks']:>8,}")
        print(f"  {_pad('청크 길이 중앙값')} {a['median_length']:>8,}")
        print("  --- 확실히 나쁨 규칙 ---")
        for label, key in (
            (f"{MIN_CHUNK_CHARS}자 미만", "short"),
            (f"스팸 {SPAM_HIT_THRESHOLD}건 이상", "spam"),
            ("키워드 미포함", "no_keyword"),
            ("본문 후반 매치(late)", "late"),
            ("UI 상투어 포함", "boilerplate"),
            (f"한글비율<{MIN_HANGUL_RATIO} (국내)", "low_hangul"),
        ):
            print(_row(label, a[key], None, a["chunks"], None))
        print("  --- 소스별 청크 ---")
        for source, n in sorted(a["per_source"].items(), key=lambda kv: -kv[1]):
            print(f"  {_pad(source)} {n:>8,} {_pct(n, a['chunks']):>8}")
    else:
        col_width = max(8, len(a["run"]), len(b["run"]))
        print(f"\n=== {a['run']} vs {b['run']} ===")
        print(f"  {_pad('')} {a['run']:>{col_width}} {b['run']:>{col_width}}  {'Δ':>{_delta_width(col_width)}}")
        print(_row("문서 통과", a["docs_out"], b["docs_out"], a["chunks"], b["chunks"], col_width))
        print(_row("청크", a["chunks"], b["chunks"], a["chunks"], b["chunks"], col_width))
        print(_row("청크 길이 중앙값", a["median_length"], b["median_length"], 0, 0, col_width))
        for label, key in (
            (f"{MIN_CHUNK_CHARS}자 미만", "short"),
            (f"스팸 {SPAM_HIT_THRESHOLD}건 이상", "spam"),
            ("키워드 미포함", "no_keyword"),
            ("본문 후반 매치(late)", "late"),
            ("UI 상투어 포함", "boilerplate"),
            (f"한글비율<{MIN_HANGUL_RATIO} (국내)", "low_hangul"),
        ):
            print(_row(label, a[key], b[key], a["chunks"], b["chunks"], col_width))
    print()
