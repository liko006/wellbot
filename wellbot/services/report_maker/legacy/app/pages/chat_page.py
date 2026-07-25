# pages/chat_page.py

import reflex as rx
from app.states.chat_state import ChatState
from app.components.chat_message_bubble import chat_message_bubble_component
from app.components.chat_input_bar import chat_input_bar
from app.components.sidebar import sidebar


def _chat_page_header() -> rx.Component:
    return rx.el.div(
        rx.el.button(
            rx.icon("arrow-left", size=20, class_name="mr-1 text-neutral-300"),
            "Back",
            on_click=ChatState.go_back_and_clear_chat,
            class_name="flex items-center text-neutral-300 hover:text-neutral-100 bg-[#2A2B2E] hover:bg-[#3a3b3e] px-3 py-1.5 rounded-md text-sm font-medium",
        ),
        rx.el.div(
            rx.icon("sparkle", size=14, class_name="text-[#E97055] mr-1"),
            rx.el.span(
                "보고서 문구 작성 지원 에이전트",
                class_name="text-neutral-300 text-xs font-medium",
            ),
            class_name="ml-auto flex items-center",
        ),
        class_name="sticky top-0 z-10 flex items-center gap-3 p-3 bg-[#202123] border-b border-neutral-700 h-14",
    )


def chat_page() -> rx.Component:
    return rx.el.div(
        sidebar(),
        rx.el.div(
            _chat_page_header(),
            rx.el.div(
                rx.el.div(
                    rx.el.div(class_name="pt-4"),
                    rx.foreach(
                        ChatState.messages,
                        lambda msg, idx: chat_message_bubble_component(msg, idx),
                    ),
                    rx.cond(
                        ChatState.is_streaming,
                        rx.el.div(
                            rx.icon(
                                "loader-circle",
                                class_name="animate-spin text-[#E97055] w-6 h-6 mx-auto",
                            ),
                            class_name="py-4",
                        ),
                        rx.el.div(),
                    ),
                    rx.cond(
                        ChatState.error_message != "",
                        rx.el.div(
                            rx.el.p(
                                ChatState.error_message,
                                class_name="text-red-500 text-sm text-center bg-red-900/30 p-3 rounded-md",
                            ),
                            class_name="py-2",
                        ),
                        rx.el.div(),
                    ),
                    rx.el.div(class_name="pb-4"),
                    rx.el.div(id="chat-bottom"),
                    class_name="w-full max-w-4xl mx-auto px-4 sm:px-6 lg:px-8",
                ),
                class_name="flex-grow overflow-y-auto",
                id="chat-container",
            ),
            chat_input_bar(),
            class_name="flex-1 flex flex-col h-screen",
        ),
        class_name="flex h-screen bg-[#202123] text-neutral-200 font-['Inter'] selection:bg-[#E97055] selection:text-white",
    )