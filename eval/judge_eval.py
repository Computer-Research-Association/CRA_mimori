"""
judge_eval.py
eval/judge.py(LLM 관련도 판정기)를 사람 라벨(gold) 대비 검증·A/B하는 하네스.

판정기가 곧 정답인 구조(gold 없음)라, 판정기 자체는 사람 라벨이라는 독립된 기준자에
대고 재야 한다. 이 스크립트가 그 gold 셋을 만들고(build) 판정기와 대조한다(score/ab).

서브커맨드:
  build : run_compare와 동일한 union 풀에서 (query_id, chunk_id) 쌍을 의도×판정밴드로
          층화 샘플 → judge_labelset.csv 뼈대 생성. human_label 칸은 비워 둔다.
          판정 밴드는 "쉬운/애매 케이스를 고루 담기" 위한 샘플링용일 뿐이라 CSV에 넣지
          않는다(사람이 판정기 답을 보고 따라가는 anchoring 방지).      [Qdrant+Mongo+NIM]
  score : 라벨 채운 CSV vs 현재 판정기 → 일치율 / 이차가중 κ / 의도별 일치 / 혼동행렬.
          CSV의 chunk_text·question을 그대로 판정기에 넣으므로 run_compare가 이미
          채점한 쌍은 캐시 히트(무통신)로 끝난다.                       [CSV+NIM(캐시히트면 무통신)]
  ab    : 두 판정기 변형(프롬프트 파일 또는 모델)을 각각 gold 대비로 재고, 지표를 좌우하는
          threshold=2 정답 여부에 paired 부트스트랩 CI로 A−B 차이를 낸다.  [CSV+NIM]

작성 순서:
  1) python -m eval.judge_eval build             # judge_labelset.csv 뼈대(기본 100쌍)
  2) 라벨러 2인이 human_label_a / human_label_b를 각자 0/1/2로 채운다
     (서로의 답도, 판정기 답도 보지 않는 blind 라벨링)
  3) python -m eval.judge_eval agree             # 사람 간 κ 확인 → 불일치는 재논의 후
                                                 # human_label_final에 확정
  4) python -m eval.judge_eval score             # 판정기 vs gold 검증 (κ≥0.6이면 신뢰)
  5) python -m eval.judge_eval ab --prompt-b eval/prompt_b.txt   # κ<0.6이면 프롬프트 A/B

관련도 척도(사람도 판정기와 동일 기준으로 라벨):
  0 = 무관 / 1 = 부분 관련(주제만 겹침) / 2 = 명확히 관련(질문에 직접 답)
"""

import argparse
import csv
import os
import random
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

# torch/네이티브 라이브러리 OpenMP 충돌 방지 (import torch 이전에 설정). run_compare와 동일.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import metrics
from eval.judge import _LIMITER, _PROMPT, JUDGE_MODEL, Judge
from eval.questions import build_eval_set, load_keywords

# 판정 병렬 워커 수. 상한은 API 한도이고 그건 judge의 페이서가 지키므로,
# 여기서는 호출 왕복 지연을 가리는 정도면 충분하다.
_JUDGE_WORKERS = int(os.getenv("JUDGE_WORKERS", "8"))

_RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
_DEFAULT_CSV = os.path.join(_RESULTS_DIR, "judge_labelset.csv")

# 질문 템플릿 → 의도(facet). facet별로 2점 기준이 다르므로 의도별 일치율을 따로 본다.
# (questions.QUESTION_TEMPLATES와 짝을 맞춰 유지할 것)
_INTENT_BY_TEMPLATE = {
    "뜻": "뜻",
    "무슨 뜻이야?": "뜻",
    "어디서 유래했어?": "유래",
    "어떤 상황에서 사용해?": "사용맥락",
}


def _intent(template: str) -> str:
    return _INTENT_BY_TEMPLATE.get(template, "기타")


