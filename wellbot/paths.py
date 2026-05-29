"""프로젝트 경로 상수.

`Path(__file__).resolve().parent.parent.parent / "config" / ...` 패턴이
여러 모듈에서 반복 등장하여, 깊이에 따라 `.parent` 개수가 달라지는
실수가 발생하기 쉬웠다. 본 모듈은 단일 진실 공급원으로 동작한다.

이 파일이 `wellbot/paths.py` 에 있으므로:
- `Path(__file__).resolve().parent`        → `<root>/wellbot/`
- `Path(__file__).resolve().parent.parent` → `<root>/` (PROJECT_ROOT)
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
PACKAGE_ROOT: Path = PROJECT_ROOT / "wellbot"

CONFIG_DIR: Path = PROJECT_ROOT / "config"
PROMPTS_DIR: Path = CONFIG_DIR / "prompts"

ENV_FILE: Path = PROJECT_ROOT / ".env"

# 자주 사용하는 YAML/MD 파일
MODELS_YAML: Path = CONFIG_DIR / "models.yaml"
PROMPTS_YAML: Path = CONFIG_DIR / "prompts.yaml"
AGENTS_YAML: Path = CONFIG_DIR / "agents.yaml"
GREETINGS_YAML: Path = CONFIG_DIR / "greetings.yaml"
NOTICE_MD: Path = CONFIG_DIR / "notice.md"
