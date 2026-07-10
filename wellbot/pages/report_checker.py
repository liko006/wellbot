"""보고서 오류 검출 페이지 (/ai-services/report-checker).

PDF 업로드 → (선택) 사용자 사전 입력 → 실시간 진행 현황 →
결과 네이티브 렌더 + HTML 다운로드.
"""

import reflex as rx

from wellbot.components.layout import chat_layout
from wellbot.state.report_checker_scripts import REPORT_CHECKER_SCRIPT
from wellbot.state.report_checker_state import ReportCheckerState
from wellbot.styles import COLORS, SPACING


def _section_card(*children: rx.Component, **kwargs) -> rx.Component:
    props = {
        "padding": "1.5em",
        "border": f"1px solid {COLORS['border']}",
        "border_radius": SPACING["border_radius_md"],
        "bg": COLORS["sidebar_bg"],
        "width": "100%",
    }
    props.update(kwargs)  # 호출부가 기본값(border 등)을 덮어쓸 수 있게
    return rx.box(*children, **props)


# ── 1. 업로드 + 사전 입력 ──────────────────────────────────────────
def _upload_panel() -> rx.Component:
    return _section_card(
        rx.vstack(
            rx.hstack(
                rx.icon("file-up", size=20, color=COLORS["text_primary"]),
                rx.text("PDF 업로드", size="4", weight="bold", color=COLORS["text_primary"]),
                align="center",
                spacing="2",
            ),
            rx.hstack(
                rx.button(
                    rx.icon("paperclip", size=16),
                    "파일 선택",
                    on_click=ReportCheckerState.pick_file,
                    variant="soft",
                    color_scheme="gray",
                    disabled=ReportCheckerState.is_running,
                ),
                rx.cond(
                    ReportCheckerState.has_file,
                    rx.hstack(
                        rx.icon("file-text", size=16, color=COLORS["text_secondary"]),
                        rx.text(
                            ReportCheckerState.pending_file_name,
                            size="2",
                            color=COLORS["text_primary"],
                            weight="medium",
                        ),
                        rx.text(
                            ReportCheckerState.file_size_label,
                            size="1",
                            color=COLORS["text_secondary"],
                        ),
                        align="center",
                        spacing="2",
                    ),
                    rx.text(
                        "선택된 파일이 없습니다.",
                        size="2",
                        color=COLORS["text_secondary"],
                    ),
                ),
                align="center",
                spacing="3",
                width="100%",
            ),
            rx.hstack(
                rx.checkbox(
                    "일관성 검사 포함 (수치·항목 불일치 검출)",
                    checked=ReportCheckerState.include_consistency,
                    on_change=ReportCheckerState.set_include_consistency,
                    disabled=ReportCheckerState.is_running,
                    color_scheme="indigo",
                    size="2",
                ),
                rx.text(
                    "· 오탈자 검사는 항상 실행됩니다",
                    size="1",
                    color=COLORS["text_secondary"],
                ),
                align="center",
                spacing="3",
                wrap="wrap",
            ),
            _dictionary_inputs(),
            rx.hstack(
                rx.button(
                    rx.icon("play", size=16),
                    "분석 시작",
                    on_click=ReportCheckerState.start_analysis,
                    disabled=ReportCheckerState.is_running | ~ReportCheckerState.has_file,
                    color_scheme="indigo",
                ),
                justify="end",
                width="100%",
            ),
            spacing="4",
            width="100%",
            align="start",
        ),
    )


