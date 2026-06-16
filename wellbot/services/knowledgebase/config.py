"""KB(Knowledge Base) 설정 로드.

config/knowBase.yaml 의 KB 동작 옵션과 .env 의 KB 인프라 변수를 병합.
앱 전역 설정(models/prompts/greetings 등)은 wellbot.services.core.settings 담당.
"""

from __future__ import annotations

import os

import yaml

from wellbot.paths import KNOWBASE_YAML

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
    return _kb_config