# ---------------------------------------------------------------- 공통: union 풀 수집
def _collect_pool(judge: Judge) -> list[dict]:
    """run_compare와 동일하게 임베딩된 키워드 × 질문으로 검색해 union 청크를 모으고,
    판정 밴드(0/1/2)를 붙여 반환한다. 밴드는 샘플링 균형용이다.

    무겁고 DB/모델이 필요하므로 build에서만 호출한다(score/ab는 CSV만 씀).
    """
    from analysis.pipeline import list_analyzable_keywords
    from embedding.encoder import encode_batch, unload_model
    from eval.retrievers import METHODS, retrieve

    embedded = set(list_analyzable_keywords())
    keywords = [k for k in load_keywords() if k in embedded]
    if not keywords:
        print("[중단] 평가 가능한(임베딩된) 키워드가 하나도 없습니다.")
        return []

    queries = build_eval_set(keywords)
    print(f"[평가셋] 키워드 {len(keywords)}개 × 템플릿 → 쿼리 {len(queries)}개")

    print("[인코딩] 질문 임베딩 생성 중...")
    dense_vecs, sparse_weights = encode_batch([q.question for q in queries])
    unload_model()

    # latency는 여기서 안 재지만, Qdrant 워밍업 1회는 run_compare와 동일하게 둔다.
    retrieve("dense", queries[0].keyword, dense_vecs[0], sparse_weights[0])

    # 1) 검색만 먼저 끝내 (질문, 청크) 쌍을 모은다 — 판정은 그다음 한꺼번에 병렬로.
    rows: list[dict] = []
    for qi, q in enumerate(queries):
        results = {
            m: retrieve(m, q.keyword, dense_vecs[qi], sparse_weights[qi]) for m in METHODS
        }
        pool: dict[str, str] = {}
        method_hits: Counter = Counter()   # 이 청크를 몇 개 방식이 뽑았나
        for res in results.values():
            for c in res.chunks:
                pool.setdefault(c.chunk_id, c.text)
                method_hits[c.chunk_id] += 1
        for cid, text in pool.items():
            rows.append({
                "query_id": q.id,
                "keyword": q.keyword,
                "question": q.question,
                "question_intent": _intent(q.template),
                "chunk_id": cid,
                "chunk_text": text,
                # 세 방식이 엇갈린 청크 = 평가가 갈리는 경계 사례 신호
                # (판정기는 confidence를 내지 않으므로 이런 프록시로 애매 케이스를 찾는다)
                "method_hits": method_hits[cid],
            })
        print(f"  [검색 {qi + 1}/{len(queries)}] {q.question}  (union 청크 {len(pool)}개)")

    # 2) 판정 병렬 실행. 실제 처리량은 judge 내부 페이서가 API 한도에 맞춰 조절하므로
    #    워커를 늘려도 한도를 넘지 않고, 호출 왕복 대기만 겹쳐서 사라진다.
    print(f"\n[판정] {len(rows)}쌍 채점 (병렬 {_JUDGE_WORKERS} 워커)...")
    _score_rows_parallel(judge, rows)
    judge.flush()

    # 근거(인용)를 못 댄 판정도 애매 케이스 신호로 쓴다.
    for r in rows:
        quote = judge.get_quote(r["query_id"], r["chunk_id"]) or ""
        r["no_quote"] = quote.strip() in ("", '"없음"', "없음")
    _print_judge_failures(judge, "build")
    return rows


def _score_rows_parallel(judge: Judge, rows: list[dict]) -> None:
    """rows 각 항목에 band(판정 점수)를 채운다. 진행률을 주기적으로 출력."""
    done = 0
    total = len(rows)
    lock = threading.Lock()

    def work(r: dict) -> None:
        nonlocal done
        r["band"] = judge.score(r["query_id"], r["question"], r["chunk_id"], r["chunk_text"])
        with lock:
            done += 1
            if done % 25 == 0 or done == total:
                print(f"    판정 {done}/{total}  (현재 페이스 {_LIMITER.current_rpm:.0f} RPM)")

    with ThreadPoolExecutor(max_workers=_JUDGE_WORKERS) as ex:
        list(ex.map(work, rows))


