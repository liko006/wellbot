"""운영 로그 모니터링 탭 컴포넌트.

상단: 조회 범위(24h/7d/전체) + 새로고침 + 기준시각.
내부: 4개 서브탭(실패 피드 / 파일 인제스트 / 모델·RAG / 인증·보안).
실패 피드 행을 클릭하면 전체 레코드·스택트레이스를 모달로 확인한다.
"""

import reflex as rx

from wellbot.components.admin import cell
from wellbot.state.monitoring_state import MonitoringState
from wellbot.styles import COLORS


# ── 공통 위젯 ─────────────────────────────────────────────────────────


def _cat_chip(c: dict) -> rx.Component:
    return rx.badge(
        rx.hstack(
            rx.text(c["category"]),
            rx.text(c["value"], weight="bold"),
            spacing="2",
            align="center",
        ),
        color_scheme=c["color"],
        variant="soft",
        size="2",
        padding="0.4em 0.7em",
    )


def _th(label: str, align: str = "center") -> rx.Component:
    return rx.table.column_header_cell(label, text_align=align)


def _cell_left(content) -> rx.Component:
    return rx.table.cell(
        content, text_align="left", vertical_align="middle", padding_y="0.5em"
    )


def _level_badge(level) -> rx.Component:
    return rx.badge(
        level,
        variant="soft",
        size="1",
        color_scheme=rx.cond(
            level == "ERROR",
            "red",
            rx.cond(level == "WARNING", "amber", "gray"),
        ),
    )


def _section(title: str, *children) -> rx.Component:
    return rx.vstack(
        rx.heading(title, size="4"),
        *children,
        spacing="3",
        width="100%",
        align="start",
    )


def _empty(msg: str, length) -> rx.Component:
    return rx.cond(
        length == 0,
        rx.callout(msg, icon="info", color_scheme="gray", size="1", width="100%"),
    )


def _table(*, headers: list, body, empty_len, empty_msg: str) -> rx.Component:
    return rx.vstack(
        rx.table.root(
            rx.table.header(rx.table.row(*headers)),
            rx.table.body(body),
            width="100%",
            size="1",
            variant="surface",
        ),
        _empty(empty_msg, empty_len),
        spacing="2",
        width="100%",
    )


# ── 실패 피드 + drill-down 모달 ───────────────────────────────────────


def _fail_row(r: dict) -> rx.Component:
    return rx.table.row(
        cell(rx.text(r["ts"], size="1")),
        cell(_level_badge(r["level"])),
        cell(rx.badge(r["category"], color_scheme=r["color"], variant="soft", size="1")),
        cell(rx.text(r["who"], size="1")),
        cell(rx.text(r["target"], size="1")),
        _cell_left(rx.text(r["summary"], size="1", color=COLORS["text_secondary"])),
        cell(
            rx.cond(
                r["count"].to(int) > 1,
                rx.badge(r["count"].to_string(), color_scheme="gray", variant="soft", size="1"),
                rx.text("-", size="1"),
            )
        ),
        cell(rx.icon("scan-search", size=14, color=COLORS["text_secondary"])),
        on_click=MonitoringState.open_detail(r),
        cursor="pointer",
        _hover={"background": rx.color("gray", 3)},
    )


def _kv(label: str, value) -> rx.Component:
    return rx.hstack(
        rx.text(label, size="1", color=COLORS["text_secondary"], width="140px", flex_shrink="0"),
        rx.text(value, size="1", weight="medium", word_break="break-all"),
        spacing="2",
        width="100%",
        align="start",
    )


def _mono_block(content) -> rx.Component:
    return rx.box(
        rx.text(content),
        white_space="pre-wrap",
        word_break="break-word",
        font_family="monospace",
        font_size="11px",
        background=rx.color("gray", 2),
        border=f"1px solid {COLORS['border']}",
        border_radius="6px",
        padding="0.6em",
        max_height="260px",
        overflow="auto",
        width="100%",
    )


