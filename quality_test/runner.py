"""
runner.py
fixture로 파이프라인을 돌려 run 디렉터리를 만든다.

Mongo도 Qdrant도 건드리지 않는다 — 파일을 읽어 파일을 쓴다.
정제/청킹은 preprocessing.pipeline.process_one()을 그대로 호출하므로,
측정한 것과 프로덕션이 도는 코드가 항상 같다.

산출물:
  <RUNS_DIR>/<run_name>/cleaned.jsonl   문서 단위 정제 결과
  <RUNS_DIR>/<run_name>/chunks.jsonl    청크 + 품질 신호
  <RUNS_DIR>/<run_name>/funnel.json     단계별 카운터
"""

import json
import os

from config.config_cilent import RUNS_DIR
from preprocessing.pipeline import process_one
from quality_test.fixture import load_fixture
from quality_test.funnel import Funnel
from quality_test.signals import compute_signals


def run(run_name: str, fixture_path: str | None = None) -> str:
    """fixture를 처리해 run 디렉터리를 만들고 그 경로를 반환."""
    out_dir = os.path.join(RUNS_DIR, run_name)
    os.makedirs(out_dir, exist_ok=True)

    fixture_label = os.path.basename(fixture_path) if fixture_path else "raw_sample.jsonl"
    funnel = Funnel(run_name, fixture_label)

    cleaned_path = os.path.join(out_dir, "cleaned.jsonl")
    chunks_path = os.path.join(out_dir, "chunks.jsonl")

    with open(cleaned_path, "w", encoding="utf-8") as cf, \
         open(chunks_path, "w", encoding="utf-8") as chf:

        for doc in load_fixture(fixture_path):
            funnel.inc_in("clean")

            clean_content, chunks = process_one(doc)

            if not clean_content.strip():
                funnel.drop("clean", "empty_content")
                continue
            funnel.inc_out("clean")

            cf.write(json.dumps({
                "_id": doc.get("_id"),
                "keyword": doc.get("keyword"),
                "source": doc.get("source"),
                "url": doc.get("url"),
                "title": doc.get("title"),
                "clean_content": clean_content,
                "chunk_count": len(chunks),
            }, ensure_ascii=False, default=str) + "\n")

            funnel.inc_in("chunk")
            for chunk in chunks:
                chunk["signals"] = compute_signals(
                    text=chunk.get("text", ""),
                    title=chunk.get("title") or "",
                    keyword=chunk.get("keyword") or "",
                )
                chf.write(json.dumps(chunk, ensure_ascii=False, default=str) + "\n")
                funnel.inc_out("chunk")

    funnel.dump(os.path.join(out_dir, "funnel.json"))
    return out_dir
