# 크롤링 · 청킹 · 임베딩 자동화 구조

> 작성 2026-07-31. `scheduler/scheduler.py` 기준.
> 관련 커밋: `2069d79`(청킹/임베딩), `c599628`(status 로그), `f4dbf1a`(스케줄러 업데이트)

## 1. 한 줄 요약

크롤링 → 청킹 → 임베딩 3단계가 전부 스케줄러에 등록되어, 사람이 CLI를 직접 돌리지 않아도
자동으로 진행된다. **크롤은 2시간마다, 청킹+임베딩은 하루 1회**로 주기를 분리했다.

## 2. 등록된 자동 작업

| job id | 하는 일 | 주기 (KST) | 실행 대상 |
|---|---|---|---|
| `crawl_job` | 5개 소스 크롤링 → Mongo `memes` 적재 | `*/2` 시 정각 (00,02,…22시) | `main.py` |
| `preprocess_embed_job` | 청킹 → Mongo `cleaned_memes`, 임베딩 → Qdrant | 매일 04:30 | `preprocess_embed_main.py` |
| `heartbeat_job` | 살아있음 로그 | 10분마다 | (인프로세스 함수) |

크롤 시각(짝수시 정각)과 청킹/임베딩 시각(04:30)이 겹치지 않게 배치했다.

## 3. 실행 구조

```
scheduler.py (컨테이너의 실제 진입점, BackgroundScheduler)
  │
  ├─ crawlrun()            ──subprocess──▶ main.py
  │                                          └─ crawlers/* → Mongo memes (is_embedded=False)
  │
  └─ preprocess_embed_run() ─subprocess──▶ preprocess_embed_main.py
                                             ├─ preprocess_documents(None)  → Mongo cleaned_memes
                                             └─ embed_documents(None)       → Qdrant mimori_chunks
                                                                              + memes.is_embedded=True
```

두 작업 모두 **함수 직접 호출이 아니라 `subprocess.run()`으로 별도 프로세스에서** 실행한다
(`scheduler.py:34`, `scheduler.py:52`). 이유:

- 임베딩은 BGE-M3 모델을 메모리에 올린다. 같은 프로세스에서 돌리면 작업이 끝나도 메모리가
  스케줄러 프로세스에 계속 남는다. 별도 프로세스면 종료와 함께 OS가 회수한다.
- 크롤/임베딩 중 어느 쪽이 죽어도 스케줄러 본체는 살아있다. `returncode`만 보고 성공/실패를
  판정하면 되므로 예외 처리가 단순해진다.

## 4. 청킹+임베딩 자동화 — 새로 만든 게 아니라 "입력만 걷어낸 것"

`preprocess_documents()`와 `embed_documents()`는 원래부터 `keyword=None`이면
**미처리 전체를 스스로 찾아 처리하도록** 짜여 있었다.

`preprocessing/pipeline.py:31`
```python
query = {"is_embedded": False}
if keyword:
    query["keyword"] = keyword
```

`embedding/pipeline.py:88`
```python
query = {"is_embedded": False}
if keyword:
    query["keyword"] = keyword
```

그래서 자동화를 위해 **새 로직을 만들 필요가 없었다.** 기존 `preprocess_main.py` /
`embed_main.py`가 `input()`으로 키워드를 물어보는 대화형이라 크론에서 못 쓰는 것뿐이었으므로,
그 부분만 없앤 비대화형 진입점 `preprocess_embed_main.py`를 새로 만들어 두 함수를 순서대로
호출한다.

```python
chunks = preprocess_documents(None)   # 전체 미처리 문서 청킹
result = embed_documents(None)        # 전체 미처리 문서 임베딩
```

기존 대화형 CLI(`preprocess_main.py`, `embed_main.py`)는 그대로 남아 있어 수동 실행도 계속 가능하다.

## 5. 왜 크롤과 주기를 분리했는가

**운영 인스턴스가 t4g.large — GPU 없는 버스터블 2vCPU다.**

