"""fetch_pending_attachments — 미전송 첨부 조회 규칙 (복원 회귀).

대화를 옮겼다 돌아오면 미전송 첨부를 msg_id 로 다시 읽어 칩을 복원한다. 이때
``already_sent`` 필터를 적용하면 **복원 대상이 자기 자신에 의해 걸러진다** — 대화를 열
때 `_load_conversation_attachments` 가 그 대화의 모든 첨부(미전송분 포함)를
`conversation_attachments` 에 담아 오고, 그게 곧 ``already_sent`` 로 쓰이기 때문이다.
증상은 "첨부가 화면에서 사라지고 질문에도 포함되지 않음"이라 원인 추적이 어려웠다.

msg_id 없는 경로(대화 첨부 전체 훑기)에서는 필터가 반드시 살아 있어야 한다 — 그쪽은
이미 보낸 첨부를 걷어내는 것이 목적이다.
"""

from types import SimpleNamespace

import pytest

from wellbot.state.chat_helpers import attachments as helper


def _row(file_no: int, token_count: int | None = 0) -> SimpleNamespace:
    """attachment_service 가 돌려주는 행의 최소 형태."""
    return SimpleNamespace(
        file_no=file_no,
        file_name=f"file{file_no}.pdf",
        mime="application/pdf",
        token_count=token_count,
    )


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """msg_id 조회는 1·2번, 대화 전체 조회는 1·2·3번 행을 돌려주는 스텁."""
    monkeypatch.setattr(
        helper.attachment_service,
        "get_attachments_by_msg_id",
        lambda msg_id: [_row(1), _row(2)],
    )
    monkeypatch.setattr(
        helper.attachment_service,
        "get_conversation_attachments",
        lambda conv_id: [_row(1), _row(2), _row(3)],
    )


def test_msg_id_lookup_ignores_already_sent(fake_service: None) -> None:
    """복원 경로: msg_id 에 묶인 행은 정의상 미전송이므로 필터하지 않는다."""
    result = helper.fetch_pending_attachments(
        emp_no="100123",
        conv_id="conv-1",
        pending_msg_id="msg-1",
        already_sent={1, 2, 3},   # 대화를 열며 전부 '이미 아는 첨부'로 잡힌 상황
    )
    assert [a.file_no for a in result] == [1, 2]


def test_conversation_lookup_filters_already_sent(fake_service: None) -> None:
    """msg_id 가 없으면 이미 보낸 첨부를 걷어낸 나머지만 pending 이다."""
    result = helper.fetch_pending_attachments(
        emp_no="100123",
        conv_id="conv-1",
        pending_msg_id="",
        already_sent={1, 2},
    )
    assert [a.file_no for a in result] == [3]


def test_missing_identity_returns_none(fake_service: None) -> None:
    """사번·대화 ID 가 없으면 상태를 갱신하지 않도록 None 을 돌려준다."""
    assert helper.fetch_pending_attachments("", "conv-1", "msg-1", set()) is None
    assert helper.fetch_pending_attachments("100123", "", "msg-1", set()) is None


def test_lookup_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """조회 실패 시 빈 목록으로 덮어써 칩이 사라지지 않도록 None 을 돌려준다."""

    def _boom(msg_id: str):
        raise RuntimeError("db down")

    monkeypatch.setattr(helper.attachment_service, "get_attachments_by_msg_id", _boom)
    assert helper.fetch_pending_attachments("100123", "conv-1", "msg-1", set()) is None


def test_status_is_derived_from_token_count() -> None:
    """UI 상태 판정: None=처리중 / 음수=실패 / 0 이상=완료."""
    assert helper.row_to_attachment_info(_row(1, None)).status == "processing"
    assert helper.row_to_attachment_info(_row(1, -1)).status == "failed"
    assert helper.row_to_attachment_info(_row(1, 0)).status == "ready"
    assert helper.row_to_attachment_info(_row(1, 1200)).status == "ready"
