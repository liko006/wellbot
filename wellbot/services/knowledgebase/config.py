"""KB(Knowledge Base) 설정 로드.

config/knowBase.yaml 의 KB 동작 옵션과 .env 의 KB 인프라 변수를 병합.
앱 전역 설정(models/prompts/greetings 등)은 wellbot.services.core.settings 담당.
"""

from __future__ import annotations

import logging
import os

import yaml

from wellbot.paths import KNOWBASE_YAML
from wellbot.services.knowledgebase import kb_registry

log = logging.getLogger(__name__)

_kb_config: dict | None = None


# .env 로 옮겨진 KB 인프라 키 (yaml 의 personal_kb / shared_kb 양 섹션에 동일하게 주입)
# s3_bucket 은 채팅 첨부파일 버킷(S3_BUCKET_NAME)과 동일 자원이라 같은 env var 를 공유
_KB_INFRA_ENV_KEYS = {
    "s3_bucket":              "S3_BUCKET_NAME",
    "s3_intermediate_bucket": "KB_S3_INTERMEDIATE_BUCKET",
    "s3_vector_bucket":       "KB_S3_VECTOR_BUCKET",
    "lambda_arn":             "KB_LAMBDA_ARN",
    "kb_role_arn":            "KB_ROLE_ARN",
}


def reload_kb_config() -> dict:
    """설정을 파일에서 다시 읽는다. 반환: 새 설정 dict.

    **앱 밖에서 yaml 이 바뀐 경우**를 위한 것이다 — CLI(`scripts/shared_kb_manager.py`,
    `cleanup_kb.py`)는 별도 프로세스라 이 캐시를 갱신할 수 없다. 폴더 이름을 CLI 로
    바꾼 뒤 admin 화면이 옛 이름을 계속 보여주면, 관리자가 사라진 경로에 티어를 써서
    유령 항목을 만든다(실제 발생).

    앱 안에서의 쓰기(`shared_kb_docs`, `shared_kb_service`)는 파일과 캐시를 함께
    갱신하므로 이 함수를 부를 필요가 없다. 다시 읽어도 결과는 같다(멱등).

    `kb_retriever._shared_kb_id` 의 lru_cache 도 함께 비운다 — 그쪽은 이 설정에서
    kb_id 를 캐싱하므로, 설정만 새로 읽으면 둘이 어긋난 채 남는다.
    """
    global _kb_config
    _kb_config = None
    config = get_kb_config()

    from wellbot.services.knowledgebase import kb_retriever

    kb_retriever._shared_kb_id.cache_clear()
    return config


def get_kb_config() -> dict:
    """knowBase.yaml + .env 의 KB_* 변수를 병합해 KB 설정을 반환 (캐싱).

    인프라 자원(S3 버킷, Lambda ARN 등)은 .env 의 KB_* 변수에서 주입되어
    personal_kb / shared_kb 양 섹션에 동일한 값으로 채워짐.
    """
    global _kb_config
    if _kb_config is None:
        with open(KNOWBASE_YAML, encoding="utf-8") as f:
            _kb_config = yaml.safe_load(f) or {}

        # env 값이 실제로 설정된 경우에만 override 적용. 미설정/빈 문자열이면
        # yaml 값을 보존 (예전에는 빈 문자열로 무조건 덮어써 ARN/버킷이 공란이
        # 되는 footgun 존재).
        env_overrides = {}
        for cfg_key, env_key in _KB_INFRA_ENV_KEYS.items():
            value = os.getenv(env_key)
            if value:
                env_overrides[cfg_key] = value
        for section in ("personal_kb", "shared_kb"):
            _kb_config.setdefault(section, {}).update(env_overrides)

        # 공용 KB id 는 shared_kb 에만 적용. KB_ID 가 설정된 경우에만 yaml 값 대체
        shared_kb_id = os.getenv("KB_ID")
        if shared_kb_id:
            _kb_config.setdefault("shared_kb", {})["kb_id"] = shared_kb_id

        _apply_registry(_kb_config)
    return _kb_config


def _apply_registry(config: dict) -> None:
    """런타임 레지스트리(kb_registry.yaml)를 씨앗 위에 덮어쓴다.

    폴더 등록·문서 속성은 앱이 런타임에 고치는 값이라 git 이 추적하지 않는 별도 파일에
    쓴다(이유는 `kb_registry` 모듈 docstring). 그 파일이 있으면 해당 섹션의 값을
    **통째로 대체**한다 — 레지스트리가 생긴 뒤부터는 그쪽이 유일한 진실이라, 씨앗과
    합치면 지운 항목이 되살아난다.

    파일이 없으면 씨앗(knowBase.yaml)이 그대로 쓰인다 — 레지스트리 도입 전에 배포된
    서버도 그대로 동작한다.
    """
    registry = kb_registry.load()
    if not registry:
        log.info("[Config] 런타임 레지스트리 없음 — knowBase.yaml 씨앗 사용")
        return
    for section, values in registry.items():
        if isinstance(values, dict):
            config.setdefault(section, {}).update(values)