- 임베딩(BGE-M3)은 GPU가 없으면 CPU로 돌아가 부하가 크다.
- 버스터블 인스턴스는 CPU 크레딧을 소진하면 성능이 baseline으로 강제 제한된다.
- 같은 인스턴스에 **mongo/qdrant(실 운영 데이터)가 같이 떠 있으므로**, 임베딩이 크레딧을
  다 쓰면 DB 응답까지 같이 느려진다.

→ 크롤은 데이터 신선도를 위해 자주(2시간), 임베딩은 부하를 감안해 드물게(하루 1회).
새벽 04:30을 고른 것도 서비스 사용이 적은 시간대이기 때문.

## 6. 실패 복구 — 별도 재시도 로직이 없는 이유

`memes.is_embedded` 플래그가 **그 자체로 재시도 큐 역할**을 한다.

`embedding/pipeline.py:110`
```python
try:
    points = _build_points(chunks)
    client.upsert(collection_name=QDRANT_COLLECTION, points=points)
except Exception as e:
    print(f"[임베딩] 실패 ({doc.get('title')}): {e}")
    failed += 1
    continue          # is_embedded는 False로 남는다

memes.update_one({"_id": doc["_id"]}, {"$set": {"is_embedded": True}})
```

한 문서의 청크가 **전부** 올라간 뒤에만 `True`가 된다. 중간에 실패하면 플래그가 `False`로
남으므로, 다음 날 04:30 실행에서 그 문서가 자동으로 다시 대상에 포함된다.

- 별도 재시도 큐/백오프 코드가 필요 없다.
- 청크 단위 부분 재개는 하지 않는다(문서당 청크 수가 적어 통째로 다시 하는 비용이 낮음).
- 단, **성공 여부와 무관하게 실행이 계속 실패하면 조용히 밀린다** — status.txt를 봐야 안다.

## 7. 캐치업 — 컨테이너가 꺼져 있었을 때

APScheduler는 `misfire_grace_time`(여기선 1800초=30분)을 넘겨 놓친 슬롯은 그냥 버린다.
로컬 PC나 재배포로 컨테이너가 오래 꺼져 있으면 그 사이 슬롯이 통째로 날아간다.

그래서 **기동 직후 "마지막 슬롯을 놓쳤는지" 직접 확인해 즉시 1회 실행**하는 로직을 두 작업
모두에 뒀다 (`scheduler.py:150-155`).

```python
if needs_catchup():
    log_status("캐치업: 마지막 크롤링 슬롯을 놓쳐 즉시 1회 실행")
    threading.Thread(target=crawlrun, daemon=True).start()
if needs_embed_catchup():
    log_status("캐치업: 마지막 전처리+임베딩 슬롯을 놓쳐 즉시 1회 실행")
    threading.Thread(target=preprocess_embed_run, daemon=True).start()
```

판정 기준은 "마지막 성공 시각 < 가장 최근에 지나간 크론 시각":

| 파일 | 기록 시점 | 판정 함수 |
|---|---|---|
| `scheduler/last_crawl_at.txt` | 크롤 성공 시 UTC ISO | `needs_catchup()` |
| `scheduler/last_embed_at.txt` | 전처리+임베딩 성공 시 UTC ISO | `needs_embed_catchup()` |

파일이 없거나 파싱 실패면 `True`(=실행)로 처리한다 — 첫 배포나 파일 손상 시 안전한 쪽으로 기운다.

부수 효과: **재배포 직후 실제 스케줄러 경로가 바로 한 번 돌아가므로, 크론 시각까지 기다리지
않고 동작을 눈으로 검증할 수 있다.**

## 8. 관측 — 어디를 보면 되는가

전부 `scheduler/status.txt` 한 파일에 append된다.

```
2026-07-29 14:52:49 - 스케줄러 정상 시작
2026-07-29 14:52:49 - 캐치업: 마지막 크롤링 슬롯을 놓쳐 즉시 1회 실행
2026-07-29 14:52:49 - 크롤링 시작
2026-07-29 15:22:20 - 크롤링 성공
```

`preprocess_embed_main.py`도 같은 파일에 `[preprocess_embed]` 태그로 단계별 시작/완료를 남긴다:

