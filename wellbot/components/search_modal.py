"""채팅 검색 모달 컴포넌트.

중앙 오버레이 팝업으로 대화를 검색하고 선택.
"""

import reflex as rx

from wellbot.state.chat_models import Conversation
from wellbot.state.chat_state import ChatState
from wellbot.state.ui_state import UIState
from wellbot.styles import COLORS, SPACING

_ICON_SIZE = 18
_ICON_BOX = "36px"


def _search_result_item(conv: Conversation) -> rx.Component:
    """검색 결과 개별 대화 항목."""
    is_active = ChatState.current_conversation_id == conv.id

    return rx.hstack(
        rx.icon(
            "message-circle",
            size=_ICON_SIZE,
            color=COLORS["text_secondary"],
            flex_shrink="0",
        ),
        rx.text(
            conv.title,
            size="2",
            color=rx.cond(is_active, COLORS["text_primary"], COLORS["text_secondary"]),
            weight=rx.cond(is_active, "medium", "regular"),
            overflow="hidden",
            text_overflow="ellipsis",
            white_space="nowrap",
            min_width="0",
            flex="1",
        ),
        width="100%",
        padding_x="1em",
        padding_y="0.5em",
        align="center",
        spacing="2",
        cursor="pointer",
        bg=rx.cond(is_active, COLORS["sidebar_active"], "transparent"),
        _hover={
            "bg": rx.cond(
                is_active, COLORS["sidebar_active"], COLORS["sidebar_hover"]
            ),
        },
        on_click=[
            ChatState.switch_conversation(conv.id),
            UIState.close_search,
        ],
        overflow="hidden",
    )


def _search_results() -> rx.Component:
    """검색 결과 목록."""
    return rx.vstack(
        rx.cond(
            ChatState.is_searching,
            # ── 검색어가 있을 때 ──
            rx.fragment(
                rx.text(
                    "검색 결과",
                    size="1",
                    color=COLORS["category_text"],
                    weight="medium",
                    padding_x="1em",
                    padding_top="0.25em",
                ),
                rx.cond(
                    ChatState.has_search_results,
                    rx.vstack(
                        rx.foreach(
                            ChatState.sorted_conversations,
                            _search_result_item,
                        ),
                        spacing="0",
                        width="100%",
                    ),
                    rx.text(
                        "일치하는 대화가 없습니다.",
                        size="2",
                        color=COLORS["text_secondary"],
                        padding_x="1em",
                        padding_y="0.75em",
                    ),
                ),
            ),
            # ── 검색어 없을 때: 최근 대화 ──
            rx.fragment(
                rx.text(
                    "최근 대화",
                    size="1",
                    color=COLORS["category_text"],
                    weight="medium",
                    padding_x="1em",
                    padding_top="0.25em",
                ),
                rx.vstack(
                    rx.foreach(
                        ChatState.sorted_conversations,
                        _search_result_item,
                    ),
                    spacing="0",
                    width="100%",
                ),
            ),
        ),
        spacing="0",
        width="100%",
    )


def search_modal() -> rx.Component:
    """채팅 검색 모달 - 화면 중앙에 오버레이 팝업"""
    return rx.cond(
        UIState.show_search_modal,
        rx.box(
            rx.box(
                position="fixed",
                top="0",
                left="0",
                width="100vw",
                height="100vh",
                bg="rgba(0, 0, 0, 0.5)",
                z_index="999",
                on_click=[
                    ChatState.clear_search_query,
                    UIState.close_search,
                ],
            ),
            rx.box(
                rx.vstack(
                    rx.hstack(
                        rx.icon(
                            "search",
                            size=_ICON_SIZE,
                            color=COLORS["text_secondary"],
                            flex_shrink="0",
                        ),
                        rx.el.input(
                            placeholder="채팅 검색...",
                            value=ChatState.search_query,
                            on_change=ChatState.set_search_query,
                            auto_focus=True,
                            style={
                                "flex": "1",
                                "background": "transparent",
                                "border": "none",
                                "box_shadow": "none",
                                "outline": "none",
                                "color": str(COLORS["text_primary"]),
                                "font_size": "0.875rem",
                                "padding": "0",
                                "min_width": "0",
                            },
                        ),
                        rx.box(
                            rx.icon("x", size=_ICON_SIZE),
                            display="flex",
                            align_items="center",
                            justify_content="center",
                            width=_ICON_BOX,
                            height=_ICON_BOX,
                            border_radius="8px",
                            cursor="pointer",
                            color=COLORS["text_secondary"],
                            flex_shrink="0",
                            _hover={
                                "bg": COLORS["sidebar_hover"],
                                "color": COLORS["text_primary"],
                            },
                            on_click=[
                                ChatState.clear_search_query,
                                UIState.close_search,
                            ],
                        ),
                        width="100%",
                        align="center",
                        spacing="3",
                        padding_x="1em",
                        padding_y="0.625em",
                        border_bottom=f"1px solid {COLORS['border']}",
                    ),
                    rx.box(
                        _search_results(),
                        flex="1",
                        overflow_y="auto",
                        overflow_x="hidden",
                        padding_y="0.25em",
                        max_height="400px",
                        width="100%",
                    ),
                    spacing="0",
                    width="100%",
                ),
                position="fixed",
                top="50%",
                left="50%",
                transform="translate(-50%, -50%)",
                width="min(560px, 90vw)",
                max_height="500px",
                bg=COLORS["sidebar_bg"],
                border=f"1px solid {COLORS['border']}",
                border_radius="12px",
                z_index="1000",
                overflow="hidden",
                box_shadow="0 16px 48px rgba(0, 0, 0, 0.3)",
                on_click=UIState.noop,
            ),
        ),
    )