def _dictionary_inputs() -> rx.Component:
    """제외어 / 동의어 사전 입력 (접이식)."""
    return rx.accordion.root(
        rx.accordion.item(
            header=rx.hstack(
                rx.icon("book-marked", size=16),
                rx.text("사용자 사전 (선택)", size="2", weight="medium"),
                spacing="2",
                align="center",
            ),
            content=rx.vstack(
                rx.vstack(
                    rx.text(
                        "제외어 — 오탈자로 보고하지 않을 올바른 표기 (콤마 또는 줄바꿈으로 구분)",
                        size="1",
                        color=COLORS["text_secondary"],
                    ),
                    rx.text_area(
                        placeholder="예: RPA, 온보딩",
                        value=ReportCheckerState.exclusions_text,
                        on_change=ReportCheckerState.set_exclusions_text,
                        rows="2",
                        width="100%",
                        disabled=ReportCheckerState.is_running,
                    ),
                    spacing="1",
                    width="100%",
                    align="start",
                ),
                rx.vstack(
                    rx.text(
                        "동의어 — 같은 뜻으로 볼 용어 묶음 (한 줄에 한 그룹, 콤마로 구분)",
                        size="1",
                        color=COLORS["text_secondary"],
                    ),
                    rx.text_area(
                        placeholder="예: 총예산, 전체예산, 총 예산\n1분기, 1Q, Q1",
                        value=ReportCheckerState.synonyms_text,
                        on_change=ReportCheckerState.set_synonyms_text,
                        rows="3",
                        width="100%",
                        disabled=ReportCheckerState.is_running,
                    ),
                    spacing="1",
                    width="100%",
                    align="start",
                ),
                rx.vstack(
                    rx.text(
                        "주의 항목 — 특별히 확인할 규칙 (한 줄에 하나)",
                        size="1",
                        color=COLORS["text_secondary"],
                    ),
                    rx.text_area(
                        placeholder="예: '2025년'은 한자 '2025年'으로 표기\n금액은 항상 '원' 단위 명시\n'AI'는 최초 1회 '인공지능(AI)'로 풀어쓰기",
                        value=ReportCheckerState.watch_items_text,
                        on_change=ReportCheckerState.set_watch_items_text,
                        rows="3",
                        width="100%",
                        disabled=ReportCheckerState.is_running,
                    ),
                    rx.text(
                        "※ 윗첨자·볼드 등 서식 규칙은 텍스트 추출 특성상 판별할 수 없습니다.",
                        size="1",
                        color=COLORS["text_secondary"],
                    ),
                    spacing="1",
                    width="100%",
                    align="start",
                ),
                spacing="3",
                width="100%",
                padding_top="0.75em",
            ),
        ),
        collapsible=True,
        width="100%",
        variant="ghost",
        type="single",
    )


# ── 2. 진행 현황 ────────────────────────────────────────────────────
_STAGE_LABEL = {
    "parsing": "PDF 페이지 추출",
    "typo": "오탈자 검사",
    "consistency": "일관성 검사",
    "done": "완료",
}


def _progress_panel() -> rx.Component:
    return _section_card(
        rx.vstack(
            rx.hstack(
                rx.spinner(size="2"),
                rx.text(
                    "분석 진행 중...",
                    size="4",
                    weight="bold",
                    color=COLORS["text_primary"],
                ),
                align="center",
                spacing="2",
            ),
            rx.progress(value=ReportCheckerState.progress_pct, width="100%"),
            rx.hstack(
                rx.text(
                    ReportCheckerState.progress_detail,
                    size="2",
                    color=COLORS["text_secondary"],
                ),
                rx.spacer(),
                rx.text(
                    ReportCheckerState.progress_pct,
                    "%",
                    size="2",
                    color=COLORS["text_secondary"],
                    weight="medium",
                ),
                width="100%",
            ),
            rx.hstack(
                rx.badge(
                    rx.icon("spell-check", size=14),
                    "오탈자 ",
                    ReportCheckerState.typo_count,
                    color_scheme="red",
                    variant="soft",
                ),
                rx.badge(
                    rx.icon("triangle-alert", size=14),
                    "일관성 ",
                    ReportCheckerState.consistency_count,
                    color_scheme="orange",
                    variant="soft",
                ),
                spacing="2",
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
    )


# ── 3. 결과 ─────────────────────────────────────────────────────────
def _stat(number, label: str, scheme: str) -> rx.Component:
    return rx.box(
        rx.text(number, size="7", weight="bold", color=rx.color(scheme, 11)),
        rx.text(label, size="1", color=COLORS["text_secondary"]),
        padding="1em 1.25em",
        border=f"1px solid {COLORS['border']}",
        border_radius=SPACING["border_radius_sm"],
        bg=COLORS["main_bg"],
        flex="1",
        min_width="120px",
    )


def _typo_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("페이지"),
                rx.table.column_header_cell("원문 (오류)"),
                rx.table.column_header_cell("교정"),
                rx.table.column_header_cell("문맥"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                ReportCheckerState.typo_errors,
                lambda e: rx.table.row(
                    rx.table.cell(rx.badge(e["page"], "p", color_scheme="blue", variant="soft")),
                    rx.table.cell(rx.text(e["original"], color=rx.color("red", 11), weight="bold")),
                    rx.table.cell(rx.text("→ ", e["correction"], color=rx.color("green", 11))),
                    rx.table.cell(rx.text(e["context"], size="1", color=COLORS["text_secondary"])),
                ),
            ),
        ),
        variant="surface",
        size="1",
        width="100%",
    )