```
[preprocess_embed] 전처리 시작 (전체 미처리 문서)
[preprocess_embed] 전처리 완료: 청크 N개 생성
[preprocess_embed] 임베딩 시작 (전체 미처리 문서)
[preprocess_embed] 임베딩 완료: 문서 N개 / 청크 N개 / 실패 N개
```

시작/완료 타임스탬프가 다 남으므로 **첫 실행 로그만 보면 CPU 인스턴스에서 실제로 얼마나
걸리는지 측정된다.** 너무 오래 걸리면 그 수치를 근거로 주기나 배치 크기를 조정하면 된다.

> `preprocess_embed_main.py`가 `scheduler.py`를 import하지 않고 `log_status`를 따로 정의한
> 이유: `scheduler.py`는 모듈 최상단에서 `scheduler.start()`가 실행되는 부작용이 있어,
> import하는 순간 스케줄러가 하나 더 뜬다.

## 9. 기대 효과

- 그동안 밀려 있던 미처리 문서가 다음 04:30 실행부터 자동으로 정리되어 RAG/분석에서 검색
  가능해진다. (밀린 건수는 시점마다 달라지므로 실행 전 `memes.count({is_embedded: false})`로
  확인할 것)
- 크롤링이 계속 새 데이터를 만들어도 **최대 하루 지연**으로 자동 반영된다.
- "사람이 깜빡해서 안 돌림" 리스크가 사라진다.

## 10. 알려진 한계 / 확인 필요

1. **04:00 크롤과 04:30 임베딩이 겹칠 수 있다.**
   APScheduler의 `max_instances`는 job 단위라 같은 job의 중복 실행만 막는다. 서로 다른 job인
   `crawl_job`과 `preprocess_embed_job`은 동시 실행이 가능하다. 04:00 크롤이 30분을 넘기면
   2vCPU에서 크롤과 임베딩이 동시에 돈다 — CPU 크레딧 절약이라는 설계 목적과 정면으로 충돌한다.
   실측 로그(위 예시에서 크롤이 약 30분 소요)를 보면 **실제로 발생할 수 있는 범위**다.
   → 대응 후보: 임베딩 시각을 04:30 → 05:10처럼 더 뒤로 밀기, 또는 실행 전 상대 작업이
   도는지 확인하는 락 도입.

2. **status.txt의 시각 표기가 섞여 있다.**
   `log_status()`는 `datetime.now()`(네이티브 로컬 시각), `last_*_at.txt`는 `datetime.utcnow()`
   (UTC)를 쓴다. 실행 환경의 TZ에 따라 로그 시각이 KST/UTC로 뒤섞여 보인다(실제 로그에
   `23:42:48` 다음 줄이 `14:52:49`로 찍힌 사례 있음). 판정 로직 자체는 UTC끼리 비교하므로
   정확하지만, **사람이 로그를 읽을 때 헷갈린다.**

3. **status.txt가 무한히 커진다.** append만 하고 로테이션이 없다. heartbeat가 10분마다
   찍히므로 하루 144줄씩 쌓인다.

4. **크론 시각이 두 곳에 중복 정의돼 있다.**
   `CRAWL_INTERVAL_HOURS = 2` / `PREPROCESS_EMBED_HOUR = 4` 상수와 `CronTrigger(...)` 인자가
   각각 따로 적혀 있고, 코드 주석에 "반드시 같은 값을 유지해야 함"이라고 명시돼 있다.
   한쪽만 고치면 캐치업 판정이 조용히 틀어진다.

5. **로컬에서는 아직 전처리+임베딩 실행 이력이 없다.** 현재 로컬 `status.txt`에는 크롤
   로그만 있다. 실제 검증은 EC2 로그로 해야 한다.

## 11. 검증 방법

```bash
# 컨테이너 재시작 → 캐치업이 걸려 즉시 1회 실행되는지 확인
docker compose restart flask && tail -f scheduler/status.txt
```

```bash
# 미처리 문서가 실제로 줄었는지 (mongo shell)
db.memes.countDocuments({is_embedded: false})
```

성공 시 `scheduler/last_embed_at.txt`에 UTC 타임스탬프가 새로 기록된다.