def _detail_modal() -> rx.Component:
    d = MonitoringState.detail
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(
                rx.hstack(
                    rx.badge(d["category"], color_scheme=d["color"], variant="soft"),
                    rx.text("로그 상세", size="3", weight="bold"),
                    spacing="2",
                    align="center",
                ),
            ),
            rx.dialog.description(
                rx.text(d["ts_full"], size="1", color=COLORS["text_secondary"]),
                margin_bottom="0.75em",
            ),
            rx.vstack(
                _kv("레벨", d["level"]),
                _kv("logger", d["logger"]),
                _kv("사원(emp_no)", d["emp_no"]),
                _kv("conversation_id", d["conversation_id"]),
                _kv("message_id", d["message_id"]),
                _kv("request_id", d["request_id"]),
                _kv("model", d["model"]),
                _kv("발생 횟수", d["count"].to_string()),
                rx.divider(),
                rx.text("message", size="1", weight="bold"),
                _mono_block(d["full_message"]),
                rx.cond(
                    d["exception"].to(str) != "",
                    rx.vstack(
                        rx.text("exception / traceback", size="1", weight="bold"),
                        _mono_block(d["exception"]),
                        spacing="2",
                        width="100%",
                        align="start",
                    ),
                ),
                spacing="2",
                width="100%",
                align="start",
            ),
            rx.flex(
                rx.dialog.close(
                    rx.button("닫기", variant="soft", color_scheme="gray", cursor="pointer"),
                ),
                justify="end",
                margin_top="1em",
                width="100%",
            ),
            max_width="720px",
        ),
        open=MonitoringState.detail_open,
        on_open_change=MonitoringState.set_detail_open,
    )


def _failures_view() -> rx.Component:
    return rx.vstack(
        rx.cond(
            MonitoringState.fail_cards.length() > 0,
            rx.flex(
                rx.foreach(MonitoringState.fail_cards, _cat_chip),
                wrap="wrap",
                gap="0.5rem",
                width="100%",
            ),
            rx.callout("집계 범위 내 실패/경고 없음", icon="check", color_scheme="green", size="1"),
        ),
        rx.text(
            "동일 원인은 1행으로 합쳐 횟수를 표시합니다. 행을 클릭하면 전체 레코드·스택트레이스를 볼 수 있습니다.",
            size="1",
            color=COLORS["text_secondary"],
        ),
        _table(
            headers=[
                _th("시각"),
                _th("레벨"),
                _th("카테고리"),
                _th("사용자"),
                _th("대상"),
                _th("내용", "left"),
                _th("횟수"),
                _th(""),
            ],
            body=rx.foreach(MonitoringState.fail_feed, _fail_row),
            empty_len=MonitoringState.fail_feed.length(),
            empty_msg="표시할 실패/경고가 없습니다.",
        ),
        _detail_modal(),
        spacing="3",
        width="100%",
        align="start",
    )


# ── 파일 인제스트 ─────────────────────────────────────────────────────


def _ingest_row(r: dict) -> rx.Component:
    return rx.table.row(
        cell(rx.text(r["ts"], size="1")),
        cell(rx.badge(r["kind"], color_scheme=r["color"], variant="soft", size="1")),
        cell(rx.text(r["target"], size="1")),
        _cell_left(rx.text(r["detail"], size="1", color=COLORS["text_secondary"])),
    )


def _ingest_view() -> rx.Component:
    return _section(
        "인제스트 실패 · 지연 피드",
        _table(
            headers=[_th("시각"), _th("유형"), _th("대상"), _th("내용", "left")],
            body=rx.foreach(MonitoringState.ingest_feed, _ingest_row),
            empty_len=MonitoringState.ingest_feed.length(),
            empty_msg="표시할 인제스트 이벤트가 없습니다.",
        ),
    )


# ── 모델 · RAG ────────────────────────────────────────────────────────


def _model_row(r: dict) -> rx.Component:
    return rx.table.row(
        cell(rx.text(r["model"], size="1", weight="medium")),
        cell(rx.text(r["turns"], size="1")),
        cell(rx.text(r["in_tok"], size="1")),
        cell(rx.text(r["out_tok"], size="1")),
        cell(rx.text(r["p95"], size="1")),
        cell(rx.text(r["cost"], size="1", weight="medium")),
    )


def _convo_row(r: dict) -> rx.Component:
    return rx.table.row(
        cell(rx.code(r["conv"], size="1")),
        cell(rx.text(r["emp"], size="1")),
        cell(rx.text(r["model"], size="1")),
        cell(rx.text(r["turns"], size="1")),
        cell(rx.text(r["tokens"], size="1", weight="medium")),
    )


