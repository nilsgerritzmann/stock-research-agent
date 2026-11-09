# Stock Research Agent

Writes a short research memo on a US-listed company from its SEC filings and
checks it against a set of investment criteria. Every number in the memo is
calculated in Python and footnoted to the 10-K it comes from; the language
model only writes the prose and is not allowed to type numbers itself.

> Status: **version 1**, a fixed workflow. Version 2 turns it into an agent
> that chooses its own steps and investigates red flags in the annual report.

## Example

`python -m research_agent NKE` → see [`memos/samples/`](memos/samples/)

## How it works

```
ticker ─► SEC EDGAR 10-K data ─► metrics in Python ─► criteria check ─► Claude writes prose ─► memo.md
                                     ▲                                       │
                     share price (yfinance, optional)          placeholders like {{roic_FY2025}},
                                                               filled in by Python with footnotes
```

Design decisions and their trade-offs are in [DECISIONS.md](DECISIONS.md).
Criteria and thresholds are in [config/criteria.yaml](config/criteria.yaml).

## Setup

Requires Python 3.11+ and a Claude subscription (the model runs through the
Claude Agent SDK on your own login, with no API key).

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"
cp .env.example .env          # add your name and email (required by the SEC)
```

The model runs on your Claude subscription through the Claude Code login on
this machine (check with `.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude auth status`).
The code refuses to run if `ANTHROPIC_API_KEY` is set, because that would be
billed instead.

## Usage

Run from the project folder:

```bash
.venv/bin/python -m research_agent MSFT            # full memo -> memos/MSFT_<date>.md
.venv/bin/python -m research_agent MSFT --no-llm   # figures and tables only, no model call
.venv/bin/pytest                                   # run the tests
```

*macOS note:* `python -m` is used instead of an installed `research` command
because macOS can mark files inside the virtual environment as hidden, and
Python then ignores the file that makes the installed command work. If the
project lives in an iCloud-synced folder, keep the environment in a folder
ending in `.nosync` (with `.venv` as a link to it) so iCloud does not sync it.

## Limitations

US companies only; banks and insurers are not supported; GAAP figures only;
lease liabilities are excluded from debt. See *Data notes* in each memo and
DECISIONS.md.

Not investment advice.
