"""경량 모델 기반 대화 제목 생성."""

from __future__ import annotations

import logging

from wellbot.services.ai.bedrock.client import get_client
from wellbot.services.core.settings import get_config

log = logging.getLogger(__name__)


def generate_title(user_msg: str, assistant_msg: str) -> str:
    """경량 모델로 대화 제목 생성. 생성 실패 시 빈 문자열 반환"""
    cfg = get_config().title
    client = get_client()
    messages = [
        {
            "role": "user",
            "content": [{"text": f"질문: {user_msg}\n\n답변: {assistant_msg}"}],
        },
    ]
    try:
        response = client.converse(
            modelId=cfg.model_id,
            messages=messages,
            system=[{"text": cfg.system_prompt}],
            inferenceConfig={"maxTokens": cfg.max_tokens, "temperature": cfg.temperature},
        )
        output = response.get("output", {})
        content = output.get("message", {}).get("content", [])
        if content and "text" in content[0]:
            return content[0]["text"].strip().strip('"').strip("'")
    except Exception:
        log.warning("대화 제목 생성 실패", exc_info=True)
    return ""
