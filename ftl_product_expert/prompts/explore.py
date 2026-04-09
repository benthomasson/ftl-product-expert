"""Explore prompt — product-focused deep dive into a specific issue or topic."""

from .common import BELIEFS_INSTRUCTIONS, TOPICS_INSTRUCTIONS


def build_explore_prompt(
    issue_text: str,
    context_text: str | None = None,
    question: str | None = None,
) -> str:
    """Build a prompt for exploring a topic with a product lens."""
    sections = [
        "You are a senior product manager analyzing a product issue or feature in depth.",
        "",
    ]

    if question:
        sections.append(f"**Focus question:** {question}")
        sections.append("")

    sections.extend([
        "## Issue Details",
        "",
        issue_text,
        "",
    ])

    if context_text:
        sections.extend([
            "## Related Issues",
            "",
            context_text,
            "",
        ])

    sections.extend([
        "## Instructions",
        "",
        "Provide a thorough product analysis:",
        "",
        "1. **User Story** — Who wants this and why? What's the user's job-to-be-done?",
        "2. **User Impact** — How many users are affected? What's the severity? What workarounds exist?",
        "3. **Product Fit** — Does this align with the product vision? Is it a core feature or edge case?",
        "4. **Competitive Context** — Do competitors handle this? Is this table-stakes or differentiating?",
        "5. **Dependencies** — What does this need? What does it unlock?",
        "6. **Prioritization** — Should this be built now? What's the cost of delay?",
        "7. **Success Criteria** — How would you measure success? What metrics move?",
        "",
        "Be specific — reference issue IDs, user segments, and business impact.",
        "",
        TOPICS_INSTRUCTIONS,
        BELIEFS_INSTRUCTIONS,
    ])

    return "\n".join(sections)
