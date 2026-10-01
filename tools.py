
import os
import re
import requests
from urllib.parse import urlparse, parse_qs, unquote

from agents import function_tool
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

ZENROWS_API_KEY = os.getenv("ZENROWS_API_KEY")
ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"

if not ZENROWS_API_KEY:
    raise RuntimeError("set ZENROWS_API_KEY in your .env file")

client = OpenAI()


# ===========================================================================
# job 1: fetch a page, protected or not
# ===========================================================================

def _fetch_page(url: str) -> str:
    """fetch logic, importable and testable without the agent."""
    params = {
        "url": url,
        "apikey": ZENROWS_API_KEY,
        "mode": "auto",              # start cheap, escalate only when the target needs it
        # markdown strips nav and boilerplate before the model reads it
        "response_type": "markdown"
    }

    try:
        response = requests.get(ZENROWS_ENDPOINT, params=params, timeout=90)
        response.raise_for_status()
    # return failures as text instead of raising, so one bad url does not end the run.
    # each message tells the model whether the failure is worth retrying
    except requests.exceptions.Timeout:
        return "FETCH_ERROR: timed out after 90s. retry once, then move on."
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            return f"FETCH_ERROR: {status}. api key rejected, do not retry."
        if status == 429:
            return "FETCH_ERROR: 429 concurrency limit. wait, then retry."
        if status in (400, 404):
            return f"FETCH_ERROR: {status}. this url is not retrievable, do not retry."
        return f"FETCH_ERROR: {status}. retry once, then report the failure."
    except requests.exceptions.RequestException as exc:
        return f"FETCH_ERROR: {exc}. do not retry."

    content = response.text.strip()
    if not content:
        return "FETCH_ERROR: empty response body. do not retry."

    return content


# the decorator replaces the function with a FunctionTool object, which is not
# callable. keeping the logic in _fetch_page above leaves it testable
@function_tool
def fetch_page(url: str) -> str:
    """Fetch any web page as markdown, including pages behind anti-bot protection.

    Args:
        url: full url of the page to fetch
    """
    return _fetch_page(url)


# ===========================================================================
# job 2: turn a directory page into leads
# ===========================================================================

# matches the tracking links directories wrap around outbound urls
REDIRECT_LINK = re.compile(r"https?://[a-z0-9.-]*/redirect\?[^\s\)\"]+")

# a full directory page is too long for one reliable extraction call
CHUNK_SIZE = 40000

# slices cut mid-listing, and the extraction prompt is told to skip partial
# entries, so a listing straddling a boundary is dropped by both slices.
# overlapping the window carries each boundary listing whole into one of them;
# _key dedupes the entries the overlap sees twice
CHUNK_OVERLAP = 2000


# strict tool schemas reject bare dicts, so every tool input and output is a model
class Lead(BaseModel):
    company: str
    name: str
    website: str
    source_url: str


class LeadList(BaseModel):
    leads: list[Lead]


def _unwrap_redirects(markdown: str) -> str:
    """rewrite directory tracking links to the destination domain."""
    def replace(match):
        url = match.group(0)
        # the real destination sits url-encoded in the u parameter
        target = parse_qs(urlparse(url).query).get("u", [""])[0]
        if not target:
            return url
        parsed = urlparse(unquote(target))
        return f"{parsed.scheme}://{parsed.netloc}"

    return REDIRECT_LINK.sub(replace, markdown)


def _reject_source_domain(leads: list[dict], source_url: str) -> list[dict]:
    """blank any website that points back at the directory itself."""
    directory = urlparse(source_url).netloc.replace("www.", "")
    for lead in leads:
        host = urlparse(lead["website"]).netloc.replace("www.", "")
        if host == directory:
            lead["website"] = ""
    return leads


def _key(lead: dict) -> str:
    """stable identity for dedupe across chunks."""
    # the model returns the same domain with and without www, so normalise before comparing
    site = lead["website"].lower()
    for prefix in ("https://", "http://", "www."):
        site = site.replace(prefix, "")
    site = site.rstrip("/")
    # fall back to company name so leads without a domain are not collapsed into one
    return site or lead["company"].strip().lower()


