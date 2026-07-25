"""report_maker 순수 함수(parsing) 단위 테스트 — LLM/DB 비의존."""

import pytest

from wellbot.services.report_maker import parsing


@pytest.mark.parametrize(
    "text,expected",
    [
        ("반장", 0.5),
        ("반페이지", 0.5),
        ("한장", 1.0),
        ("세페이지", 3.0),
        ("1장반", 1.5),
        ("2.5page", 2.5),
        ("5", 5.0),
        ("추천", 0),
        ("", 0),
    ],
)
def test_parse_page_count(text, expected):
    assert parsing.parse_page_count(text) == expected


def test_fmt_pages():
    assert parsing.fmt_pages(1.0) == "1"
    assert parsing.fmt_pages(0.5) == "0.5"
    assert parsing.fmt_pages(2) == "2"
    assert parsing.fmt_pages("x") == "x"


def test_strip_code_fences():
    assert parsing.strip_code_fences('```json\n{"a":1}\n```') == '{"a":1}'
    assert parsing.strip_code_fences("plain text") == "plain text"


def test_to_safe_id():
    assert parsing.to_safe_id("Monthly") == "Monthly"
    assert parsing.to_safe_id("report_2026") == "report_2026"
    # 한글/기호는 해시로 (안정적·재현 가능)
    hashed = parsing.to_safe_id("먼슬리")
    assert hashed.startswith("tpl_")
    assert parsing.to_safe_id("먼슬리") == hashed  # 결정론적
    assert parsing.to_safe_id("") == "tpl_" + parsing.to_safe_id("")[4:]  # 빈값도 해시


def test_has_table_data():
    assert parsing.has_table_data("| a | b |\n|---|---|\n| 1 | 2 |") is True
    assert parsing.has_table_data("△12 ▲8") is True
    assert parsing.has_table_data("+2% +3건 +4명") is True
    assert parsing.has_table_data("일반 문장입니다") is False
    assert parsing.has_table_data("") is False


def test_normalize_md_tables_splits_joined():
    joined = "| 구분 | A | B ||---|---||x|1|2|"
    out = parsing.normalize_md_tables(joined)
    # 한 줄에 붙은 표가 여러 행으로 분리되고 구분선 칸 수가 헤더(3)에 맞춰짐
    assert "\n" in out
    assert "|---|---|---|" in out


def test_normalize_md_tables_fixes_separator_width():
    # 헤더 4칸인데 구분선 2칸 → 4칸으로 보정
    text = "| a | b | c | d |\n|---|---|\n| 1 | 2 | 3 | 4 |"
    out = parsing.normalize_md_tables(text)
    assert "|---|---|---|---|" in out


def test_md_linebreaks_hard_breaks_and_indent():
    out = parsing.md_linebreaks("□ 대분류\n- 중분류\n· 소분류")
    lines = out.split("\n")
    # 모든 비표/비공백 줄은 하드 브레이크(끝 2칸)로 끝난다
    assert all(ln.endswith("  ") for ln in lines if ln.strip())
    # 중분류(-)·소분류(·)는 들여쓰기(non-breaking space)가 앞에 붙는다
    assert lines[1].startswith(" ")
    assert lines[2].startswith(" ")


def test_extract_and_strip_question_block():
    text = (
        "본문 내용\n"
        "**추가 정보가 필요합니다**\n"
        "1. 일정은 언제입니까?\n"
        "2. 예산 규모는?\n"
        "[답변]\n계속"
    )
    qs = parsing.extract_questions(text)
    assert qs == ["일정은 언제입니까?", "예산 규모는?"]
    stripped = parsing.strip_question_block(text)
    assert "추가 정보가 필요합니다" not in stripped
    assert "본문 내용" in stripped
    assert "[답변]" in stripped  # 답변([) 섹션은 보존


def test_extract_questions_empty_when_no_block():
    assert parsing.extract_questions("그냥 텍스트") == []
    assert parsing.strip_question_block("그냥 텍스트") == "그냥 텍스트"
