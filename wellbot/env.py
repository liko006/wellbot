"""환경변수 초기화.

`.env` 로딩을 모듈 import 사이드이펙트가 아닌 명시적 호출로 분리한다.
엔트리포인트(`wellbot/wellbot.py`) 에서 다른 import 전에 1회 호출해야 한다.

테스트·CLI 스크립트도 필요한 시점에 동일하게 호출하면 된다.
"""

from __future__ import annotations

from dotenv import load_dotenv

from wellbot.paths import ENV_FILE

_loaded: bool = False


def init_env(*, override: bool = False) -> None:
    """프로젝트 루트의 `.env` 파일을 로드한다.

    재호출은 무시한다 (override=True 명시 시 강제 갱신).
    """
    global _loaded
    if _loaded and not override:
        return
    load_dotenv(ENV_FILE, override=override)
    _loaded = True
