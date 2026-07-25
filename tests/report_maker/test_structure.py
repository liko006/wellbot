"""structure.remaining_questions — LLM 반환값 클램프 계약.

미답 질문 판정은 LLM 에 맡기지만, 결과는 **반드시 원본 질문 집합 안으로 제한**해야 한다.
폴백으로 LLM 원본 배열을 그대로 돌려주면 환각으로 만들어진 문장이 사용자에게 그대로
재질문되므로, 매칭 실패 시엔 '남은 질문 없음'으로 본다(과잉 질문보다 안전한 방향).
"""

from wellbot.services.report_maker import bedrock, structure


def _fake_llm(monkeypatch, payload):
    monkeypatch.setattr(bedrock, "call_json", lambda prompt, mt: payload)


QUESTIONS = ["예산 규모는?", "목표 시점은?", "대상 조직은?"]


class TestRemainingQuestions:
    def test_subset_returned_in_original_order(self, monkeypatch):
        # LLM 이 순서를 뒤집어 돌려줘도 원본 순서를 따른다
        _fake_llm(monkeypatch, {"remaining": ["대상 조직은?", "예산 규모는?"]})
        assert structure.remaining_questions(QUESTIONS, "5월까지입니다") == [
            "예산 규모는?",
            "대상 조직은?",
        ]

    def test_all_answered_returns_empty(self, monkeypatch):
        _fake_llm(monkeypatch, {"remaining": []})
        assert structure.remaining_questions(QUESTIONS, "전부 답했습니다") == []

    def test_hallucinated_questions_are_dropped(self, monkeypatch):
        """원본에 없는 문장은 전부 버린다 — 폴백으로 새어 나가면 안 된다."""
        _fake_llm(
            monkeypatch,
            {"remaining": ["경쟁사 대응 전략은?", "ROI 산정 근거는?"]},
        )
        assert structure.remaining_questions(QUESTIONS, "답변") == []

    def test_partial_hallucination_keeps_only_matches(self, monkeypatch):
        _fake_llm(
            monkeypatch,
            {"remaining": ["목표 시점은?", "존재하지 않는 질문"]},
        )
        assert structure.remaining_questions(QUESTIONS, "답변") == ["목표 시점은?"]

    def test_llm_failure_returns_empty(self, monkeypatch):
        # call_json 이 None(파싱 실패) 이거나 키가 없어도 예외 없이 빈 목록
        _fake_llm(monkeypatch, None)
        assert structure.remaining_questions(QUESTIONS, "답변") == []
        _fake_llm(monkeypatch, {})
        assert structure.remaining_questions(QUESTIONS, "답변") == []

    def test_no_questions_skips_llm(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("질문이 없으면 LLM 을 호출하지 않아야 한다")

        monkeypatch.setattr(bedrock, "call_json", _boom)
        assert structure.remaining_questions([], "답변") == []
