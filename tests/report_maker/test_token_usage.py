"""report_maker 스트리밍 토큰 사용량 집계 — stream_model 의 metadata usage 캡처.

Converse 스트림 말미의 metadata 이벤트에서 input/output 토큰을 usage_out 에 채워야
_persist_turn 이 chtb_*_tokn_ecnt 컬럼에 기록할 수 있다. 실제 Bedrock 없이 가짜
클라이언트로 스트림 이벤트를 주입해 검증한다.
"""

from types import SimpleNamespace

from wellbot.services.report_maker import bedrock


class _Throttle(Exception):
    pass


class _FakeExceptions:
    ThrottlingException = _Throttle


class _FakeClient:
    exceptions = _FakeExceptions()

    def __init__(self, events):
        self._events = events

    def converse_stream(self, **kwargs):
        return {"stream": self._events}


def _patch(monkeypatch, events):
    cfg = SimpleNamespace(
        region="us-east-1",
        read_timeout_sec=60,
        model_id="anthropic.claude-test",
        max_retries=2,
        retry_base_delay_sec=0.01,
    )
    monkeypatch.setattr(bedrock, "get_config", lambda: cfg)
    monkeypatch.setattr(bedrock, "_client", lambda region, timeout: _FakeClient(events))


def test_stream_model_captures_usage(monkeypatch):
    events = [
        {"contentBlockDelta": {"delta": {"text": "안녕"}}},
        {"contentBlockDelta": {"delta": {"text": "하세요"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 123, "outputTokens": 45, "totalTokens": 168}}},
    ]
    _patch(monkeypatch, events)

    usage: dict = {}
    out = list(bedrock.stream_model("prompt", 100, usage_out=usage))

    assert "".join(out) == "안녕하세요"
    assert usage == {"input_tokens": 123, "output_tokens": 45}


def test_stream_model_no_metadata_leaves_usage_empty(monkeypatch):
    # 스트림이 metadata 없이 끝나면(중단 등) usage 는 비어 있어야 한다 → 호출측이 0 처리.
    events = [
        {"contentBlockDelta": {"delta": {"text": "부분"}}},
    ]
    _patch(monkeypatch, events)

    usage: dict = {}
    out = list(bedrock.stream_model("prompt", 100, usage_out=usage))

    assert "".join(out) == "부분"
    assert usage == {}
    assert usage.get("input_tokens", 0) == 0


def test_stream_model_without_usage_out_does_not_crash(monkeypatch):
    events = [
        {"contentBlockDelta": {"delta": {"text": "x"}}},
        {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 2}}},
    ]
    _patch(monkeypatch, events)

    out = list(bedrock.stream_model("prompt", 100))
    assert "".join(out) == "x"
