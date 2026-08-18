import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path

from shroom_fm.retry import get_with_retry, post_with_retry

KAIA_QUERY_URL = "https://avaandmed.keskkonnaportaal.ee/api/lists/active/items/query"
KAIA_DOWNLOAD_URL_TEMPLATE = (
    "https://avaandmed.keskkonnaportaal.ee/api/lists/active/items/{id}/files/{file_id}"
)
RADAR_CONTENT_TYPE = "0102FB01"
RADAR_PHENOMENON = "COMP"
MAX_WORKERS = 6
_PAGE_SIZE = 2000


def query_radar_documents(since: datetime) -> list[dict]:
    documents: list[dict] = []
    bookmark = None
    while True:
        body = {
            "filter": {
                "and": {
                    "children": [
                        {"underContentType": {"contentType": RADAR_CONTENT_TYPE}},
                        {"isEqual": {"field": "Phenomenon", "value": RADAR_PHENOMENON}},
                        {
                            "greaterThanOrEqual": {
                                "field": "Timestamp",
                                "value": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            }
                        },
                    ]
                }
            },
            "pageSize": _PAGE_SIZE,
            "includeFileMetadata": True,
            "fields": ["Timestamp"],
        }
        if bookmark is not None:
            body["bookmark"] = bookmark
        response = post_with_retry(KAIA_QUERY_URL, json=body, timeout=30)
        data = response.json()
        for doc in data["documents"]:
            documents.append(
                {
                    "id": doc["id"],
                    "file_id": doc["fileMetadata"][0]["id"],
                    "timestamp": datetime.fromisoformat(
                        doc["metadata"]["Timestamp"]
                    ).astimezone(timezone.utc),
                }
            )
        bookmark = data.get("nextBookmark")
        if bookmark is None:
            break
    return documents


def _cache_filename(document: dict) -> str:
    return f"{document['timestamp']:%Y%m%dT%H%M%SZ}_{document['id']}.h5"


def cached_radar_timestamp(path: Path) -> datetime:
    stem = path.name.split("_", 1)[0]
    return datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def download_radar_composite(document: dict, cache_dir: Path) -> Path:
    path = cache_dir / _cache_filename(document)
    if path.exists():
        return path
    url = KAIA_DOWNLOAD_URL_TEMPLATE.format(
        id=document["id"], file_id=document["file_id"]
    )
    response = get_with_retry(url, timeout=30)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return path


def fetch_new_radar_composites(
    cache_dir: Path, since: datetime, *, max_workers: int = MAX_WORKERS
) -> list[Path]:
    documents = query_radar_documents(since)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not documents:
        return []
    paths: list[Path | None] = [None] * len(documents)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(download_radar_composite, doc, cache_dir): i
            for i, doc in enumerate(documents)
        }
        done = 0
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            paths[index] = future.result()
            done += 1
            print(f"  downloaded {done}/{len(documents)} radar composites")
    return paths


def expire_old_radar_composites(cache_dir: Path, cutoff: datetime) -> None:
    if not cache_dir.exists():
        return
    for path in cache_dir.glob("*.h5"):
        if cached_radar_timestamp(path) < cutoff:
            path.unlink()


def cached_radar_files(
    cache_dir: Path, window_start: datetime, window_end: datetime
) -> list[Path]:
    if not cache_dir.exists():
        return []
    return sorted(
        (
            p
            for p in cache_dir.glob("*.h5")
            if window_start <= cached_radar_timestamp(p) <= window_end
        ),
        key=cached_radar_timestamp,
    )


def newest_cached_radar_timestamp(cache_dir: Path) -> datetime | None:
    if not cache_dir.exists():
        return None
    files = list(cache_dir.glob("*.h5"))
    if not files:
        return None
    return max(cached_radar_timestamp(p) for p in files)
