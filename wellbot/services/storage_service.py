"""S3 스토리지 서비스.

첨부 파일 원본 및 파생물(chunks.jsonl, index.faiss) 을
S3 버킷에 저장/조회/삭제.

멀티파트 업로드를 통해 대용량 파일을 스트리밍으로 처리
-> 서버 메모리 부담을 최소화.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Iterator

import boto3
from botocore.exceptions import ClientError

# ── 설정 ──
MULTIPART_CHUNK_SIZE: int = 5 * 1024 * 1024  # 5MB (S3 multipart 최소 단위)
PRESIGNED_URL_EXPIRES: int = 3600            # 1시간


@lru_cache(maxsize=1)
def _get_client():
    """S3 클라이언트를 생성한다 (싱글턴). boto3 가 환경변수에서 자격증명 자동 로드."""
    region = os.environ.get("S3_REGION", os.environ.get("AWS_REGION", "ap-northeast-2"))
    return boto3.client("s3", region_name=region)


def _get_bucket() -> str:
    """S3 버킷 이름을 환경변수에서 읽는다."""
    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        raise RuntimeError("S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.")
    return bucket


def _get_key_prefix() -> str:
    """S3 키 프리픽스를 환경변수에서 읽는다.

    모듈 로드 시점이 아닌 호출 시점에 평가하여
    load_dotenv() 이후에도 올바른 값을 반환한다.
    """
    return os.environ.get("S3_KEY_PREFIX", "files")


def build_prefix(emp_no: str, smry_id: str, file_no: int) -> str:
    """파일 저장 prefix 를 생성한다.

    구조: {S3_KEY_PREFIX}/{emp_no}/{smry_id}/{file_no}/
    """
    base = _get_key_prefix().strip("/")
    if base:
        return f"{base}/{emp_no}/{smry_id}/{file_no}/"
    return f"{emp_no}/{smry_id}/{file_no}/"


def upload_streaming(
    file_stream: BinaryIO,
    s3_key: str,
    content_type: str | None = None,
) -> None:
    """파일 스트림을 S3 에 multipart 업로드한다.

    Args:
        file_stream: 업로드할 파일의 바이너리 스트림 (read() 지원).
        s3_key: S3 오브젝트 키 (prefix 포함 전체 경로).
        content_type: MIME 타입. 미지정 시 S3 가 기본값 사용.
    """
    client = _get_client()
    bucket = _get_bucket()

    extra_args: dict = {"ServerSideEncryption": "AES256"}
    if content_type:
        extra_args["ContentType"] = content_type

    # TransferConfig 를 사용하면 boto3 가 자동으로 multipart 처리
    from boto3.s3.transfer import TransferConfig

    config = TransferConfig(
        multipart_threshold=MULTIPART_CHUNK_SIZE,
        multipart_chunksize=MULTIPART_CHUNK_SIZE,
        use_threads=True,
    )

    client.upload_fileobj(
        file_stream,
        bucket,
        s3_key,
        ExtraArgs=extra_args,
        Config=config,
    )


def upload_bytes(
    data: bytes,
    s3_key: str,
    content_type: str | None = None,
) -> None:
    """바이트 데이터를 S3 에 업로드한다 (소형 파생물용)."""
    client = _get_client()
    bucket = _get_bucket()

    kwargs: dict = {
        "Bucket": bucket,
        "Key": s3_key,
        "Body": data,
        "ServerSideEncryption": "AES256",
    }
    if content_type:
        kwargs["ContentType"] = content_type

    client.put_object(**kwargs)


def download_bytes(s3_key: str) -> bytes:
    """S3 오브젝트를 바이트로 다운로드한다."""
    client = _get_client()
    bucket = _get_bucket()
    response = client.get_object(Bucket=bucket, Key=s3_key)
    return response["Body"].read()


def download_to_file(s3_key: str, target_path: Path) -> None:
    """S3 오브젝트를 로컬 파일로 다운로드한다 (대용량용)."""
    client = _get_client()
    bucket = _get_bucket()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, s3_key, str(target_path))


def head_object(s3_key: str) -> dict | None:
    """오브젝트 메타 조회. 존재하지 않으면 None."""
    client = _get_client()
    bucket = _get_bucket()
    try:
        return client.head_object(Bucket=bucket, Key=s3_key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return None
        raise


def object_exists(s3_key: str) -> bool:
    """오브젝트 존재 여부."""
    return head_object(s3_key) is not None


def get_presigned_url(
    s3_key: str,
    expires_in: int = PRESIGNED_URL_EXPIRES,
    filename: str = "",
) -> str:
    """presigned GET URL 을 발급한다 (다운로드용).

    filename 이 지정되면 Content-Disposition: attachment 헤더를 포함하여
    브라우저가 파일을 직접 다운로드하도록 유도한다.
    """
    client = _get_client()
    bucket = _get_bucket()

    params: dict = {"Bucket": bucket, "Key": s3_key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return client.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires_in,
    )


def list_objects(prefix: str) -> list[str]:
    """prefix 하위의 모든 오브젝트 키를 반환한다."""
    client = _get_client()
    bucket = _get_bucket()

    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            keys.append(obj["Key"])
    return keys


def delete_object(s3_key: str) -> None:
    """단일 오브젝트를 삭제한다. 키가 없어도 예외를 발생시키지 않는다 (S3 표준)."""
    client = _get_client()
    bucket = _get_bucket()
    client.delete_object(Bucket=bucket, Key=s3_key)


def delete_prefix(prefix: str) -> int:
    """prefix 하위의 모든 오브젝트를 삭제한다.

    Returns:
        삭제된 오브젝트 개수.
    """
    client = _get_client()
    bucket = _get_bucket()

    keys = list_objects(prefix)
    if not keys:
        return 0

    # delete_objects 는 한 번에 최대 1000개
    deleted = 0
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch]},
        )
        deleted += len(batch)
    return deleted


def iter_download_stream(s3_key: str, chunk_size: int = 8192) -> Iterator[bytes]:
    """S3 오브젝트를 청크 단위로 스트리밍 다운로드한다."""
    client = _get_client()
    bucket = _get_bucket()
    response = client.get_object(Bucket=bucket, Key=s3_key)
    body = response["Body"]
    try:
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        body.close()
