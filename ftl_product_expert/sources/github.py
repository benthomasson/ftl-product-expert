"""GitHub issue source using the gh CLI."""

from __future__ import annotations

import json
import subprocess

from .models import Issue, IssueComment


class GitHubSource:
    """Fetch issues from GitHub via the gh CLI."""

    def __init__(self, repo: str):
        """Args:
            repo: owner/repo slug (e.g., "benthomasson/ftl-code-expert")
        """
        self.repo = repo
        self.platform = "github"

    def list_issues(
        self,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int = 100,
    ) -> list[Issue]:
        """List issues from the repository."""
        cmd = [
            "gh", "issue", "list",
            "--repo", self.repo,
            "--state", state,
            "--json", "number,title,url,body,state,labels,assignees,"
                      "milestone,author,createdAt,updatedAt,closedAt,comments",
            "--limit", str(limit),
        ]
        if labels:
            for label in labels:
                cmd.extend(["--label", label])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"gh issue list failed: {result.stderr.strip()}")

        raw_issues = json.loads(result.stdout)
        return [self._normalize(raw) for raw in raw_issues]

    def get_issue(self, number: int) -> Issue:
        """Get a single issue with full details and comments."""
        cmd = [
            "gh", "issue", "view", str(number),
            "--repo", self.repo,
            "--json", "number,title,url,body,state,labels,assignees,"
                      "milestone,author,createdAt,updatedAt,closedAt,comments",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"gh issue view failed: {result.stderr.strip()}")

        raw = json.loads(result.stdout)
        return self._normalize(raw)

    def _normalize(self, raw: dict) -> Issue:
        """Convert gh JSON to normalized Issue."""
        number = raw.get("number", 0)
        comments = []
        for c in raw.get("comments", []):
            comments.append(IssueComment(
                author=c.get("author", {}).get("login", ""),
                body=c.get("body", ""),
                created=c.get("createdAt", ""),
                url=c.get("url", ""),
            ))

        labels = [l.get("name", "") for l in raw.get("labels", [])]
        assignees = [a.get("login", "") for a in raw.get("assignees", [])]

        milestone = ""
        ms = raw.get("milestone")
        if ms:
            milestone = ms.get("title", "") if isinstance(ms, dict) else str(ms)

        return Issue(
            id=f"GH-{number}",
            title=raw.get("title", ""),
            url=raw.get("url", ""),
            platform=self.platform,
            body=raw.get("body", ""),
            state=raw.get("state", "").lower(),
            labels=labels,
            assignees=assignees,
            milestone=milestone,
            author=raw.get("author", {}).get("login", ""),
            created=raw.get("createdAt", ""),
            updated=raw.get("updatedAt", ""),
            closed=raw.get("closedAt", "") or "",
            comments=comments,
            comment_count=len(comments),
            raw=raw,
        )
