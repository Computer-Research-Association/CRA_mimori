import os
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

# Tavily
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# MongoDB
MONGO_URI = os.getenv("MONGODB_URI", "")
MONGO_DB = "mimori"
MONGO_COLLECTION = "memes"
CLEANED_COLLECTION = "cleaned_memes"  # 전처리/청킹 결과 저장용 (원본 memes와 분리)
TREND_COLLECTION = "trend_scores"     # 트렌드 판정 결과 저장용 (키워드+날짜 단위)

# 검색 설정
TAVILY_MAX_RESULTS = 20
TAVILY_SEARCH_DEPTH = "advanced"  # "basic" or "advanced"
# Tavily relevance score 하한선. 기존 수집분의 is_relevant 라벨 기준
# 0.3에서 정상 문서 89% 유지 / 오염 문서 58% 차단 — 잔여 오염은 전처리 judge가 거름.
TAVILY_MIN_SCORE = 0.3

# YouTube
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_MAX_RESULTS = 30    # 키워드당 검색할 영상 개수
YOUTUBE_MAX_COMMENTS = 50   # 영상당 가져올 댓글 개수
# (댓글 수 하한선은 제거됨 — 제목/설명 키워드 필터가 관련성 판별을 대신함)


# 웹 크롤러 공통 설정
CRAWL_DELAY_MIN = 1.5       # 요청 사이 최소 대기 (초)
CRAWL_DELAY_MAX = 4.0       # 요청 사이 최대 대기 (초)
CRAWL_MAX_RETRIES = 3       # 실패 시 최대 재시도 횟수
CRAWL_MAX_POSTS = 20        # 사이트당 최대 수집 게시글 수

# 크롤링 병렬화 설정 (main.py)
# 모든 (키워드 × 소스) 크롤 작업을 하나의 평평한 스레드풀에서 병렬 실행한다.
#
# 차단을 유발하는 건 '동시 연결 수'가 아니라 '단위시간당 요청 수(rate)'다.
# 그래서 동시성 상한(세마포어) 대신, 스크래퍼는 crawlers/base.py 의 도메인별
# RateLimiter 가 요청 rate 자체를 직렬 수준으로 묶는다(min-interval 은 위의
# CRAWL_DELAY_MIN/MAX 재사용). 워커 수를 늘려도 한 사이트로 가는 rate 는 그대로다.
# API 소스(tavily/youtube)는 IP 차단이 아니라 쿼터 방식이라 동시 요청에 관대.
CRAWL_WORKERS = 8          # (키워드 × 소스) 평평한 풀의 워커 수

# 크롤링 필터링 기준
# 밈은 생명주기가 있어 오래된 글은 현재 맥락과 다를 수 있음 → 날짜 하한선 적용
CRAWL_MAX_AGE_YEARS = 3       # 이보다 오래된 게시글은 수집 제외
CRAWL_MAX_SEARCH_PAGES = 10   # 검색 결과 페이지 탐색 상한 (날짜 필터로 인한 무한 탐색 방지)

# 소스별 정렬 기준 (각 사이트가 지원하는 값이 다름)
NATEPANN_SORT = "HD"          # PD 정확도 / DD 최신 / HD 인기 / VD 조회 / CD 댓글
DCINSIDE_SORT = "accuracy"    # accuracy 정확도 / latest 최신 (디시 검색은 인기순 미지원)
YOUTUBE_ORDER = "relevance"   # relevance / date / viewCount / rating

# 전처리 / 청킹 설정
CHUNK_SIZE = 500            # 청크 최대 글자 수 (RecursiveCharacterTextSplitter 기준)
CHUNK_OVERLAP = 50           # 청크 간 중복 글자 수
REPEAT_CHAR_LIMIT = 3        # 동일 문자 반복 시 축약할 최대 개수 (예: "ㅋㅋㅋㅋㅋ" -> "ㅋㅋㅋ")

# 임베딩 / Qdrant 설정
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DENSE_DIM = 1024          # BGE-M3 dense 벡터 차원 (고정값)
EMBEDDING_BATCH_SIZE = 16           # encode() 1회 호출당 청크 수 (VRAM 6GB 기준)

QDRANT_COLLECTION = "mimori_chunks"
QDRANT_DENSE_VECTOR_NAME = "dense"
QDRANT_SPARSE_VECTOR_NAME = "sparse"
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))

# 나무위키 크롤러가 섹션 헤딩 자리에 남기는 마커.
# preprocessing/chunker.py가 이 마커를 기준으로 문서 구조 기반 청킹을 수행하므로
# 크롤러와 전처리 모듈이 같은 값을 공유해야 함 -> config에 정의.
NAMUWIKI_SECTION_MARKER = "[[SECTION]] "

# 브라우저처럼 보이기 위한 User-Agent 목록
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

# LLM 분석 설정
ANALYSIS_MODEL = ("qwen3:8b")
ANALYSIS_PROMPT_PATH = os.path.join(_ROOT, "analysis", "prompt_template.md")

# RAG 질의응답 설정
RAG_TOP_K = 5
RAG_PROMPT_PATH = os.path.join(_ROOT, "analysis", "rag_prompt_template.md")

# facet(의미/유행_이유/사용법/사용자층) 4항목 고정 분석용 프롬프트.
# 자유질문용(RAG_PROMPT_PATH, {question} 포함)과 달리 {question}이 없고 4항목을 고정 지시한다.
RAG_FACET_PROMPT_PATH = os.path.join(_ROOT, "analysis", "rag_facet_prompt_template.md")

# facet 검색 결과 병합 단계 튜너블 (rag_pipeline.merge_facet_results / facet_search 기본값).
# max_per_source는 facet 하나 안에서만 걸리므로, facet 4개가 같은 소스를 2개씩 뽑으면 합계가
# 8개까지 쏠릴 수 있어 병합(합산) 단계에서 한 번 더 상한을 건다.
RAG_FACET_MERGED_MAX_PER_SOURCE = 6  # facet 전체 합산 기준 소스당 상한
RAG_FACET_MIN_MERGED_TOTAL = 8       # 소스 상한 때문에 컨텍스트가 비지 않도록 최소 확보 개수
RAG_FACET_NEAR_DUP_THRESHOLD = 0.8   # 이 이상 유사하면 재게시(미러링)로 보고 제외

#nvidia_api
NIM_KEY = os.getenv("NIM_KEY", "")

