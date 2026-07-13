import sys

# Windows 콘솔은 시스템 로케일(한국어 Windows -> cp949)로 표준입출력을 여는데,
# 크롤러가 다루는 한글/한자 콘텐츠 중 cp949 범위를 벗어나는 문자가 있으면
# 콘솔 출력이 깨지거나 UnicodeEncodeError로 죽는다. chcp/PYTHONUTF8 같은
# 사용자별 로컬 설정에 의존하지 않도록, crawlers 패키지가 임포트되는 시점에
# 표준입출력을 utf-8로 고정한다. (main.py, 각 크롤러 단독 실행 모두 적용됨)
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
