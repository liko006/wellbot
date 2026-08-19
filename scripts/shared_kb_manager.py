"""
shared_kb_manager.py

공용 Knowledge Base 관리 CLI. 챗봇 서버와 무관하게 관리자가 직접 실행한다.

**로직은 ``wellbot/services/knowledgebase/shared_kb_service.py`` 에 있고 이 스크립트는
얇은 래퍼다** — 디스크에서 파일을 읽고, 인자를 파싱하고, 서비스 로그를 stdout 으로
흘려보내는 일만 한다. 같은 서비스를 admin UI 도 호출하므로 동작은 한 곳에서만 바뀐다.

2단계 폴더 계층 (대분류/소분류):
    --folder 는 "대분류/소분류"(예: 규정/인사) 또는 "대분류"(예: 규정) 형태.
    Data Source 는 대분류 단위로 1개만 생성되며, 소분류는 그 raw/ 안의 하위 폴더다.
    따라서 대분류 1개가 소분류를 무제한 담을 수 있어 'KB당 DS 5개' 한도를 우회한다.

S3 경로 구조:
    shared{env}/{대분류}/raw/{소분류}/       ← 색인 대상 (원본 또는 변환본)
    shared{env}/{대분류}/originals/{소분류}/ ← 변환 원본 보관 (색인 제외)
    shared{env}/{대분류}/processed/          ← Lambda 변환 결과 (intermediate 버킷)

사용 예시:
    # 소분류 디렉토리 전체 업로드 + Ingestion (규정 대분류, 인사 소분류)
    python scripts/shared_kb_manager.py --action upload --folder 규정/인사 --dir ./docs/shared_kb_docs/규정/인사/

    # 파일 1개 업로드 (대분류만 — raw/ 바로 밑)
    python scripts/shared_kb_manager.py --action upload --folder policy --file ./docs/shared_kb_docs/policy/policy_2026.pdf

    # 파싱 방식 강제 지정 (--parser auto|upstage|local). 예: 이번 업로드는 로컬 파서로
    python scripts/shared_kb_manager.py --action upload --folder policy --dir ./docs/... --parser local

    # 특정 대분류 Ingestion만 실행 (그 DS 의 전 소분류 재처리)
    python scripts/shared_kb_manager.py --action ingest --folder 규정

    # Ingestion 상태 확인
    python scripts/shared_kb_manager.py --action status --folder 규정 --job-id abc123

    # 등록된 대분류(Data Source) 목록 확인
    python scripts/shared_kb_manager.py --action list

    # 새 대분류(Data Source) 등록
    python scripts/shared_kb_manager.py --action add-folder --folder 규정

    # 대분류 이름 변경 (S3 서버사이드 이동 + DS 갱신 + 재-ingest, 재업로드 불필요)
    python scripts/shared_kb_manager.py --action rename-folder --folder 규정 --to 사내규정

설정은 .env(인프라) + config/knowBase.yaml(동작 옵션) 두 곳에서 가져온다.
get_kb_config() 가 .env 의 KB_* 변수를 shared_kb 섹션에 주입하므로,
인프라 키(s3_bucket·s3_intermediate_bucket·lambda_arn·kb_role_arn)는 yaml 이 아닌 .env 에 둔다.

.env 에 채워야 하는 항목:
    S3_BUCKET_NAME              # KB 파일 저장 버킷 (채팅 첨부와 공유)
    KB_S3_INTERMEDIATE_BUCKET   # Lambda 변환 결과 중간 버킷
    KB_LAMBDA_ARN               # Custom Transformation Lambda ARN
    KB_ROLE_ARN                 # Bedrock KB IAM Role ARN
    KB_ID                       # 사전 생성한 공용 KB ID (환경별로 다름)
    # (KB_S3_VECTOR_BUCKET 는 공용 KB 에선 미사용 — 인덱스를 새로 만들지 않음)

config/knowBase.yaml 의 shared_kb 섹션에 둘 항목 (동작 옵션 + 폴더 레지스트리):
    shared_kb:
        embedding_model: "amazon.titan-embed-text-v2:0"
        poll_interval:   5
        poll_timeout:    300
        folders:                                # add-folder 실행 시 자동 추가
            policy:     "ds-id-policy"
            manual:     "ds-id-manual"
            notice:     "ds-id-notice"
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (scripts/ 에서 직접 실행하기 위함)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wellbot.env import init_env  # noqa: E402
init_env()  # KB 모듈의 모듈레벨 os.getenv 보장 (다른 wellbot import 전에 호출)

from wellbot.services.knowledgebase import shared_kb_service as svc  # noqa: E402
from wellbot.services.knowledgebase.kb_utils import (  # noqa: E402
    PARSER_CHOICES,
    SHARED_PARSE_POLICY,
    SUPPORTED_EXTENSIONS,
    validate_size_bytes,
)


def _setup_logging() -> None:
    """서비스 진행 로그를 사람이 읽는 형태로 stdout 에 흘린다.

    서비스는 print 를 쓰지 않고 모듈 logger 로만 말한다(앱에서 호출될 때는 앱의
    로깅 설정을 따라야 하므로). CLI 에서는 메시지만 그대로 찍어 기존 출력과
    같은 모양을 유지한다. boto3 등 서드파티 로그는 끌어올리지 않는다.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    service_log = logging.getLogger("wellbot.services.knowledgebase")
    service_log.setLevel(logging.INFO)
    service_log.addHandler(handler)
    service_log.propagate = False
    # print 와 logging 이 같은 stdout 을 쓰므로, 파이프로 넘길 때 순서가 뒤섞이지
    # 않도록 줄 단위 flush 로 맞춘다(터미널에서는 원래 줄 버퍼링).
    sys.stdout.reconfigure(line_buffering=True)


