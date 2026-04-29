"""Ingest prompt — product-focused analysis of documents."""

from .common import BELIEFS_INSTRUCTIONS, TOPICS_INSTRUCTIONS


def build_ingest_prompt(
    doc_text: str,
    doc_name: str,
) -> str:
    """Build a prompt for analyzing a product document."""
    return f"""You are a senior product manager analyzing a product document.

## Document: {doc_name}

## Content

{doc_text}

## Instructions

Analyze this document from a **product perspective** and provide:

1. **Document Summary** — What is this document about? What decisions or plans does it capture?
2. **User Impact** — What users or user segments are affected? What problems are being solved?
3. **Feature Implications** — What features, changes, or capabilities are described or implied?
4. **Risks & Gaps** — What's missing? What assumptions are untested? What could go wrong?
5. **Dependencies** — What does this depend on? What does it unlock?
6. **Prioritization Signals** — How important is this relative to other product work?

Be specific — reference sections, decisions, and stakeholders mentioned in the document.

{TOPICS_INSTRUCTIONS}

{BELIEFS_INSTRUCTIONS}
"""
