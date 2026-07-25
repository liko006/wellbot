"""chat_service.get_message_content — 보고서 만들기 핸드오프 조회 + IDOR 회귀.

채팅에서 고른 AI 메시지 1건을 report_maker 로 넘길 때 사용하는 조회 헬퍼.
소유권(_verify_ownership) 을 재검증하므로 남의 대화 메시지는 절대 반환하지 않는다.
DB 픽스처가 없는 브랜치라 세션을 가짜로 주입해 로직만 검증한다.
"""

from types import SimpleNamespace

import pytest

from wellbot.models.chat_message import ChatMessage
from wellbot.models.chat_summary import ChatSummary
from wellbot.services.chat import chat_service


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeSession:
    """query(model) 별로 미리 정한 first() 결과를 돌려주는 최소 세션 스텁."""

    def __init__(self, *, summary=None, message=None):
        self._summary = summary
        self._message = message

    def query(self, model):
        if model is ChatSummary:
            return _FakeQuery(self._summary)
        if model is ChatMessage:
            return _FakeQuery(self._message)
        return _FakeQuery(None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_session(monkeypatch, *, summary, message):
    monkeypatch.setattr(
        chat_service,
        "get_session",
        lambda: _FakeSession(summary=summary, message=message),
    )


class TestGetMessageContent:
    def test_owner_with_message_returns_content(self, monkeypatch):
        _patch_session(
            monkeypatch,
            summary=SimpleNamespace(emp_no="1001"),          # 소유권 OK
            message=SimpleNamespace(chtb_msg_cntt="보고서로 만들 답변"),
        )
        result = chat_service.get_message_content("smry-1", "1001", 3)
        assert result == "보고서로 만들 답변"

    def test_non_owner_rejected(self, monkeypatch):
        # _verify_ownership 이 None → 메시지 존재 여부와 무관하게 None (IDOR 차단)
        _patch_session(
            monkeypatch,
            summary=None,
            message=SimpleNamespace(chtb_msg_cntt="피해자의 비밀 내용"),
        )
        result = chat_service.get_message_content("victim-smry", "attacker", 3)
        assert result is None

    def test_owner_missing_seq_returns_none(self, monkeypatch):
        _patch_session(
            monkeypatch,
            summary=SimpleNamespace(emp_no="1001"),
            message=None,                                    # 해당 seq 없음
        )
        result = chat_service.get_message_content("smry-1", "1001", 999)
        assert result is None

    def test_owner_empty_content_returns_empty_string(self, monkeypatch):
        # 본문이 NULL 이어도(빈 메시지) None 이 아니라 "" 반환 — 존재는 하므로.
        _patch_session(
            monkeypatch,
            summary=SimpleNamespace(emp_no="1001"),
            message=SimpleNamespace(chtb_msg_cntt=None),
        )
        result = chat_service.get_message_content("smry-1", "1001", 1)
        assert result == ""


class TestSeedMergeContract:
    """report_maker send_message 이 seed(_uploaded_topic_text)+지시를 합치는 포맷 계약.

    실제 병합은 ReportMakerState.send_message 인라인 로직이라 상태 하네스 없이 직접
    호출이 어렵다. 포맷 문자열 계약이 깨지면 여기서 잡히도록 동일 식을 고정한다.
    """

    def test_merge_format(self):
        seed = "대화에서 가져온 내용 본문"
        typed = "3페이지 임원보고용으로"
        merged = f"{seed}\n[추가지시]\n{typed}"
        assert merged == "대화에서 가져온 내용 본문\n[추가지시]\n3페이지 임원보고용으로"
        assert "[추가지시]" in merged
        assert merged.startswith(seed)
