"""Normalized issue model shared across all source adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class IssueComment:
    """A comment on an issue."""
    author: str
    body: str
    created: str
    url: str = ""


@dataclass
class Issue:
    """Normalized issue from any platform."""
    # Identity
    id: str              # e.g., "GH-123", "GL-456", "PROJ-789"
    title: str
    url: str
    platform: str        # "github", "gitlab", "jira"

    # Content
    body: str = ""
    state: str = ""      # "open", "closed", "in_progress", etc.
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    milestone: str = ""
    priority: str = ""   # "critical", "high", "medium", "low", ""
    issue_type: str = "" # "bug", "feature", "epic", "story", "task", ""

    # Relationships
    parent: str = ""     # parent epic/issue ID
    children: list[str] = field(default_factory=list)
    linked: list[str] = field(default_factory=list)  # related issues

    # Metadata
    author: str = ""
    created: str = ""
    updated: str = ""
    closed: str = ""
    comments: list[IssueComment] = field(default_factory=list)
    comment_count: int = 0

    # Raw data for platform-specific fields
    raw: dict = field(default_factory=dict)

    def summary(self) -> str:
        """One-line summary for topic queues."""
        parts = [f"[{self.state}]" if self.state else ""]
        if self.issue_type:
            parts.append(f"({self.issue_type})")
        parts.append(self.title)
        if self.assignees:
            parts.append(f"→ {', '.join(self.assignees)}")
        return " ".join(p for p in parts if p)

    def to_prompt_text(self) -> str:
        """Format issue for inclusion in an LLM prompt."""
        lines = [
            f"## {self.id}: {self.title}",
            f"- URL: {self.url}",
            f"- State: {self.state}",
        ]
        if self.issue_type:
            lines.append(f"- Type: {self.issue_type}")
        if self.priority:
            lines.append(f"- Priority: {self.priority}")
        if self.labels:
            lines.append(f"- Labels: {', '.join(self.labels)}")
        if self.assignees:
            lines.append(f"- Assignees: {', '.join(self.assignees)}")
        if self.milestone:
            lines.append(f"- Milestone: {self.milestone}")
        if self.parent:
            lines.append(f"- Parent: {self.parent}")
        if self.children:
            lines.append(f"- Children: {', '.join(self.children)}")
        if self.linked:
            lines.append(f"- Linked: {', '.join(self.linked)}")
        lines.append(f"- Created: {self.created}")
        if self.updated:
            lines.append(f"- Updated: {self.updated}")
        if self.author:
            lines.append(f"- Author: {self.author}")
        if self.body:
            lines.append(f"\n### Description\n\n{self.body}")
        if self.comments:
            lines.append(f"\n### Comments ({len(self.comments)})\n")
            for c in self.comments[:10]:
                lines.append(f"**{c.author}** ({c.created}):\n{c.body}\n")
            if len(self.comments) > 10:
                lines.append(f"... and {len(self.comments) - 10} more comments")
        return "\n".join(lines)
