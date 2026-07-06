import os
from dotenv import load_dotenv

load_dotenv()

# Tavily
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# MongoDB
MONGO_URI = os.getenv("MONGODB_URI", "")
MONGO_DB = "mimori"
MONGO_COLLECTION = "memes"

# 검색 설정
TAVILY_MAX_RESULTS = 20
TAVILY_SEARCH_DEPTH = "advanced"  # "basic" or "advanced"

# YouTube
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_MAX_RESULTS = 30    # 키워드당 검색할 영상 개수
YOUTUBE_MAX_COMMENTS = 50  #영상당 가져올 댓글 개수


# 웹 크롤러 공통 설정
CRAWL_DELAY_MIN = 1.5       # 요청 사이 최소 대기 (초)
CRAWL_DELAY_MAX = 4.0       # 요청 사이 최대 대기 (초)
CRAWL_MAX_RETRIES = 3       # 실패 시 최대 재시도 횟수
CRAWL_MAX_POSTS = 20        # 사이트당 최대 수집 게시글 수

# 브라우저처럼 보이기 위한 User-Agent 목록
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

