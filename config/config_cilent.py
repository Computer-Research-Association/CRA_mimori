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
