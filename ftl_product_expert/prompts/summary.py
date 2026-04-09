"""Summary prompt — synthesize product state from beliefs."""


def build_summary_prompt(
    beliefs_text: str,
    project_name: str,
    belief_count: int,
) -> str:
    """Build a prompt for summarizing product state from beliefs."""
    return f"""You are a senior product manager synthesizing a comprehensive product summary from verified beliefs.

## Product: {project_name}
## Beliefs analyzed: {belief_count}

## Beliefs

{beliefs_text}

## Instructions

Synthesize these beliefs into a single, authoritative product summary. This should be the document a CPO reads to understand the product's current state in 5 minutes.

1. **Product Overview** — What is this product? Who are its users? What value does it deliver?
2. **Feature Health** — Which feature areas are strong? Which are degraded or missing?
3. **User Experience** — What's the current user experience like? Where are the pain points?
4. **Product-Market Fit Signals** — What do the beliefs tell you about fit? Adoption? Retention?
5. **Key Risks** — The top 3-5 product risks, ordered by user impact
6. **Competitive Position** — Where is the product strong vs. vulnerable?
7. **Strategic Themes** — What cross-cutting patterns emerge from the beliefs?
8. **Recommendations** — Top 3-5 product actions, ordered by expected impact on users

Be concrete — reference specific beliefs, user segments, and feature areas. Avoid generic product advice.
"""
