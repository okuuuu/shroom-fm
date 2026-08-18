import concurrent.futures
import json

from shroom_fm.retry import get_with_retry

MAX_WORKERS = 6


def fetch_hit_count(url: str, base_params: dict, *, timeout: int = 30) -> int:
    response = get_with_retry(
        url, params={**base_params, "count": 1, "startIndex": 0}, timeout=timeout
    )
    data = json.loads(response.content)
    return int(data["totalFeatures"])


def fetch_pages_concurrently(
    url: str,
    params_list: list[dict],
    *,
    max_workers: int = MAX_WORKERS,
    timeout: int = 30,
    progress_label: str = "page",
) -> list[bytes]:
    if not params_list:
        return []

    total = len(params_list)
    results: list[bytes | None] = [None] * total
    done = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(get_with_retry, url, params=params, timeout=timeout): index
            for index, params in enumerate(params_list)
        }
        try:
            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                results[index] = future.result().content
                done += 1
                print(f"  fetched {done}/{total} {progress_label}s")
        except Exception:
            for pending in future_to_index:
                pending.cancel()
            raise

    return results
