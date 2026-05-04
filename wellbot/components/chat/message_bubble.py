"""메시지 버블 컴포넌트.

사용자: 우측 정렬, 둥근 배경 버블.
AI: 좌측 정렬, 배경 없이 마크다운 렌더링.
첨부파일은 GNB 팝오버에서 대화 단위로 표시.
"""

import reflex as rx

from wellbot.state.chat_state import ChatState, Message
from wellbot.styles import COLORS, MARKDOWN_COMPONENT_MAP, SPACING


def user_message(message: Message) -> rx.Component:
    """사용자 메시지 - 우측 정렬, 둥근 버블."""
    return rx.hstack(
        rx.spacer(),
        rx.box(
            rx.text(
                message.content,
                size="3",
                color=COLORS["text_primary"],
                white_space="pre-wrap",
                word_break="break-word",
            ),
            bg=COLORS["user_bubble"],
            padding="0.75em 1.25em",
            border_radius=SPACING["border_radius"],
            max_width="70%",
        ),
        class_name="chat-msg",
        width="100%",
        justify="end",
        padding_x="1em",
    )


def _action_icon(icon: str, on_click: object, tooltip: str = "") -> rx.Component:
    """AI 메시지 하단 액션 아이콘 버튼."""
    return rx.tooltip(
        rx.el.button(
            rx.icon(icon, size=15),
            on_click=on_click,
            background="transparent",
            border="none",
            cursor="pointer",
            color=str(COLORS["text_secondary"]),
            padding="0.3em",
            border_radius="4px",
            display="flex",
            align_items="center",
            _hover={
                "color": str(COLORS["text_primary"]),
                "background": str(COLORS["sidebar_hover"]),
            },
        ),
        content=tooltip,
    )


def _ai_message_actions(message: Message) -> rx.Component:
    """AI 메시지 하단 액션 버튼 바."""
    return rx.hstack(
        _action_icon(
            "copy",
            on_click=rx.set_clipboard(message.content),  # type: ignore
            tooltip="응답 복사",
        ),
        gap="0.25em",
        padding_top="0.25em",
    )


def ai_message(message: Message) -> rx.Component:
    """AI 메시지 - 좌측 정렬, 마크다운 렌더링."""
    return rx.box(
        rx.markdown(
            message.content,
            component_map=MARKDOWN_COMPONENT_MAP,
        ),
        _ai_message_actions(message),
        class_name="chat-msg",
        width="100%",
        color=COLORS["text_primary"],
        padding_x="1em",
    )


def message_bubble(message: Message) -> rx.Component:
    """개별 메시지 - 역할에 따라 분기."""
    return rx.cond(
        message.role == "user",
        user_message(message),
        ai_message(message),
    )
