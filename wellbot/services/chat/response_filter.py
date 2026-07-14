"""응답 텍스트 후처리 필터.

LLM 이 사고 과정(chain-of-thought)을 일반 텍스트로 출력하는 경우를 감지해 제거.
확장 사고(extended thinking)를 지원하지 않는 모델(Nova 계열)에서 발생.

제거 패턴:
  1. <thinking>...</thinking> 등 사고 명시 태그
  2. 응답 시작부의 영어 사고 과정 (한국어 답변 앞)
"""

from __future__ import annotations

import re

# ── XML 태그 기반 사고 과정 패턴 ──
_THINKING_TAG_PATTERN = re.compile(
    r"<(?:thinking|chain_of_thought|reasoning|thought|inner_monologue)>"
    r".*?"
    r"</(?:thinking|chain_of_thought|reasoning|thought|inner_monologue)>",
    re.DOTALL | re.IGNORECASE,
)

# ── 응답 시작부 영어 사고 과정 문장 패턴 ──
# "The user wants...", "I should...", "I need to...", "Let me..." 등 시작 문장만 대상
_THINKING_SENTENCE_STARTERS = re.compile(
    r"^("
    r"(?:The user (?:wants|is asking|has requested|would like|seems to).*?[.!]\s*)+"
    r"|(?:I (?:should|need to|will|must|can|cannot|don't have).*?[.!]\s*)+"
    r"|(?:Let me (?:check|think|look|analyze|search|find).*?[.!]\s*)+"
    r"|(?:This (?:is|seems|appears|looks like|means).*?[.!]\s*)+"
    r")+",
    re.MULTILINE,
)


def strip_thinking(text: str) -> str:
    """응답 텍스트에서 사고 과정 제거.

    Args:
        text: LLM 응답 원문

    Returns:
        사고 과정이 제거된 텍스트. 제거할 것이 없으면 원문 그대로
    """
    if not text:
        return text

    result = text

    result = _THINKING_TAG_PATTERN.sub("", result)

    stripped = result.strip()
    match = _THINKING_SENTENCE_STARTERS.match(stripped)
    if match:
        remaining = stripped[match.end():].strip()
        # 전체가 영어 답변일 수 있으므로, 제거 후 실질적인 내용이 남아있을 때만 적용
        if remaining and len(remaining) > 20:
            result = remaining

    return result.strip()


# ── Bedrock 스트리밍 예외 → 사용자 안내 메시지 ──

# 입력이 모델 컨텍스트 한도를 넘겼을 때 Bedrock 이 돌려주는 메시지 조각(소문자 비교).
# 모델/리전마다 문구가 조금씩 달라 여러 변형을 포괄한다.
_INPUT_OVERFLOW_MARKERS = (
    "too many input tokens",
    "input is too long",
    "too long for requested model",
    "exceed context limit",
    "maximum context length",
    "input length and `max_tokens`",
)

_INPUT_OVERFLOW_MESSAGE = (
    "첨부하신 내용이 한 번에 처리할 수 있는 크기를 넘어섰어요. "
    "문서 전체 대신 필요한 부분만 콕 집어 질문하시거나, "
    "파일을 더 작게 나눠 첨부한 뒤 다시 시도해 주세요."
)

_THROTTLE_MESSAGE = (
    "지금 요청이 많아 잠시 처리가 지연되고 있어요. "
    "잠깐 기다렸다가 다시 시도해 주세요."
)


def classify_stream_error(exc: BaseException) -> str | None:
    """Bedrock 스트리밍 예외를 사용자 안내 메시지로 분류.

    입력 토큰 초과·쓰로틀링 등 원인이 분명한 오류는 대처법이 담긴 안내 문구를,
    분류 불가한 오류는 None 을 반환한다(호출자가 기본 문구 사용).
    """
    blob = str(exc).lower()
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", "")).lower()

    if any(m in blob for m in _INPUT_OVERFLOW_MARKERS):
        return _INPUT_OVERFLOW_MESSAGE
    # 일부 모델은 컨텍스트 초과도 일반 ValidationException 으로 던지므로,
    # 토큰 관련 문구가 함께 있으면 초과로 간주한다.
    if "validationexception" in code and "token" in blob and (
        "exceed" in blob or "too" in blob or "long" in blob
    ):
        return _INPUT_OVERFLOW_MESSAGE

    if code in ("throttlingexception", "toomanyrequestsexception") or (
        "throttl" in blob or "too many requests" in blob
    ):
        return _THROTTLE_MESSAGE

    return None
