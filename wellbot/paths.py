"""프로젝트 경로 상수.

경로 계산 패턴이 여러 모듈에서 반복되면 .parent 개수가 달라지는 실수가 잦으므로
단일 진실 공급원으로 집중.

wellbot/paths.py 기준 경로:
- Path(__file__).resolve().parent        → <root>/wellbot/
- Path(__file__).resolve().parent.parent → <root>/ (PROJECT_ROOT)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
PACKAGE_ROOT: Path = PROJECT_ROOT / "wellbot"

CONFIG_DIR: Path = PROJECT_ROOT / "config"
PROMPTS_DIR: Path = CONFIG_DIR / "prompts"

ENV_FILE: Path = PROJECT_ROOT / ".env"

# LOG_TO_FILE=true 시 사용
LOG_DIR: Path = PROJECT_ROOT / "logs"

MODELS_YAML: Path = CONFIG_DIR / "models.yaml"
PROMPTS_YAML: Path = CONFIG_DIR / "prompts.yaml"
AI_SERVICES_YAML: Path = CONFIG_DIR / "ai_services.yaml"
GREETINGS_YAML: Path = CONFIG_DIR / "greetings.yaml"
NOTICE_MD: Path = CONFIG_DIR / "notice.md"
KNOWBASE_YAML: Path = CONFIG_DIR / "knowBase.yaml"


def wellbot_temp_dir(name: str) -> Path:
    """현재 실행 유저가 쓰기 가능한 wellbot 임시 하위 디렉터리를 보장 반환.

    /tmp 의 고정 이름 디렉터리(예: /tmp/wellbot_upload)는 다른 유저(예: root)가
    먼저 만들면 이후 다른 유저의 파일 생성이 EACCES(Errno 13) 로 막힌다.
    유저별 접미사를 붙여 유저마다 독립된 디렉터리를 쓰게 함으로써 충돌을 피한다.
    """
    try:
        suffix = str(os.getuid())  # POSIX: 안정적인 유저 식별자
    except AttributeError:
        suffix = os.environ.get("USERNAME", "user")  # Windows 폴백
    tmp_dir = Path(tempfile.gettempdir()) / f"{name}_{suffix}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir
