"""모델별 조정 가능한 파라미터 오버라이드.

사용자가 채팅 UI(모델 설정 패널)에서 조정한 값을 base ModelConfig 에 적용해
유효 설정 모델을 만든다. 저장은 브라우저 LocalStorage(JSON 문자열)에서 이뤄지고,
여기서는 파싱·클램프·적용만 담당한다. 값은 UI select 특성상 문자열로 저장된다.
"""

from __future__ import annotations

import json
from dataclasses import replace

from wellbot.services.core.settings import ModelConfig

# 조정 가능 파라미터의 UI 프리셋(select 항목). 저장/전달은 문자열.
TEMPERATURE_PRESETS = [
    "0.0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "1.0",
]
TOP_P_PRESETS = ["0.5", "0.6", "0.7", "0.8", "0.9", "1.0"]
MAX_TOKENS_PRESETS = ["8192", "16384", "32768", "65536"]
THINKING_BUDGET_PRESETS = ["2048", "4096", "8192", "16384"]
EFFORT_PRESETS = ["low", "medium", "high", "xhigh"]
# effort 슬라이더 눈금 라벨 (0~3 인덱스 = EFFORT_PRESETS 순서)
EFFORT_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high"}

# 현재 Claude 모델(최소 Sonnet 4.5 = 64k output)에 안전한 상한.
_MAX_TOKENS_CEILING = 65536
_THINKING_BUDGET_MIN = 1024


def _clampf(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def nearest_index(presets: list[str], value) -> int:
    """value 에 가장 가까운 preset 의 인덱스 (프리셋 슬라이더 위치 산출용, 안전 폴백)."""
    try:
        target = float(value)
    except (ValueError, TypeError):
        return 0
    best, best_diff = 0, None
    for i, p in enumerate(presets):
        try:
            diff = abs(float(p) - target)
        except (ValueError, TypeError):
            continue
        if best_diff is None or diff < best_diff:
            best, best_diff = i, diff
    return best


def parse_overrides(raw: str) -> dict:
    """LocalStorage JSON 문자열 → {model_name: {param: value}} (안전 파싱)."""
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def apply_overrides(model: ModelConfig, ov: dict | None) -> ModelConfig:
    """base 모델에 사용자 오버라이드를 클램프해 적용한 새 ModelConfig 반환.

    지원하지 않는/범위 밖 값은 무시(base 유지). 클램프로 과도한 비용·오류 방지.
    """
    if not ov:
        return model

    changes: dict = {}
    if "temperature" in ov:
        try:
            changes["temperature"] = _clampf(float(ov["temperature"]), 0.0, 1.0)
        except (ValueError, TypeError):
            pass
    if "top_p" in ov:
        try:
            changes["top_p"] = _clampf(float(ov["top_p"]), 0.0, 1.0)
        except (ValueError, TypeError):
            pass
    if "max_tokens" in ov:
        try:
            changes["max_tokens"] = int(
                _clampf(float(ov["max_tokens"]), 1024, _MAX_TOKENS_CEILING)
            )
        except (ValueError, TypeError):
            pass
    if ov.get("effort") in EFFORT_PRESETS:
        changes["effort"] = ov["effort"]
    if "thinking_budget" in ov:
        try:
            mt = int(changes.get("max_tokens", model.max_tokens))
            hi = max(_THINKING_BUDGET_MIN, mt - 1)
            changes["thinking_budget"] = int(
                _clampf(float(ov["thinking_budget"]), _THINKING_BUDGET_MIN, hi)
            )
        except (ValueError, TypeError):
            pass

    return replace(model, **changes) if changes else model
