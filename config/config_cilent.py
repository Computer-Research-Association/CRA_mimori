import os
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

# api/routes.py의 관리자 전용 엔드포인트(hide/unhide/완전삭제/admin stats)를 보호하는
# 공유 시크릿. X-Admin-Key 헤더와 비교한다. 비어있으면(미설정) 해당 엔드포인트는
# 전부 401로 막는다(fail-closed) — 값을 깜빡 안 넣었다고 인증이 풀리면 안 되므로.
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

# Tavily
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# MongoDB
MONGO_URI = os.getenv("MONGODB_URI", "")
MONGO_DB = "mimori"
# 컬렉션 이름은 env로 덮어쓸 수 있다 — 테스트용 컬렉션(memes_test 등)으로 돌릴 때 쓴다.
# 기본값이 현재와 같으므로 .env를 안 건드리면 동작이 동일하다.
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "memes")
CLEANED_COLLECTION = os.getenv("CLEANED_COLLECTION", "cleaned_memes")
TREND_COLLECTION = "trend_scores"     # 트렌드 판정 결과 저장용 (키워드+날짜 단위)
CRAWL_REQUESTS_COLLECTION = "crawl_requests"     # 새 키워드 온디맨드 수집 큐
HIDDEN_KEYWORDS_COLLECTION = "hidden_keywords"   # 검색 목록에서 숨긴 키워드 (완전삭제 전 1단계)
LOCKS_COLLECTION = "locks"                        # heavy_job_lock 등 프로세스 간 락
LLM_REQUESTS_COLLECTION = "llm_requests"          # analyze/rag 비동기 큐 겸 결과 캐시

# llm_request_worker.py는 컨테이너 기동 시 --loop로 한 번만 떠서 계속 상주한다
# (매 요청마다 새 프로세스를 띄우면 BGE-M3 로드에 수십 초가 반복돼 실제 처리
# 시간보다 콜드스타트가 훨씬 커지기 때문). 이 폴링 간격만큼마다 큐를 확인한다.
# 상시 상주는 유휴 시에도 BGE-M3(RAM 2~3GB)를 계속 점유한다는 뜻이지만, EC2
# free -h로 확인한 가용 메모리(6.3GB)가 감당 가능해 콜드스타트 완전 제거를
# 택했다(2026-08-14). RAM 여유가 없는 환경으로 옮기면 이 상주 방식부터 재검토할 것.
LLM_WORKER_POLL_INTERVAL_SECONDS = 2

# analyze 스트리밍 답변을 Mongo에 반영하는 최소 간격(초). 청크마다 쓰면 LLM이
# 토큰을 뱉는 속도로 Mongo write가 발생해 부담이 크므로, 이 간격보다 짧게는
# 건너뛴다. 마지막 청크가 스로틀에 걸려도 상관없다 — 완료 시 최종 전체 텍스트로
# 덮어써진다(scripts/llm_request_worker.py).
ANALYZE_STREAM_WRITE_INTERVAL_SECONDS = 0.75

# 검색 설정
TAVILY_MAX_RESULTS = 20
TAVILY_SEARCH_DEPTH = "advanced"  # "basic" or "advanced"
# Tavily relevance score 하한선. 기존 수집분의 is_relevant 라벨 기준
# 0.3에서 정상 문서 89% 유지 / 오염 문서 58% 차단 — 잔여 오염은 전처리 judge가 거름.
TAVILY_MIN_SCORE = 0.3
# 같은 키워드를 이 일수 이내에 이미 크롤했으면 API 호출을 건너뛴다.
# 밈 키워드는 단기간에 새 문서가 폭증하지 않으므로 7일이 적정값.
TAVILY_RECRAWL_DAYS = 3

# YouTube
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_MAX_RESULTS = 30    # 키워드당 검색할 영상 개수
YOUTUBE_MAX_COMMENTS = 50   # 영상당 가져올 댓글 개수
# (댓글 수 하한선은 제거됨 — 제목/설명 키워드 필터가 관련성 판별을 대신함)
# 같은 키워드를 이 일수 이내에 이미 크롤했으면 API 호출을 건너뛴다 (쿼터 절약).
YOUTUBE_RECRAWL_DAYS = 3
# 자막(트랜스크립트) 텍스트 상한(글자 수). 긴 영상은 자막이 수천~수만 자에 달해
# 청킹/임베딩 비용이 커지므로 앞부분만 쓴다(밈/신조어 설명 영상은 보통 도입부에
# 핵심 정의가 나온다). youtube-transcript-api(비공식, OAuth 불필요)로 가져오며,
# 자막이 없는 영상(다수)은 조용히 건너뛴다 — 댓글/설명만으로도 문서 자체는 유효하므로.
YOUTUBE_MAX_TRANSCRIPT_CHARS = 4000