def _consistency_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("항목"),
                rx.table.column_header_cell("충돌 값"),
                rx.table.column_header_cell("불일치 내용"),
                rx.table.column_header_cell("교정 필요 사유"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                ReportCheckerState.consistency_errors,
                lambda e: rx.table.row(
                    rx.table.cell(
                        rx.vstack(
                            rx.badge(e["key"], color_scheme="purple", variant="soft"),
                            rx.text(e["pages_str"], size="1", color=COLORS["text_secondary"]),
                            spacing="1",
                            align="start",
                        ),
                    ),
                    rx.table.cell(
                        rx.text(
                            e["values_str"],
                            size="1",
                            color=rx.color("orange", 11),
                            weight="bold",
                        ),
                    ),
                    rx.table.cell(rx.text(e["inconsistent_content"], size="1")),
                    rx.table.cell(rx.text(e["reason"], size="1", color=COLORS["text_secondary"])),
                ),
            ),
        ),
        variant="surface",
        size="1",
        width="100%",
    )


def _attention_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("페이지"),
                rx.table.column_header_cell("규칙"),
                rx.table.column_header_cell("발췌"),
                rx.table.column_header_cell("위반 내용"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                ReportCheckerState.attention_errors,
                lambda e: rx.table.row(
                    rx.table.cell(rx.badge(e["page"], "p", color_scheme="green", variant="soft")),
                    rx.table.cell(rx.badge(e["rule"], color_scheme="green", variant="soft")),
                    rx.table.cell(rx.text(e["excerpt"], size="1", color=COLORS["text_secondary"])),
                    rx.table.cell(rx.text(e["issue"], size="1")),
                ),
            ),
        ),
        variant="surface",
        size="1",
        width="100%",
    )


