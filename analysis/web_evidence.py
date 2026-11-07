"""저장된 근거가 빈약할 때 설명형 검색 자료를 한 번 보충한다."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from config.config_cilent import (
    ANALYSIS_EVIDENCE_MIN_CHARS, ANALYSIS_EVIDENCE_MIN_DOCUMENT_CHARS,
    ANALYSIS_WEB_MAX_RESULTS, ANALYSIS_WEB_TIMEOUT_SECONDS,
    ANALYSIS_WEB_EXCLUDE_DOMAINS, TAVILY_API_KEY, TAVILY_MIN_SCORE,
)
from quality_test.matching import find_keyword
from analysis.evidence import prepare_evidence
from analysis.rag_pipeline import clean_source_url, is_content_source


def _search(keyword):
    from tavily import TavilyClient

    def search_one(spec):
        query, topic = spec
        try:
            with TavilyClient(api_key=TAVILY_API_KEY) as client:
                response = client.search(
                    query=query, topic=topic, search_depth="advanced",
                    max_results=ANALYSIS_WEB_MAX_RESULTS, chunks_per_source=3,
                    include_answer=False, include_raw_content=False,
                    exclude_domains=ANALYSIS_WEB_EXCLUDE_DOMAINS,
                    timeout=ANALYSIS_WEB_TIMEOUT_SECONDS,
                )
            return response.get("results", [])
        except Exception as exc:
            print(f"[근거 보충] {topic} 검색 실패: {type(exc).__name__}", flush=True)
            return []

    # 최근 보도와 뜻·유래 해설을 동시에 찾는다. 한쪽 실패가 다른 결과를 버리지 않는다.
    queries = [(f'"{keyword}"', "news"), (f'"{keyword}" 뜻 유래', "general")]
    with ThreadPoolExecutor(max_workers=len(queries)) as executor:
        return [result for batch in executor.map(search_one, queries) for result in batch]


def enrich_evidence(keyword, points, sources=None):
    """출처 필터·분량 제한을 지키며 보충한다. 검색 실패 시 기존 근거를 유지한다."""
    if not TAVILY_API_KEY or (sources is not None and "tavily" not in sources):
        return points
    lengths = [len(p.payload.get("text") or "") for p in points]
    if (sum(lengths) >= ANALYSIS_EVIDENCE_MIN_CHARS
            and max(lengths, default=0) >= ANALYSIS_EVIDENCE_MIN_DOCUMENT_CHARS):
        return points
    try:
        results = _search(keyword)
    except Exception as exc:
        # 공급자 예외 메시지에 요청 정보가 섞일 수 있으므로 종류만 남긴다.
        print(f"[근거 보충] 검색 실패: {type(exc).__name__}", flush=True)
        return points

    additions = []
    for result in results:
        title = result.get("title") or ""
        text = (result.get("content") or "").strip()
        url = clean_source_url(result.get("url"))
        if (result.get("score", 0) < TAVILY_MIN_SCORE
                or not url.startswith(("https://", "http://"))
                or not is_content_source(url)
                or not find_keyword(keyword, title, text).matched
                or len(text) < ANALYSIS_EVIDENCE_MIN_DOCUMENT_CHARS):
            continue
        additions.append(SimpleNamespace(payload={
            "title": title, "url": url, "text": text, "source": "tavily",
        }))
    if not additions:
        return points
    # 같은 URL의 짧은 검색 조각보다 이번에 확보한 설명 본문을 우선한다.
    combined = prepare_evidence(additions + points, clean_source_url, keyword=keyword)
    print(f"[근거 보충] 설명 자료 {len(additions)}개 확보", flush=True)
    return combined
