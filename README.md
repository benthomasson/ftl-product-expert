# product-expert

Deep product analysis through belief networks. Scans issue trackers (GitHub, GitLab, Jira) with a product management lens, extracts factual beliefs about product state, and surfaces risks that dashboards and status meetings miss.

**What it finds:** Not project delays, but product gaps — features users need that aren't being built, user experience pain points buried across multiple issues, competitive risks from missing table-stakes features, and prioritization misalignment between what's getting built and what matters to users.

**How it works:** product-expert fetches issues from your tracker, analyzes them through a product lens (user impact, competitive context, product-market fit), extracts factual claims into a reason maintenance system, and synthesizes a product summary from verified beliefs.

## Install

```bash
uv tool install git+https://github.com/benthomasson/ftl-product-expert
```

Prerequisites — these CLIs must be on your PATH:

- [`entry`](https://github.com/benthomasson/entry) — chronological entry creation
- [`beliefs`](https://github.com/benthomasson/beliefs) or [`reasons`](https://github.com/benthomasson/reasons) — belief registry management
- `claude` or `gemini` — at least one LLM CLI

Platform CLIs (install whichever you need):

- [`gh`](https://cli.github.com) — GitHub CLI
- [`glab`](https://gitlab.com/gitlab-org/cli) — GitLab CLI
- For Jira: set `JIRA_URL`, `JIRA_USER`, `JIRA_TOKEN` env vars

## Quick Start

```bash
# 1. Point product-expert at an issue tracker
product-expert init github owner/repo --domain "Payment platform"

# 2. Scan issues for a product overview
product-expert scan

# 3. Explore topics one at a time
product-expert explore              # next topic
product-expert explore --pick 3     # specific topic
product-expert explore --pick 1,3,8 # multiple (stable indices)
product-expert explore --skip       # skip and move on

# 4. Extract beliefs from exploration entries
product-expert propose-beliefs
# Review proposed-beliefs.md — mark entries ACCEPT or REJECT
product-expert accept-beliefs

# 5. Build a product summary from beliefs
product-expert summary

# 6. Check progress
product-expert status
```

## How It Works

product-expert follows a **scan -> explore -> distill -> reason -> summarize** pipeline:

```
scan              Fetch issues -> product-focused LLM analysis -> topic queue
  |
  v
explore           Pop one topic, fetch full issue, analyze product impact
  |                 |- feature      Feature request deep-dive
  |                 |- epic         Epic with user stories and scope
  |                 |- roadmap      Roadmap alignment assessment
  |                 |- user-story   User story analysis
  |                 |- feedback     User feedback pattern analysis
  |                 +- general      Cross-cutting product analysis
  |
  v
propose-beliefs   Batch-extract factual claims from entries
  |
  v
accept-beliefs    Import reviewed claims into beliefs.md / reasons.db
  |
  v
summary           Synthesize product summary from verified beliefs
```

## Commands

### `product-expert init <platform> <target>`

Bootstrap a knowledge base.

### `product-expert scan`

Fetch issues and produce a product-focused overview covering feature landscape, user impact, product gaps, and prioritization concerns.

```bash
product-expert scan --limit 10 --all-pages  # paginate through all issues
```

### `product-expert explore`

Deep-dive into topics with product analysis: user stories, competitive context, success criteria.

### `product-expert propose-beliefs` / `accept-beliefs`

Extract and import factual claims about the product.

### `product-expert summary`

Synthesize a product summary from verified beliefs — the document a CPO reads to understand product state in 5 minutes.

### `product-expert status`

Dashboard showing entries, beliefs, topic queue, and cached issues.

## Global Options

| Option | Description |
|--------|-------------|
| `--model`, `-m` | Model to use: `claude` or `gemini` (default: claude) |
| `--quiet`, `-q` | Suppress explanation output to stdout |
| `--timeout`, `-t` | LLM timeout in seconds (default: 300) |
| `--version` | Show version |
