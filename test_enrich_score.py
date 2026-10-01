# test_enrich_score.py

import json
import os
import time
from tools import _fetch_page, _unwrap_redirects, _extract_leads, _enrich_lead, _score_lead

SOURCE_URL = "https://clutch.co/it-services"
FIXTURE = "fixtures/directory_page.md"
LEADS_CACHE = "fixtures/leads.json"

ICP = (
    "IT services agencies that build custom software for B2B clients, "
    "publish detailed case studies with named clients, and offer ai and cloud services"
)

# ---- job 1: fetch ---------------------------------------------------------
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
if os.path.exists(LEADS_CACHE):
    with open(LEADS_CACHE) as f:
        leads = json.load(f)
    print(f"using {len(leads)} cached leads\n")
else:
    leads = _extract_leads(markdown, SOURCE_URL)
    with open(LEADS_CACHE, "w") as f:
        json.dump(leads, f, indent=2)
    print(f"{len(leads)} leads extracted\n")

for lead in leads[:5]:
    print(f"{lead['company']:<32} {lead['website'] or '(none)'}")

missing_site = sum(1 for lead in leads if not lead["website"])
print(f"\n{missing_site} of {len(leads)} leads have no website\n")
print("-" * 60 + "\n")

# ---- jobs 3 and 4: enrich and score ---------------------------------------
# enrichment is one fetch per nav link found, so start with a few leads
sample = [lead for lead in leads if lead["website"]][:3]

for lead in sample:
    start = time.time()
    enriched = _enrich_lead(lead["company"], lead["website"])
    elapsed = time.time() - start

    found = [s.name for s in enriched.signals if s.found]
    print(f"{enriched.company}  ({elapsed:.1f}s)")
    print(f"  found: {', '.join(found) if found else 'nothing'}")

    scored = _score_lead(enriched, ICP)
    print(f"  score: {scored.score}")
    print(f"  {scored.reasoning}\n")