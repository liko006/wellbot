import reflex as rx
from app.states.chat_state import ChatState


def style_editor_page() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.div(
                rx.el.button(
                    "뒤로",
                    on_click=rx.redirect("/"),
                    class_name="text-xs text-neutral-400 hover:text-neutral-200 bg-[#2A2B2E] px-3 py-1.5 rounded-lg mr-3",
                ),
                rx.el.h1(
                    "작성 가이드 편집",
                    class_name="text-xl font-['Lora'] text-neutral-100",
                ),
                class_name="flex items-center mb-6",
            ),
            rx.el.div(
                rx.el.p(
                    "적용 중인 작성 가이드라인을 직접 편집할 수 있습니다.",
                    class_name="text-neutral-300 text-sm leading-relaxed",
                ),
                rx.el.p(
                    "저장하면 이후 보고서 문구 생성에 즉시 반영됩니다.",
                    class_name="text-neutral-500 text-xs mt-1",
                ),
                class_name="bg-[#2A2B2E] p-4 rounded-xl mb-6",
            ),
            rx.el.form(
                rx.el.textarea(
                    name="edited_style_input",
                    default_value=ChatState.edited_style,
                    class_name="w-full bg-[#353740] text-neutral-300 focus:outline-none resize-none text-sm p-4 rounded-xl min-h-[400px]",
                    rows=20,
                ),
                rx.el.button(
                    rx.cond(
                        ChatState.is_streaming,
                        rx.hstack(
                            rx.icon("loader-circle", size=16, class_name="animate-spin mr-2"),
                            rx.text("저장 중..."),
                        ),
                        rx.text("저장"),
                    ),
                    type="submit",
                    is_disabled=ChatState.is_streaming,
                    class_name="w-full mt-4 py-2.5 bg-[#E97055] hover:bg-[#d3654c] text-white font-medium rounded-xl transition-colors",
                ),
                on_submit=ChatState.save_edited_style,
                reset_on_submit=False,
                class_name="w-full",
            ),
            class_name="w-full max-w-2xl bg-[#202123] p-8 rounded-2xl border border-neutral-700",
        ),
        class_name="min-h-screen bg-[#202123] flex items-center justify-center px-4 font-['Inter']",
    )