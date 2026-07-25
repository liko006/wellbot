# components/header_section.py

import reflex as rx
from app.states.chat_state import ChatState

def header_section() -> rx.Component:
    return rx.el.div(
        # 좌측: 서비스 명
        rx.el.div(
            rx.icon("sparkle", size=16, class_name="text-[#E97055] mr-1"),
            rx.el.span(
                "보고서 문구 작성 지원 에이전트",
                class_name="text-sm text-neutral-300 font-medium",
            ),
            class_name="flex items-center",
        ),
        class_name="w-full flex items-center justify-between px-4 py-3",
    )