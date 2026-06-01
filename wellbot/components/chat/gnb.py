"""채팅 영역 상단 GNB (Global Navigation Bar).

좌: 채팅 모드 드롭다운 | 중앙: 대화 제목 | 우: 첨부파일 + 다크/라이트 모드 토글
"""

import reflex as rx

from wellbot.state.chat_models import AttachmentInfo
from wellbot.state.chat_state import ChatState
from wellbot.styles import COLORS, SPACING


def _chat_mode_item(mode: rx.Var) -> rx.Component:
    """드롭다운 메뉴 아이템."""
    return rx.menu.item(
        rx.hstack(
            rx.icon(mode.icon, size=14),  # type: ignore
            rx.text(mode.name, size="2"),  # type: ignore
            align="center",
            gap="0.5em",
        ),
        on_click=ChatState.set_chat_mode(mode.id),  # type: ignore
    )


def _chat_mode_dropdown() -> rx.Component:
    """채팅 모드 선택 드롭다운."""
    return rx.menu.root(
        rx.menu.trigger(
            rx.button(
                rx.icon(
                    ChatState.current_chat_mode_icon,
                    size=14,
                ),
                rx.text(
                    ChatState.current_chat_mode_name,
                    size="2",
                    weight="medium",
                ),
                rx.icon("chevron-down", size=12),
                variant="ghost",
                size="1",
                cursor="pointer",
                color=COLORS["text_secondary"],
                _hover={"color": COLORS["text_primary"]},
                gap="0.35em",
            ),
        ),
        rx.menu.content(
            rx.foreach(
                ChatState.chat_mode_list,
                _chat_mode_item,
            ),
            side="bottom",
            align="start",
        ),
    )


def _conversation_title() -> rx.Component:
    """현재 대화 제목 (중앙 정렬)."""
    return rx.text(
        ChatState.current_title,
        size="2",
        weight="medium",
        color=COLORS["text_secondary"],
        text_align="center",
        overflow="hidden",
        text_overflow="ellipsis",
        white_space="nowrap",
        max_width="400px",
    )


def _attachment_item(att: AttachmentInfo) -> rx.Component:
    """팝오버 내 첨부파일 한 줄."""
    return rx.hstack(
        rx.icon(
            "file",
            size=14,
            color=COLORS["text_secondary"],
            flex_shrink="0",
        ),
        rx.box(
            rx.tooltip(
                rx.text(
                    att.name,
                    size="1",
                    color=COLORS["text_primary"],
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                ),
                content=att.name,
            ),
            flex="1",
            min_width="0",
            overflow="hidden",
        ),
        rx.icon_button(
            rx.icon("cloud-download", size=12),
            variant="ghost",
            size="1",
            cursor="pointer",
            color=COLORS["text_secondary"],
            _hover={"color": COLORS["text_primary"]},
            on_click=ChatState.download_attachment(att.file_no),
            flex_shrink="0",
        ),
        align="center",
        width="100%",
        height="32px",
        padding="0.35em 0.5em",
        gap="0.5em",
        border_radius="4px",
        _hover={"bg": COLORS["sidebar_hover"]},
    )


def _attachment_popover() -> rx.Component:
    """첨부파일 팝오버 버튼 (파일 있을 때만 표시)."""
    return rx.cond(
        ChatState.has_conversation_attachments,
        rx.popover.root(
            rx.popover.trigger(
                rx.button(
                    rx.icon("paperclip", size=14),
                    rx.text(
                        ChatState.conversation_attachment_count,
                        size="1",
                        weight="bold",
                    ),
                    variant="ghost",
                    size="1",
                    cursor="pointer",
                    color=COLORS["text_secondary"],
                    _hover={"color": COLORS["text_primary"]},
                    gap="0.2em",
                ),
            ),
            rx.popover.content(
                rx.vstack(
                    rx.text(
                        "첨부파일",
                        size="2",
                        weight="medium",
                        color=COLORS["text_primary"],
                    ),
                    rx.separator(size="4"),
                    rx.vstack(
                        rx.foreach(
                            ChatState.conversation_attachments,
                            _attachment_item,
                        ),
                        spacing="0",
                        width="100%",
                        max_height="240px",
                        overflow_y="auto",
                    ),
                    spacing="2",
                    width="260px",
                ),
                side="bottom",
                align="end",
            ),
        ),
    )


def _color_mode_toggle() -> rx.Component:
    """다크/라이트 모드 전환 버튼."""
    return rx.icon_button(
        rx.color_mode_cond(
            light=rx.icon("moon", size=16),
            dark=rx.icon("sun", size=16),
        ),
        variant="ghost",
        size="1",
        cursor="pointer",
        on_click=rx.toggle_color_mode,
        color=COLORS["text_secondary"],
        _hover={"color": COLORS["text_primary"]},
    )


def chat_gnb() -> rx.Component:
    """채팅 영역 GNB."""
    return rx.hstack(
        rx.box(
            _chat_mode_dropdown(),
            flex="1",
            display="flex",
            align_items="center",
        ),
        rx.box(
            _conversation_title(),
            flex="1",
            display="flex",
            justify_content="center",
            align_items="center",
        ),
        rx.hstack(
            _attachment_popover(),
            _color_mode_toggle(),
            flex="1",
            justify="end",
            align="center",
            gap="0.25em",
        ),
        width="100%",
        height=SPACING["gnb_height"],
        padding_x="1em",
        align="center",
        border_bottom=f"1px solid {COLORS['border']}",
        bg=COLORS["main_bg"],
        position="relative",
        z_index="10",
        flex_shrink="0",
    )
