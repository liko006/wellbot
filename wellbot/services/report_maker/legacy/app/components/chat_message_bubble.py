# components/chat_message_bubble.py

import reflex as rx
from app.states.chat_state import ChatState, Message


def user_message_bubble(message_content: str, file_name: str = "") -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.div("ME", class_name="flex items-center justify-center w-8 h-8 bg-neutral-700 text-neutral-300 rounded-full text-xs font-medium ml-4 shrink-0"),
            rx.el.div(
                rx.cond(                                    # ← 파일명 표시
                    file_name != "",
                    rx.el.div(file_name, class_name="text-xs text-neutral-400 mb-1"),
                ),
                rx.el.p(message_content, class_name="text-neutral-200 whitespace-pre-wrap break-words leading-relaxed leading-8"),
                class_name="bg-[#39393d] p-3 rounded-lg shadow w-fit max-w-[75%]",
            ),
            class_name="flex flex-row items-start justify-end w-full",
        ),
        class_name="w-full flex justify-end mb-6",
    )


def ai_message_bubble(message: dict, index: int) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.icon(
                "sparkle",
                class_name="text-[#E97055] w-8 h-8 mr-3 shrink-0 p-1",
            ),
            rx.el.div(
                rx.markdown(
                    message["content"],
                    class_name="bg-[#2a2b2e] text-neutral-200 prose prose-invert prose-base max-w-none prose-p:leading-8 prose-li:leading-8",
                ),
                rx.el.div(
                    rx.el.button(
                        rx.icon("copy", size=16, class_name="text-neutral-500 hover:text-neutral-300"),
                        on_click=ChatState.copy_message(index),
                    ),
                    rx.icon("thumbs-up", size=16, class_name="text-neutral-500 hover:text-neutral-300 cursor-pointer"),
                    rx.icon("thumbs-down", size=16, class_name="text-neutral-500 hover:text-neutral-300 cursor-pointer"),
                    rx.cond(
                        message["is_outline"] & ~message["is_flow"],
                        rx.cond(
                            ChatState.saving_style,
                            rx.el.span(
                                "저장 중...",
                                class_name="text-xs text-neutral-500 ml-auto",
                            ),
                            rx.cond(
                                message["style_saved"],
                                rx.el.span(
                                    "저장됨",
                                    class_name="text-xs text-neutral-500 ml-auto",
                                ),
                                rx.el.button(
                                    rx.icon("bookmark", size=12, class_name="mr-1"),
                                    "이 스타일 저장",
                                    on_click=ChatState.save_outline_style(index),
                                    class_name="flex items-center text-xs bg-[#E97055]/20 text-[#E97055] hover:bg-[#E97055]/30 border border-[#E97055]/40 px-2 py-1 rounded-md ml-auto",
                                ),
                            ),
                        ),
                        rx.el.div(),
                    ),
                    class_name="flex items-center space-x-3 mt-3",
                ),
                class_name="bg-[#2A2B2E] p-3 rounded-lg shadow flex-grow",
            ),
            class_name="flex items-start",
        ),
        class_name="w-full mb-6",
    )



def chat_message_bubble_component(message: Message, index: int) -> rx.Component:
    return rx.cond(
        message["role"] == "user",
        user_message_bubble(message["content"], message["file_name"]),
        ai_message_bubble(message, index),
    )