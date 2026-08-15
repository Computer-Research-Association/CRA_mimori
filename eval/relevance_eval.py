"""
relevance_eval.py
quality_test.matching(find_keyword) 판정기를 검증·측정하는 하네스.

서브커맨드:
  df     : corpus 전체 대비 각 키워드의 문서빈도(DF) 계산.
           DF가 높은 키워드 = 흔한 토큰 → 오염 탐지 불가 → 자동 강등 근거.
           임계값은 여기 출력된 분포를 보고 정한다(하드코딩 안 함).   [Mongo 필요]
  build  : cleaned_memes에서 소스·위치 층화 샘플 → 라벨셋 CSV.
           사람이 human_label 칸(1=관련 / 0=무관)을 채운다.             [Mongo 필요]
  qdrant-backfill
         : cleaned_memes의 relevance 필드를 기존 Qdrant point payload에 반영.
                                                                       [Mongo+Qdrant 필요]
  score  : 라벨 채워진 CSV vs 판정기 → 오염판정 precision/recall +
           키워드·소스·위치별 일치율. 위치별로 사람 판정이 갈리면
           position 가중치가 필요하다는 신호.                          [CSV만, DB 불필요]

Mongo 접속: EC2 터널 또는 로컬 docker (세션 초반 localhost 오버라이드 이슈 참고).
실행: python -m eval.relevance_eval df|build|score [옵션]
"""

import argparse
import csv
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config_cilent import CLEANED_COLLECTION, QDRANT_COLLECTION
from DB.mongo_client import get_collection
from eval.questions import load_keywords
from quality_test.matching import find_keyword, normalize

# 오염이 의심되는 소스(피드백 기준). 라벨셋에서 이 소스들을 가중 샘플한다.
_SUSPECT_SOURCES = ("tavily", "youtube", "natepann")

_DEFAULT_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "results", "relevance_labelset.csv"
)
# 샘플/집계 때 doc 하나에서 판정기가 보는 것과 동일한 필드만 가져온다.
_PROJECTION = {"keyword": 1, "title": 1, "source": 1, "url": 1, "chunks.text": 1}


def _load_docs() -> list[dict]:
    """cleaned_memes 전체를 판정에 필요한 필드만 투영해 로드."""
    cleaned = get_collection(CLEANED_COLLECTION)
    return list(cleaned.find({}, _PROJECTION))


def _doc_body(doc: dict) -> str:
    """cleaned_memes 문서의 전체 청크 텍스트를 이어붙인다."""
    return "\n".join(c.get("text", "") for c in doc.get("chunks", []))


# ---------------------------------------------------------------- df
def cmd_df(_args) -> None:
    """전체 corpus 대비 각 키워드의 문서빈도. 높을수록 흔한 토큰(강등 후보)."""
    keywords = load_keywords()
    docs = _load_docs()
    total = len(docs)
    if not total:
        print("[중단] cleaned_memes에 문서가 없습니다.")
        return

    # doc마다 정규화 전체 텍스트(제목+본문)를 한 번만 만든다.
    norm_by_id = {
        str(d["_id"]): normalize(d.get("title", "") + "\n" + _doc_body(d)) for d in docs
    }

    # cross_df: "다른 keyword로 크롤된 doc 중 이 키워드가 등장한 비율"
    # (전체 DF는 own_docs 수 때문에 distinctive 신조어도 30%대로 올라가 misleading)
    print(f"corpus 문서 {total}개 기준 키워드별 cross-DF (다른 keyword doc에서의 등장 비율):\n")
    print(f"  {'keyword':<12}  {'own_docs':>8}  {'cross_match':>12}  {'cross_df':>9}  판정")
    print("  " + "-" * 60)
    rows = []
    for kw in keywords:
        nkw = normalize(kw)
        own = [d for d in docs if d.get("keyword") == kw]
        other = [d for d in docs if d.get("keyword") != kw]
        cross = sum(1 for d in other if nkw and nkw in norm_by_id[str(d["_id"])])
        cross_df = cross / len(other) if other else 0.0
        rows.append((kw, len(own), cross, cross_df))
    for kw, own_n, cross, cross_df in sorted(rows, key=lambda r: r[3], reverse=True):
        note = "← 진짜 흔한 토큰(강등 후보)" if cross_df >= 0.10 else ""
        print(f"  {kw:<12}  {own_n:>8}  {cross:>12}  {cross_df:>8.1%}  {note}")
    print("\n※ 강등 임계값 cross_df ≥ 10%는 실데이터 분포로 확정된 기준.")


