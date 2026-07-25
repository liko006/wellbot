# components/chat_input_bar.py

import reflex as rx
from app.states.chat_state import ChatState


def chat_input_bar() -> rx.Component:
    return rx.el.div(
        rx.el.form(
            rx.el.div(
                rx.cond(
                    ChatState.uploaded_file_name != "",
                    rx.el.div(
                        rx.el.span(
                            ChatState.uploaded_file_name,
                            class_name="text-neutral-300 text-xs",
                        ),
                        rx.el.button(
                            "✕",
                            on_click=ChatState.clear_uploaded_file,
                            class_name="ml-2 text-neutral-500 hover:text-neutral-300 text-xs",
                        ),
                        class_name="flex items-center px-3 py-1.5 bg-[#2A2B2E] rounded-lg mb-2",
                    ),
                    rx.el.div(),
                ),
                
                rx.el.textarea(
                    name="chat_page_prompt_input",
                    placeholder="주제 또는 관련 텍스트를 입력하세요...",
                    class_name="w-full bg-transparent text-neutral-300 placeholder-neutral-500 focus:outline-none resize-none text-base p-3 min-h-[60px]",
                    rows=2,
                    enter_key_submit=True,
                ),
                rx.el.div(
                    rx.el.div(
                        rx.upload(
                            rx.el.button(
                                rx.icon("paperclip", size=16, class_name="text-neutral-400"),
                                type="button",
                                style={
                                    "padding": "6px 8px",
                                    "background": "#40414F",
                                    "border_radius": "6px",
                                    "cursor": "pointer",
                                },
                            ),
                            id="upload_chat",
                            accept={
                                "application/pdf": [".pdf"],
                                "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
                                "image/*": [".jpg", ".jpeg", ".png"],
                            },
                            on_drop=ChatState.handle_file_upload(rx.upload_files(upload_id="upload_chat")),
                            width="auto",
                            height="auto",
                            border="none",
                            padding="0",
                        ),
                        class_name="flex items-center",
                    ),
                    rx.el.button(
                        rx.cond(
                            ChatState.is_streaming,
                            rx.icon("loader-circle", size=18, class_name="text-white animate-spin"),
                            rx.icon("arrow-up", size=18, class_name="text-white"),
                        ),
                        type="submit",
                        style={
                            "padding": "8px",
                            "background": "#E97055",
                            "border_radius": "6px",
                        },
                        is_disabled=ChatState.is_streaming,
                    ),
                    class_name="flex items-center justify-between p-2 pt-0",
                ),
                class_name="bg-[#353740] rounded-xl shadow-lg w-full flex flex-col",
            ),
            on_submit=ChatState.send_chat_page_message,
            reset_on_submit=True,
            class_name="w-full max-w-4xl mx-auto px-4",
        ),
        rx.el.p(
            "Claude can make mistakes. Please double-check responses.",
            class_name="text-xs text-neutral-500 text-center mt-2",
        ),
        class_name="sticky bottom-0 bg-[#202123] pt-2 pb-4 border-t border-neutral-700",
    )