def _extract_chunk(chunk: str, source_url: str) -> list[dict]:
    """run one extraction call over a slice of the page."""
    response = client.responses.parse(
        model="gpt-4o-mini",
        max_output_tokens=8000,
        input=[
            {
                "role": "system",
                "content": (
                    "extract every company listed in this page fragment. "
                    "website is the company's own domain, taken from that listing's "
                    "visit website link. "
                    "never use a link from the directory's own domain as the website. "
                    "name is a person's name and is usually absent from a directory "
                    "listing, so leave it empty unless a person is actually named. "
                    "use an empty string for any field the fragment does not state, "
                    "and never invent a value. "
                    "the fragment may start or end mid listing, so skip any partial entry."
                )
            },
            {
                "role": "user",
                "content": f"source_url: {source_url}\n\n{chunk}"
            }
        ],
        text_format=LeadList
    )
    return [lead.model_dump() for lead in response.output_parsed.leads]


def _extract_leads(content: str, source_url: str) -> list[dict]:
    """parse markdown into lead dicts, chunking long pages."""
    # a fetch failure is a string, not a page. parsing it would return an empty
    # list that looks identical to a genuinely empty directory
    if content.startswith("FETCH_ERROR"):
        return []

    # resolve tracking links before the model reads them
    content = _unwrap_redirects(content)

    leads, seen = [], set()
    for start in range(0, len(content), CHUNK_SIZE - CHUNK_OVERLAP):
        chunk = content[start:start + CHUNK_SIZE]
        for lead in _extract_chunk(chunk, source_url):
            key = _key(lead)
            if key in seen:
                continue
            seen.add(key)
            leads.append(lead)

    return _reject_source_domain(leads, source_url)


@function_tool
def extract_leads(content: str, source_url: str) -> list[Lead]:
    """Parse a fetched page into a list of leads.

    Args:
        content: markdown returned by fetch_page
        source_url: url the content came from, recorded on every lead
    """
    return [Lead(**lead) for lead in _extract_leads(content, source_url)]

# fetch and extract are one job from the agent's point of view. exposing them
# separately means the whole page passes through the model's context to get
# from one tool to the next, which overflows the window on a large directory
@function_tool
def discover_leads(source_url: str) -> list[Lead]:
    """Fetch a directory page and return the companies listed on it.

    Args:
        source_url: url of the directory or listing page
    """
    markdown = _fetch_page(source_url)
    if markdown.startswith("FETCH_ERROR"):
        return []
    return [Lead(**lead) for lead in _extract_leads(markdown, source_url)]

# ===========================================================================
# job 3: enrich each lead from its own website
# ===========================================================================

# matches any markdown link, used to read a company's own navigation
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")

# guessing paths costs a fetch per miss, so read the site's nav instead and
# follow only the links it actually has
SIGNAL_KEYWORDS = {
    "hiring": ["career", "job", "join", "hiring", "work with us", "we are hiring"],
    "services": ["service", "what we do", "solutions", "expertise", "capabilities"],
    "portfolio": ["portfolio", "case stud", "our work", "projects", "clients"],
    "about": ["about", "who we are", "our story", "team"],
    "contact": ["contact", "get in touch", "book a call", "let's talk", "talk to us"],
}


# dict[str, X] is rejected by strict schemas too, which is why signals is a list
class Signal(BaseModel):
    name: str
    found: bool
    url: str
    excerpt: str


class EnrichedLead(BaseModel):
    company: str
    website: str
    signals: list[Signal]


