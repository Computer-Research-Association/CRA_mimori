"""
dashboard_main.py
수집된 밈 데이터(키워드/통계/LLM 분석)를 dashboard/public/data.json 스냅샷으로
저장한다. 그 폴더의 index.html이 이 파일을 읽어 화면에 그린다.

main.py / preprocess_main.py / embed_main.py / analyze_main.py와 동일하게
루트에서 바로 실행 가능:
    python dashboard_main.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dashboard.export import build_snapshot, write_snapshot

if __name__ == "__main__":
    print("[수집 중] Mongo 통계 + LLM 분석 스냅샷 생성...")
    snapshot = build_snapshot()
    write_snapshot(snapshot)

    summary = snapshot["summary"]
    print(
        f"[완료] 키워드 {summary['total_keywords']}개, "
        f"분석 완료 {summary['analyzed_keywords']}개 -> dashboard/public/data.json"
    )
    print()
    print("미리보기 방법 (새 터미널에서):")
    print("  cd dashboard/public")
    print("  python -m http.server")
    print("  -> 브라우저로 http://localhost:8000 접속")
