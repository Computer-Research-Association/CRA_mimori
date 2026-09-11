FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# trend/ 모듈 전체(datalab_client, zscore.drop_incomplete_today, trend_service.save_trend_score
# 등)가 date.today()/datetime.now()(시간대 미지정)로 "오늘"을 판단하는데, 이게 KST 기준이라는
# 전제로 짜여 있다(CRAWL_HOUR도 KST 02:00). 베이스 이미지 기본 시간대(UTC)로 두면 KST 02:00
# 배치가 UTC로는 아직 전날이라 trend_scores.date가 실제보다 하루 밀려 저장된다
# (2026-09-11, 실측: 매일 새벽 배치 결과가 하루 전 날짜로 쌓이던 문제).
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Seoul

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

COPY . .

CMD ["uv", "run", "--no-dev", "python", "scheduler/scheduler.py"]