def _attention_section() -> rx.Component:
    return _section_card(
        rx.vstack(
            rx.hstack(
                rx.icon("scan-search", size=18, color=rx.color("green", 11)),
                rx.text("주의 항목", size="4", weight="bold", color=COLORS["text_primary"]),
                rx.badge(ReportCheckerState.attention_count, "건", color_scheme="green", variant="soft"),
                align="center",
                spacing="2",
            ),
            rx.cond(
                ReportCheckerState.attention_count > 0,
                _attention_table(),
                rx.text("주의 항목 위반이 발견되지 않았습니다.", size="2", color=COLORS["text_secondary"]),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
    )


def _result_panel() -> rx.Component:
    return rx.vstack(
        _section_card(
            rx.hstack(
                _stat(ReportCheckerState.total_errors, "총 오류", "indigo"),
                _stat(ReportCheckerState.typo_count, "오탈자", "red"),
                _stat(
                    rx.cond(ReportCheckerState.ran_consistency, ReportCheckerState.consistency_count, "—"),
                    "수치/기술 오류",
                    "orange",
                ),
                rx.cond(
                    ReportCheckerState.watch_active,
                    _stat(ReportCheckerState.attention_count, "주의 항목", "green"),
                    rx.fragment(),
                ),
                rx.spacer(),
                rx.vstack(
                    rx.cond(
                        ReportCheckerState.download_url != "",
                        rx.link(
                            rx.button(
                                rx.icon("download", size=16),
                                "HTML 다운로드",
                                color_scheme="indigo",
                                variant="soft",
                            ),
                            href=ReportCheckerState.download_url,
                            is_external=True,
                        ),
                    ),
                    rx.button(
                        rx.icon("rotate-ccw", size=16),
                        "새 분석",
                        on_click=ReportCheckerState.reset_checker,
                        variant="ghost",
                        color_scheme="gray",
                    ),
                    spacing="2",
                    align="end",
                ),
                width="100%",
                align="center",
                spacing="3",
                wrap="wrap",
            ),
        ),
        _section_card(
            rx.vstack(
                rx.hstack(
                    rx.icon("spell-check", size=18, color=rx.color("red", 11)),
                    rx.text("오탈자", size="4", weight="bold", color=COLORS["text_primary"]),
                    rx.badge(ReportCheckerState.typo_count, "건", color_scheme="red", variant="soft"),
                    align="center",
                    spacing="2",
                ),
                rx.cond(
                    ReportCheckerState.typo_count > 0,
                    _typo_table(),
                    rx.text("오탈자가 발견되지 않았습니다.", size="2", color=COLORS["text_secondary"]),
                ),
                spacing="3",
                width="100%",
                align="start",
            ),
        ),
        _section_card(
            rx.vstack(
                rx.hstack(
                    rx.icon("triangle-alert", size=18, color=rx.color("orange", 11)),
                    rx.text("수치/기술 오류", size="4", weight="bold", color=COLORS["text_primary"]),
                    rx.cond(
                        ReportCheckerState.ran_consistency,
                        rx.badge(ReportCheckerState.consistency_count, "건", color_scheme="orange", variant="soft"),
                        rx.badge("미검사", color_scheme="gray", variant="soft"),
                    ),
                    align="center",
                    spacing="2",
                ),
                rx.cond(
                    ReportCheckerState.ran_consistency,
                    rx.cond(
                        ReportCheckerState.consistency_count > 0,
                        _consistency_table(),
                        rx.text("수치·기술 오류가 발견되지 않았습니다.", size="2", color=COLORS["text_secondary"]),
                    ),
                    rx.text("일관성 검사를 실행하지 않았습니다.", size="2", color=COLORS["text_secondary"]),
                ),
                spacing="3",
                width="100%",
                align="start",
            ),
        ),
        rx.cond(ReportCheckerState.watch_active, _attention_section(), rx.fragment()),
        spacing="4",
        width="100%",
    )


def _error_panel() -> rx.Component:
    return _section_card(
        rx.vstack(
            rx.hstack(
                rx.icon("circle-x", size=20, color=rx.color("red", 11)),
                rx.text("분석 실패", size="4", weight="bold", color=rx.color("red", 11)),
                align="center",
                spacing="2",
            ),
            rx.text(ReportCheckerState.error_message, size="2", color=COLORS["text_secondary"]),
            rx.button(
                rx.icon("rotate-ccw", size=16),
                "다시 시도",
                on_click=ReportCheckerState.reset_checker,
                variant="soft",
                color_scheme="gray",
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        border=f"1px solid {rx.color('red', 6)}",
    )


def report_checker_page() -> rx.Component:
    # rx.script 는 페이지 fragment 루트에 두어야(레이아웃 내부 깊숙이 X) mount 타이밍
    # 이슈 없이 window 전역 함수(reportPickFile 등)가 클릭 전에 정의된다. (index.py 와 동일 패턴)
    return rx.fragment(
        rx.script(REPORT_CHECKER_SCRIPT),
        chat_layout(
            rx.box(
                rx.vstack(
                rx.vstack(
                    rx.hstack(
                        rx.link(
                            rx.icon("arrow-left", size=18, color=COLORS["text_secondary"]),
                            href="/ai-services",
                        ),
                        rx.heading("보고서 오류 검출", size="7", color=COLORS["text_primary"]),
                        align="center",
                        spacing="3",
                    ),
                    rx.text(
                        "PDF 보고서의 오탈자와 수치·논리(일관성) 오류를 AI가 자동으로 검출합니다.",
                        size="3",
                        color=COLORS["text_secondary"],
                    ),
                    spacing="1",
                    align="start",
                    width="100%",
                ),
                rx.cond(
                    ReportCheckerState.has_result,
                    _result_panel(),
                    rx.vstack(
                        _upload_panel(),
                        rx.cond(ReportCheckerState.is_running, _progress_panel(), rx.fragment()),
                        rx.cond(
                            ReportCheckerState.status == "error",
                            _error_panel(),
                            rx.fragment(),
                        ),
                        spacing="4",
                        width="100%",
                    ),
                ),
                spacing="6",
                width="100%",
                max_width="1100px",
                margin="0 auto",
            ),
                width="100%",
                height="100%",
                overflow_y="auto",
                padding="2.5em 2em",
            ),
        ),
    )
