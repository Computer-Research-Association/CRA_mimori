"""
quality_test_main.py
파이프라인 품질 계측 CLI.

  dump     실제 문서를 로컬 JSONL fixture로 뜬다 (Mongo 읽기, 1회)
  run      fixture로 파이프라인을 돌려 run 디렉터리를 만든다 (파일만)
  report   run을 집계해 표로 보여준다 / 두 run을 비교한다
  inspect  run에서 나쁜 청크 실물을 보여준다

dump 외에는 외부 연결이 전혀 없다.
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def cmd_dump(args):
    from quality_test.fixture import dump_fixture

    path, count = dump_fixture(limit=args.limit, keyword=args.keyword)
    print(f"[dump] {count}개 문서 → {path}")
    if count == 0:
        print("[dump] 경고: 0건이다. Mongo 연결이나 --keyword 값을 확인할 것.")


def cmd_run(args):
    from quality_test.runner import run

    out_dir = run(run_name=args.name, fixture_path=args.fixture)
    print(f"[run] 완료 → {out_dir}")


def cmd_report(args):
    from quality_test.report import print_report

    print_report(args.run, args.compare_to)


def cmd_inspect(args):
    from quality_test.inspect import print_worst

    print_worst(run_name=args.run, keyword=args.keyword, n=args.worst)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quality_test_main.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_dump = sub.add_parser("dump", help="실제 문서를 JSONL fixture로 뜬다")
    p_dump.add_argument("--limit", type=int, default=1000, help="최대 문서 수 (기본 1000)")
    p_dump.add_argument("--keyword", default=None, help="이 키워드 문서만 (기본: 전체)")
    p_dump.set_defaults(func=cmd_dump)

    p_run = sub.add_parser("run", help="fixture로 파이프라인을 돌려 run을 만든다")
    p_run.add_argument("--name", required=True, help="run 이름 (예: before, after)")
    p_run.add_argument("--fixture", default=None, help="fixture 경로 (기본: 기본 fixture)")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="run을 집계하거나 두 run을 비교한다")
    p_report.add_argument("run", help="run 이름")
    p_report.add_argument("compare_to", nargs="?", default=None, help="비교할 run (선택)")
    p_report.set_defaults(func=cmd_report)

    p_inspect = sub.add_parser("inspect", help="나쁜 청크 실물을 본다")
    p_inspect.add_argument("run", help="run 이름")
    p_inspect.add_argument("--keyword", default=None, help="이 키워드만")
    p_inspect.add_argument("--worst", type=int, default=10, help="상위 N개 (기본 10)")
    p_inspect.set_defaults(func=cmd_inspect)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
