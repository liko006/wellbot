"""메시지 표시 영역 컴포넌트.

대화 메시지 목록, 스트리밍 응답, 사고 과정 인디케이터, 환영 메시지.
자동 스크롤: 하단 근처에 있으면 새 메시지 시 자동 스크롤, 위로 스크롤하면 중단.
"""

import reflex as rx

from wellbot.components.chat.message_bubble import message_bubble
from wellbot.components.icons import north_star_icon
from wellbot.state.chat_state import ChatState
from wellbot.styles import COLORS, MARKDOWN_COMPONENT_MAP, SPACING


def welcome_message() -> rx.Component:
    """대화가 비어있을 때 표시되는 환영 화면."""
    return rx.center(
        rx.vstack(
            rx.box(
                north_star_icon(size=36),
                color=COLORS["text_primary"],
                margin_bottom="0.5em",
            ),
            rx.heading(
                ChatState.greeting_text,
                size="6",
                color=COLORS["text_primary"],
                weight="medium",
            ),
            align="center",
            spacing="2",
        ),
        flex="1",
    )


def loading_indicator() -> rx.Component:
    """AI 응답 대기 중 로딩 인디케이터 (스트리밍 시작 전)."""
    return rx.hstack(
        rx.box(
            rx.icon("message-circle-dashed", size=18, color=COLORS["accent"]),
            width="30px",
            height="30px",
            border_radius="50%",
            bg=COLORS["user_bubble"],
            display="flex",
            align_items="center",
            justify_content="center",
            flex_shrink="0",
        ),
        rx.hstack(
            rx.spinner(size="1"),
            rx.text(
                "응답 준비 중...",
                size="2",
                color=COLORS["text_secondary"],
            ),
            spacing="2",
            align="center",
        ),
        width="100%",
        align="start",
        spacing="3",
        padding_x="1em",
    )


def thinking_indicator() -> rx.Component:
    """AI 사고 과정 진행 중 인디케이터 (Extended Thinking)."""
    return rx.hstack(
        rx.box(
            rx.icon("message-circle-dashed", size=18, color=COLORS["accent"]),
            width="30px",
            height="30px",
            border_radius="50%",
            bg=COLORS["user_bubble"],
            display="flex",
            align_items="center",
            justify_content="center",
            flex_shrink="0",
        ),
        rx.hstack(
            rx.spinner(size="1"),
            rx.text(
                "깊이 생각하는 중...",
                size="2",
                color=COLORS["text_secondary"],
            ),
            spacing="2",
            align="center",
        ),
        width="100%",
        align="start",
        spacing="3",
        padding_x="1em",
    )


def streaming_message() -> rx.Component:
    """스트리밍 중인 AI 응답 표시."""
    return rx.box(
        rx.markdown(
            ChatState.streaming_content,
            component_map=MARKDOWN_COMPONENT_MAP,
        ),
        width="100%",
        color=COLORS["text_primary"],
        padding_x="1em",
    )


def _rail_button(
    icon: str, btn_id: str, tooltip: str, margin_top: str = "0"
) -> rx.Component:
    """네비게이션 레일 내 단일 원형 버튼."""
    return rx.tooltip(
        rx.el.button(
            rx.icon(icon, size=16, color=COLORS["text_secondary"]),
            id=btn_id,
            style={
                "width": "32px",
                "height": "32px",
                "border_radius": "50%",
                "background": str(COLORS["input_bg"]),
                "border": f"1px solid {COLORS['border']}",
                "display": "flex",
                "align_items": "center",
                "justify_content": "center",
                "cursor": "pointer",
                "padding": "0",
                "outline": "none",
                "transition": "opacity 0.15s ease, background 0.15s ease",
                "margin_top": margin_top,
            },
        ),
        content=tooltip,
        side="left",
    )


def navigation_rail() -> rx.Component:
    """채팅 영역 우측 사이드 네비게이션 레일.

    - 이전 메시지(↑) / 다음 메시지(↓): 메시지 단위 이동
    - 최하단(⤓): 스크롤 끝으로 이동, 바닥에 있으면 비활성 표시
    """
    return rx.box(
        _rail_button("chevron-up", "nav-prev-btn", "이전 메시지"),
        _rail_button("chevron-down", "nav-next-btn", "다음 메시지"),
        _rail_button(
            "chevrons-down", "scroll-to-bottom-btn", "최하단으로", margin_top="8px"
        ),
        id="navigation-rail",
        style={
            "position": "absolute",
            "right": "16px",
            "top": "50%",
            "transform": "translateY(-50%)",
            "display": "flex",
            "flex_direction": "column",
            "gap": "4px",
            "z_index": "10",
            "pointer_events": "auto",
        },
    )


def message_area() -> rx.Component:
    """메시지 표시 영역."""
    return rx.box(
        rx.cond(
            ChatState.has_messages,
            rx.vstack(
                rx.foreach(
                    ChatState.current_messages,
                    message_bubble,
                ),
                rx.cond(ChatState.has_streaming, streaming_message()),
                rx.cond(
                    ChatState.is_loading & ChatState.is_thinking,
                    thinking_indicator(),
                ),
                rx.cond(
                    ChatState.is_loading
                    & ~ChatState.is_thinking
                    & ~ChatState.has_streaming,
                    loading_indicator(),
                ),
                spacing="4",
                padding_y="1.5em",
                width="100%",
                max_width=SPACING["message_max_width"],
                margin_x="auto",
            ),
            welcome_message(),
        ),
        id="message-area",
        flex="1",
        overflow_y="auto",
        width="100%",
        padding_top="1em",
        position="relative",
        transition="all 0.2s ease",
    )
