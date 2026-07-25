import reflex as rx
from app.states.chat_state import ChatState


def style_upload_component() -> rx.Component:
    return rx.el.form(
        rx.upload(
            rx.el.button(
                "파일 선택 (PPTX / PDF)",
                type="button",
                style={
                    "padding": "12px 24px",
                    "background": "#40414F",
                    "border_radius": "8px",
                    "cursor": "pointer",
                    "color": "#d1d5db",
                    "width": "100%",
                },
            ),
            id="upload_style",
            accept={
                "application/pdf": [".pdf"],
                "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
            },
            multiple=True,
            on_drop=ChatState.handle_style_upload(rx.upload_files(upload_id="upload_style")),
            width="auto",
            height="auto",
            border="none",
            padding="0",
        ),
    )


def style_upload_page() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.div(
                rx.el.button(
                    "Back",
                    on_click=rx.redirect("/"),
                    class_name="text-xs text-neutral-400 hover:text-neutral-200 bg-[#2A2B2E] px-3 py-1.5 rounded-lg mr-3",
                ),
                rx.el.h1(
                    "참고 문서 업로드",
                    class_name="text-xl font-['Lora'] text-neutral-100",
                ),
                class_name="flex items-center mb-6",
            ),
            rx.el.div(
                rx.el.p(
                    "PPTX, PDF 파일을 업로드 하세요.",
                    class_name="text-neutral-300 text-sm leading-relaxed",
                ),
                class_name="p-4 rounded-xl mb-6",
            ),
            rx.cond(
                ChatState.style_upload_status != "",
                rx.el.div(
                    rx.el.p(
                        ChatState.style_upload_status,
                        class_name="text-neutral-300 text-sm",
                    ),
                    class_name="bg-[#2A2B2E] p-4 rounded-xl mb-4",
                ),
                rx.el.div(),
            ),
            style_upload_component(),
            rx.el.button(
                rx.cond(
                    ChatState.is_streaming,
                    rx.hstack(
                        rx.icon("loader-circle", size=16, class_name="animate-spin mr-2"),
                        rx.text("Analyzing..."),
                        justify ="center",
                        align="center",
                        width="100%",
                    ),
                    rx.text("종료"),
                ),
                type="button",
                on_click=rx.redirect("/"),
                is_disabled=ChatState.is_streaming,
                class_name="w-full mt-4 py-2.5 bg-[#E97055] hover:bg-[#d3654c] text-white font-medium rounded-xl transition-colors",
            ),
            class_name="w-full max-w-lg bg-[#202123] p-8 rounded-2xl border border-neutral-700",
        ),
        class_name="min-h-screen bg-[#202123] flex items-center justify-center px-4 font-['Inter']",
    )