# components/input_section.py

import reflex as rx
from app.states.chat_state import ChatState


def input_section() -> rx.Component:
    return rx.el.form(
        rx.el.div(
            rx.el.textarea(
                name="prompt_input",
                placeholder="주제 또는 관련 텍스트를 입력하세요...",
                class_name="w-full bg-transparent text-neutral-300 placeholder-neutral-500 focus:outline-none resize-none text-lg p-4 min-h-[80px]",
                rows=3,
                enter_key_submit=True,
            ),
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
                    id="upload_home",
                    accept={
                        "application/pdf": [".pdf"],
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
                        "image/*": [".jpg", ".jpeg", ".png"],
                    },
                    multiple=True,
                    on_drop=ChatState.handle_file_upload(rx.upload_files(upload_id="upload_home")),
                    width="auto",
                    height="auto",
                    border="none",
                    padding="0",
                ),
                rx.el.button(
                    rx.icon("arrow-up", size=18, class_name="text-white"),
                    type="submit",
                    style={
                        "padding": "8px",
                        "background": "#E97055",
                        "border_radius": "6px",
                        "margin_left": "auto",
                    },
                    is_disabled=ChatState.is_streaming,
                ),
                style={
                    "display": "flex",
                    "align_items": "center",
                    "padding": "8px",
                },
            ),
            class_name="bg-[#353740] rounded-xl shadow-lg w-full flex flex-col",
        ),
        rx.cond(
            ChatState.upload_status != "",
            rx.el.p(
                ChatState.upload_status,
                class_name="text-xs text-neutral-400 mt-2 text-center",
            ),
            rx.el.div(),
        ),
        on_submit=ChatState.send_initial_message_and_navigate,
        reset_on_submit=True,
        class_name="w-full",
    )