# 웹 크롤러 공통 설정
CRAWL_DELAY_MIN = 1.5       # 요청 사이 최소 대기 (초)
CRAWL_DELAY_MAX = 4.0       # 요청 사이 최대 대기 (초)
CRAWL_MAX_RETRIES = 3       # 실패 시 최대 재시도 횟수
CRAWL_MAX_POSTS = 20        # 사이트당 최대 수집 게시글 수

# 내용 필터(본문 길이/관련성)로 걸러낸 글을 기록해 두는 컬렉션.
# 걸러진 글은 memes에 저장되지 않아 '이미 저장됨' 판정에 안 걸리고, 그래서 매 실행마다
# 다시 다운로드된 뒤 다시 버려졌다(요청 1건당 도메인 rate limit 평균 2.3초).
REJECT_COLLECTION = os.getenv("REJECT_COLLECTION", "crawl_rejects")
# 거절 이력의 유효기간. 이 기간이 지나면 다시 한 번 받아서 재평가한다 —
# 영구 스킵으로 두면 필터 기준(MIN_CONTENT_LEN 등)을 고쳐도 옛 판정이 그대로 굳는다.
CRAWL_REJECT_TTL_DAYS = 30

# 크롤링 병렬화 설정 (main.py)
# 모든 (키워드 × 소스) 크롤 작업을 하나의 평평한 스레드풀에서 병렬 실행한다.
#
# 차단을 유발하는 건 '동시 연결 수'가 아니라 '단위시간당 요청 수(rate)'다.
# 그래서 동시성 상한(세마포어) 대신, 스크래퍼는 crawlers/base.py 의 도메인별
# RateLimiter 가 요청 rate 자체를 직렬 수준으로 묶는다(min-interval 은 위의
# CRAWL_DELAY_MIN/MAX 재사용). 워커 수를 늘려도 한 사이트로 가는 rate 는 그대로다.
# API 소스(tavily/youtube)는 IP 차단이 아니라 쿼터 방식이라 동시 요청에 관대.
CRAWL_WORKERS = 8          # (키워드 × 소스) 평평한 풀의 워커 수

# 배치 크롤 우선순위(main.py): 커뮤니티 크롤러(dcinside/namuwiki/youtube/natepann/
# todayhumor)를 먼저 돌리고, 한 키워드의 합계 문서 수가 이 기준 미만이면 그 키워드만
# Tavily로 보완 호출한다. Tavily 자체가 예외로 실패하면 DuckDuckGo로 한 번 더
# 보완한다(폴백의 폴백). 전체 실패는 합계가 자연히 0이 되어 같은 조건에 포함된다.
MIN_COMMUNITY_DOCS_FOR_TAVILY = 3

# Keywords.md(배치 크롤 대상) 상한. 이 파일이 커질수록 매일 새벽 배치 크롤 시간과
# Tavily/YouTube API 쿼터 소모가 함께 늘어난다. 상한을 넘으면 새 키워드는 (이미
# 수집·분석은 끝난 채로) Keywords.md 편입만 거부되고, crawl_requests 문서에
# promotion_skipped="cap"이 남아 관리자 화면에서 알림으로 보인다 — 관리자가 오래되거나
# 인기 없는 키워드를 정리하면 다음 신규 키워드부터 다시 편입된다.
# 2026-08-14 기준 실제 등록 22개 — 여유를 넉넉히 둔 값이라 필요하면 조정할 것.
MAX_BATCH_KEYWORDS = 60

# DuckDuckGo 폴백 검색 설정 — Tavily 크롤이 예외로 실패했을 때만 호출된다.
DUCKDUCKGO_MAX_RESULTS = 10   # 페이지네이션 없이 첫 페이지만 사용(폴백이라 비용 대비 실효 우선)
DUCKDUCKGO_RECRAWL_DAYS = 3   # Tavily가 며칠째 계속 실패해도 이 폴백을 매일 다시 두드리지 않음

# 크롤링 필터링 기준
# 밈은 생명주기가 있어 오래된 글은 현재 맥락과 다를 수 있음 → 날짜 하한선 적용
CRAWL_MAX_AGE_YEARS = 3       # 이보다 오래된 게시글은 수집 제외
CRAWL_MAX_SEARCH_PAGES = 10   # 검색 결과 페이지 탐색 상한 (날짜 필터로 인한 무한 탐색 방지)

