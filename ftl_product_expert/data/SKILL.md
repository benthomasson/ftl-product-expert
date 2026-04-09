---
name: product-expert
description: Build expert knowledge bases from product management data — scan issues through a product lens, extract beliefs
argument-hint: "[init|scan|explore|topics|propose-beliefs|accept-beliefs|summary|status]"
allowed-tools: Bash(product-expert *), Bash(uv run product-expert *), Bash(uvx *ftl-product-expert*), Read, Grep, Glob
---

# Product Expert

Build expert knowledge bases from product management data (GitHub, GitLab, Jira) by analyzing issues through a product lens.

## How to Run

Try these in order until one works:
1. `product-expert $ARGUMENTS` (if installed via `uv tool install`)
2. `uv run product-expert $ARGUMENTS` (if in the repo with pyproject.toml)
3. `uvx --from git+https://github.com/benthomasson/ftl-product-expert product-expert $ARGUMENTS` (fallback)

## Typical Workflow

```bash
product-expert init github owner/repo --domain "Payment platform"
product-expert scan                           # fetch issues, product overview, populate queue
product-expert scan --limit 10 --all-pages    # paginate through all issues
product-expert explore                        # explore next topic
product-expert explore --pick 1,3,8           # explore multiple by index (stable indices)
product-expert topics                         # see exploration queue
product-expert propose-beliefs                # extract beliefs from entries
# edit proposed-beliefs.md: mark [ACCEPT] or [REJECT]
product-expert accept-beliefs                 # import accepted beliefs
product-expert summary                        # synthesize product summary from beliefs
product-expert status                         # dashboard
```

## Commands

- `init <platform> <target>` — Bootstrap knowledge base (github/gitlab/jira)
- `scan [--limit N] [--labels L] [--page P] [--all-pages] [--jql Q]` — Fetch and analyze issues with product lens
- `explore [--skip] [--pick N[,N,...]] [--loop N]` — Work through topic queue
- `topics [--all]` — Show exploration queue
- `propose-beliefs [--batch-size N]` — Extract beliefs from entries
- `accept-beliefs [--file F]` — Import accepted beliefs
- `summary` — Synthesize product summary from beliefs
- `status` — Dashboard

## Natural Language

If the user says:
- "analyze this product" → `product-expert init github owner/repo && product-expert scan`
- "what's the product state" → `product-expert summary`
- "what should I look at next" → `product-expert explore`
- "what are users asking for" → `product-expert scan --labels feature-request`
- "extract what we've learned" → `product-expert propose-beliefs`
- "how far along are we" → `product-expert status`
