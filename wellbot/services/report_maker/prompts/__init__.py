"""report_maker 프롬프트 상수 모음 (legacy config.py 원문 분리)."""

from .outline_rules import OUTLINE_GENERATION_RULES
from .structure_rules import (
    PAGE_STRUCTURE_GUIDE,
    REPORT_STRUCTURES,
    STRUCTURE_HEADING_RULES,
)
from .table_rules import TABLE_READING_RULES

__all__ = [
    "OUTLINE_GENERATION_RULES",
    "STRUCTURE_HEADING_RULES",
    "TABLE_READING_RULES",
    "REPORT_STRUCTURES",
    "PAGE_STRUCTURE_GUIDE",
    "structures_block",
]


def structures_block() -> str:
    """보고 유형 후보 목록을 프롬프트 블록 문자열로."""
    lines = ["[보고 유형 후보 — 이 보고에 가장 적합한 하나를 선택해 적용]"]
    for k, v in REPORT_STRUCTURES.items():
        lines.append(
            f"- {k}({v['name']}): 목적={v['purpose']} / "
            f"구성요소={', '.join(v['elements'])} / 원칙={v['principles']}"
        )
    return "\n".join(lines)