# ---------------------------------------------------------------- build
def cmd_build(args) -> None:
    """의도×판정밴드로 층화 샘플해 judge_labelset.csv 뼈대를 만든다."""
    judge = Judge()
    rows = _collect_pool(judge)
    if not rows:
        return

    rng = random.Random(args.seed)
    rng.shuffle(rows)

    # (의도, 밴드) 버킷으로 나눠 라운드로빈 → 의미/유래/용례와 0/1/2 경계를 고루 담는다.
    # 특히 1↔2 경계(facet 답변 여부)가 판정 기준의 핵심이라 밴드 균형이 중요하다.
    buckets: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in rows:
        buckets[(r["question_intent"], r["band"])].append(r)

    # 버킷 안에서는 '경계 사례'를 먼저 뽑는다. 판정기가 confidence를 주지 않으므로
    # (a) 세 검색 방식이 엇갈린 청크, (b) 판정 근거(인용)를 못 댄 청크를 애매함의 프록시로 쓴다.
    # pop()이 뒤에서 꺼내므로 경계 사례가 리스트 끝에 오도록 정렬한다.
    for bucket in buckets.values():
        bucket.sort(key=lambda r: (r.get("method_hits", 3) == 3) and not r.get("no_quote"))

    intents = ["뜻", "유래", "사용맥락"]
    bands = [2, 1, 0]
    picked: list[dict] = []
    while len(picked) < args.n:
        progressed = False
        for band in bands:
            for intent in intents:
                bucket = buckets.get((intent, band), [])
                if bucket:
                    picked.append(bucket.pop())
                    progressed = True
                    if len(picked) >= args.n:
                        break
            if len(picked) >= args.n:
                break
        if not progressed:  # 더 뽑을 쌍이 없음
            break

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    # chunk_text는 판정기가 보는 원문 그대로 저장 → score/ab가 DB 없이 동일 판정 재현.
    # judge_band는 anchoring 방지로 넣지 않는다.
    # 라벨러 2인이 독립·blind로 각자 칸을 채우고(_a/_b), 불일치는 재논의해 final에 확정한다.
    # final이 비어 있으면 score/ab는 a==b인 행만 gold로 인정한다(미해결 불일치는 제외).
    fields = ["query_id", "keyword", "question", "question_intent", "chunk_id",
              "human_label_a", "human_label_b", "human_label_final", "note", "chunk_text"]
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for r in picked:
            w.writerow([r["query_id"], r["keyword"], r["question"], r["question_intent"],
                        r["chunk_id"], "", "", "", "", r["chunk_text"]])

    dist = Counter((r["question_intent"], r["band"]) for r in picked)
    print(f"\n[저장] {args.out}  ({len(picked)}쌍)")
    print("의도×판정밴드 분포(샘플 균형 확인용):")
    for intent in intents:
        cells = "  ".join(f"{b}점:{dist.get((intent, b), 0)}" for b in bands)
        print(f"  {intent:<5} {cells}")
    band_total = Counter(r["band"] for r in picked)
    print(f"밴드 합계: " + "  ".join(f"{b}점:{band_total.get(b, 0)}" for b in bands))
    for b in bands:
        if band_total.get(b, 0) < 15:
            print(f"  ⚠ {b}점 표본 {band_total.get(b, 0)}개 — 판정기가 이 점수를 잘 주지 않아 "
                  f"층화로도 못 채웠습니다(특히 1점). κ가 이 구간에서 불안정할 수 있습니다.")
    boundary = sum(1 for r in picked if r.get("method_hits", 3) != 3 or r.get("no_quote"))
    print(f"경계 사례(방식 엇갈림·근거 없음) 포함: {boundary}/{len(picked)}쌍")
    print("\n다음: 라벨러 2인이 각자 human_label_a / human_label_b 칸을 0/1/2로 채운 뒤"
          "(서로·판정기 답 안 보고) `agree`로 사람 간 κ를 확인하세요.")


# ---------------------------------------------------------------- 라벨 로드 & 지표
def _valid(v: str | None) -> bool:
    return (v or "").strip() in ("0", "1", "2")


def _read_csv(csv_path: str) -> list[dict]:
    if not os.path.exists(csv_path):
        print(f"[중단] 라벨셋 CSV가 없습니다: {csv_path}")
        return []
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_labeled(csv_path: str) -> list[dict]:
    """
    gold가 확정된 행만 돌려준다.

    우선순위: human_label_final > (a == b인 경우 그 값) > 제외.
    2인이 갈린 채 재논의되지 않은 행을 gold로 쓰면 판정기 검증이 사람 간 불일치까지
    떠안게 되므로 제외하고, 몇 건이 빠졌는지 알린다.
    (구버전 CSV의 단일 human_label 컬럼도 그대로 인정한다.)
    """
    raw = _read_csv(csv_path)
    if not raw:
        return []

    rows, unresolved, unlabeled = [], 0, 0
    for r in raw:
        if _valid(r.get("human_label_final")):
            gold = r["human_label_final"].strip()
        elif _valid(r.get("human_label")):          # 구버전 단일 컬럼
            gold = r["human_label"].strip()
        elif _valid(r.get("human_label_a")) and _valid(r.get("human_label_b")):
            a, b = r["human_label_a"].strip(), r["human_label_b"].strip()
            if a != b:
                unresolved += 1
                continue
            gold = a
        elif _valid(r.get("human_label_a")):
            # 1인 라벨링(또는 재라벨 전) — a만 채워졌으면 그대로 gold로 인정한다.
            gold = r["human_label_a"].strip()
        else:
            unlabeled += 1
            continue
        rows.append({**r, "human_label": gold})

    if unresolved:
        print(f"[주의] 2인 불일치 미해결 {unresolved}건은 gold에서 제외했습니다 "
              f"(`agree`로 확인 후 human_label_final을 채우세요).")
    if unlabeled and not rows:
        print("[중단] 라벨이 채워진 행이 없습니다.")
    elif unlabeled:
        print(f"[주의] 미라벨 {unlabeled}건은 건너뜁니다.")
    return rows


