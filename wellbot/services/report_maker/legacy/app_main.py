# app_main.py

import reflex as rx
from app.states.chat_state import ChatState
from app.components.greeting_section  import greeting_section
from app.components.input_section     import input_section
from app.pages.chat_page              import chat_page
from app.pages.style_upload_page      import style_upload_page
from app.pages.style_editor_page import style_editor_page
from app.pages.style_upload_page import style_upload_page
from app.components.sidebar import sidebar


def placeholder_page() -> rx.Component:
    return rx.el.div(
        rx.el.button(
            rx.icon("arrow-left", size=16, class_name="mr-1 text-neutral-400"),
            "Back",
            on_click=rx.redirect("/"),
            class_name="absolute top-4 left-4 flex items-center text-neutral-400 hover:text-neutral-200 bg-[#2A2B2E] px-3 py-1.5 rounded-md text-sm",
        ),
        class_name="relative flex flex-col items-center justify-center h-screen bg-[#202123] font-['Inter']",
    )

    
def index() -> rx.Component:
    return rx.cond(
        ~ChatState.session_ready & ~ChatState.is_streaming,
        rx.el.div(
            rx.el.div(
                rx.icon("sparkle", size=32, class_name="text-[#E97055] mb-4"),
                rx.el.h1(
                    "보고서 문구 작성 지원 에이전트",
                    class_name="text-2xl font-['Lora'] text-neutral-100 mb-1",
                ),
                rx.el.p(
                    "사용자 정보를 입력하고 시작하세요.",
                    class_name="text-neutral-400 text-sm mb-8",
                ),
                rx.el.div(
                    # 사용자 ID 입력
                    rx.el.label("사번", class_name="text-xs text-neutral-400 mb-1 block"),
                    rx.el.div(
                        rx.el.input(
                            placeholder="예) 2000xxxx",
                            value=ChatState.user_id,
                            on_change=ChatState.set_user_id,
                            on_key_down=ChatState.fetch_templates_on_enter,
                            class_name="w-full bg-[#353740] text-neutral-300 placeholder-neutral-500 focus:outline-none px-3 py-2 rounded-lg text-sm",
                        ),
                    rx.el.button(
                        "조회",
                        on_click=ChatState.fetch_templates,
                        class_name="ml-2 px-4 py-2 bg-[#40414F] hover:bg-[#4a4b5a] text-neutral-300 text-sm rounded-lg whitespace-nowrap shrink-0",
                    ),
                    class_name="flex items-center mb-4",
                    ),

                    rx.cond(
                        ChatState.available_templates.length() > 0,
                        rx.el.div(
                            rx.el.label("보고서 유형 선택", class_name="text-xs text-neutral-400 mb-1 block"),
                            rx.el.div(
                                rx.foreach(
                                    ChatState.available_templates,
                                    lambda t: rx.el.div(
                                        rx.el.button(
                                            t["display"],
                                            on_click=ChatState.select_template(t["display"]),
                                            class_name=rx.cond(
                                                ChatState.template_id == t,
                                                "flex-1 text-left px-3 py-2 rounded-lg text-sm bg-[#E97055] text-white",
                                                "flex-1 text-left px-3 py-2 rounded-lg text-sm bg-[#353740] text-neutral-300 hover:bg-[#40414F]",
                                            ),
                                        ),
                                        rx.el.button(
                                            rx.icon("trash-2", size=14),
                                            on_click=ChatState.delete_template(t["id"]),
                                            class_name="ml-2 px-2 py-2 text-neutral-500 hover:text-red-400 rounded-lg",
                                        ),
                                        class_name="flex items-center mb-1",
                                    ),
                                ),
                                rx.el.button(
                                    "+ 보고서 유형 추가",
                                    on_click=ChatState.toggle_new_template_input,
                                    class_name="w-fit text-left px-3 py-2 rounded-lg text-sm border border-dashed border-neutral-500 text-neutral-300 bg-transparent hover:border-[#E97055] hover:text-[#E97055] transition-colors"
                                ),
                                class_name="mb-4",
                            ),
                        ),
                        rx.el.div(),
                    ),

                    rx.cond(
                        ChatState.show_new_template_input,
                        rx.el.div(                           
                            rx.el.label(
                                rx.el.span(
                                    "보고서 유형명",
                                    class_name="text-xs text-neutral-400 mb-1 block",
                                ),
                                class_name="block mb-2",
                            ),
                        
                            rx.el.input(
                                placeholder="예) 먼슬리",
                                default_value=ChatState.template_id,
                                on_change=ChatState.set_template_id,          
                                on_key_down=ChatState.start_session_on_enter,
                                class_name="w-full bg-[#353740] text-neutral-300 placeholder-neutral-500 focus:outline-none px-3 py-2 rounded-lg text-sm mb-4",
                            ),
                        ),
                        rx.el.div(),
                    ),

                    rx.el.button(
                        "시작하기",
                        on_click=ChatState.start_session,
                        class_name="w-full py-2.5 bg-[#E97055] hover:bg-[#d3654c] text-white font-medium rounded-xl transition-colors",
                    ),
                    class_name="w-full",
                ),
                class_name="w-full max-w-sm bg-[#2A2B2E] p-8 rounded-2xl border border-neutral-700 flex flex-col items-center",
            ),
            class_name="min-h-screen bg-[#202123] flex items-center justify-center px-4 font-['Inter']",
        ),

        rx.cond(
            ChatState.is_streaming & ~ChatState.session_ready,
            rx.el.div(
                rx.icon("loader-circle", size=32, class_name="text-[#E97055] animate-spin mb-4"),
                rx.el.p(
                    "스타일 로드 중...",
                    class_name="text-neutral-400 text-sm",
                ),
                class_name="min-h-screen bg-[#202123] flex flex-col items-center justify-center font-['Inter']",
            ),


            rx.el.div(
                sidebar(),
                rx.el.div(
                    rx.el.main(
                          
                        greeting_section(),
                        input_section(),
                        # ── 한 줄 요약 + 가이드 토글 ──
                        rx.el.div(
                                                        rx.el.p(
                                "주제·목적·배경·진행 경과·기대효과·향후 계획 등을 적어주시면 보고서 초안을 만들어 드립니다.",
                                class_name="text-sm text-gray-400 mt-1 text-center",
                            ),
                            rx.el.button(
                                rx.cond(
                                    ChatState.show_guide,
                                    "상세 작성 가이드 접기 ▴",
                                    "상세 작성 가이드 보기 ▾",
                                ),
                                on_click=ChatState.toggle_guide,
                                class_name="text-base font-medium text-gray-300 hover:text-white mt-3 px-4 py-2 rounded-lg border border-gray-600 cursor-pointer",
                            ),

                            # ── 펼쳤을 때만 보이는 6개 항목 ──
                            rx.cond(
                                ChatState.show_guide,
                                rx.el.div(
                                    rx.el.p("1. 어떤 주제인가요?  (보고서로 다루려는 과제·사안)"),
                                    rx.el.p("2. 보고서의 목적은?  (의사결정·지원 요청 / 현황 공유 / 성과 보고)"),
                                    rx.el.p("3. 왜 하는 업무인가요?  (배경- 왜 중요한가, 어떤 문제·기회인가)"),
                                    rx.el.p("4. 진행 경과는?  (진행한 일, 검증 결과, 수치, 일정)"),
                                    rx.el.p("5. 기대효과는?  (회사에 어떤 가치인가? - 리스크 감소, 상담 콜수 감소)"),
                                    rx.el.p("6. 향후 계획과 (필요한 의사결정은?  다음 단계 + 결정·지원할 사항)"),
                                    rx.el.p(" * 모르는 항목은 비워두셔도 됩니다. PPT·PDF·이미지도 업로드할 수 있습니다."),
                                    class_name="bg-neutral-700 rounded-xl p-4 text-sm text-gray-200 mt-3 space-y-1 max-w-xl mx-auto text-left",
                                ),
                            ),
                            class_name="flex flex-col items-center max-w-xl mx-auto",
                        ),
                        class_name="flex flex-col items-center justify-center grow w-full max-w-2xl px-4 space-y-6 mt-[-5vh]",
                    ),
                    class_name="flex-1 flex flex-col items-center bg-[#202123] min-h-screen text-neutral-200 font-['Inter'] selection:bg-[#E97055] selection:text-white",
                ),
                class_name="flex h-screen bg-[#202123]",
            ),
        ),
    )

def chat_page_protected() -> rx.Component:
    """chat 페이지 진입 시 session 및 필수 정보 확인"""
    return rx.cond(
        ChatState.session_ready & (ChatState.user_id != "") & (ChatState.template_id != ""),
        chat_page(),
        rx.script("window.location.href = '/';"),
    )

app = rx.App(
    theme=rx.theme(appearance="light"),
    stylesheets=[
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap",
        "https://fonts.googleapis.com/css2?family=Lora:wght@400;500;700&display=swap",
    ],
)
app.add_page(index, route="/")
app.add_page(chat_page_protected, route="/chat")
app.add_page(style_upload_page, route="/style-upload")
app.add_page(placeholder_page, route="/rag/chat")
app.add_page(placeholder_page, route="/summary/chat")
app.add_page(style_editor_page, route="/style_editor")
app.add_page(style_upload_page, route="/style-upload")