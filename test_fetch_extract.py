# test_fetch_extract.py
import json
import os
from tools import _fetch_page, _unwrap_redirects, _extract_leads

SOURCE_URL = "https://clutch.co/it-services"
FIXTURE = "fixtures/directory_page.md"
LEADS_CACHE = "fixtures/leads.json"

# ---- job 1: fetch ---------------------------------------------------------
# cache the page so extraction can be tuned without paying for a fetch each time
if os.path.exists(FIXTURE):
    with open(FIXTURE) as f:
        markdown = f.read()
    print(f"using cached page, {len(markdown)} chars")
else:
    markdown = _fetch_page(SOURCE_URL)
    if markdown.startswith("FETCH_ERROR"):
        raise SystemExit(markdown)
    os.makedirs("fixtures", exist_ok=True)
    with open(FIXTURE, "w") as f:
        f.write(markdown)
    print(f"fetched and cached, {len(markdown)} chars")

unwrapped = _unwrap_redirects(markdown)
print(f"{len(unwrapped)} chars after unwrapping redirects, "
      f"{len(markdown) - len(unwrapped)} saved\n")

# ---- job 2: extract -------------------------------------------------------
leads = _extract_leads(markdown, SOURCE_URL)

with open(LEADS_CACHE, "w") as f:
    json.dump(leads, f, indent=2)

print(f"{len(leads)} leads extracted\n")
for lead in leads[:5]:
    print(f"{lead['company']:<32} {lead['website'] or '(none)'}")

missing_site = sum(1 for lead in leads if not lead["website"])
print(f"\n{missing_site} of {len(leads)} leads have no website")