# 소스별 정렬 기준 (각 사이트가 지원하는 값이 다름)
# 정렬 하나만 쓰면 "아직 인기를 못 얻은 최신 글"이 계속 순위 밖으로 밀리는 편향이
# 생긴다(인기/정확도순은 추천·조회가 쌓일 시간이 필요해서 갓 올라온 글은 못 낌).
# 그래서 성격이 다른 정렬을 섞어서 수집한다 — CRAWL_MAX_POSTS를 정렬 개수로 나눠 쓴다.
NATEPANN_SORTS = ("HD", "DD")           # 인기 + 최신 (PD 정확도 / DD 최신 / HD 인기 / VD 조회 / CD 댓글)
DCINSIDE_SORTS = ("accuracy", "latest")  # 정확도 + 최신 (디시 검색은 인기순 미지원)
YOUTUBE_ORDER = "relevance"   # relevance / date / viewCount / rating (YouTube는 쿼터 문제로 보류 — 아래 참고)

# 전처리 / 청킹 설정
CHUNK_SIZE = 500            # 청크 최대 글자 수 (RecursiveCharacterTextSplitter 기준)
CHUNK_OVERLAP = 50           # 청크 간 중복 글자 수
REPEAT_CHAR_LIMIT = 3        # 동일 문자 반복 시 축약할 최대 개수 (예: "ㅋㅋㅋㅋㅋ" -> "ㅋㅋㅋ")
MIN_CHUNK_CHARS = 30         # 이 미만이면 정보 없는 청크로 본다 (RAG 필터와 같은 값)
SPAM_HIT_THRESHOLD = 3       # 스팸 패턴이 이 개수 이상이면 광고로 본다
BOILERPLATE_PHRASES = (
    "본문 바로가기", "메뉴 바로가기", "마이페이지",
    "이웃추가", "구독하기", "공유하기", "URL복사", "신고하기",
    "찬반대결", "책갈피", "최신순", "추천순",
    "dc official App",
    # Daum 카페(tavily가 그대로 긁어오는 경우, 실사례 2026-08-04 cafe.daum.net) UI 상투어.
    # "로그인"/"스크랩0"처럼 너무 흔하거나(오탐 위험) 이번 건에만 해당하는(방문자 수 등)
    # 문구는 일부러 제외했다 — 일반화 가능한 것만 넣는다.
    "카페정보", "카페 프로필 이미지", "카페 가입하기", "카페 전체 메뉴",
    "검색이 허용된 게시물입니다", "게시글 본문내용", "검색 옵션 선택상자",
    "댓글내용선택됨", "서비스 약관/정책", "권리침해신고", "카페 고객센터", "검색비공개 요청",
    "카페 게시글", "목록 이전글 다음글", "다음검색", "옵션 더 보기", "댓글 작성자", "최신목록",
)

# 임베딩 / Qdrant 설정
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DENSE_DIM = 1024          # BGE-M3 dense 벡터 차원 (고정값)
EMBEDDING_BATCH_SIZE = 16           # encode() 1회 호출당 청크 수 (VRAM 6GB 기준)

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "mimori_chunks")
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

ANALYSIS_MODEL = "openai/gpt-oss-120b"
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

# ── 품질 계측 (quality_test) ────────────────────────────────────────────────
# 산출물 경로. cwd가 아니라 프로젝트 루트 기준으로 고정한다 —
# 다른 폴더에서 실행해도 같은 곳에 쌓이게 하기 위함.
DATA_TEST_DIR = os.path.join(_ROOT, "data_test")
FIXTURE_DIR = os.path.join(DATA_TEST_DIR, "fixtures")
RUNS_DIR = os.path.join(DATA_TEST_DIR, "runs")
DEFAULT_FIXTURE_NAME = "raw_sample.jsonl"

# 품질 판정 임계값. signals.py는 값만 계산하고, 판정은 이 상수를 읽는 쪽에서 한다.
# 전부 '확실히 나쁜 것만' 잡도록 보수적으로 잡은 시작값이며, 리포트로 분포를 보고 조정한다.
# MIN_CHUNK_CHARS / SPAM_HIT_THRESHOLD / BOILERPLATE_PHRASES는 위(전처리/청킹 설정)에서
# 이미 정의됨 — quality_test/signals.py와 preprocessing/{cleaner,chunker}.py가 탐지·제거
# 기준을 공유해야 하므로 같은 상수를 그대로 재사용한다(중복 정의 금지 — 갈라지면 조용히
# 탐지 기준과 실제 필터링 기준이 어긋난다).
MIN_HANGUL_RATIO = 0.3      # 국내 소스인데 이 미만이면 본문 추출 실패 의심
DOMESTIC_SOURCES = ("natepann", "dcinside", "namuwiki", "todayhumor")  # 한글 비율 규칙을 적용할 소스


