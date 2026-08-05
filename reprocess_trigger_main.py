"""
reprocess_trigger_main.py
memes 문서의 is_embedded를 다시 False로 되돌려 재처리 대상에 포함시킨다.

이 스크립트 자체는 재처리를 실행하지 않는다 — 표시만 바꾸고, 실제 정제/청킹/
judge/임베딩은 다음 preprocess_embed_job(스케줄러) 또는 preprocess_embed_main.py
수동 실행에서 돈다.

keyword/source 중 최소 하나는 반드시 지정해야 한다(전체 재처리 사고 방지).

사용법:
    python reprocess_trigger_main.py --keyword 야르
    python reprocess_trigger_main.py --source tavily
    python reprocess_trigger_main.py --keyword 야르 --source tavily
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from preprocessing.pipeline import reset_for_reprocessing

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="문서를 재처리 대상으로 되돌리기 (is_embedded=False)")
    parser.add_argument("--keyword", default=None, help="이 키워드의 문서만 대상")
    parser.add_argument("--source", default=None, help="이 소스의 문서만 대상 (예: tavily)")
    args = parser.parse_args()

    try:
        count = reset_for_reprocessing(keyword=args.keyword, source=args.source)
    except ValueError as e:
        print(f"오류: {e}")
        sys.exit(1)

    print(f"[재처리 트리거] {count}개 문서를 재처리 대상으로 되돌렸습니다.")
    print("다음 preprocess_embed_job 실행(또는 preprocess_embed_main.py 수동 실행)에서 처리됩니다.")
