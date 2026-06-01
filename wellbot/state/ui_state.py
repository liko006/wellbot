"""UI 상태 관리 - UIState.

Sidebar 접힘/펼침 등 UI 관련 상태를 담당.
"""

import reflex as rx


class UIState(rx.State):
    """UI 관련 상태 관리"""

    sidebar_expanded: bool = True
    show_search: bool = False
    show_search_modal: bool = False

    def toggle_sidebar(self) -> None:
        """Sidebar 접힘/펼침 토글"""
        self.sidebar_expanded = not self.sidebar_expanded

    def expand_sidebar(self) -> None:
        """Sidebar 펼침"""
        self.sidebar_expanded = True

    def collapse_sidebar(self) -> None:
        """Sidebar 접힘"""
        self.sidebar_expanded = False

    def open_search(self) -> None:
        """채팅 검색 모달 열기"""
        self.show_search_modal = True

    def close_search(self) -> None:
        """채팅 검색 모달 닫기"""
        self.show_search_modal = False

    def toggle_search(self) -> None:
        """채팅 검색 모달 토글"""
        self.show_search_modal = not self.show_search_modal

    def noop(self) -> None:
        """빈 이벤트 핸들러 플레이스홀더"""