# ---------------------------------------------------------------- build
def cmd_build(args) -> None:
    """소스·위치 층화 샘플로 라벨셋 CSV를 만든다(human_label 칸은 비워둠)."""
    docs = _load_docs()
    if not docs:
        print("[중단] cleaned_memes에 문서가 없습니다.")
        return

    rng = random.Random(args.seed)
    rng.shuffle(docs)

    # (source, position) 버킷으로 나눠, 위치별 커버리지를 보장하며 의심 소스를 가중.
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    judged: dict[str, object] = {}
    for d in docs:
        r = find_keyword(d.get("keyword", ""), d.get("title", ""), _doc_body(d))
        judged[str(d["_id"])] = r
        src = d.get("source") or "unknown"
        buckets[(src, r.position)].append(d)

    # 위치 4종을 고르게 담되, 의심 소스에 가중치를 줘 라운드로빈으로 뽑는다.
    positions = ["title", "early", "late", "none"]
    picked: list[dict] = []
    seen: set[str] = set()
    # 의심 소스를 앞쪽에 3배 가중해 우선 소진
    src_order = [s for s in _SUSPECT_SOURCES for _ in range(3)] + sorted(
        {(d.get("source") or "unknown") for d in docs}
    )
    while len(picked) < args.n:
        progressed = False
        for pos in positions:
            for src in src_order:
                bucket = buckets.get((src, pos), [])
                while bucket:
                    d = bucket.pop()
                    if str(d["_id"]) not in seen:
                        seen.add(str(d["_id"]))
                        picked.append(d)
                        progressed = True
                        break
                if len(picked) >= args.n:
                    break
            if len(picked) >= args.n:
                break
        if not progressed:  # 더 뽑을 문서가 없음
            break

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "keyword", "source", "judge_matched",
                    "judge_position", "human_label", "title", "snippet"])
        for d in picked:
            r = judged[str(d["_id"])]
            snippet = _doc_body(d)[:300].replace("\n", " ")
            w.writerow([str(d["_id"]), d.get("keyword", ""), d.get("source") or "",
                        int(r.matched), r.position, "",  # human_label 비움
                        (d.get("title") or "")[:120], snippet])

    dist = Counter((d.get("source") or "unknown", judged[str(d["_id"])].position) for d in picked)
    print(f"[저장] {args.out}  ({len(picked)}행)")
    print("소스×위치 분포:")
    for (src, pos), c in sorted(dist.items()):
        print(f"  {src:<10} {pos:<6} {c}")
    print("\n다음: CSV의 human_label 칸을 1(관련)/0(무관)로 채운 뒤 `score`를 실행하세요.")


# ---------------------------------------------------------------- backfill
def cmd_backfill(args) -> None:
    """cleaned_memes 전체에 is_relevant / relevance_position 필드를 기록한다.

    판정기가 이미 정확도를 확인받은 뒤 실행하는 것이 원칙.
    --dry-run 옵션으로 DB 쓰기 없이 예상 집계만 확인할 수 있다."""
    from pymongo import UpdateOne

    cleaned = get_collection(CLEANED_COLLECTION)
    docs = list(cleaned.find({}, {"keyword": 1, "title": 1, "chunks.text": 1}))
    total = len(docs)
    if not total:
        print("[중단] cleaned_memes에 문서가 없습니다.")
        return

    from collections import Counter
    ops = []
    dist: Counter = Counter()

    for doc in docs:
        r = find_keyword(doc.get("keyword") or "", doc.get("title") or "", _doc_body(doc))
        dist[(r.matched, r.position)] += 1
        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {"is_relevant": r.matched, "relevance_position": r.position}},
        ))

    rel = sum(v for (rel, _), v in dist.items() if rel)
    irr = total - rel
    print(f"대상 문서: {total}개  →  관련 {rel}개 ({rel/total:.1%}) / 비관련 {irr}개 ({irr/total:.1%})")
    print("위치별 분포:")
    for pos in ("title", "early", "late", "none"):
        n = dist[(True, pos)] + dist[(False, pos)]
        if n:
            print(f"  {pos:<6} {n}개  (관련 {dist[(True,pos)]} / 비관련 {dist[(False,pos)]})")

    if args.dry_run:
        print("\n[dry-run] DB 쓰기 건너뜀.")
        return

    result = cleaned.bulk_write(ops, ordered=False)
    print(f"\n[완료] 수정됨 {result.modified_count}개 / 매칭 {result.matched_count}개")


