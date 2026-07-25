"""단일 편집기 스타일 모델 — memory.add_doc_style / set_style.

단일 편집기 전환: 스타일은 하나의 편집 가능한 정본(combined)이다.
- add_doc_style: 문서 추출본을 기존 정본에 병합(정본이 비면 그대로).
- set_style: 정본 전체를 편집 텍스트로 덮어쓴다.
AgentCore 미가용(순수 S3) 경로를 가짜 스토리지로 검증한다.
"""

from wellbot.services.report_maker import memory


class _FakeStorage:
    def __init__(self, combined=""):
        self.combined = combined

    def load_combined_style(self, emp_no, template):
        return self.combined

    def save_combined_style(self, emp_no, template, desc):
        self.combined = desc


def _patch(monkeypatch, fake):
    monkeypatch.setattr(memory, "storage", fake)
    monkeypatch.setattr(memory, "_agentcore_ready", lambda: False)
    # 병합은 결정적 스텁으로 — 실제 LLM 대신 '기존 + 신규' 결합 확인
    monkeypatch.setattr(
        memory.style, "merge_style_desc",
        lambda existing, new: f"{existing} || {new}" if existing.strip() else new,
    )


def test_add_doc_into_empty_takes_desc_verbatim(monkeypatch):
    fake = _FakeStorage(combined="")
    _patch(monkeypatch, fake)

    memory.add_doc_style("100", "주간", "개조식, 표 활용")

    assert fake.combined == "개조식, 표 활용"  # 정본이 비어 병합 없이 그대로


def test_add_doc_merges_over_existing(monkeypatch):
    fake = _FakeStorage(combined="기존 스타일")
    _patch(monkeypatch, fake)

    memory.add_doc_style("100", "주간", "새 문서 스타일")

    assert fake.combined == "기존 스타일 || 새 문서 스타일"  # 기존 위에 병합


def test_set_style_overwrites(monkeypatch):
    fake = _FakeStorage(combined="이전 내용")
    _patch(monkeypatch, fake)

    memory.set_style("100", "주간", "  사용자가 직접 쓴 스타일  ")

    assert fake.combined == "사용자가 직접 쓴 스타일"  # 병합 없이 전체 교체(+trim)


def test_load_style_returns_combined_verbatim(monkeypatch):
    fake = _FakeStorage(combined="[문서 작성 스타일]\n* 문서유형: 주간보고")
    _patch(monkeypatch, fake)
    # 정본이 있으면 AgentCore 폴백/LLM 없이 그대로 반환
    monkeypatch.setattr(memory, "_agentcore_ready", lambda: True)

    assert memory.load_style("100", "주간") == "[문서 작성 스타일]\n* 문서유형: 주간보고"


def test_load_style_fallback_normalizes_and_materializes(monkeypatch):
    """정본이 비고 AgentCore 에만 (JSON) 레코드가 있을 때: 순수 지시문으로 정규화 + 정본 이관."""
    fake = _FakeStorage(combined="")
    _patch(monkeypatch, fake)
    monkeypatch.setattr(memory, "_agentcore_ready", lambda: True)
    # AgentCore 레코드는 JSON 원문 — 그대로 노출되면 안 됨
    monkeypatch.setattr(
        memory, "_record_summaries",
        lambda ns: [{"content": {"text": '{"preference": "개조식", "tone": "단정"}'}}],
    )
    monkeypatch.setattr(
        memory.style, "summarize_style",
        lambda raw: "[문서 작성 스타일]\n* 문장종결: 개조식",
    )

    out = memory.load_style("100", "주간")

    assert out == "[문서 작성 스타일]\n* 문장종결: 개조식"        # JSON 아님, 지시문
    assert fake.combined == "[문서 작성 스타일]\n* 문장종결: 개조식"  # 정본으로 materialize


def test_delete_doc_does_not_touch_style(monkeypatch):
    fake = _FakeStorage(combined="유지되어야 함")
    _patch(monkeypatch, fake)
    # delete_doc 은 원본 파일만 지운다 — 정본 텍스트는 건드리지 않음
    monkeypatch.setattr(
        memory.storage, "delete_style_doc_file",
        lambda emp_no, template, basename: True, raising=False,
    )

    memory.delete_doc("100", "주간", "250101120000_a.pdf")

    assert fake.combined == "유지되어야 함"
