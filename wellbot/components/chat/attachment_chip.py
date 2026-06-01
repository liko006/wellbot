"""첨부파일 칩 컴포넌트.

입력창 상단에 표시되는 미니 카드. 파일명, 상태(처리중/완료), 삭제 버튼을 포함.
"""

from __future__ import annotations

import reflex as rx

from wellbot.state.chat_models import AttachmentInfo
from wellbot.state.chat_state import ChatState
from wellbot.styles import COLORS, SPACING


def _file_icon(mime: str) -> rx.Component:
    """MIME 타입에 맞는 아이콘."""
    return rx.cond(
        mime.contains("image"),
        rx.icon("image", size=16, color=COLORS["text_secondary"]),
        rx.cond(
            mime.contains("pdf"),
            rx.icon("file-text", size=16, color=COLORS["text_secondary"]),
            rx.cond(
                mime.contains("spreadsheet"),
                rx.icon("table-2", size=16, color=COLORS["text_secondary"]),
                rx.cond(
                    mime.contains("presentation"),
                    rx.icon("file-chart-pie", size=16, color=COLORS["text_secondary"]),
                    rx.cond(
                        mime.contains("word") | mime.contains("hwp"),
                        rx.icon("file-pen-line", size=16, color=COLORS["text_secondary"]),
                        rx.cond(
                            mime.contains("markdown"),
                            rx.icon("file-code", size=16, color=COLORS["text_secondary"]),
                            rx.cond(
                                mime.contains("text/plain"),
                                rx.icon("file-type", size=16, color=COLORS["text_secondary"]),
                                rx.icon("file", size=16, color=COLORS["text_secondary"]),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _status_indicator(status: str) -> rx.Component:
    """처리 상태 인디케이터."""
    return rx.cond(
        status == "ready",
        rx.icon("check", size=14, color=COLORS["accent"]),
        rx.cond(
            status == "failed",
            rx.icon("triangle-alert", size=14, color="#e5484d"),
            rx.spinner(size="1"),
        ),
    )


def attachment_chip(att: AttachmentInfo) -> rx.Component:
    """단일 첨부파일 칩."""
    return rx.hstack(
        _file_icon(att.mime),
        rx.text(
            att.name,
            size="1",
            weight="medium",
            max_width="180px",
            overflow="hidden",
            text_overflow="ellipsis",
            white_space="nowrap",
        ),
        _status_indicator(att.status),
        rx.icon_button(
            rx.icon("x", size=12),
            variant="ghost",
            size="1",
            cursor="pointer",
            color=COLORS["text_secondary"],
            _hover={
                "color": COLORS["text_primary"],
                "bg": COLORS["tool_btn_hover"],
            },
            on_click=ChatState.remove_pending_attachment(att.file_no),
            type="button",
        ),
        align="center",
        gap="0.4em",
        padding="0.25em 0.5em",
        border=f"1px solid {COLORS['border']}",
        border_radius=SPACING["border_radius_sm"],
        bg=COLORS["input_bg"],
    )


def attachment_chip_list() -> rx.Component:
    """pending_attachments 목록 + 첨부 에러 메시지 표시"""
    return rx.cond(
        ChatState.has_pending_attachments | (ChatState.attachment_error != ""),
        rx.vstack(
            rx.cond(
                ChatState.has_pending_attachments,
                rx.hstack(
                    rx.foreach(ChatState.pending_attachments, attachment_chip),
                    gap="0.4em",
                    flex_wrap="wrap",
                    width="100%",
                ),
            ),
            rx.cond(
                ChatState.attachment_error != "",
                rx.text(
                    ChatState.attachment_error,
                    size="1",
                    color="#e5484d",
                ),
            ),
            spacing="1",
            width="100%",
            padding_bottom="0.4em",
        ),
    )