def _discover_links(markdown: str, website: str) -> dict:
    """find real urls for each signal by reading the homepage nav."""
    host = urlparse(website).netloc.replace("www.", "")
    homepage_path = urlparse(website).path.rstrip("/") or "/"
    found = {}

    for text, url in MARKDOWN_LINK.findall(markdown):
        parsed = urlparse(url)

        # only follow links on the company's own domain
        if parsed.netloc.replace("www.", "") != host:
            continue

        # an anchor on the homepage is content already in the homepage excerpt
        if parsed.fragment and (parsed.path.rstrip("/") or "/") == homepage_path:
            continue

        # drop the fragment, it never changes what the server returns
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        haystack = f"{text} {url}".lower()
        for signal, keywords in SIGNAL_KEYWORDS.items():
            if signal not in found and any(k in haystack for k in keywords):
                found[signal] = clean_url

    return found


def _enrich_lead(company: str, website: str) -> EnrichedLead:
    """collect signals from the company's own site."""
    # a lead with no domain still flows through to scoring, judged on what little is known
    if not website:
        return EnrichedLead(company=company, website="", signals=[])

    homepage = _fetch_page(website)
    if homepage.startswith("FETCH_ERROR"):
        return EnrichedLead(company=company, website=website, signals=[])

    # the homepage is the most informative single page, and on a one-page site
    # it holds everything the nav links point at
    signals = [Signal(name="homepage", found=True, url=website, excerpt=homepage[:6000])]

    for name, url in _discover_links(homepage, website).items():
        content = _fetch_page(url)
        ok = not content.startswith("FETCH_ERROR")
        signals.append(Signal(
            name=name,
            found=ok,
            url=url,
            # keep excerpts small, scoring reads all of them in one call
            excerpt=content[:2000] if ok else ""
        ))

    return EnrichedLead(company=company, website=website, signals=signals)


@function_tool
def enrich_lead(company: str, website: str) -> EnrichedLead:
    """Collect signals from a company's own website.

    Args:
        company: company name from the directory listing
        website: company's own domain
    """
    return _enrich_lead(company, website)


# ===========================================================================
# job 4: score each lead against the icp
# ===========================================================================

class Score(BaseModel):
    score: int
    reasoning: str


class ScoredLead(BaseModel):
    company: str
    website: str
    score: int
    reasoning: str


def _score_lead(lead: EnrichedLead, icp: str) -> ScoredLead:
    """score an enriched lead against the icp description."""
    # no fetching happens here. every page was already retrieved in job 3
    summary = "\n\n".join(
        f"{s.name} page: {'found at ' + s.url if s.found else 'not found'}\n{s.excerpt}"
        for s in lead.signals
    )

    response = client.responses.parse(
        model="gpt-4o-mini",
        max_output_tokens=500,
        input=[
            {
                "role": "system",
                "content": (
                    "score how well this company matches the ideal customer profile. "
                    "return an integer from 0 to 100 and one sentence of reasoning. "
                    "base the score only on evidence in the signals provided. "
                    "a missing page is weak evidence, not disqualifying. "
                    "if the signals are too thin to judge, score below 30 and say so."
                )
            },
            {
                "role": "user",
                "content": (
                    f"ideal customer profile:\n{icp}\n\n"
                    f"company: {lead.company}\n"
                    f"website: {lead.website}\n\n"
                    f"signals:\n{summary}"
                )
            }
        ],
        text_format=Score
    )

    return ScoredLead(
        company=lead.company,
        website=lead.website,
        score=response.output_parsed.score,
        reasoning=response.output_parsed.reasoning
    )


@function_tool
def score_lead(lead: EnrichedLead, icp: str) -> ScoredLead:
    """Score an enriched lead against an ICP description.

    Args:
        lead: enriched lead returned by enrich_lead
        icp: plain-language description of the ideal customer
    """
    return _score_lead(lead, icp)

# an EnrichedLead carries page excerpts the model has no reason to read. passing
# it between two tools means the agent has to reproduce all of it as an argument,
# and it summarises instead, so scoring reads a summary rather than the pages
@function_tool
def qualify_lead(company: str, website: str, icp: str) -> ScoredLead:
    """Enrich a lead from its own website and score it against an ICP.

    Args:
        company: company name from the directory listing
        website: company's own domain
        icp: plain-language description of the ideal customer
    """
    enriched = _enrich_lead(company, website)
    return _score_lead(enriched, icp)