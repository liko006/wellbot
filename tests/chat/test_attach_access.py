"""can_attach — 첨부 업로드 대화 소유권 게이트 (IDOR 회귀).

첨부 조회(get_conversation_attachments)는 emp_no 가 아니라 대화 ID 기준이므로, 업로드
시점에 대화 소유권을 확인하지 않으면 남의 대화 ID 를 넣어 첨부를 끼워 넣을 수 있다
(피해자 세션의 LLM 컨텍스트로 공격자 파일이 유입).

동시에 report_maker/메인 챗 모두 '첫 메시지 저장 전' 업로드를 허용해야 하므로,
"행 없음 + 메시지 없음 = 새 세션" 은 통과해야 한다.

DB 픽스처가 없는 브랜치라 세션을 가짜로 주입해 판정 로직만 검증한다.
"""

from types import SimpleNamespace

import pytest

from wellbot.models.chat_message import ChatMessage
from wellbot.models.chat_summary import ChatSummary
from wellbot.services.chat import chat_service
from wellbot.services.report_maker import db as rmdb


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeSession:
    """query(모델) 의 첫 인자로 대상 테이블을 판별하는 최소 세션 스텁.

    can_attach 는 컬럼(ChatSummary.emp_no)만 select 하므로, 컬럼식에서 소속 모델을
    되짚어 분기한다.
    """

    def __init__(self, *, owner=None, message=None):
        self._owner = owner
        self._message = message

    def query(self, column):
        model = getattr(column, "class_", None) or getattr(column, "parent", None)
        entity = getattr(model, "class_", model)
        if entity is ChatSummary:
            return _FakeQuery(self._owner)
        if entity is ChatMessage:
            return _FakeQuery(self._message)
        return _FakeQuery(None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch(monkeypatch, module, *, owner, message):
    monkeypatch.setattr(
        module, "get_session", lambda: _FakeSession(owner=owner, message=message)
    )


# 메인 챗과 report_maker 는 같은 판정 규칙을 각자 구현한다 → 동일 케이스로 함께 검증
MODULES = [
    pytest.param(chat_service, id="chat_service"),
    pytest.param(rmdb, id="report_maker_db"),
]


@pytest.mark.parametrize("module", MODULES)
class TestCanAttach:
    def test_owner_allowed(self, monkeypatch, module):
        _patch(monkeypatch, module, owner=SimpleNamespace(emp_no="1001"), message=None)
        assert module.can_attach("smry-1", "1001") is True

    def test_other_owner_rejected(self, monkeypatch, module):
        """대화가 이미 존재하고 소유자가 다르면 거부 — IDOR 차단 핵심 케이스."""
        _patch(monkeypatch, module, owner=SimpleNamespace(emp_no="victim"), message=None)
        assert module.can_attach("victim-smry", "attacker") is False

    def test_new_session_allowed(self, monkeypatch, module):
        """대화 행도 메시지도 없으면 진짜 새 세션 → 첫 메시지 전 업로드 허용."""
        _patch(monkeypatch, module, owner=None, message=None)
        assert module.can_attach("brand-new", "1001") is True

    def test_messages_without_summary_rejected(self, monkeypatch, module):
        """행은 없는데 메시지가 있는 비정상 상태는 거부(정상 흐름에서 생기지 않음)."""
        _patch(
            monkeypatch, module, owner=None, message=SimpleNamespace(chtb_tlk_id="t-1")
        )
        assert module.can_attach("weird", "1001") is False

    def test_empty_smry_id_rejected(self, monkeypatch, module):
        # DB 조회 없이 즉시 거부되어야 하므로 세션을 아예 주입하지 않는다
        assert module.can_attach("", "1001") is False
