"""report_checker 도메인 모델.

원본 스크립트의 dataclass 를 이식하고, 사용자 사전(UserDictionary)과
진행 이벤트(ProgressEvent)를 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class AnalysisCancelled(Exception):
    """사용자가 분석을 중단했을 때 발생 (협조적 취소)."""


@dataclass
class TypoError:
    """오탈자 1건."""

    page: int
    original: str
    correction: str
    context: str = ""

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "original": self.original,
            "correction": self.correction,
            "context": self.context,
        }


@dataclass
class Fact:
    """보고서에서 추출한 핵심 사실 1건 (일관성 검사용)."""

    page: int
    key: str
    value: str
    sentence: str


@dataclass
class ConsistencyError:
    """일관성(수치/기술) 오류 1건."""

    pages: list
    key: str
    values: list
    inconsistent_content: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "pages": list(self.pages),
            "key": self.key,
            "values": list(self.values),
            "inconsistent_content": self.inconsistent_content,
            "reason": self.reason,
        }


@dataclass
class AttentionIssue:
    """사용자 지정 주의 항목(가이드라인) 위반 1건."""

    page: int
    rule: str       # 위반한 주의 규칙
    excerpt: str    # 위반이 발견된 원문 발췌
    issue: str      # 무엇이 규칙에 어긋나는지 설명

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "rule": self.rule,
            "excerpt": self.excerpt,
            "issue": self.issue,
        }


@dataclass
class Usage:
    """LLM 토큰 사용량 누적기 (잡 전체 합산)."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0

    def add(self, u: dict | None) -> None:
        """Converse 응답의 usage 블록을 누적."""
        u = u or {}
        it = int(u.get("inputTokens", 0) or 0)
        ot = int(u.get("outputTokens", 0) or 0)
        tt = int(u.get("totalTokens", 0) or 0) or (it + ot)
        self.input_tokens += it
        self.output_tokens += ot
        self.total_tokens += tt
        self.calls += 1


@dataclass
class NotationIssue:
    """표기 일관성 위반 — 같은 개념을 다르게 표기한 경우 (값과 무관).

    예: '총 금액'(3p) 와 '총금액'(22p) 이 함께 쓰임.
    """

    concept: str        # 대표(정규화 후 첫 표기)
    variants: list      # [{"form": str, "pages": [int]}]

    def to_dict(self) -> dict:
        variants = [
            {"form": v["form"], "pages": list(v["pages"])} for v in self.variants
        ]
        # 중첩 foreach 회피용 표시 문자열: "총 금액(3p, 10p) · 총금액(22p)"
        variants_str = " · ".join(
            f"{v['form']}({', '.join(f'{p}p' for p in v['pages'])})" for v in variants
        )
        return {
            "concept": self.concept,
            "variants": variants,
            "variants_str": variants_str,
        }


@dataclass
class AnalysisResult:
    """분석 최종 결과."""

    typo_errors: list = field(default_factory=list)
    consistency_errors: list = field(default_factory=list)
    attention_errors: list = field(default_factory=list)
    notation_errors: list = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


@dataclass
class UserDictionary:
    """사용자 사전 (분석 1회성, 저장하지 않음).

    Attributes:
        exclusions: 오탈자로 보고하지 않을 올바른 표기 목록
                    (예: 고유명사·전문용어·브랜드명).
        alias_groups: 동일 항목 별칭(표기 통일) — 같은 항목을 사람마다 다르게 기입한
                    표기 변형들의 묶음. 라벨이 달라 서로 다른 항목으로 흩어진 사실을
                    하나로 묶어 값을 교차비교하게 한다(값 불일치 자체는 이후 LLM 검증).
                    각 그룹은 나열된 표기들과 정확히 일치하는 키를 하나로 취급.
                    예: [["총 금액", "합계 금액", "Total"], ["지원금", "지원 금액"]]
    """

    exclusions: list[str] = field(default_factory=list)
    alias_groups: list[list[str]] = field(default_factory=list)
    # 주의 항목: 자연어 규칙 목록. 각 규칙 위반을 AI 가 텍스트에서 찾아 보고.
    # (예: "'2025년'은 한자 '2025年'으로 표기", "금액은 항상 '원' 단위 명시")
    # 주의: 윗첨자/굵게 등 순수 서식은 텍스트 추출로 판별 불가하여 검증 대상이 아님.
    watch_items: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "UserDictionary":
        data = data or {}
        exclusions = [str(x).strip() for x in (data.get("exclusions") or []) if str(x).strip()]
        groups: list[list[str]] = []
        for grp in data.get("alias_groups") or []:
            terms = [str(t).strip() for t in (grp or []) if str(t).strip()]
            if len(terms) >= 2:
                groups.append(terms)
        watch = [str(w).strip() for w in (data.get("watch_items") or []) if str(w).strip()]
        return cls(exclusions=exclusions, alias_groups=groups, watch_items=watch)

    def is_empty(self) -> bool:
        return not self.exclusions and not self.alias_groups and not self.watch_items


@dataclass
class ProgressEvent:
    """분석 진행 이벤트 (콜백으로 State 에 전달).

    stage: "parsing" | "typo" | "consistency" | "rendering" | "done" | "error"
    """

    stage: str
    detail: str = ""
    current: int = 0
    total: int = 0
    typo_count: int = 0
    consistency_count: int = 0
    attention_count: int = 0
    notation_count: int = 0
