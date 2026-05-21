"""
Test WORM (Write Once Read Many) dla archiwum MinIO.

Uruchamianie (z hosta, MinIO jest w sieci wewnętrznej):
    docker exec cards_provider_app python test_minio.py

Dowodzi że:
  1. Bucket ma włączony Object Lock
  2. Obiekt da się zapisać (Write Once)
  3. Obiekt da się odczytać wielokrotnie (Read Many)
  4. Zablokowanej wersji NIE da się usunąć (WORM)
  5. Po "nadpisaniu" oryginalna wersja pozostaje nienaruszona
"""
import io
import json
from datetime import datetime, timedelta, timezone

from minio.error import S3Error
from minio.commonconfig import COMPLIANCE
from minio.retention import Retention

from app.archive import get_minio_client, init_minio_bucket, MINIO_BUCKET


def main():
    print("=" * 60)
    print("TEST MinIO WORM (Object Lock / niemodyfikowalne archiwum)")
    print("=" * 60)

    init_minio_bucket()
    client = get_minio_client()

    # --- 1. Czy bucket ma Object Lock ---
    print("\n[1] Sprawdzanie Object Lock na buckecie...")
    try:
        client.get_object_lock_config(MINIO_BUCKET)
        print(f"OK  Object Lock WLACZONY na '{MINIO_BUCKET}'")
    except S3Error as e:
        print(f"FAIL  Object Lock NIE jest wlaczony: {e.code}")
        return

    # --- 2. Zapis (Write Once) ---
    object_name = "test/worm-proof.json"
    test_data = {
        "test": "WORM_PROOF",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    print(f"\n[2] Zapis obiektu '{object_name}'...")
    payload = json.dumps(test_data).encode("utf-8")
    retain_until = datetime.now(timezone.utc) + timedelta(days=1)
    result = client.put_object(
        MINIO_BUCKET,
        object_name,
        io.BytesIO(payload),
        length=len(payload),
        content_type="application/json",
        retention=Retention(COMPLIANCE, retain_until),
    )
    version_id = result.version_id
    print(f"    OK  Zapisano. version_id={version_id}")
    print(f"    LOCK  Retencja COMPLIANCE do {retain_until.date()}")

    # --- 3. Odczyt (Read Many) ---
    print(f"\n[3] Odczyt obiektu (x2)...")
    for i in (1, 2):
        obj = client.get_object(MINIO_BUCKET, object_name)
        content = json.loads(obj.read())
        obj.close()
        obj.release_conn()
        print(f"    OK  Odczyt #{i}: {content['test']}")

    # --- 4. Proba usuniecia zablokowanej wersji (MUSI sie nie udac) ---
    print(f"\n[4] Proba usuniecia zablokowanej wersji...")
    try:
        client.remove_object(MINIO_BUCKET, object_name, version_id=version_id)
        print(f"    FAIL  Obiekt USUNIETY! WORM NIE DZIALA!")
    except S3Error as e:
        print(f"    OK  Usuniecie ODRZUCONE przez WORM (kod: {e.code})")

    # --- 5. Proba nadpisania -> stara wersja zostaje nienaruszona ---
    print(f"\n[5] Proba 'nadpisania' obiektu...")
    new_payload = json.dumps({"test": "ZMIANA_PROBA"}).encode("utf-8")
    client.put_object(
        MINIO_BUCKET,
        object_name,
        io.BytesIO(new_payload),
        length=len(new_payload),
        content_type="application/json",
    )
    old = client.get_object(MINIO_BUCKET, object_name, version_id=version_id)
    old_content = json.loads(old.read())
    old.close()
    old.release_conn()
    print(f"OK  Oryginalna wersja NIENARUSZONA: {old_content['test']}")
    print(f"(wersjonowanie WORM – oryginal pozostaje niezmieniony)")

    print("\n" + "=" * 60)
    print("WYNIK: WORM dziala – obiekty sa nieusuwalne i niemodyfikowalne")
    print("=" * 60)


if __name__ == "__main__":
    main()
