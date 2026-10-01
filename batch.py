# batch.py
# Scale discovery across several directory pages with Zenrows Batch.
#
# One Fetch call retrieves one page. Batch takes the whole list as a single
# managed job and handles the queue, concurrency and retries, so a directory
# with many pages costs one submission instead of one call per page.

import time

import requests
from agents import function_tool

from tools import ZENROWS_API_KEY, Lead, _extract_leads

BATCH_ENDPOINT = "https://async.api.zenrows.com/v1/jobs"

# batch authenticates by header, unlike fetch which takes apikey as a query param
BATCH_HEADERS = {"X-API-Key": ZENROWS_API_KEY, "Content-Type": "application/json"}

# a job accepts up to 100,000 urls (truth/batch-truth.md, Limits)
MAX_URLS_PER_JOB = 100_000


def _submit_batch(urls: list[str]) -> str:
    """submit a url list as one job, return the job id."""
    if len(urls) > MAX_URLS_PER_JOB:
        raise ValueError(f"a job accepts at most {MAX_URLS_PER_JOB} urls")

    payload = {
        "type": "regular",
        "status": "closed",  # run once, accept no further tasks
        "zenrows_params": {"mode": "auto", "response_type": "markdown"},
        "tasks": [{"url": url} for url in urls],
    }
    response = requests.post(
        BATCH_ENDPOINT, headers=BATCH_HEADERS, json=payload, timeout=30
    )
    response.raise_for_status()
    return response.json()["job_id"]


def _collect_batch(job_id: str, max_attempts: int = 60) -> list[tuple[str, str]]:
    """wait for the job to reach a terminal state, then pull each task's markdown."""
    # terminal run states are completed, stopped and deleted. "failed" is a task
    # status, not a run status, so polling for it never returns
    # (truth/batch-truth.md, Domain model)
    for _ in range(max_attempts):
        time.sleep(5)
        status = requests.get(
            f"{BATCH_ENDPOINT}/{job_id}", headers=BATCH_HEADERS, timeout=30
        )
        status.raise_for_status()
        run = status.json()["latest_run"]
        if run["status"] in ("completed", "stopped", "deleted"):
            break
    else:
        raise TimeoutError(f"job {job_id} did not reach a terminal state in time")

    response = requests.get(
        f"{BATCH_ENDPOINT}/{job_id}/results", headers=BATCH_HEADERS, timeout=30
    )
    response.raise_for_status()

    pages = []
    for task in response.json()["results"]:
        if task["status"] != "successful":
            continue
        # result_url is presigned and valid for 2 hours; re-list the results for a
        # fresh link rather than storing this one (truth/batch-truth.md, Limits)
        markdown = requests.get(task["result_url"], timeout=60).text
        pages.append((task["url"], markdown))

    return pages


@function_tool
def discover_leads_batch(source_urls: list[str]) -> list[Lead]:
    """Fetch several directory pages as one job and return every company listed.

    Args:
        source_urls: directory or listing page urls to fetch
    """
    job_id = _submit_batch(source_urls)

    leads = []
    for url, markdown in _collect_batch(job_id):
        leads.extend(Lead(**lead) for lead in _extract_leads(markdown, url))

    return leads


if __name__ == "__main__":
    # two pages of the same directory, submitted as one job
    urls = [
        "https://clutch.co/it-services",
        "https://clutch.co/it-services?page=2",
    ]
    job_id = _submit_batch(urls)
    print(f"submitted {job_id}")

    for url, markdown in _collect_batch(job_id):
        print(f"{url}: {len(markdown)} chars")