def cmd_qdrant_backfill(args) -> None:
    """cleaned_memes의 relevance 필드를 기존 Qdrant point payload에 반영한다.

    벡터는 다시 만들지 않고 payload만 갱신한다. cleaned_memes에 아직 relevance
    필드가 없는 문서는 현재 판정기로 즉석 계산해 함께 저장한다.
    """
    from pymongo import UpdateOne

    cleaned = get_collection(CLEANED_COLLECTION)
    docs = list(cleaned.find({}, {
        "keyword": 1,
        "title": 1,
        "chunks.text": 1,
        "chunks.chunk_index": 1,
        "is_relevant": 1,
        "relevance_position": 1,
    }))
    total_docs = len(docs)
    if not total_docs:
        print("[중단] cleaned_memes에 문서가 없습니다.")
        return

    if not args.dry_run:
        from DB.drant_clitent import ensure_collection, get_client
        from qdrant_client.http import models

        ensure_collection()
        client = get_client()

    mongo_ops = []
    updated_docs = 0
    updated_points = 0
    missing_chunks = 0

    for doc in docs:
        if "is_relevant" not in doc or "relevance_position" not in doc:
            r = find_keyword(doc.get("keyword") or "", doc.get("title") or "", _doc_body(doc))
            is_relevant = r.matched
            relevance_position = r.position
            mongo_ops.append(UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {
                    "is_relevant": is_relevant,
                    "relevance_position": relevance_position,
                }},
            ))
        else:
            is_relevant = doc["is_relevant"]
            relevance_position = doc["relevance_position"]

        chunk_count = sum(1 for chunk in doc.get("chunks", []) if "chunk_index" in chunk)
        if not chunk_count:
            missing_chunks += 1
            continue

        if args.dry_run:
            updated_docs += 1
            updated_points += chunk_count
            continue

        client.set_payload(
            collection_name=QDRANT_COLLECTION,
            payload={
                "is_relevant": is_relevant,
                "relevance_position": relevance_position,
            },
            points=models.Filter(
                must=[
                    models.FieldCondition(
                        key="parent_id",
                        match=models.MatchValue(value=str(doc["_id"])),
                    )
                ]
            ),
        )
        updated_docs += 1
        updated_points += chunk_count

    if mongo_ops and not args.dry_run:
        cleaned.bulk_write(mongo_ops, ordered=False)

    print(
        f"대상 문서: {total_docs}개 / Qdrant payload 갱신 대상: "
        f"문서 {updated_docs}개, 포인트 {updated_points}개"
    )
    if mongo_ops:
        suffix = "예정" if args.dry_run else "완료"
        print(f"Mongo relevance 누락 문서 보정: {len(mongo_ops)}개 {suffix}")
    if missing_chunks:
        print(f"청크 없음으로 스킵: {missing_chunks}개")
    if args.dry_run:
        print("\n[dry-run] DB/Qdrant 쓰기 건너뜀.")
    else:
        print("\n[완료] Qdrant payload backfill 완료")


