# count_domains.py
# Ground-truth check for extraction recall.
#
# Counts the unique non-directory domains linked on the cached page, which is
# roughly what _extract_leads should return. Run it after test_fetch_extract.py
# has written the fixture.

import re
from urllib.parse import urlparse

from tools import _unwrap_redirects

FIXTURE = "fixtures/directory_page.md"

# hosts belonging to the directory itself, not to a lead
IGNORED_HOST_FRAGMENTS = ("clutch", "shgstatic")


def main() -> None:
    try:
        with open(FIXTURE) as f:
            markdown = f.read()
    except FileNotFoundError:
        raise SystemExit(f"{FIXTURE} not found. Run test_fetch_extract.py first.")

    # resolve tracking links so the destination domain is what gets counted
    markdown = _unwrap_redirects(markdown)

    hosts = {
        urlparse(url).netloc.replace("www.", "").lower()
        for url in re.findall(r"https?://[a-z0-9.-]+", markdown)
    }
    leads = {
        host for host in hosts
        if host and not any(f in host for f in IGNORED_HOST_FRAGMENTS)
    }

    print(f"{len(leads)} unique non-directory domains on the page")


if __name__ == "__main__":
    main()