def _models_view() -> rx.Component:
    return rx.vstack(
        _section(
            "모델별 사용량 · 추정 비용",
            _table(
                headers=[
                    _th("모델"),
                    _th("턴"),
                    _th("입력 토큰"),
                    _th("출력 토큰"),
                    _th("지연 p95"),
                    _th("추정 비용"),
                ],
                body=rx.foreach(MonitoringState.model_rows, _model_row),
                empty_len=MonitoringState.model_rows.length(),
                empty_msg="집계된 응답이 없습니다.",
            ),
        ),
        rx.divider(),
        _section(
            "토큰 상위 대화 Top 10",
            _table(
                headers=[_th("대화"), _th("사원"), _th("모델"), _th("턴"), _th("토큰")],
                body=rx.foreach(MonitoringState.convo_rows, _convo_row),
                empty_len=MonitoringState.convo_rows.length(),
                empty_msg="집계된 대화가 없습니다.",
            ),
        ),
        spacing="4",
        width="100%",
        align="start",
    )


# ── 인증 · 보안 ───────────────────────────────────────────────────────


def _auth_row(r: dict) -> rx.Component:
    return rx.table.row(
        cell(rx.text(r["ts"], size="1")),
        cell(rx.badge(r["kind"], color_scheme=r["color"], variant="soft", size="1")),
        cell(rx.text(r["emp"], size="1")),
        _cell_left(rx.text(r["detail"], size="1", color=COLORS["text_secondary"])),
    )


def _auth_view() -> rx.Component:
    return _section(
        "인증 · 보안 이벤트",
        _table(
            headers=[_th("시각"), _th("유형"), _th("사번"), _th("상세", "left")],
            body=rx.foreach(MonitoringState.auth_feed, _auth_row),
            empty_len=MonitoringState.auth_feed.length(),
            empty_msg="표시할 인증 이벤트가 없습니다.",
        ),
    )


# ── 상단 바 + 조립 ────────────────────────────────────────────────────


def _window_btn(label: str, value: str) -> rx.Component:
    return rx.button(
        label,
        size="1",
        variant=rx.cond(MonitoringState.window == value, "solid", "surface"),
        color_scheme="gray",
        cursor="pointer",
        on_click=MonitoringState.set_window(value),
    )


def _toolbar() -> rx.Component:
    return rx.hstack(
        rx.hstack(
            _window_btn("24시간", "24h"),
            _window_btn("7일", "7d"),
            _window_btn("전체", "all"),
            spacing="1",
        ),
        rx.spacer(),
        rx.hstack(
            rx.icon("clock", size=14, color=COLORS["text_secondary"]),
            rx.text("기준 ", size="1", color=COLORS["text_secondary"]),
            rx.text(MonitoringState.ref_time, size="1", weight="medium"),
            spacing="1",
            align="center",
        ),
        rx.button(
            rx.icon("refresh-cw", size=14),
            "새로고침",
            size="1",
            variant="soft",
            cursor="pointer",
            loading=MonitoringState.loading,
            on_click=MonitoringState.load,
        ),
        width="100%",
        align="center",
        spacing="3",
    )


def _subtabs() -> rx.Component:
    return rx.tabs.root(
        rx.tabs.list(
            rx.tabs.trigger("실패 피드", value="failures"),
            rx.tabs.trigger("파일 인제스트", value="ingest"),
            rx.tabs.trigger("모델·RAG", value="models"),
            rx.tabs.trigger("인증·보안", value="auth"),
        ),
        rx.tabs.content(_failures_view(), value="failures", padding_top="1em"),
        rx.tabs.content(_ingest_view(), value="ingest", padding_top="1em"),
        rx.tabs.content(_models_view(), value="models", padding_top="1em"),
        rx.tabs.content(_auth_view(), value="auth", padding_top="1em"),
        value=MonitoringState.sub_tab,
        on_change=MonitoringState.set_sub_tab,
        width="100%",
    )


def monitoring_tab() -> rx.Component:
    """운영 로그 모니터링 탭 진입점."""
    return rx.box(
        rx.vstack(
            _toolbar(),
            rx.text(MonitoringState.source_info, size="1", color=COLORS["text_secondary"]),
            rx.cond(
                MonitoringState.error != "",
                rx.callout(
                    MonitoringState.error, icon="triangle-alert", color_scheme="red", size="1", width="100%"
                ),
            ),
            rx.cond(
                MonitoringState.has_data,
                _subtabs(),
                rx.callout(
                    "표시할 로그가 없습니다. LOG_DIR/wellbot.log 를 확인하세요.",
                    icon="info",
                    color_scheme="gray",
                    width="100%",
                ),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        on_mount=MonitoringState.load_if_needed,
        width="100%",
    )
