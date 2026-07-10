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
class AnalysisResult:
    """분석 최종 결과."""

    typo_errors: list = field(default_factory=list)
    consistency_errors: list = field(default_factory=list)
    attention_errors: list = field(default_factory=list)


@dataclass
class UserDictionary:
    """사용자 사전 (분석 1회성, 저장하지 않음).

    Attributes:
        exclusions: 오탈자로 보고하지 않을 올바른 표기 목록
                    (예: 고유명사·전문용어·브랜드명).
        synonym_groups: 동일하다고 간주할 용어 묶음 목록.
                    각 그룹의 용어들은 일관성 검사에서 같은 값/항목으로 정규화된다.
                    예: [["총예산", "전체예산", "총 예산"], ["1분기", "1Q", "Q1"]]
    """

    exclusions: list[str] = field(default_factory=list)
    synonym_groups: list[list[str]] = field(default_factory=list)
    # 주의 항목: 자연어 규칙 목록. 각 규칙 위반을 AI 가 텍스트에서 찾아 보고.
    # (예: "'2025년'은 한자 '2025年'으로 표기", "금액은 항상 '원' 단위 명시")
    # 주의: 윗첨자/굵게 등 순수 서식은 텍스트 추출로 판별 불가하여 검증 대상이 아님.
    watch_items: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "UserDictionary":
        data = data or {}
        exclusions = [str(x).strip() for x in (data.get("exclusions") or []) if str(x).strip()]
        groups: list[list[str]] = []
        for grp in data.get("synonym_groups") or []:
            terms = [str(t).strip() for t in (grp or []) if str(t).strip()]
            if len(terms) >= 2:
                groups.append(terms)
        watch = [str(w).strip() for w in (data.get("watch_items") or []) if str(w).strip()]
        return cls(exclusions=exclusions, synonym_groups=groups, watch_items=watch)

    def is_empty(self) -> bool:
        return not self.exclusions and not self.synonym_groups and not self.watch_items


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
