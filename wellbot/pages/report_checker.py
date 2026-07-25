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
            rx.vstack(
                rx.checkbox(
                    "값 일관성 검사 (같은 항목의 수치가 페이지마다 다른지)",
                    checked=ReportCheckerState.include_consistency,
                    on_change=ReportCheckerState.set_include_consistency,
                    disabled=ReportCheckerState.is_running,
                    color_scheme="indigo",
                    size="2",
                ),
                rx.checkbox(
                    "표기 일관성 검사 (같은 개념을 다르게 표기했는지, 예: 총 금액/총금액)",
                    checked=ReportCheckerState.include_notation,
                    on_change=ReportCheckerState.set_include_notation,
                    disabled=ReportCheckerState.is_running,
                    color_scheme="purple",
                    size="2",
                ),
                rx.text(
                    "· 오탈자 검사는 항상 실행됩니다",
                    size="1",
                    color=COLORS["text_secondary"],
                ),
                align="start",
                spacing="2",
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
    """제외어 / 동일 항목(표기 통일) / 주의 항목 입력 (접이식)."""
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
                        "동일 항목(표기 통일) — 같은 항목을 다르게 적은 표기들을 한 묶음으로 (한 줄=한 묶음, 콤마 구분)",
                        size="1",
                        color=COLORS["text_secondary"],
                    ),
                    rx.text_area(
                        placeholder="예: 총 금액, 합계 금액, Total\n지원금, 지원 금액",
                        value=ReportCheckerState.aliases_text,
                        on_change=ReportCheckerState.set_aliases_text,
                        rows="3",
                        width="100%",
                        disabled=ReportCheckerState.is_running,
                    ),
                    rx.text(
                        "※ 같은 항목을 '총 금액/합계 금액/Total'처럼 다르게 기입한 경우, 하나로 묶어 "
                        "값을 교차 비교합니다. 값 불일치가 실제 오류인지는 AI가 다시 판정합니다.",
                        size="1",
                        color=COLORS["text_secondary"],
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


def _step(label: str, index: int, count_var=None) -> rx.Component:
    """진행 스텝 한 줄. stage_index 와 자신의 index 를 비교해 상태 표시.

    완료(stage_index > index): ✓ + (건수 또는 '완료')
    진행중(== index): 스피너 + '현재/전체'
    대기(< index): ○ + '대기'
    """
    icon = rx.cond(
        ReportCheckerState.stage_index > index,
        rx.icon("circle-check", size=18, color=rx.color("green", 11)),
        rx.cond(
            ReportCheckerState.stage_index == index,
            rx.spinner(size="2"),
            rx.icon("circle", size=18, color=COLORS["text_secondary"]),
        ),
    )
    done_status = (
        rx.hstack(
            rx.text(count_var, size="2", weight="medium", color=COLORS["text_primary"]),
            rx.text("건", size="2", color=COLORS["text_secondary"]),
            spacing="1",
            align="center",
        )
        if count_var is not None
        else rx.text("완료", size="2", color=rx.color("green", 11))
    )
    active_status = rx.cond(
        ReportCheckerState.stage_total > 0,
        rx.text(
            ReportCheckerState.stage_current,
            " / ",
            ReportCheckerState.stage_total,
            size="2",
            color=COLORS["text_secondary"],
        ),
        rx.text("진행 중", size="2", color=COLORS["text_secondary"]),
    )
    status = rx.cond(
        ReportCheckerState.stage_index > index,
        done_status,
        rx.cond(
            ReportCheckerState.stage_index == index,
            active_status,
            rx.text("대기", size="2", color=COLORS["text_secondary"]),
        ),
    )
    label_color = rx.cond(
        ReportCheckerState.stage_index >= index,
        COLORS["text_primary"],
        COLORS["text_secondary"],
    )
    return rx.hstack(
        rx.box(icon, width="20px", display="flex", justify_content="center"),
        rx.text(label, size="2", weight="medium", color=label_color),
        rx.spacer(),
        status,
        width="100%",
        align="center",
        spacing="3",
    )


def _progress_panel() -> rx.Component:
    return _section_card(
        rx.vstack(
            rx.hstack(
                rx.spinner(size="2"),
                rx.text("분석 진행 중...", size="4", weight="bold", color=COLORS["text_primary"]),
                rx.spacer(),
                rx.text(
                    ReportCheckerState.progress_pct,
                    "%",
                    size="3",
                    weight="bold",
                    color=rx.color("indigo", 11),
                ),
                align="center",
                spacing="2",
                width="100%",
            ),
            rx.progress(value=ReportCheckerState.progress_pct, width="100%"),
            rx.vstack(
                _step("PDF 페이지 추출", 0),
                _step("오탈자 검사", 1, ReportCheckerState.typo_count),
                rx.cond(
                    ReportCheckerState.watch_active,
                    _step("주의 항목 검사", 2, ReportCheckerState.attention_count),
                    rx.fragment(),
                ),
                rx.cond(
                    ReportCheckerState.notation_active,
                    _step("표기 일관성 검사", 3, ReportCheckerState.notation_count),
                    rx.fragment(),
                ),
                rx.cond(
                    ReportCheckerState.include_consistency,
                    _step("값 일관성 검사", 4, ReportCheckerState.consistency_count),
                    rx.fragment(),
                ),
                spacing="2",
                width="100%",
                padding_top="0.5em",
            ),
            rx.hstack(
                rx.spacer(),
                rx.button(
                    rx.icon("square", size=15),
                    rx.cond(ReportCheckerState.cancel_requested, "중단 중...", "분석 중단"),
                    on_click=ReportCheckerState.request_cancel,
                    disabled=ReportCheckerState.cancel_requested,
                    color_scheme="red",
                    variant="soft",
                    size="2",
                ),
                width="100%",
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


def _notation_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("개념"),
                rx.table.column_header_cell("표기 변형 (페이지)"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                ReportCheckerState.notation_errors,
                lambda e: rx.table.row(
                    rx.table.cell(rx.badge(e["concept"], color_scheme="purple", variant="soft")),
                    rx.table.cell(
                        rx.text(e["variants_str"], size="1", color=rx.color("purple", 11), weight="medium")
                    ),
                ),
            ),
        ),
        variant="surface",
        size="1",
        width="100%",
    )


def _notation_section() -> rx.Component:
    return _section_card(
        rx.vstack(
            rx.hstack(
                rx.icon("case-sensitive", size=18, color=rx.color("purple", 11)),
                rx.text("표기 일관성", size="4", weight="bold", color=COLORS["text_primary"]),
                rx.badge(ReportCheckerState.notation_count, "건", color_scheme="purple", variant="soft"),
                align="center",
                spacing="2",
            ),
            rx.cond(
                ReportCheckerState.notation_count > 0,
                _notation_table(),
                rx.text("표기 불일치가 발견되지 않았습니다.", size="2", color=COLORS["text_secondary"]),
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
                    ReportCheckerState.notation_active,
                    _stat(ReportCheckerState.notation_count, "표기 불일치", "purple"),
                    rx.fragment(),
                ),
                rx.cond(
                    ReportCheckerState.watch_active,
                    _stat(ReportCheckerState.attention_count, "주의 항목", "green"),
                    rx.fragment(),
                ),
                rx.spacer(),
                rx.vstack(
                    rx.cond(
                        ReportCheckerState.download_ready,
                        rx.button(
                            rx.icon("download", size=16),
                            "HTML 다운로드",
                            on_click=ReportCheckerState.download_result,
                            color_scheme="indigo",
                            variant="soft",
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
        rx.cond(ReportCheckerState.notation_active, _notation_section(), rx.fragment()),
        rx.cond(ReportCheckerState.watch_active, _attention_section(), rx.fragment()),
        spacing="4",
        width="100%",
    )


def _notice_panel(icon: str, message: str, scheme: str) -> rx.Component:
    """간단한 안내 카드 (중단됨 등) + 새 분석 버튼."""
    return _section_card(
        rx.hstack(
            rx.icon(icon, size=18, color=rx.color(scheme, 11)),
            rx.text(message, size="2", color=COLORS["text_primary"]),
            rx.spacer(),
            rx.button(
                rx.icon("rotate-ccw", size=15),
                "새 분석",
                on_click=ReportCheckerState.reset_checker,
                variant="ghost",
                color_scheme="gray",
                size="2",
            ),
            width="100%",
            align="center",
            spacing="3",
        ),
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
                        rx.heading("보고서 오류 탐지", size="7", color=COLORS["text_primary"]),
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
                            ReportCheckerState.status == "cancelled",
                            _notice_panel("circle-slash", "분석이 중단되었습니다.", "gray"),
                            rx.fragment(),
                        ),
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
