# Build an AI Lead Generation Agent With OpenAI Agents SDK and Zenrows

An AI agent that reads a bot-protected business directory, extracts every company listed, visits each company's own website for signals, and scores each lead against a plain-language ideal customer profile (ICP). Output is a ranked JSON list with a score and one sentence of reasoning per lead.

Companion code for the Zenrows article [Build an AI Lead Generation Agent With OpenAI Agents SDK and Zenrows](https://www.zenrows.com/blog/ai-lead-generation-agent-openai-agents-sdk-zenrows).

## Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [How it works](#how-it-works)
- [Running the project](#running-the-project)
- [Output](#output)
- [Technologies](#technologies)
- [Data sources](#data-sources)
- [Troubleshooting](#troubleshooting)
- [Maintenance](#maintenance)
- [Related article](#related-article)

## Features

- Fetches bot-protected directory pages with Zenrows Fetch using `mode=auto`
- Extracts structured leads from a page with chunked LLM calls, deduped across chunks
- Resolves directory tracking redirects to real company domains before the model reads them
- Enriches each lead by reading its homepage navigation and following only the links that exist
- Scores each lead 0 to 100 against an ICP description, with reasoning
- Composes tools so large page content never passes through the agent's context window
- Scales discovery across many directory pages with Zenrows Batch as a single managed job
- Ships standalone test scripts for each half of the pipeline

## Prerequisites

- Python 3.10 or above
- An OpenAI API key, used by the agent and by the extraction and scoring tools. Get one from the [OpenAI developer dashboard](https://platform.openai.com/api-keys)
- A Zenrows API key for retrieving protected pages. [Create a free account](https://app.zenrows.com/register)

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/ZenRows/ai-lead-generation-agent-openai-agents-sdk-zenrows.git
cd ai-lead-generation-agent-openai-agents-sdk-zenrows
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

### 3. Activate the environment

```bash
# macOS and Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

## Configuration

Copy the example environment file and add your keys:

```bash
cp .env.example .env
```

```
OPENAI_API_KEY=your_openai_api_key
ZENROWS_API_KEY=your_zenrows_api_key
```

`.env` is excluded from version control through `.gitignore`.

The source directory and ICP description are set at the top of `agent.py`. Change `SOURCE_URL` to target a different directory and `ICP` to describe the companies you want.

## Project structure

```
.
├── fixtures/                 # cached page and extracted leads, written on first run
├── agent.py                  # agent definition and entry point
├── tools.py                  # all four tools plus the two composed for the agent
├── batch.py                  # Zenrows Batch for many directory pages
├── test_fetch_extract.py     # jobs 1 and 2 in isolation
├── test_enrich_score.py      # jobs 3 and 4 in isolation
├── count_domains.py          # ground-truth check for extraction recall
├── requirements.txt
├── .env.example
├── LICENSE
└── README.md
```

`tools.py` is organised into four labelled jobs. `fixtures/` is gitignored: `test_fetch_extract.py` writes the cached directory page and extracted leads there so extraction can be tuned without paying for a fetch each time.

## How it works

The pipeline is four jobs. The agent decides which directory to read and which leads to qualify, and Python handles everything else.

```
ICP + source URL
  ↓
fetch_page        Zenrows Fetch, mode=auto, returns Markdown
  ↓
extract_leads     chunked LLM extraction into Lead records
  ↓
enrich_lead       reads each company's homepage nav, fetches the pages it finds
  ↓
score_lead        scores the signals against the ICP, 0 to 100
  ↓
ranked JSON
```

The agent is exposed to two tools rather than four. `discover_leads` wraps jobs 1 and 2, and `qualify_lead` wraps jobs 3 and 4. Passing an enriched lead between two separate tools would mean the agent reproduces every page excerpt as a tool argument, which overflows the context window on a large directory.

## Running the project

Run the two test scripts first, so any failure you see later belongs to the agent loop rather than the tools.

| Script | What it does | Run it |
| --- | --- | --- |
| `test_fetch_extract.py` | Tests the halves in isolation: jobs 1 and 2 | `python test_fetch_extract.py` |
| `test_enrich_score.py` | Tests the halves in isolation: jobs 3 and 4 | `python test_enrich_score.py` |
| `agent.py` | Runs the agent | `python agent.py` |
| `count_domains.py` | Checks extraction recall | `python count_domains.py` |
| `batch.py` | Scales across several directory pages | `python batch.py` |

## Output

### `test_fetch_extract.py`

Prints the cached page size, the saving from unwrapping redirects, and the first five extracted leads. It writes `fixtures/directory_page.md` and `fixtures/leads.json`.

### `test_enrich_score.py`

Prints, per lead, the pages enrichment found on the company site, how long the fetch took, the score, and the reasoning.

### `agent.py`

Prints the tool-call trace followed by the ranked JSON array. Each value comes from a tool result rather than the model's own knowledge, so the trace is the first thing to check when a run misbehaves: an agent that qualifies before discovering, or calls discovery twice, has a prompt problem rather than a tool problem.

`agent.py` caps the run at 10 leads. Raise or remove that rule in `INSTRUCTIONS` for a full pass.

## Technologies

- Python
- OpenAI Agents SDK
- Zenrows Fetch
- Zenrows Batch
- Pydantic
- OpenAI API

## Data sources

The examples target a public IT services directory, a listing of service providers behind Cloudflare protection. The tools work on any page. Whether you should point them at a given site is a legal and policy question rather than a technical one, so check the target's terms and robots.txt first, and scrape only pages that allow it.

## Troubleshooting

Find the symptom you are seeing, grouped by the part of the pipeline it comes from.

### Agent and tool schema errors

#### `TypeError: 'FunctionTool' object is not callable`

`@function_tool` replaces the function with a `FunctionTool` object holding the JSON schema the model reads. Import the private function instead: `_fetch_page`, not `fetch_page`.

#### `additionalProperties should not be set for object types`

Strict tool schemas reject bare `dict` type hints, and `dict[str, X]` as well. Every tool input and output must be a Pydantic model, which is why `EnrichedLead.signals` is a list rather than a dict keyed by signal name.

#### `context_length_exceeded` when running the agent

A tool is returning more content than the model can hold. Compose the steps into a single tool so the bulky intermediate stays inside one Python call, as `discover_leads` and `qualify_lead` do.

### Extraction

#### Extraction returns far fewer leads than the page contains

A single call over a long page fails quietly. `CHUNK_SIZE` in `tools.py` controls the slice size; lower it if recall is still short. Slices overlap by `CHUNK_OVERLAP` so a listing straddling a boundary is not dropped by both sides, and `_key` dedupes what the overlap sees twice. Raise the overlap if your listings are long. Run `count_domains.py` to see what the target should be.

#### Duplicate leads in the output

The model returns the same domain with and without `www.`, so `_key` normalises before comparing. Check that function first if duplicates survive.

#### Empty website fields

Directory listings wrap outbound links in tracking redirects with the real domain url-encoded inside. `_unwrap_redirects` resolves them with a regex before the model reads the page. A directory using a different redirect format needs that pattern adjusting.

### Enrichment and scoring

#### Enrichment is very slow

`_discover_links` reads the homepage navigation and follows only the links that exist. An earlier version guessed paths like `/careers` and `/pricing` and paid a full fetch per miss, which cost 218 seconds on a single lead.

#### All scores cluster in the same range

The ICP is asking for something the pages do not state. Headcount is the usual culprit, since no agency site publishes it. Replace it with criteria a homepage or services page states plainly.

### Batch

#### A Batch job never finishes

Terminal run states are `completed`, `stopped` and `deleted`. `failed` is a task status, so a loop polling for it will time out on a run that stopped. `_collect_batch` polls for the three terminal states.

## Maintenance

Dependencies and the Zenrows API surface are re-verified each quarter. File issues on this repository.

## Related article

This repository accompanies the Zenrows article:

**[Build an AI Lead Generation Agent With OpenAI Agents SDK and Zenrows](https://www.zenrows.com/blog/ai-lead-generation-agent-openai-agents-sdk-zenrows)**
