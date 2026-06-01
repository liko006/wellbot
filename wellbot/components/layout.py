"""2단 레이아웃 컴포넌트.

ChatGPT 스타일 레이아웃.
Sidebar(좌, 접이식) + 메인 대화 영역(우).
"""

import reflex as rx

from wellbot.components.search_modal import search_modal
from wellbot.components.sidebar.sidebar import sidebar
from wellbot.styles import COLORS


def chat_layout(main_content: rx.Component) -> rx.Component:
    """2단 레이아웃: Sidebar(접이식) + 메인 영역"""
    return rx.fragment(
        rx.hstack(
            sidebar(),
            rx.box(
                main_content,
                flex="1",
                height="100vh",
                overflow="hidden",
                bg=COLORS["main_bg"],
                position="relative",
            ),
            height="100vh",
            width="100%",
            spacing="0",
            overflow="hidden",
            bg=COLORS["main_bg"],
        ),
        search_modal(),
    )
