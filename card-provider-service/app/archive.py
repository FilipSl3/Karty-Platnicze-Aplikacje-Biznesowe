import io
import os
import json
import time
import logging
from datetime import datetime, timedelta, timezone

from minio import Minio
from minio.error import S3Error
from minio.commonconfig import COMPLIANCE
from minio.retention import Retention

logger = logging.getLogger(__name__)

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio_admin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio_admin_2026")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "settlements")
MINIO_RETENTION_DAYS = int(os.getenv("MINIO_RETENTION_DAYS", "1"))

_client: Minio | None = None


def get_minio_client() -> Minio:
    """Zwraca singleton klienta MinIO (połączenie HTTP w sieci wewnętrznej)."""
    global _client
    if _client is None:
        _client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=False,
        )
    return _client


def init_minio_bucket(retries: int = 10, delay: float = 3.0) -> bool:
    """
    Tworzy bucket z włączonym Object Lock (WORM).
    """
    client = get_minio_client()
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            if client.bucket_exists(MINIO_BUCKET):
                logger.info(f"MinIO bucket '{MINIO_BUCKET}' już istnieje")
                return True
            # object_lock=True automatycznie włącza wersjonowanie + WORM
            client.make_bucket(MINIO_BUCKET, object_lock=True)
            logger.info(f"MinIO bucket '{MINIO_BUCKET}' utworzony z Object Lock (WORM)")
            return True
        except Exception as e:
            last_err = e
            logger.warning(f"MinIO init próba {attempt}/{retries} nieudana: {e}")
            time.sleep(delay)
    logger.error(f"MinIO init nieudany po {retries} próbach: {last_err}")
    return False


def archive_record(data: dict, object_name: str, retention_days: int | None = None) -> bool:
    """
    Archiwizuje słownik jako niemodyfikowalny obiekt JSON w MinIO (WORM).

    Args:
        data: dowolny słownik (transakcja, zdarzenie, settlement)
        object_name: nazwa/ścieżka obiektu,
        retention_days: okres blokady WORM (domyślnie z env MINIO_RETENTION_DAYS)
    """
    days = retention_days if retention_days is not None else MINIO_RETENTION_DAYS
    try:
        client = get_minio_client()
        payload = json.dumps(data, indent=2, default=str, ensure_ascii=False).encode("utf-8")
        retain_until = datetime.now(timezone.utc) + timedelta(days=days)
        result = client.put_object(
            MINIO_BUCKET,
            object_name,
            io.BytesIO(payload),
            length=len(payload),
            content_type="application/json",
            retention=Retention(COMPLIANCE, retain_until),
        )
        logger.info(
            f"Zarchiwizowano '{object_name}' (WORM COMPLIANCE do {retain_until.date()}, "
            f"version_id={result.version_id})"
        )
        return True
    except S3Error as e:
        logger.error(f"MinIO archiwizacja '{object_name}' – błąd S3: {e}")
        return False
    except Exception as e:
        logger.error(f"MinIO archiwizacja '{object_name}' – błąd: {e}")
        return False
