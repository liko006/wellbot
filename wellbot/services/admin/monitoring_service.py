"""운영 로그(JSONL) 모니터링 집계 서비스.

`logs/wellbot.log` (+ 회전 백업) 을 읽어 Admin 모니터링 화면이 그대로
렌더링할 수 있는 "표시용 dict/list" 로 집계한다. Reflex State 는 이 결과를
변수에 담기만 하면 되도록, 숫자 포매팅·색상까지 여기서 끝낸다.

로그 포맷(JsonFormatter, 1줄 JSON):
  ts, level, logger, message, emp_no, conversation_id, message_id, request_id,
  (+exception), (+extra: model, input_tokens, output_tokens, elapsed_ms,
   ttfb_ms, stop_reason, interrupted, chars, file, bytes, tokens, chunks, ...)

핵심 신호 일부(tool_loop reason, kb grounding retrieved/cited, hits, 로그인 emp_no)는
구조화 필드가 아니라 message 문자열에 있어 정규식으로 추출한다.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from wellbot.paths import LOG_DIR as DEFAULT_LOG_DIR

# ── 설정 ──────────────────────────────────────────────────────────────

LOG_FILE_NAME = "wellbot.log"
MAX_LINES = 40000  # 집계에 사용할 최신 로그 라인 상한 (메모리/시간 보호)
FEED_LIMIT = 150  # 피드 테이블 최대 행 수

WINDOW_SECONDS: dict[str, float | None] = {
    "24h": 86_400.0,
    "7d": 604_800.0,
    "all": None,
}

# Bedrock 모델별 추정 단가 (USD / 1M tokens). 대략치이며 운영 단가로 교체 가능.
_MODEL_RATES: dict[str, tuple[float, float]] = {
    "Claude Opus 4.8": (15.0, 75.0),
    "Claude Opus 4.7": (15.0, 75.0),
    "Claude Opus 4.6": (15.0, 75.0),
    "Claude Sonnet 4.5": (3.0, 15.0),
    "Amazon Nova Pro": (0.8, 3.2),
    "Amazon Nova Lite": (0.06, 0.24),
}
# 단가 미등록 모델은 추정하지 않고 "단가미정"으로 노출 (Sonnet 단가로 뭉뚱그리지 않음)
_DEFAULT_RATE = None

# 실패 카테고리 메타: 내부키 → (표시명, radix color_scheme, accent hex)
_CAT_META: dict[str, tuple[str, str, str]] = {
    "image_too_large": ("이미지 용량/크기 초과", "red", "#e5484d"),
    "embed_overflow": ("임베딩 토큰 초과", "red", "#e5484d"),
    "kb_ingest_fail": ("KB 인제스트 실패", "red", "#e5484d"),
    "stream_fail": ("응답 스트리밍 실패", "red", "#e5484d"),
    "content_filtered": ("콘텐츠 필터 차단", "orange", "#f76b15"),
    "upstage_413": ("파싱 페이지 초과", "orange", "#f76b15"),
    "upstage_timeout": ("파싱 타임아웃/5xx", "orange", "#f76b15"),
    "parse_fail": ("파싱 실패(기타)", "amber", "#ffc53d"),
    "kb_upload_fail": ("KB 업로드 오류", "amber", "#ffc53d"),
    "login_fail": ("로그인 실패", "yellow", "#ffe629"),
    "interrupted": ("응답 중단", "gray", "#8b8d98"),
    "other": ("기타 경고/에러", "gray", "#8b8d98"),
}

_ACCENT = {
    "good": "#30a46c",
    "warn": "#f76b15",
    "bad": "#e5484d",
    "info": "#0090ff",
    "neutral": "#8b8d98",
    "purple": "#8e4ec6",
}

# ── 로그 파일 로드 ────────────────────────────────────────────────────


def _log_dir() -> Path:
    return Path(os.environ.get("LOG_DIR") or DEFAULT_LOG_DIR)


def _file_index(p: Path) -> int:
    """회전 파일 정렬 키. wellbot.log=0(최신), wellbot.log.N=N(오래됨)."""
    suffix = p.suffix
    if suffix == ".log":
        return 0
    try:
        return int(suffix.lstrip("."))
    except ValueError:
        return 999


def _candidate_files() -> list[Path]:
    """존재하는 로그 파일을 최신 → 오래된 순으로 반환."""
    base = _log_dir()
    if not base.exists():
        return []
    files = [
        p
        for p in base.glob("wellbot.log*")
        if p.is_file() and (p.suffix == ".log" or p.suffix.lstrip(".").isdigit())
    ]
    files.sort(key=_file_index)  # 오름차순 = 최신 우선
    return files


def load_events(max_lines: int = MAX_LINES) -> list[dict]:
    """최신 로그부터 최대 max_lines 라인을 파싱해 시간순 정렬로 반환."""
    acc: list[str] = []
    for path in _candidate_files():  # 최신 파일부터
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = [ln for ln in text.splitlines() if ln.strip()]
        acc = lines + acc  # 앞쪽 = 더 오래된 파일
        if len(acc) >= max_lines:
            break

    events: list[dict] = []
    for line in acc[-max_lines:]:
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(ev, dict):
            continue
        ev["_epoch"] = _to_epoch(ev.get("ts"))
        events.append(ev)
    events.sort(key=lambda e: e.get("_epoch") or 0.0)
    return events


def _to_epoch(ts: object) -> float | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


# ── 포매팅 헬퍼 ───────────────────────────────────────────────────────


def _num(n: float | int) -> str:
    return f"{int(round(n)):,}"


def _ms(ms: float | int) -> str:
    if not ms:
        return "-"
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{int(ms)}ms"


def _pct(x: float) -> str:
    return f"{x:.1f}%"


def _usd(x: float) -> str:
    return f"${x:,.2f}"


def _short_ts(ts: object) -> str:
    if not isinstance(ts, str) or len(ts) < 19:
        return "-"
    return ts[5:19].replace("T", " ")  # MM-DD HH:MM:SS


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def _rate(model: str) -> tuple[float, float] | None:
    return _MODEL_RATES.get(model, _DEFAULT_RATE)


def _cost(model: str, in_tok: int, out_tok: int) -> float | None:
    """모델 단가 기반 추정 비용(USD). 단가 미등록 모델은 None(=단가미정)."""
    rate = _rate(model)
    if rate is None:
        return None
    r_in, r_out = rate
    return (in_tok / 1_000_000) * r_in + (out_tok / 1_000_000) * r_out


def _card(label: str, value: str, sub: str = "", accent: str = _ACCENT["info"]) -> dict:
    return {"label": label, "value": value, "sub": sub, "accent": accent}


def _gi(ev: dict, key: str) -> int:
    v = ev.get(key)
    return v if isinstance(v, int) else 0


# ── 메시지 정규식 추출기 ──────────────────────────────────────────────

_RE_GROUNDING = re.compile(r"retrieved=(\d+)\s+cited=(\d+)")
_RE_REASON = re.compile(r"reason=([a-zA-Z_]+)")
_RE_HITS = re.compile(r"->\s*(\d+)\s*hits")
_RE_LOGIN_OK = re.compile(r"login success: emp_no=(\S+) role=(\S+)")
_RE_LOGIN_BAD = re.compile(r"bad password emp_no=(\S+) fail_count=(\d+)")
_RE_LOGIN_UNKNOWN = re.compile(r"unknown emp_no=(\S+)")
_RE_REGISTER = re.compile(r"registered \(PENDING\): emp_no=(\S+)")
_RE_ERR_EQ = re.compile(r"err=(.+)$")


def _classify_failure(ev: dict) -> tuple[str, str, str] | None:
    """이벤트를 실패 카테고리로 분류. 실패 아니면 None.

    반환: (category_key, 표시명, color_scheme)
    """
    level = ev.get("level", "")
    logger = ev.get("logger", "")
    msg = ev.get("message", "") or ""
    exc = ev.get("exception", "") or ""
    blob = f"{msg}\n{exc}".lower()

    def meta(key: str) -> tuple[str, str, str]:
        label, color, _ = _CAT_META[key]
        return key, label, color

    if "image exceeds" in blob or "dimensions exceed" in blob:
        return meta("image_too_large")
    if ev.get("stop_reason") == "content_filtered":
        return meta("content_filtered")
    if "too many input tokens" in blob:
        if "ingestion" in blob or "knowledgebase" in logger or "kb_utils" in logger:
            return meta("kb_ingest_fail")
        return meta("embed_overflow")
    if "ingestion 실패" in msg or "bedrock ingestion" in blob:
        return meta("kb_ingest_fail")
    if "kb 처리 오류" in msg or ("kb ingestion" in blob and "롤백" in msg):
        return meta("kb_upload_fail")
    if "page limit" in blob or "413" in blob:
        return meta("upstage_413")
    if "server disconnected" in blob or "502" in blob or "bad gateway" in blob:
        return meta("upstage_timeout")
    if "chat streaming 실패" in msg or "converse_stream 호출 실패" in msg:
        return meta("stream_fail")
    if ("parse" in logger or "file_parser" in logger or "attachment" in logger) and (
        "실패" in msg or "failed" in blob
    ):
        return meta("parse_fail")
    if "login failed" in msg:
        return meta("login_fail")
    if msg == "chat response" and ev.get("interrupted") is True:
        return meta("interrupted")
    if level in ("ERROR", "CRITICAL", "WARNING"):
        return meta("other")
    return None


# ── 메인 집계 ─────────────────────────────────────────────────────────


def build_dashboard(window: str = "7d") -> dict:
    """window(24h/7d/all) 기준으로 전체 대시보드 데이터를 집계."""
    events = load_events()
    if not events:
        return _empty_dashboard()

    ref_epoch = max((e["_epoch"] for e in events if e.get("_epoch")), default=None)
    win = WINDOW_SECONDS.get(window)
    if ref_epoch is not None and win is not None:
        lo = ref_epoch - win
        scoped = [e for e in events if (e.get("_epoch") or 0) >= lo]
    else:
        scoped = events

    ref_ts = max((e.get("ts", "") for e in scoped), default="")

    return {
        "has_data": True,
        "ref_time": _short_ts(ref_ts),
        "source_info": (
            f"{LOG_FILE_NAME} 외 {max(len(_candidate_files()) - 1, 0)}개 · "
            f"이벤트 {_num(len(events))}건(범위 {_num(len(scoped))}건)"
        ),
        **_overview(scoped),
        **_failures(scoped),
        **_ingest(scoped),
        **_models(scoped),
        **_ai_services(scoped),
        **_auth(scoped),
    }


def _empty_dashboard() -> dict:
    base = Path(_log_dir()) / LOG_FILE_NAME
    return {
        "has_data": False,
        "ref_time": "-",
        "source_info": f"로그 파일 없음: {base}",
        "overview_cards": [],
        "fail_cards": [],
        "fail_feed": [],
        "ingest_cards": [],
        "ingest_feed": [],
        "model_rows": [],
        "convo_rows": [],
        "ai_cards": [],
        "ai_rows": [],
        "rag_cards": [],
        "auth_cards": [],
        "auth_feed": [],
    }


def _overview(events: list[dict]) -> dict:
    responses = [e for e in events if e.get("message") == "chat response"]
    converses = [e for e in events if e.get("message") == "bedrock converse done"]

    users = {e.get("emp_no") for e in responses if e.get("emp_no") not in (None, "-")}
    convos = {
        e.get("conversation_id")
        for e in responses
        if e.get("conversation_id") not in (None, "-")
    }

    total = len(responses)
    bad = sum(1 for e in responses if e.get("interrupted") is True or _gi(e, "chars") == 0)
    success = ((total - bad) / total * 100) if total else 0.0

    elapsed = [_gi(e, "elapsed_ms") for e in responses if _gi(e, "elapsed_ms") > 0]
    ttfb = [_gi(e, "ttfb_ms") for e in converses if _gi(e, "ttfb_ms") > 0]

    in_tok = sum(_gi(e, "input_tokens") for e in responses)
    out_tok = sum(_gi(e, "output_tokens") for e in responses)
    costs = [
        _cost(e.get("model", ""), _gi(e, "input_tokens"), _gi(e, "output_tokens"))
        for e in responses
    ]
    cost = sum(c for c in costs if c is not None)
    unpriced = sum(1 for c in costs if c is None)

    errors = sum(1 for e in events if e.get("level") in ("ERROR", "CRITICAL"))
    warns = sum(1 for e in events if e.get("level") == "WARNING")

    cards = [
        _card("활성 사용자", _num(len(users)), "고유 사원", _ACCENT["info"]),
        _card("대화 세션", _num(len(convos)), "고유 conversation", _ACCENT["info"]),
        _card("응답(턴)", _num(total), "chat response", _ACCENT["info"]),
        _card(
            "응답 성공률",
            _pct(success),
            f"중단·빈응답 {bad}건",
            _ACCENT["good"] if success >= 95 else _ACCENT["warn"] if success >= 80 else _ACCENT["bad"],
        ),
        _card("응답 지연 p50", _ms(_percentile(elapsed, 0.5)), f"p95 {_ms(_percentile(elapsed, 0.95))}", _ACCENT["neutral"]),
        _card("TTFB p95", _ms(_percentile(ttfb, 0.95)), "첫 토큰까지", _ACCENT["neutral"]),
        _card("토큰 사용", _num(in_tok + out_tok), f"in {_num(in_tok)} / out {_num(out_tok)}", _ACCENT["purple"]),
        _card(
            "추정 비용",
            _usd(cost),
            "모델 단가 추정치" if not unpriced else f"단가미정 {unpriced}건 제외",
            _ACCENT["purple"],
        ),
        _card(
            "에러/경고",
            f"{_num(errors)} / {_num(warns)}",
            "ERROR / WARNING",
            _ACCENT["bad"] if errors else _ACCENT["warn"] if warns else _ACCENT["good"],
        ),
    ]
    return {"overview_cards": cards}


def _cap(s: object, limit: int = 16000) -> str:
    s = str(s or "")
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n… (이하 생략, 총 {len(s):,}자)"


def _make_fail_row(ev: dict, label: str, color: str, count: int) -> dict:
    """실패 피드 1행 + drill-down 모달용 전체 레코드."""
    msg = ev.get("message", "") or ""
    summary = msg.replace("\n", " ")
    if len(summary) > 140:
        summary = summary[:140] + "…"
    exc = ev.get("exception", "") or ""
    return {
        "ts": _short_ts(ev.get("ts")),
        "ts_full": ev.get("ts") or "-",
        "epoch": ev.get("_epoch") or 0.0,
        "level": ev.get("level", "") or "-",
        "category": label,
        "color": color,
        "who": _who(ev),
        "target": _target(ev),
        "summary": summary,
        "count": count,
        # 모달 상세 필드
        "logger": ev.get("logger", "-") or "-",
        "emp_no": _who(ev),
        "conversation_id": ev.get("conversation_id", "-") or "-",
        "message_id": ev.get("message_id", "-") or "-",
        "request_id": ev.get("request_id", "-") or "-",
        "model": ev.get("model") or ev.get("model_id") or "-",
        "full_message": _cap(msg),
        "exception": _cap(exc),
        "has_exception": 1 if exc else 0,
    }


def _failures(events: list[dict]) -> dict:
    counts: dict[str, int] = defaultdict(int)
    grouped: dict[tuple[str, str], dict] = {}

    for ev in events:
        hit = _classify_failure(ev)
        if not hit:
            continue
        key, label, color = hit
        counts[key] += 1

        mid = ev.get("message_id")
        msgkey = mid if mid and mid != "-" else (ev.get("message", "") or "")[:80]
        gkey = (key, msgkey)
        row = grouped.get(gkey)
        if row is None:
            grouped[gkey] = _make_fail_row(ev, label, color, 1)
        else:
            new_count = row["count"] + 1
            # 최신 발생 이벤트를 대표 레코드로 유지 (drill-down 시 최신 traceback)
            if (ev.get("_epoch") or 0.0) >= row["epoch"]:
                grouped[gkey] = _make_fail_row(ev, label, color, new_count)
            else:
                row["count"] = new_count

    fail_cards = [
        {
            "category": _CAT_META[k][0],
            "color": _CAT_META[k][1],
            "accent": _CAT_META[k][2],
            "count": counts[k],
            "value": _num(counts[k]),
        }
        for k in sorted(counts, key=lambda k: counts[k], reverse=True)
    ]

    feed = sorted(grouped.values(), key=lambda r: r["epoch"], reverse=True)[:FEED_LIMIT]
    for r in feed:
        r.pop("epoch", None)
    return {"fail_cards": fail_cards, "fail_feed": feed}


def _who(ev: dict) -> str:
    emp = ev.get("emp_no")
    return emp if emp and emp != "-" else "-"


def _target(ev: dict) -> str:
    f = ev.get("file")
    if f:
        name = str(f)
        return name if len(name) <= 32 else "…" + name[-31:]
    conv = ev.get("conversation_id")
    if conv and conv != "-":
        return conv[:8]
    return "-"


def _ingest(events: list[dict]) -> dict:
    ok = fail = embed_fail = kb_fail = rolled = 0
    parse_ms: list[float] = []
    feed: list[dict] = []

    for ev in events:
        msg = ev.get("message", "") or ""
        logger = ev.get("logger", "")

        if msg.startswith("process_attachment 완료"):
            ok += 1
        elif msg.startswith("process_attachment 실패"):
            fail += 1
            feed.append(_ingest_row(ev, "첨부 처리 실패", "red"))
        elif msg == "attachment.embed failed":
            embed_fail += 1
            feed.append(_ingest_row(ev, "임베딩 실패", "red"))
        elif "upstage parse done" in msg:
            e = _gi(ev, "elapsed_ms")
            if e:
                parse_ms.append(e)
        elif msg.startswith("upstage parse HTTP") or "parse 호출 실패" in msg:
            fail_label = "파싱 오류"
            feed.append(_ingest_row(ev, fail_label, "orange"))
        elif "ingestion 실패" in msg or "bedrock ingestion" in msg.lower():
            kb_fail += 1
            feed.append(_ingest_row(ev, "KB 인제스트 실패", "red"))
        elif "롤백 삭제" in msg:
            rolled += 1
            feed.append(_ingest_row(ev, "업로드 롤백", "amber"))

    # 매우 느린 파싱(>60s) 도 피드에 노출
    cards = [
        _card("첨부 처리 성공", _num(ok), "process_attachment", _ACCENT["good"]),
        _card("첨부 처리 실패", _num(fail), "parse/기타", _ACCENT["bad"] if fail else _ACCENT["neutral"]),
        _card("임베딩 실패", _num(embed_fail), "8192 토큰 초과 등", _ACCENT["bad"] if embed_fail else _ACCENT["neutral"]),
        _card("KB 인제스트 실패", _num(kb_fail), f"롤백 {rolled}건", _ACCENT["bad"] if kb_fail else _ACCENT["neutral"]),
        _card("파싱 지연 p95", _ms(_percentile(parse_ms, 0.95)), f"p50 {_ms(_percentile(parse_ms, 0.5))}", _ACCENT["neutral"]),
    ]
    feed.sort(key=lambda r: r["epoch"], reverse=True)
    feed = feed[:FEED_LIMIT]
    for r in feed:
        r.pop("epoch", None)
    return {"ingest_cards": cards, "ingest_feed": feed}


def _ingest_row(ev: dict, kind: str, color: str) -> dict:
    msg = ev.get("message", "") or ""
    m = _RE_ERR_EQ.search(msg)
    detail = m.group(1) if m else msg
    detail = detail.replace("\n", " ")
    if len(detail) > 120:
        detail = detail[:120] + "…"
    fno = ev.get("file_no")
    target = _target(ev)
    if fno is not None and target == "-":
        target = f"file#{fno}"
    return {
        "ts": _short_ts(ev.get("ts")),
        "epoch": ev.get("_epoch") or 0.0,
        "kind": kind,
        "color": color,
        "target": target,
        "detail": detail,
    }


def _ai_services(events: list[dict]) -> dict:
    """AI 서비스/에이전트 사용량 — 채팅과 분리 집계.

    service 필드가 있는 이벤트(완료/취소/에러 모두 — 토큰은 결과와 무관하게 소비됨)를
    agnt_id 별로 묶어 실행 수·토큰·비용·사용자·중단/실패 건수를 집계한다.
    채팅 지표(chat response)와 독립적이라 대시보드에서 별도 섹션으로 노출한다.
    """
    svc_events = [e for e in events if e.get("service") == "report_checker"]

    by_agent: dict[str, dict] = defaultdict(
        lambda: {
            "runs": 0, "in": 0, "out": 0, "pages": 0, "model": "-",
            "emps": set(), "cancelled": 0, "failed": 0,
        }
    )
    for e in svc_events:
        agnt = e.get("agnt_id") or e.get("service") or "?"
        a = by_agent[agnt]
        a["runs"] += 1
        a["in"] += _gi(e, "input_tokens")
        a["out"] += _gi(e, "output_tokens")
        a["pages"] += _gi(e, "pages")
        a["model"] = e.get("model", "-") or "-"
        status = e.get("status")
        if status == "cancelled":
            a["cancelled"] += 1
        elif status == "failed":
            a["failed"] += 1
        if e.get("emp_no") not in (None, "-"):
            a["emps"].add(e.get("emp_no"))

    total_runs = sum(a["runs"] for a in by_agent.values())
    total_in = sum(a["in"] for a in by_agent.values())
    total_out = sum(a["out"] for a in by_agent.values())
    total_cost = sum(_cost(a["model"], a["in"], a["out"]) for a in by_agent.values())
    total_users = len({emp for a in by_agent.values() for emp in a["emps"]})

    ai_cards = [
        _card("AI 서비스 실행", _num(total_runs), "에이전트 호출(잡)", _ACCENT["purple"]),
        _card("사용 토큰", _num(total_in + total_out), "입력+출력", _ACCENT["info"]),
        _card("예상 비용", _usd(total_cost), "추정 단가 기준", _ACCENT["warn"]),
        _card("고유 사용자", _num(total_users), "서비스 이용 사원", _ACCENT["good"]),
    ]

    ai_rows = []
    for agnt, a in by_agent.items():
        cost = _cost(a["model"], a["in"], a["out"])
        ai_rows.append(
            {
                "agent": agnt,
                "model": a["model"],
                "runs": _num(a["runs"]),
                "aborted": _num(a["cancelled"] + a["failed"]),
                "pages": _num(a["pages"]),
                "in_tok": _num(a["in"]),
                "out_tok": _num(a["out"]),
                "users": _num(len(a["emps"])),
                "cost": _usd(cost),
                "_sort": cost,
            }
        )
    ai_rows.sort(key=lambda r: r["_sort"], reverse=True)
    for r in ai_rows:
        r.pop("_sort", None)

    return {"ai_cards": ai_cards, "ai_rows": ai_rows}


def _models(events: list[dict]) -> dict:
    responses = [e for e in events if e.get("message") == "chat response"]

    by_model: dict[str, dict] = defaultdict(lambda: {"turns": 0, "in": 0, "out": 0, "ms": []})
    by_conv: dict[str, dict] = defaultdict(lambda: {"turns": 0, "tok": 0, "emp": "-", "model": "-"})

    for e in responses:
        model = e.get("model", "?") or "?"
        m = by_model[model]
        m["turns"] += 1
        m["in"] += _gi(e, "input_tokens")
        m["out"] += _gi(e, "output_tokens")
        if _gi(e, "elapsed_ms") > 0:
            m["ms"].append(_gi(e, "elapsed_ms"))

        conv = e.get("conversation_id")
        if conv and conv != "-":
            c = by_conv[conv]
            c["turns"] += 1
            c["tok"] += _gi(e, "input_tokens") + _gi(e, "output_tokens")
            if e.get("emp_no") not in (None, "-"):
                c["emp"] = e.get("emp_no")
            c["model"] = model

    model_rows = []
    for model, m in by_model.items():
        cost = _cost(model, m["in"], m["out"])
        model_rows.append(
            {
                "model": model,
                "turns": _num(m["turns"]),
                "in_tok": _num(m["in"]),
                "out_tok": _num(m["out"]),
                "p95": _ms(_percentile(m["ms"], 0.95)),
                "cost": _usd(cost) if cost is not None else "단가미정",
                "_sort": cost if cost is not None else -1.0,
            }
        )
    model_rows.sort(key=lambda r: r["_sort"], reverse=True)
    for r in model_rows:
        r.pop("_sort", None)

    convo_rows = []
    for conv, c in by_conv.items():
        convo_rows.append(
            {
                "conv": conv[:8],
                "emp": c["emp"],
                "model": c["model"],
                "turns": _num(c["turns"]),
                "tokens": _num(c["tok"]),
                "_sort": c["tok"],
            }
        )
    convo_rows.sort(key=lambda r: r["_sort"], reverse=True)
    convo_rows = convo_rows[:10]
    for r in convo_rows:
        r.pop("_sort", None)

    # tool_loop / grounding 품질 지표
    max_iter = empty_limit = 0
    grounded = retrieved = cited = unused = 0
    searches = zero_hits = 0
    for e in events:
        msg = e.get("message", "") or ""
        if msg.startswith("tool_loop forced fallback"):
            r = _RE_REASON.search(msg)
            if r and r.group(1) == "max_iter":
                max_iter += 1
            elif r and r.group(1) == "empty_limit":
                empty_limit += 1
        elif msg.startswith("kb grounding"):
            g = _RE_GROUNDING.search(msg)
            if g:
                grounded += 1
                rv, cv = int(g.group(1)), int(g.group(2))
                retrieved += rv
                cited += cv
                if rv > 0 and cv == 0:
                    unused += 1
        elif msg.startswith("kb_search") or msg.startswith("search_attachment"):
            searches += 1
            h = _RE_HITS.search(msg)
            if h and int(h.group(1)) == 0:
                zero_hits += 1

    cite_ratio = (cited / retrieved * 100) if retrieved else 0.0
    zero_ratio = (zero_hits / searches * 100) if searches else 0.0
    rag_cards = [
        _card("폴백 max_iter", _num(max_iter), "툴 반복 상한 도달", _ACCENT["warn"] if max_iter else _ACCENT["neutral"]),
        _card("폴백 empty", _num(empty_limit), "연속 0-hit 종료", _ACCENT["warn"] if empty_limit else _ACCENT["neutral"]),
        _card("그라운딩 인용율", _pct(cite_ratio), f"미인용 {unused}건", _ACCENT["good"] if cite_ratio >= 60 else _ACCENT["warn"]),
        _card("0-hit 검색 비율", _pct(zero_ratio), f"{zero_hits}/{searches} 검색", _ACCENT["warn"] if zero_ratio >= 20 else _ACCENT["neutral"]),
    ]

    return {"model_rows": model_rows, "convo_rows": convo_rows, "rag_cards": rag_cards}


def _auth(events: list[dict]) -> dict:
    ok = 0
    ok_users: set[str] = set()
    fail = 0
    lock_candidates: set[str] = set()
    pendings: set[str] = set()
    feed: list[dict] = []

    for ev in events:
        if ev.get("logger") != "wellbot.services.auth.auth_service":
            continue
        msg = ev.get("message", "") or ""

        m_ok = _RE_LOGIN_OK.search(msg)
        if m_ok:
            ok += 1
            ok_users.add(m_ok.group(1))
            continue

        m_bad = _RE_LOGIN_BAD.search(msg)
        if m_bad:
            fail += 1
            emp, cnt = m_bad.group(1), int(m_bad.group(2))
            if cnt >= 3:
                lock_candidates.add(emp)
            feed.append(
                {
                    "ts": _short_ts(ev.get("ts")),
                    "epoch": ev.get("_epoch") or 0.0,
                    "kind": "비밀번호 오류",
                    "color": "red" if cnt >= 3 else "amber",
                    "emp": emp,
                    "detail": f"fail_count={cnt}",
                }
            )
            continue

        m_unk = _RE_LOGIN_UNKNOWN.search(msg)
        if m_unk:
            fail += 1
            feed.append(
                {
                    "ts": _short_ts(ev.get("ts")),
                    "epoch": ev.get("_epoch") or 0.0,
                    "kind": "미등록 사번",
                    "color": "amber",
                    "emp": m_unk.group(1),
                    "detail": "unknown emp_no",
                }
            )
            continue

        m_reg = _RE_REGISTER.search(msg)
        if m_reg:
            pendings.add(m_reg.group(1))
            feed.append(
                {
                    "ts": _short_ts(ev.get("ts")),
                    "epoch": ev.get("_epoch") or 0.0,
                    "kind": "가입 대기(PENDING)",
                    "color": "blue",
                    "emp": m_reg.group(1),
                    "detail": "승인 필요",
                }
            )

    cards = [
        _card("로그인 성공", _num(ok), f"고유 {_num(len(ok_users))}명", _ACCENT["good"]),
        _card("로그인 실패", _num(fail), "비번오류+미등록", _ACCENT["warn"] if fail else _ACCENT["neutral"]),
        _card("잠금 후보", _num(len(lock_candidates)), "fail_count≥3", _ACCENT["bad"] if lock_candidates else _ACCENT["neutral"]),
        _card("가입 대기", _num(len(pendings)), "PENDING 승인 필요", _ACCENT["info"] if pendings else _ACCENT["neutral"]),
    ]
    feed.sort(key=lambda r: r["epoch"], reverse=True)
    feed = feed[:FEED_LIMIT]
    for r in feed:
        r.pop("epoch", None)
    return {"auth_cards": cards, "auth_feed": feed}