# ──────────────────────────────────────────────
# 디스크 입력 (CLI 전용 관심사)
# ──────────────────────────────────────────────
def collect_files_from_dir(dir_path: str) -> list[str]:
    """디렉토리에서 지원 형식 파일 경로를 수집 (하위 디렉토리는 보지 않음)."""
    paths = [
        str(p) for p in Path(dir_path).iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    print(f"[Dir] {len(paths)}개 파일 수집: {dir_path}")
    return paths


def upload_paths(folder: str, file_paths: list[str], parser: str) -> list[str]:
    """디스크의 파일들을 공용 KB 에 업로드. 반환: 업로드된 S3 URI 목록.

    **파일을 하나씩 읽어 올린다** — 디렉토리를 통째로 올릴 때 전부를 메모리에
    담지 않기 위함. 대신 중간에 실패하면 앞서 올린 파일까지 되돌려, 배치 전체가
    한 단위로 성공하거나 실패하는 기존 동작을 유지한다.
    """
    # 존재·크기는 한 파일이라도 읽기 전에 전부 확인한다 (예전 동작 유지 —
    # 5번째 파일이 상한 초과라고 4개를 올렸다가 되돌리는 낭비를 피한다).
    for file_path in file_paths:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"파일 없음: {file_path}")
        validate_size_bytes(path.stat().st_size, path.name)

    policy = SHARED_PARSE_POLICY.with_parser(parser)
    uploaded: list[str] = []
    try:
        for file_path in file_paths:
            path = Path(file_path)
            uploaded.extend(
                svc.upload_files(folder, [(path.read_bytes(), path.name)], policy=policy)
            )
    except Exception:
        if uploaded:
            print(f"[S3] 실패 — 앞서 올린 {len(uploaded)}개도 롤백합니다.")
            svc.delete_uris(uploaded)
        raise
    return uploaded


def list_folders() -> None:
    folders = svc.list_folders()
    if not folders:
        print("등록된 대분류가 없습니다.")
        return
    print(f"\n{'대분류':<20} {'Data Source ID'}")
    print("-" * 60)
    for folder, ds_id in folders.items():
        print(f"{folder:<20} {ds_id}")


# ──────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────
def _parse_args():
    parser = argparse.ArgumentParser(description="공용 KB 관리 스크립트")
    parser.add_argument(
        "--action",
        required=True,
        choices=["upload", "ingest", "status", "list", "add-folder", "rename-folder"],
    )
    parser.add_argument("--folder",  help="대분류 또는 대분류/소분류 (예: 규정, 규정/인사)")
    parser.add_argument("--to",      help="rename-folder 시 새 대분류 이름")
    parser.add_argument("--file",    nargs="+", help="업로드할 파일 경로")
    parser.add_argument("--dir",     help="업로드할 디렉토리 경로")
    parser.add_argument("--job-id",  help="Ingestion Job ID (status 확인용)")
    parser.add_argument(
        "--parser", choices=list(PARSER_CHOICES), default="auto",
        help="xlsx/pdf 파싱 방식 (auto=기본 게이트, upstage=강제 Upstage, local=pandas/pdfplumber)",
    )
    return parser.parse_args()


def _require(condition: bool, message: str) -> None:
    if not condition:
        print(message)
        sys.exit(1)


def main() -> None:
    _setup_logging()
    args = _parse_args()

    if args.action == "list":
        list_folders()

    elif args.action == "add-folder":
        _require(bool(args.folder), "--folder 옵션이 필요합니다.")
        ds_id = svc.create_folder(args.folder)
        print(f"✅ 폴더 등록 완료: {args.folder} → {ds_id}")

    elif args.action == "rename-folder":
        _require(
            bool(args.folder and args.to),
            "--folder(옛 대분류) 와 --to(새 대분류) 옵션이 필요합니다.",
        )
        status = svc.rename_folder(args.folder, args.to)
        print(f"✅ 이름 변경 완료: {args.folder} → {args.to}, status={status}")

    elif args.action == "upload":
        _require(bool(args.folder), "--folder 옵션이 필요합니다.")
        _require(bool(args.file or args.dir), "--file 또는 --dir 옵션이 필요합니다.")

        file_paths: list[str] = []
        if args.dir:
            file_paths.extend(collect_files_from_dir(args.dir))
        if args.file:
            file_paths.extend(args.file)
        _require(bool(file_paths), "업로드할 파일이 없습니다.")

        print(
            f"📂 업로드 대상: {len(file_paths)}개 파일 → "
            f"folder={args.folder} (parser={args.parser})"
        )
        uploaded = upload_paths(args.folder, file_paths, args.parser)
        if not uploaded:
            print("업로드된 파일이 없습니다.")
            return

        print(f"\n📤 {len(file_paths)}개 파일 업로드 완료 (S3 오브젝트 {len(uploaded)}개)")
        job_id = svc.start_ingestion(args.folder)
        print(f"⚙️  Ingestion 시작: job_id={job_id}")
        status = svc.poll_ingestion_status(args.folder, job_id)
        print(f"✅ 완료: folder={args.folder}, status={status}")

    elif args.action == "ingest":
        _require(bool(args.folder), "--folder 옵션이 필요합니다.")
        job_id = svc.start_ingestion(args.folder)
        print(f"Ingestion 시작: job_id={job_id}")
        status = svc.poll_ingestion_status(args.folder, job_id)
        print(f"✅ 완료: status={status}")

    elif args.action == "status":
        _require(
            bool(args.folder and args.job_id),
            "--folder, --job-id 옵션이 필요합니다.",
        )
        status = svc.poll_ingestion_status(args.folder, args.job_id)
        print(f"상태: {status}")


if __name__ == "__main__":
    main()
