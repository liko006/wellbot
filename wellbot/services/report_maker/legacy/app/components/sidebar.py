# app/components/sidebar.py

import reflex as rx
from app.states.chat_state import ChatState


def sidebar() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.icon("sparkle", size=16, class_name="text-[#E97055] mr-2"),
            rx.el.span(
                "보고서 문구 작성 지원 에이전트",
                class_name="text-neutral-200 text-sm font-medium",
            ),
            class_name="flex items-center px-4 border-b border-neutral-700 h-14",
        ),

        # ① 상단 (새 대화, 스타일 학습, 스타일 편집, 템플릿 변경)
        rx.el.div(
            rx.el.button(
                rx.icon("plus", size=16),
                "새 대화",
                on_click=ChatState.start_new_chat,
                class_name="w-full py-1.5 bg-[#E97055] hover:bg-[#d3654c] text-white text-xs rounded-lg transition-colors flex items-center justify-center gap-2",
            ),
            rx.el.button(
                rx.icon("upload", size=16),
                "참고 문서 등록",
                on_click=rx.redirect("/style-upload"),
                class_name="w-full py-1.5 bg-[#353740] hover:bg-[#3a3b3e] text-neutral-300 text-xs rounded-lg transition-colors flex items-center justify-center gap-2",
            ),
            rx.el.button(
                rx.icon("edit", size=16),
                "작성 가이드 편집",
                on_click=ChatState.open_style_editor,
                class_name="w-full py-1.5 bg-[#353740] hover:bg-[#3a3b3e] text-neutral-300 text-xs rounded-lg transition-colors flex items-center justify-center gap-2",
            ),


            # 템플릿 변경 버튼
            rx.el.div(
                rx.el.button(
                    rx.icon("layout-template", size=16),
                    "보고서 유형 변경",
                    on_click=ChatState.toggle_user_menu,
                    class_name="w-full py-1.5 bg-[#353740] hover:bg-[#3a3b3e] text-neutral-300 text-xs rounded-lg transition-colors flex items-center justify-center gap-2",
                ),
            
                rx.cond(
                    ChatState.show_user_menu,
                    rx.fragment(
                        # 다른 곳 클릭 시 닫히는 투명 overlay
                        rx.el.div(
                            on_click=ChatState.close_user_menu,
                            class_name="fixed inset-0 z-40",
                        ),
            
                        # 오른쪽 템플릿 선택 창
                        rx.el.div(
                            rx.el.div(
                                rx.el.p(
                                    "보고서 유형 선택",
                                    class_name="text-xs text-neutral-400 px-3 py-2 border-b border-neutral-700",
                                ),
                                rx.foreach(
                                    ChatState.template_options,
                                    lambda t: rx.el.button(
                                        rx.el.div(
                                            rx.el.span(
                                                t["display"],
                                                class_name="text-xs text-neutral-300",
                                            ),
                                            rx.cond(
                                                t["id"] == ChatState.template_id,
                                                rx.icon(
                                                    "check",
                                                    size=14,
                                                    class_name="text-[#E97055]",
                                                ),
                                                rx.el.span(""),
                                            ),
                                            class_name="flex items-center justify-between w-full",
                                        ),
                                        # 템플릿 선택 시 select_template 내부에서 자동 닫힘
                                        on_click=ChatState.select_template(t["display"]),
                                        class_name=rx.cond(
                                            t == ChatState.template_id,
                                            "w-full text-left px-3 py-2 bg-[#353740] rounded text-xs",
                                            "w-full text-left px-3 py-2 hover:bg-[#353740] rounded text-xs",
                                        ),
                                    ),
                                ),
                                class_name="w-48 bg-[#2A2B2E] border border-neutral-700 rounded-lg p-1 shadow-lg",
                            ),
                            class_name="absolute left-full top-0 ml-2 z-50",
                        ),
                    ),
                    rx.el.div(),
                ),
            
                class_name="relative",
            ),
            class_name="p-2 border-b border-neutral-700 space-y-2",
        ),

        # ② 중단 (대화 목록)
        rx.el.div(
            rx.el.p(ChatState.recent_chats_label, class_name="text-xs text-neutral-500 px-4 py-2"),
            rx.foreach(
                ChatState.conversation_list,
                lambda conv: rx.el.div(
                    rx.el.div(
                        rx.el.input(
                            default_value=conv.title,
                            on_blur=lambda e: ChatState.rename_conversation(
                                conv.session_id, e
                            ),
                            class_name="text-sm text-neutral-300 truncate flex-1 bg-transparent focus:outline-none focus:border-b focus:border-neutral-500",
                        ),
                        rx.el.button(
                            rx.icon(
                                "trash-2",
                                size=12,
                                class_name="text-neutral-500 hover:text-red-400",
                            ),
                            on_click=ChatState.delete_conversation_by_id(
                                conv.session_id
                            ),
                            class_name="p-1 rounded hover:bg-[#353740]",
                        ),
                        class_name="flex items-center gap-2",
                    ),
                    rx.el.p(
                        conv.saved_at,
                        class_name="text-xs text-neutral-500 mt-0.5",
                    ),
                    on_click=ChatState.load_conversation_by_id(conv.session_id),
                    class_name=rx.cond(
                        conv.session_id == ChatState.session_id,
                        "px-4 py-2.5 bg-[#2A2B2E] border-l-2 border-[#E97055] cursor-pointer transition-colors",
                        "px-4 py-2.5 hover:bg-[#2A2B2E] cursor-pointer transition-colors",
                    ),
                    key=conv.session_id,
                ),
            ),
            class_name="flex-1 overflow-y-auto",
        ),

        # ③ 맨 밑 (로그인 정보 + 로그아웃)
        rx.el.div(
            rx.el.div(
                rx.icon("user", size=16),
                rx.el.div(
                    rx.el.span(ChatState.user_id, class_name="text-xs block"),
                    rx.el.span(
                        ChatState.template_id,
                        class_name="text-xs text-neutral-500 block",
                    ),
                    class_name="ml-2",
                ),
                class_name="w-full flex items-center px-3 py-2 bg-[#2A2B2E] rounded-lg",
            ),
            rx.el.button(
                "로그아웃",
                on_click=ChatState.logout,
                class_name="w-full text-left px-3 py-1.5 text-xs text-neutral-300 hover:bg-[#353740] rounded-lg mt-2",
            ),
            class_name="p-3 border-t border-neutral-700 relative",
        ),

        class_name="w-64 h-screen bg-[#202123] flex flex-col border-r border-neutral-700 text-neutral-200 relative",
    )