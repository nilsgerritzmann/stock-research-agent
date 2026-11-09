# Stock Research Agent Project Brief

## Before you write any code

Read this whole brief first. Then ask me your questions in one round, starting with the ones listed at the end of this file and adding any of your own. Wait for my answers. Then propose a short plan for version 1 and wait for my OK before you build anything.

## About me

I am Nils, a recent finance graduate (M.Sc. Business Administration, major Accounting). I have basic Python skills and built a small job search tool in VS Code before. This project has two purposes.

1. A tool I actually use for my own investments. I mostly invest in ETFs and want to start analyzing single shares in a structured way.
2. A portfolio project I can show in applications for finance trainee programs. I need to understand every part well enough to explain it in an interview, so explain your design decisions in plain language as you go.

## Goal

An AI agent that analyzes one company at a time and writes a short research memo. I give it a company and my investment criteria. It gathers the figures, checks them against my criteria, digs deeper when something looks off, and ends with a memo and open questions. I make the decision myself.

## What makes it an agent

A fixed pipeline is not enough. The model should decide its own next step in a loop. It picks a tool, looks at the result and decides what to do next until it can answer my criteria.

The key behavior is the deep dive. When a figure breaks one of my criteria or changes sharply (for example, operating margin falls from 18% to 11%), the agent investigates the cause before it judges. It might search the annual report for the segment that caused the drop.

## Ground rules

- **Every number is exact and has a source.** The model never does math in its head. All figures come from a calculation tool written in plain Python, and every number in the memo links back to its filing or data source.
- **No trading and no buy or sell calls.** The memo ends with findings, risks and open questions.
- **Step limit.** The agent stops after a fixed number of steps (around 15 to start) and says what it could not finish.
- **Uncertainty is stated.** If data is missing or unclear, the memo says so instead of guessing.
- **Secrets stay local.** The API key lives in a `.env` file that is in `.gitignore` from the very first commit. The repo will be public on GitHub with sample output only.

## Possible tools

- Get the financial statements for several years
- Calculate ratios and changes in Python
- Search the annual report for a topic
- Get the share price and valuation history
- Get a few competitors for comparison

Suggest changes to this list if you see a better setup.

## Data

Start with US companies through the SEC EDGAR data, because it is free, official and structured, which suits the source requirement. European companies (annual report PDFs) can come later. Price data can come from a free source, but the project should not break if that source fails.

## Version plan

1. **Version 1** is a fixed workflow for one company. It produces exact numbers with sources and a memo as a markdown file.
2. **Version 2** gives the model the tools and lets it decide its own steps, including the deep dive.
3. **Version 3** adds a bear case pass that argues against the investment, memory (rerunning a company shows what changed since last time), the user interface and a README with a short demo.

## Out of scope

Automatic trading, real time data, multiple agents talking to each other, price predictions and backtesting.

## Open decision on the interface

I have not decided how I want to use it day to day. Options I see are a small local dashboard (for example Streamlit) where I enter a ticker, start a run and read the memo with charts of the key figures, or saving memos and a watchlist to Notion, which I already use. Give me your recommendation with the pros and cons before version 3. Version 1 only needs the markdown file.

## Questions to ask me first

1. Which markets should version 1 cover, only US companies or also European ones?
2. Which investment criteria do I want to use? Suggest a sensible default set with thresholds that I can react to.
3. Which AI provider and model should we use, and what monthly budget should the code respect?
4. Should the memo be in English or German, and how long should it be?
5. Which three to five companies should we use for testing?
6. How much explanation do I want while you build, and how comfortable am I with Python?
7. Should it only run on demand, or also recheck a watchlist on a schedule later?
8. Which interface do I lean toward at this point?