# ---------------------------------------------------------------- agree (라벨러 간 일치도)
def cmd_agree(args) -> None:
    """두 벌의 라벨(_a/_b)을 비교해 κ와 불일치 목록을 낸다.

    쓰임새는 둘 중 하나다.
      - 2인 독립 라벨링: a=라벨러1, b=라벨러2 → 사람 간 일치도
      - 1인 라벨링: a=1차, b=시차를 두고 다시 매긴 재라벨(일부만) → 자기 일관성
        (test-retest). 혼자 라벨링할 때 '기준이 흔들린 지점'을 찾는 표준 대체 수단이다.
    어느 쪽이든 사람 라벨이 안 맞는 구간은 판정 기준 자체가 모호하다는 신호다.
    """
    raw = _read_csv(args.csv)
    if not raw:
        return
    pairs, rows = [], []
    for r in raw:
        if _valid(r.get("human_label_a")) and _valid(r.get("human_label_b")):
            a, b = int(r["human_label_a"]), int(r["human_label_b"])
            pairs.append((a, b))
            rows.append((r, a, b))
    if not pairs:
        print("[중단] human_label_a/b가 모두 채워진 행이 없습니다.")
        return

    _agreement_report(pairs, "라벨러 A vs 라벨러 B (사람 간 일치도)")
    kappa = _quadratic_weighted_kappa(pairs)
    if kappa < 0.6:
        print("  ⚠ 사람 간 κ<0.6 — 판정 기준(2/1/0 정의)이 사람에게도 모호하다는 신호입니다. "
              "기준을 먼저 다듬고 재라벨링하세요.")

    disagree = [(r, a, b) for r, a, b in rows if a != b]
    print(f"\n불일치 {len(disagree)}/{len(pairs)}건 — 재논의 후 human_label_final을 채우세요:")
    for r, a, b in disagree[:args.show]:
        print(f"  [{r.get('question_intent','?')}] {r.get('question','')[:38]}  A={a} B={b}")
        print(f"      {r.get('chunk_text','')[:70].replace(chr(10), ' ')}...")
    if len(disagree) > args.show:
        print(f"  ... 외 {len(disagree) - args.show}건 (--show 로 더 보기)")

    final_done = sum(1 for r in raw if _valid(r.get("human_label_final")))
    print(f"\nhuman_label_final 채워짐: {final_done}/{len(raw)}행")


def _quadratic_weighted_kappa(pairs: list[tuple[int, int]], n_classes: int = 3) -> float:
    """이차가중 Cohen's κ. 순서형(0/1/2)이라 |i-j|이 클수록 불일치를 크게 벌한다.
    1.0=완전일치, 0=우연 수준, 음수=우연보다 나쁨. 관측=기대면(=변별 불가) 1.0 반환."""
    n = len(pairs)
    if n == 0:
        return 0.0
    O = [[0] * n_classes for _ in range(n_classes)]
    row_marg = [0] * n_classes
    col_marg = [0] * n_classes
    for h, j in pairs:
        O[h][j] += 1
        row_marg[h] += 1
        col_marg[j] += 1
    num = den = 0.0
    denom_w = (n_classes - 1) ** 2
    for i in range(n_classes):
        for k in range(n_classes):
            w = ((i - k) ** 2) / denom_w
            e = row_marg[i] * col_marg[k] / n
            num += w * O[i][k]
            den += w * e
    if den == 0:
        return 1.0
    return 1.0 - num / den