# ---------------------------------------------------------------- score
def _prf(tp: int, fp: int, fn: int) -> tuple[float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return precision, recall


def cmd_score(args) -> None:
    """라벨 채워진 CSV로 오염판정 precision/recall + 키워드·소스·위치별 일치율."""
    if not os.path.exists(args.csv):
        print(f"[중단] 라벨셋 CSV가 없습니다: {args.csv}")
        return
    with open(args.csv, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("human_label", "").strip() in ("0", "1")]
    if not rows:
        print("[중단] human_label(0/1)이 채워진 행이 없습니다.")
        return

    # 양성 클래스 = '오염(무관)'. 판정기는 matched=False일 때 오염이라고 예측.
    tp = fp = fn = tn = 0
    by_pos = defaultdict(lambda: {"n": 0, "human_rel": 0})  # 위치별 사람-관련 비율
    per_key = defaultdict(lambda: {"agree": 0, "n": 0})
    per_src = defaultdict(lambda: {"agree": 0, "n": 0})

    for r in rows:
        human_rel = r["human_label"].strip() == "1"       # 사람: 관련?
        judge_rel = r["judge_matched"].strip() == "1"      # 판정기: 관련?
        judge_contam = not judge_rel
        human_contam = not human_rel

        if judge_contam and human_contam:
            tp += 1
        elif judge_contam and not human_contam:
            fp += 1
        elif not judge_contam and human_contam:
            fn += 1
        else:
            tn += 1

        agree = int(judge_rel == human_rel)
        per_key[r["keyword"]]["agree"] += agree
        per_key[r["keyword"]]["n"] += 1
        per_src[r.get("source", "")]["agree"] += agree
        per_src[r.get("source", "")]["n"] += 1

        pos = r["judge_position"]
        by_pos[pos]["n"] += 1
        by_pos[pos]["human_rel"] += int(human_rel)

    n = len(rows)
    acc = (tp + tn) / n
    p, rec = _prf(tp, fp, fn)
    print(f"라벨 {n}개 기준 (양성=오염/무관)\n")
    print(f"  정확도           {acc:.1%}")
    print(f"  오염판정 precision {p:.1%}  (판정기가 오염이라 한 게 진짜 오염인 비율 — 멀쩡한 데이터 죽이지 않는 게 핵심)")
    print(f"  오염판정 recall    {rec:.1%}  (실제 오염을 얼마나 잡아내나)")
    print(f"  혼동행렬  TP={tp} FP={fp} FN={fn} TN={tn}")

    print("\n위치별 사람-관련 비율 (late가 낮으면 position 가중치 필요 신호):")
    for pos in ("title", "early", "late", "none"):
        b = by_pos.get(pos)
        if b and b["n"]:
            print(f"  {pos:<6} n={b['n']:<3} 사람이 관련이라 한 비율 {b['human_rel']/b['n']:.0%}")

    print("\n키워드별 일치율(판정기=사람):")
    for kw, s in sorted(per_key.items(), key=lambda kv: kv[1]["agree"] / kv[1]["n"]):
        print(f"  {kw:<12} {s['agree']}/{s['n']} = {s['agree']/s['n']:.0%}")

    print("\n소스별 일치율:")
    for src, s in sorted(per_src.items(), key=lambda kv: kv[1]["agree"] / kv[1]["n"]):
        print(f"  {src:<10} {s['agree']}/{s['n']} = {s['agree']/s['n']:.0%}")


def main() -> None:
    parser = argparse.ArgumentParser(description="relevance 판정기 검증·측정 하네스")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("df", help="키워드별 corpus 문서빈도(흔한 토큰 강등 근거)").set_defaults(func=cmd_df)

    pb_fill = sub.add_parser("backfill", help="cleaned_memes 전체에 is_relevant 필드 기록")
    pb_fill.add_argument("--dry-run", action="store_true", help="DB 쓰기 없이 집계만 출력")
    pb_fill.set_defaults(func=cmd_backfill)

    pq_fill = sub.add_parser(
        "qdrant-backfill",
        help="cleaned_memes의 is_relevant 필드를 기존 Qdrant point payload에 반영",
    )
    pq_fill.add_argument("--dry-run", action="store_true", help="DB/Qdrant 쓰기 없이 갱신 대상만 집계")
    pq_fill.set_defaults(func=cmd_qdrant_backfill)

    pb = sub.add_parser("build", help="층화 라벨셋 CSV 생성")
    pb.add_argument("--n", type=int, default=50, help="샘플 문서 수 (기본 50)")
    pb.add_argument("--seed", type=int, default=42, help="재현용 시드")
    pb.add_argument("--out", default=_DEFAULT_CSV, help="출력 CSV 경로")
    pb.set_defaults(func=cmd_build)

    ps = sub.add_parser("score", help="라벨 채운 CSV 채점")
    ps.add_argument("--csv", default=_DEFAULT_CSV, help="라벨셋 CSV 경로")
    ps.set_defaults(func=cmd_score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
