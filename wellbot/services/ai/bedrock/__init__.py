"""AWS Bedrock ConverseStream 클라이언트 패키지.

기존 bedrock_client.py 의 책임을 다음 서브모듈로 분리:
- client.py     : boto3 클라이언트 싱글턴
- converse.py   : 단일 턴 호출 + 동기/비동기 스트리밍
- tool_loop.py  : tool-use 루프 + 빈결과/중복 가드 + 폴백
- title.py      : 경량 모델 기반 제목 생성
- image.py      : 이미지 포맷 판별
"""

from wellbot.services.ai.bedrock.converse import astream_chat, stream_chat
from wellbot.services.ai.bedrock.image import fit_image_for_bedrock, image_format
from wellbot.services.ai.bedrock.title import generate_title
from wellbot.services.ai.bedrock.tool_loop import (
    astream_chat_with_tools,
    read_budget_for,
)

# build_messages, stream_one_turn, safe_next, stream_one_turn_iter, get_client 는
# 패키지 내부 헬퍼이므로 공개 API 에 포함하지 않음.
# 직접 호출이 필요하면 해당 모듈에서 명시적으로 import 할 것.
__all__ = [
    "astream_chat",
    "astream_chat_with_tools",
    "fit_image_for_bedrock",
    "generate_title",
    "image_format",
    "read_budget_for",
    "stream_chat",
]
