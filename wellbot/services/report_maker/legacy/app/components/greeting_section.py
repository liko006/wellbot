# components/greeting_section.py

import reflex as rx


def greeting_section() -> rx.Component:
    return rx.el.div(
        rx.icon("sparkle", class_name="text-[#E97055] mr-3", size=25),
        rx.el.h1(
            "안녕하세요!",
            class_name="text-4xl font-['Lora'] text-neutral-100",
        ),
        rx.el.p(
            "주제를 입력하거나 문서를 업로드하면,  내 스타일에 맞는 보고서 문구를 생성합니다.",
            class_name="text-neutral-400 text-sm mt-2 text-center",
        ),
        class_name="flex flex-col items-center justify-center",
    )
