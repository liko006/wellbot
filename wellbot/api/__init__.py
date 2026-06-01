"""Wellbot FastAPI 커스텀 라우트.

Reflex 의 rx.App.api_transformer 에 마운트되는 독립 FastAPI 앱.
파일 업로드처럼 멀티파트 스트리밍이 필요한 HTTP 엔드포인트 제공.
"""

from wellbot.api.app import api_app

__all__ = ["api_app"]
