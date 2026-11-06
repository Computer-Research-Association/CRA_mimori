"""스케줄러를 시작하지 않고 실제 갱신 함수의 후속 캐시 작업을 검증한다."""
import ast
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


def scheduler_functions(tmp_path, embed_code=0, cache_counts=None, cache_code=0):
    # 모듈 최상위에서 daemon이 시작되므로 테스트할 함수만 원문 그대로 로드한다.
    source = Path('scheduler/scheduler.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'preprocess_embed_run', 'warm_analysis_cache_run'}]
    report = tmp_path / 'report.json'
    calls, logs = [], []
    reports = iter(cache_counts or [{'done': 54}])
    def run(command):
        calls.append(command)
        if command[1] == 'embed.py':
            return SimpleNamespace(returncode=embed_code)
        if cache_code == 0:
            report.write_text(json.dumps({'counts': next(reports)}))
        return SimpleNamespace(returncode=cache_code)
    namespace = {
        'sys': SimpleNamespace(executable='python'),
        'subprocess': SimpleNamespace(run=run),
        'datetime': datetime, 'json': json,
        'log_status': logs.append, 'logger': SimpleNamespace(error=lambda *args: None),
        'PREPROCESS_EMBED_PY': 'embed.py', 'WARM_ANALYSIS_CACHE_PY': 'warm.py',
        'CACHE_REPORT_PATH': str(report), 'LAST_EMBED_PATH': str(tmp_path / 'embedded_at'),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'scheduler/scheduler.py', 'exec'), namespace)
    return namespace, calls, logs


def test_임베딩성공후_자동으로_캐시를_다시_채운다(tmp_path):
    module, calls, logs = scheduler_functions(tmp_path)
    module['preprocess_embed_run']()
    assert [command[1] for command in calls] == ['embed.py', 'warm.py']
    assert Path(module['LAST_EMBED_PATH']).exists()
    assert any('분석 캐시 사전 생성 완료' in log for log in logs)


def test_임베딩실패면_캐시생성을_시작하지_않는다(tmp_path):
    module, calls, logs = scheduler_functions(tmp_path, embed_code=1)
    module['preprocess_embed_run']()
    assert len(calls) == 1
    assert not Path(module['LAST_EMBED_PATH']).exists()


def test_일부캐시실패시_한번만_직렬로_재시도한다(tmp_path):
    module, calls, logs = scheduler_functions(tmp_path, cache_counts=[{'done': 53, 'failed': 1}, {'done': 54}])
    module['preprocess_embed_run']()
    assert len(calls) == 3
    assert calls[-1][-2:] == ['--concurrency', '1']
    assert any('분석 캐시 사전 생성 완료' in log for log in logs)


def test_캐시생성실패가_임베딩성공기록을_되돌리지_않는다(tmp_path):
    module, calls, logs = scheduler_functions(tmp_path, cache_code=1)
    module['preprocess_embed_run']()
    assert Path(module['LAST_EMBED_PATH']).exists()
    assert any('사전 생성 실행 실패' in log for log in logs)


def test_재시도후에도_실패가남으면_완료로_보고하지_않는다(tmp_path):
    module, calls, logs = scheduler_functions(tmp_path, cache_counts=[{'done': 53, 'failed': 1}] * 2)
    module['preprocess_embed_run']()
    assert len(calls) == 3
    assert not any('사전 생성 완료' in log for log in logs)
    assert any('미완료 항목' in log for log in logs)
