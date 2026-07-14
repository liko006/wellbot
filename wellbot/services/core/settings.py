"""앱 설정 로드.

config/models.yaml 에서 모델 정의를, config/prompts.yaml 에서 프롬프트 설정 로드.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from string import Template

import yaml

from wellbot.constants import REPORT_GENERATOR_URL
from wellbot.paths import (
    AI_SERVICES_YAML,
    GREETINGS_YAML,
    MODELS_YAML,
    PROMPTS_DIR,
    PROMPTS_YAML,
)

# AI 서비스 카탈로그 route 에서 치환 가능한 변수 (config/ai_services.yaml 의 ${...}).
# 외부 시스템 URL 처럼 환경별로 달라지는 값을 yaml 에 하드코딩하지 않기 위함.
_ROUTE_VARS = {"REPORT_GENERATOR_URL": REPORT_GENERATOR_URL}

log = logging.getLogger(__name__)


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
    # thinking 제어 방식:
    #   "manual"   — 레거시. thinking:{type:enabled, budget_tokens} (Sonnet 4.5, Opus 4.5)
    #   "adaptive" — 신형. thinking:{type:adaptive} + output_config.effort
    #                (Opus 4.6+/Sonnet 4.6+; Opus 4.7/4.8 은 manual 전송 시 400 에러)
    thinking_mode: str = "manual"
    thinking_budget: int = 0           # manual 모드 전용
    effort: str = "high"               # adaptive 모드 thinking 깊이 (low|medium|high|xhigh|max)
    top_p: float | None = None
    # Opus 4.7/4.8·Sonnet 5·Fable 5 등 신형 모델은 temperature/top_p/top_k 샘플링
    # 파라미터를 폐기(deprecated)했으며, 전송 시 ConverseStream 이 ValidationException
    # ('temperature' is deprecated for this model) 을 던진다. False 면 thinking 여부와
    # 무관하게 inferenceConfig 에 temperature/topP 를 넣지 않는다.
    supports_temperature: bool = True
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
class AIServiceConfig:
    """AI 서비스 카탈로그 항목 (/ai-services 카드)."""

    id: str
    name: str
    description: str = ""
    icon: str = "layers-plus"
    route: str = ""          # 비우면 '준비 중'으로 표시
    external: bool = False   # True 면 route 를 외부 URL 로 보고 새 redirect(is_external)
    enabled: bool = True


@dataclass(frozen=True)
class AppConfig:
    """앱 전체 설정."""

    system_prompt: str
    models: tuple[ModelConfig, ...]
    prompts: tuple[PromptTemplate, ...]
    title: TitleConfig
    embedding: EmbeddingConfig

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


_config: AppConfig | None = None


def _load_prompt_config() -> tuple[str, tuple[PromptTemplate, ...]]:
    """config/prompts.yaml 과 config/prompts/ 디렉토리에서 프롬프트 로드

    프롬프트 파일 매핑 우선순위:
        1. yaml 항목의 ``file`` 키 (예: file: general.md) — 권장.
           파일명을 ASCII 로 유지해 배포 환경 locale/인코딩 영향을 차단.
        2. 파일 stem == name 매칭 (레거시 폴백)
    yaml 에 없는 *.md 파일은 stem 을 이름으로 하여 목록 끝에 추가.
    """
    prompt_entries: list[dict[str, str] | str] = []
    default_prompt = "default"
    if PROMPTS_YAML.exists():
        with open(PROMPTS_YAML, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        default_prompt = raw.get("default", "default")
        prompt_entries = raw.get("prompts", [])

    desc_map: dict[str, str] = {}
    file_map: dict[str, str] = {}
    order_names: list[str] = []
    for item in prompt_entries:
        if isinstance(item, dict):
            name = item.get("name", "")
            desc_map[name] = item.get("description", "")
            if item.get("file"):
                file_map[name] = item["file"]
            order_names.append(name)
        else:
            order_names.append(item)

    by_name: dict[str, PromptTemplate] = {}
    consumed_files: set[str] = set()
    if PROMPTS_DIR.exists():
        # 1순위: yaml 의 file 키로 명시 매핑
        for name, fname in file_map.items():
            fpath = PROMPTS_DIR / fname
            if not fpath.exists():
                log.warning("프롬프트 파일 없음: name=%s file=%s", name, fname)
                continue
            by_name[name] = PromptTemplate(
                name=name,
                content=fpath.read_text(encoding="utf-8").strip(),
                description=desc_map.get(name, ""),
            )
            consumed_files.add(fpath.name)

        # 2순위(폴백): stem == name 매칭 + yaml 에 없는 파일 추가
        for f in PROMPTS_DIR.glob("*.md"):
            if f.name in consumed_files or f.stem in by_name:
                continue
            by_name[f.stem] = PromptTemplate(
                name=f.stem,
                content=f.read_text(encoding="utf-8").strip(),
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


def load_config(path: Path | None = None) -> AppConfig:
    """YAML 파일에서 설정 로드"""
    if path is None:
        path = MODELS_YAML

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    models = tuple(ModelConfig(**m) for m in raw.get("models", []))
    system_prompt, prompts = _load_prompt_config()
    title = _build_title_config(raw.get("title", {}))
    embedding = _build_embedding_config(raw.get("embedding", {}))

    return AppConfig(
        system_prompt=system_prompt,
        models=models,
        prompts=prompts,
        title=title,
        embedding=embedding,
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


# ── AI 서비스 카탈로그 ──

_ai_services: tuple[AIServiceConfig, ...] | None = None


def get_ai_services() -> tuple[AIServiceConfig, ...]:
    """캐싱된 AI 서비스 카탈로그 반환. config/ai_services.yaml 에서 로드."""
    global _ai_services
    if _ai_services is None:
        try:
            with open(AI_SERVICES_YAML, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            items = raw.get("ai_services", [])
            _ai_services = tuple(
                AIServiceConfig(**{
                    **item,
                    "route": Template(item.get("route", "")).safe_substitute(
                        _ROUTE_VARS
                    ),
                })
                for item in items
            )
        except Exception:
            log.warning("AI 서비스 카탈로그 로드 실패", exc_info=True)
            _ai_services = ()
    return _ai_services


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
