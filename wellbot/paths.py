"""프로젝트 경로 상수.

경로 계산 패턴이 여러 모듈에서 반복되면 .parent 개수가 달라지는 실수가 잦으므로
단일 진실 공급원으로 집중.

wellbot/paths.py 기준 경로:
- Path(__file__).resolve().parent        → <root>/wellbot/
- Path(__file__).resolve().parent.parent → <root>/ (PROJECT_ROOT)
"""

from __future__ import annotations

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
CHAT_MODES_YAML: Path = CONFIG_DIR / "chat_modes.yaml"
GREETINGS_YAML: Path = CONFIG_DIR / "greetings.yaml"
NOTICE_MD: Path = CONFIG_DIR / "notice.md"
