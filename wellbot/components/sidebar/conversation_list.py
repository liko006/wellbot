"""대화 목록 컴포넌트.

활성 대화 하이라이트, 대화 전환, 삭제 기능.
"""

import reflex as rx

from wellbot.state.chat_models import ConvListItem
from wellbot.state.chat_state import ChatState
from wellbot.styles import COLORS, SPACING


def conversation_item(conv: ConvListItem) -> rx.Component:
    """개별 대화 항목. id·title 만 쓴다(목록 델타를 가볍게 유지)."""
    is_active = ChatState.on_chat_page & (
        ChatState.current_conversation_id == conv.id
    )

    return rx.hstack(
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
        rx.icon_button(
            rx.icon("trash-2", size=14),
            variant="ghost",
            size="1",
            cursor="pointer",
            on_click=ChatState.delete_conversation(conv.id),
            opacity="0",
            flex_shrink="0",
            color=COLORS["text_secondary"],
            class_name="delete-btn",
            _hover={"color": rx.color("red", 9)},
        ),
        width="100%",
        max_width="100%",
        padding_x="0.75em",
        padding_y="0.5em",
        align="center",
        spacing="2",
        cursor="pointer",
        border_radius=SPACING["border_radius_sm"],
        bg=rx.cond(is_active, COLORS["sidebar_active"], "transparent"),
        _hover={
            "bg": rx.cond(is_active, COLORS["sidebar_active"], COLORS["sidebar_hover"]),
            "& .delete-btn": {"opacity": "1"},
        },
        on_click=ChatState.switch_conversation(conv.id),
        overflow="hidden",
    )


def _load_more_button() -> rx.Component:
    """목록 하단 '더 보기'. 한 번에 CONVERSATION_LIMIT 개씩 과거 대화를 펼친다."""
    is_loading = ChatState.is_loading_more_conversations
    return rx.el.button(
        rx.cond(is_loading, "불러오는 중...", "이전 대화 더 보기"),
        on_click=ChatState.load_more_conversations,
        disabled=is_loading,
        width="100%",
        padding_x="0.75em",
        padding_y="0.5em",
        margin_top="0.25em",
        font_size="0.8rem",
        color=COLORS["text_secondary"],
        background="transparent",
        border="none",
        border_radius=SPACING["border_radius_sm"],
        cursor=rx.cond(is_loading, "default", "pointer"),
        _hover={"bg": COLORS["sidebar_hover"], "color": COLORS["text_primary"]},
    )


def conversation_list() -> rx.Component:
    """대화 목록."""
    return rx.box(
        rx.vstack(
            rx.text(
                rx.cond(ChatState.is_searching, "검색 결과", "최근 대화"),
                size="1",
                color=COLORS["category_text"],
                weight="medium",
                padding_x="0.75em",
                padding_top="0.5em",
                padding_bottom="0.25em",
            ),
            rx.cond(
                ChatState.is_searching & ~ChatState.has_search_results,
                rx.text(
                    "일치하는 대화가 없습니다.",
                    size="1",
                    color=COLORS["text_secondary"],
                    padding_x="0.75em",
                    padding_y="0.5em",
                ),
                rx.foreach(
                    ChatState.sorted_conversations,
                    conversation_item,
                ),
            ),
            # 검색 중에는 숨긴다 — 검색은 이미 불러온 목록만 훑으므로,
            # 여기서 더 불러와도 결과가 늘지 않아 오해를 준다.
            rx.cond(
                ChatState.has_more_conversations & ~ChatState.is_searching,
                _load_more_button(),
            ),
            spacing="0",
            width="100%",
        ),
        flex="1",
        overflow_y="auto",
        overflow_x="hidden",
        padding_x="0.5em",
        padding_y="0.25em",
        width="100%",
    )
