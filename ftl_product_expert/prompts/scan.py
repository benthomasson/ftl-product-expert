"""Scan prompt — product-focused overview of issues."""

from .common import BELIEFS_INSTRUCTIONS, TOPICS_INSTRUCTIONS


def build_scan_prompt(
    issues_text: str,
    project_name: str,
    platform: str,
    issue_count: int,
) -> str:
    """Build a prompt for scanning issues with a product lens."""
    return f"""You are a senior product manager analyzing a product's issue tracker.

## Product: {project_name}
## Platform: {platform}
## Total issues scanned: {issue_count}

## Issues

{issues_text}

## Instructions

Analyze these issues from a **product perspective** and provide:

1. **Product Overview** — What is this product? Who are its users? What problems does it solve?
2. **Feature Landscape** — What feature areas are represented? Which are getting the most attention?
3. **User Impact** — Which issues affect users most? What's the user experience cost of open bugs?
4. **Product Gaps** — What's missing? What are users asking for that isn't being built?
5. **Roadmap Signals** — What do the issues tell you about product direction? Are there strategic themes?
6. **Competitive Risk** — Any issues that suggest the product is falling behind? Table-stakes features missing?
7. **Prioritization Concerns** — Are high-impact items getting attention? Are low-value items consuming capacity?

Be specific — reference issue IDs, user impact, and feature areas. Think like a product manager, not a project manager.

{TOPICS_INSTRUCTIONS}

{BELIEFS_INSTRUCTIONS}
"""
