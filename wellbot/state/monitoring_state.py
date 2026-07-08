"""운영 로그 모니터링 화면 State.

`monitoring_service.build_dashboard()` 결과를 그대로 담아 렌더링만 한다.
로그 파싱/집계는 서비스에서 끝내므로 여기서는 IO 호출과 표시 상태만 관리.
"""

import reflex as rx

from wellbot.services.admin import monitoring_service


class MonitoringState(rx.State):
    """Admin 모니터링 탭 상태."""

    # 조회 조건 / 표시 상태
    window: str = "7d"  # 24h | 7d | all
    sub_tab: str = "failures"
    loading: bool = False
    loaded: bool = False
    error: str = ""
    ref_time: str = ""
    source_info: str = ""
    has_data: bool = False

    # 실패 피드 drill-down 모달
    detail_open: bool = False
    detail: dict = {}

    # 집계 결과 (모두 표시용 dict/list)
    overview_cards: list[dict] = []
    fail_cards: list[dict] = []
    fail_feed: list[dict] = []
    ingest_cards: list[dict] = []
    ingest_feed: list[dict] = []
    model_rows: list[dict] = []
    convo_rows: list[dict] = []
    rag_cards: list[dict] = []
    auth_cards: list[dict] = []
    auth_feed: list[dict] = []

    def set_sub_tab(self, value: str):
        self.sub_tab = value

    def open_detail(self, row: dict):
        self.detail = row
        self.detail_open = True

    def close_detail(self):
        self.detail_open = False

    def set_detail_open(self, value: bool):
        self.detail_open = value

    def set_window(self, value: str):
        self.window = value
        return MonitoringState.load

    def load_if_needed(self):
        """탭 최초 진입 시 1회 로드."""
        if self.loaded or self.loading:
            return
        return MonitoringState.load

    def load(self):
        """로그를 다시 읽어 대시보드를 재계산."""
        self.loading = True
        self.error = ""
        yield  # 스피너 표시용 중간 렌더

        try:
            data = monitoring_service.build_dashboard(self.window)
        except Exception as exc:  # noqa: BLE001 - 화면에 원인 노출
            self.loading = False
            self.error = f"로그 집계 실패: {exc}"
            return

        self.has_data = data["has_data"]
        self.ref_time = data["ref_time"]
        self.source_info = data["source_info"]
        self.overview_cards = data["overview_cards"]
        self.fail_cards = data["fail_cards"]
        self.fail_feed = data["fail_feed"]
        self.ingest_cards = data["ingest_cards"]
        self.ingest_feed = data["ingest_feed"]
        self.model_rows = data["model_rows"]
        self.convo_rows = data["convo_rows"]
        self.rag_cards = data["rag_cards"]
        self.auth_cards = data["auth_cards"]
        self.auth_feed = data["auth_feed"]
        self.loaded = True
        self.loading = False
