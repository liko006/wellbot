"""앱 설정 로드.

config/models.yaml 에서 모델 정의를, config/prompts.yaml 에서 프롬프트 설정 로드.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from wellbot.paths import (
    CHAT_MODES_YAML,
    GREETINGS_YAML,
    MODELS_YAML,
    PROMPTS_DIR,
    PROMPTS_YAML,
)


@dataclass(frozen=True)
class ModelConfig:
    """개별 모델 설정."""

    name: str
    provider: str
    model_id: str
    context_window: int
    max_tokens: int
    temperature: float
    description: str = ""
    thinking: bool = False
    thinking_budget: int = 0
    top_p: float | None = None
    supports_vision: bool = False      # 이미지 입력 (Converse image block)
    supports_document: bool = False    # document block 입력


@dataclass(frozen=True)
class PromptTemplate:
    """시스템 프롬프트 템플릿."""

    name: str
    content: str
    description: str = ""


@dataclass(frozen=True)
class TitleConfig:
    """대화 제목 생성용 경량 모델 설정."""

    model_id: str
    max_tokens: int
    temperature: float
    system_prompt: str


@dataclass(frozen=True)
class EmbeddingConfig:
    """임베딩 모델 설정."""

    model_id: str
    dimension: int


@dataclass(frozen=True)
class AppConfig:
    """앱 전체 설정."""

    system_prompt: str
    models: tuple[ModelConfig, ...]
    prompts: tuple[PromptTemplate, ...]
    title: TitleConfig
    embedding: EmbeddingConfig
    chat_modes: tuple[ChatMode, ...] = ()

    def get_model(self, name: str) -> ModelConfig | None:
        """이름으로 모델 설정 조회. 없으면 None"""
        for m in self.models:
            if m.name == name:
                return m
        return None

    def get_prompt(self, name: str) -> PromptTemplate | None:
        """이름으로 프롬프트 템플릿 조회. 없으면 None"""
        for p in self.prompts:
            if p.name == name:
                return p
        return None

    @property
    def default_model(self) -> ModelConfig:
        """첫 번째 모델을 기본값으로 반환"""
        return self.models[0]

    @property
    def model_names(self) -> list[str]:
        """모델 이름 목록"""
        return [m.name for m in self.models]

    @property
    def prompt_names(self) -> list[str]:
        """프롬프트 템플릿 이름 목록"""
        return [p.name for p in self.prompts]

    @property
    def chat_mode_ids(self) -> list[str]:
        """채팅 모드 ID 목록"""
        return [a.id for a in self.chat_modes]

    def get_chat_mode(self, mode_id: str) -> ChatMode | None:
        """ID 로 채팅 모드 조회. 없으면 None"""
        for a in self.chat_modes:
            if a.id == mode_id:
                return a
        return None


@dataclass(frozen=True)
class ChatMode:
    """채팅 모드 설정 (채팅 UI 의 모드 선택 드롭다운)"""

    id: str
    name: str
    description: str = ""
    icon: str = "message-circle"


_config: AppConfig | None = None


def _load_prompt_config() -> tuple[str, tuple[PromptTemplate, ...]]:
    """config/prompts.yaml 과 config/prompts/ 디렉토리에서 프롬프트 로드"""
    prompt_entries: list[dict[str, str] | str] = []
    default_prompt = "default"
    if PROMPTS_YAML.exists():
        with open(PROMPTS_YAML, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        default_prompt = raw.get("default", "default")
        prompt_entries = raw.get("prompts", [])

    desc_map: dict[str, str] = {}
    order_names: list[str] = []
    for item in prompt_entries:
        if isinstance(item, dict):
            name = item.get("name", "")
            desc_map[name] = item.get("description", "")
            order_names.append(name)
        else:
            order_names.append(item)

    by_name: dict[str, PromptTemplate] = {}
    if PROMPTS_DIR.exists():
        for f in PROMPTS_DIR.glob("*.md"):
            content = f.read_text(encoding="utf-8").strip()
            by_name[f.stem] = PromptTemplate(
                name=f.stem,
                content=content,
                description=desc_map.get(f.stem, ""),
            )

    if order_names:
        ordered = []
        for name in order_names:
            if name in by_name:
                ordered.append(by_name.pop(name))
        ordered.extend(sorted(by_name.values(), key=lambda p: p.name))
        prompts = tuple(ordered)
    else:
        prompts = tuple(sorted(by_name.values(), key=lambda p: p.name))

    system_prompt = default_prompt
    for p in prompts:
        if p.name == default_prompt:
            system_prompt = p.content
            break

    return system_prompt, prompts


def _load_chat_modes() -> tuple[ChatMode, ...]:
    """config/chat_modes.yaml 에서 채팅 모드 로드"""
    if not CHAT_MODES_YAML.exists():
        return (ChatMode(id="chat", name="기본 대화", icon="message-circle"),)
    try:
        with open(CHAT_MODES_YAML, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        items = raw.get("chat_modes", [])
        return tuple(ChatMode(**item) for item in items) if items else (
            ChatMode(id="chat", name="기본 대화", icon="message-circle"),
        )
    except Exception:
        return (ChatMode(id="chat", name="기본 대화", icon="message-circle"),)


def load_config(path: Path | None = None) -> AppConfig:
    """YAML 파일에서 설정 로드"""
    if path is None:
        path = MODELS_YAML

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    models = tuple(ModelConfig(**m) for m in raw.get("models", []))
    system_prompt, prompts = _load_prompt_config()
    chat_modes = _load_chat_modes()
    title = _build_title_config(raw.get("title", {}))
    embedding = _build_embedding_config(raw.get("embedding", {}))

    return AppConfig(
        system_prompt=system_prompt,
        models=models,
        prompts=prompts,
        title=title,
        embedding=embedding,
        chat_modes=chat_modes,
    )


def _build_title_config(raw: dict) -> TitleConfig:
    """models.yaml 의 title 섹션을 TitleConfig 로 변환"""
    return TitleConfig(
        model_id=raw.get("model_id", ""),
        max_tokens=int(raw.get("max_tokens", 30)),
        temperature=float(raw.get("temperature", 0.3)),
        system_prompt=str(raw.get("system_prompt", "")).strip(),
    )


def _build_embedding_config(raw: dict) -> EmbeddingConfig:
    """models.yaml 의 embedding 섹션을 EmbeddingConfig 로 변환"""
    return EmbeddingConfig(
        model_id=raw.get("model_id", ""),
        dimension=int(raw.get("dimension", 1024)),
    )


def get_config() -> AppConfig:
    """캐싱된 앱 설정 반환"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


# ── 환영 메시지 ──

_DEFAULT_GREETING = "오늘은 무슨 이야기를 할까요?"
_greetings: tuple[str, ...] | None = None


def get_greetings() -> tuple[str, ...]:
    """캐싱된 환영 메시지 목록 반환"""
    global _greetings
    if _greetings is None:
        try:
            with open(GREETINGS_YAML, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            items = raw.get("greetings", [])
            _greetings = tuple(items) if items else (_DEFAULT_GREETING,)
        except Exception:
            _greetings = (_DEFAULT_GREETING,)
    return _greetings