def _agreement_report(pairs: list[tuple[int, int]], label: str) -> None:
    """(human, judge) 쌍 리스트로 일치 지표를 출력한다."""
    n = len(pairs)
    exact = sum(1 for h, j in pairs if h == j) / n
    # threshold=2 이진 일치: Precision/MRR이 실제로 쓰는 기준이라 이게 제일 중요.
    bin2 = sum(1 for h, j in pairs if (h >= 2) == (j >= 2)) / n
    kappa = _quadratic_weighted_kappa(pairs)
    print(f"\n[{label}]  n={n}")
    print(f"  정확 일치율(0/1/2 그대로)   {exact:.1%}")
    print(f"  threshold=2 이진 일치율      {bin2:.1%}   ← Precision/MRR이 쓰는 기준, 가장 중요")
    print(f"  이차가중 κ                   {kappa:.3f}   (0.6+ 실용, 0.8+ 우수)")


# ---------------------------------------------------------------- score
def _judge_rows(rows: list[dict], judge: Judge) -> list[tuple[int, int, str]]:
    """각 행을 판정기로 채점해 (human, judge, intent) 리스트를 만든다(입력 순서 유지)."""
    def one(r: dict) -> tuple[int, int, str]:
        human = int(r["human_label"].strip())
        j = judge.score(r["query_id"], r["question"], r["chunk_id"], r["chunk_text"])
        return human, j, r.get("question_intent", "기타")

    with ThreadPoolExecutor(max_workers=_JUDGE_WORKERS) as ex:
        out = list(ex.map(one, rows))   # map은 입력 순서를 보존한다
    judge.flush()
    return out


def cmd_score(args) -> None:
    """라벨 채운 CSV로 현재 판정기의 사람 대비 일치도를 측정한다."""
    rows = _load_labeled(args.csv)
    if not rows:
        return

    judge = Judge(model=args.model) if args.model else Judge()
    scored = _judge_rows(rows, judge)
    pairs = [(h, j) for h, j, _ in scored]

    _agreement_report(pairs, f"판정기 검증 (model={judge.model}, prompt={judge.prompt_version})")

    # 혼동행렬 3x3 (행=사람, 열=판정기)
    print("\n혼동행렬 (행=사람 라벨, 열=판정기):")
    print("          판정0   판정1   판정2")
    conf = [[0, 0, 0] for _ in range(3)]
    for h, j in pairs:
        conf[h][j] += 1
    for h in range(3):
        print(f"  사람{h}   " + "  ".join(f"{conf[h][j]:>5}" for j in range(3)))

    # 의도별 일치율: 유래·용례에서 낮으면 facet 조건부 기준이 필요하다는 신호.
    print("\n의도별 정확 일치율 (낮은 의도 = 그 facet 기준이 애매):")
    by_intent: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for h, j, intent in scored:
        by_intent[intent].append((h, j))
    for intent, ps in sorted(by_intent.items(), key=lambda kv: sum(a == b for a, b in kv[1]) / len(kv[1])):
        acc = sum(1 for a, b in ps if a == b) / len(ps)
        print(f"  {intent:<5} n={len(ps):<3} {acc:.0%}")

    _print_judge_failures(judge)


# ---------------------------------------------------------------- ab
def _load_prompt(path: str | None) -> str:
    """프롬프트 템플릿 파일을 읽는다. None이면 judge.py의 기본 프롬프트."""
    if path is None:
        return _PROMPT
    with open(path, encoding="utf-8") as f:
        return f.read()


def cmd_ab(args) -> None:
    """두 판정기 변형(A/B)을 같은 gold에 대고 재고 A−B 차이의 CI를 낸다.

    변형은 프롬프트(--prompt-a/--prompt-b)와 모델(--model-a/--model-b)로 준다.
    아무것도 안 주면 A=현재 판정기라 무의미 → 최소 한쪽은 바꿔야 한다.
    """
    rows = _load_labeled(args.csv)
    if not rows:
        return

    judge_a = Judge(model=args.model_a, prompt=_load_prompt(args.prompt_a))
    judge_b = Judge(model=args.model_b, prompt=_load_prompt(args.prompt_b))
    if judge_a._cache_key("x", "y") == judge_b._cache_key("x", "y"):
        print("[경고] A와 B가 동일한 판정기입니다(모델·프롬프트 둘 다 같음). "
              "--prompt-b 또는 --model-b로 한쪽을 바꾸세요.")
        return

    scored_a = _judge_rows(rows, judge_a)
    scored_b = _judge_rows(rows, judge_b)

    _agreement_report([(h, j) for h, j, _ in scored_a],
                      f"A (model={judge_a.model}, prompt={judge_a.prompt_version})")
    _agreement_report([(h, j) for h, j, _ in scored_b],
                      f"B (model={judge_b.model}, prompt={judge_b.prompt_version})")

    # 쿼리 순서로 정렬된 per-item 정답 여부(threshold=2 기준)로 paired 차이 CI.
    # 지표를 좌우하는 건 "2점을 사람과 똑같이 맞히느냐"라서 이 기준으로 A/B를 가른다.
    correct_a = [int((h >= 2) == (j >= 2)) for h, j, _ in scored_a]
    correct_b = [int((h >= 2) == (j >= 2)) for h, j, _ in scored_b]
    ci = metrics.paired_diff_ci(correct_a, correct_b)

    # McNemar 성분: A만 맞음 / B만 맞음 (동시 맞음·틀림은 상쇄되어 무의미)
    a_only = sum(1 for ca, cb in zip(correct_a, correct_b) if ca and not cb)
    b_only = sum(1 for ca, cb in zip(correct_a, correct_b) if cb and not ca)

    mark = "유의✅" if ci["significant"] else "무의미(차이없음 가능)"
    print("\n" + "=" * 60)
    print("A/B 판정 (threshold=2 정답 여부, A−B paired 부트스트랩 CI):")
    print(f"  ΔAccuracy = {ci['mean_diff']:+.4f}   CI[{ci['ci_low']:+.4f}, {ci['ci_high']:+.4f}]   → {mark}")
    print(f"  A만 정답 {a_only}쌍 / B만 정답 {b_only}쌍 / n={ci['n']}")
    winner = "A" if ci["mean_diff"] > 0 else ("B" if ci["mean_diff"] < 0 else "무승부")
    print(f"  → 사람 라벨에 더 가까운 쪽: {winner}"
          + ("" if ci["significant"] else " (단, CI가 0을 포함 → 표본을 더 늘려야 확정)"))

    _print_judge_failures(judge_a, "A")
    _print_judge_failures(judge_b, "B")


def _print_judge_failures(judge: Judge, tag: str = "") -> None:
    total = judge.total_scored
    if not total:
        return  # 전부 캐시 히트 → 신규 호출 없음
    fails = judge.parse_failures + judge.api_failures
    suffix = f" ({tag})" if tag else ""
    print(f"\n판정 신규호출{suffix}: {total}건  [파싱실패 {judge.parse_failures} / "
          f"API실패 {judge.api_failures}] — 실패는 0점 폴백이라 일치율을 낮출 수 있음")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 관련도 판정기 검증·A/B 하네스")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="union 풀에서 층화 라벨셋 CSV 생성")
    # 60쌍은 κ 신뢰구간이 넓어 A/B 승부를 가리기 어렵다 → 기본 100쌍.
    pb.add_argument("--n", type=int, default=100, help="샘플 쌍 수 (기본 100)")
    pb.add_argument("--seed", type=int, default=42, help="재현용 시드")
    pb.add_argument("--out", default=_DEFAULT_CSV, help="출력 CSV 경로")
    pb.set_defaults(func=cmd_build)

    pg = sub.add_parser("agree", help="라벨러 2인 간 κ·불일치 목록 (gold 확정 전 단계)")
    pg.add_argument("--csv", default=_DEFAULT_CSV, help="라벨셋 CSV 경로")
    pg.add_argument("--show", type=int, default=15, help="출력할 불일치 건수")
    pg.set_defaults(func=cmd_agree)

    ps = sub.add_parser("score", help="라벨 채운 CSV로 현재 판정기 검증")
    ps.add_argument("--csv", default=_DEFAULT_CSV, help="라벨셋 CSV 경로")
    ps.add_argument("--model", default=None, help="판정 모델 오버라이드(기본: judge.py JUDGE_MODEL)")
    ps.set_defaults(func=cmd_score)

    pa = sub.add_parser("ab", help="두 판정기 변형(프롬프트/모델)을 gold 대비로 A/B")
    pa.add_argument("--csv", default=_DEFAULT_CSV, help="라벨셋 CSV 경로")
    pa.add_argument("--prompt-a", default=None, help="A 프롬프트 템플릿 파일(기본: 내장 프롬프트)")
    pa.add_argument("--prompt-b", default=None, help="B 프롬프트 템플릿 파일")
    pa.add_argument("--model-a", default=JUDGE_MODEL, help="A 판정 모델")
    pa.add_argument("--model-b", default=JUDGE_MODEL, help="B 판정 모델")
    pa.set_defaults(func=cmd_ab)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
