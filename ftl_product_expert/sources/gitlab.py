"""GitLab issue source using the glab CLI."""

from __future__ import annotations

import json
import subprocess

from .models import Issue, IssueComment


class GitLabSource:
    """Fetch issues from GitLab via the glab CLI."""

    def __init__(self, repo: str):
        """Args:
            repo: group/project slug (e.g., "mygroup/myproject")
        """
        self.repo = repo
        self.platform = "gitlab"

    def list_issues(
        self,
        state: str = "opened",
        labels: list[str] | None = None,
        limit: int = 100,
        page: int = 1,
    ) -> list[Issue]:
        """List issues from the project."""
        cmd = [
            "glab", "issue", "list",
            "--repo", self.repo,
            "--per-page", str(limit),
            "--page", str(page),
            "--output", "json",
        ]
        if state:
            if state == "closed":
                cmd.append("--closed")
            elif state == "all":
                cmd.append("--all")
            # "opened" is the default when --closed is not used
        if labels:
            cmd.extend(["--label", ",".join(labels)])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"glab issue list failed: {result.stderr.strip()}")

        raw_issues = json.loads(result.stdout)
        return [self._normalize(raw) for raw in raw_issues]

    def get_issue(self, iid: int) -> Issue:
        """Get a single issue with comments."""
        cmd = [
            "glab", "issue", "view", str(iid),
            "--repo", self.repo,
            "--output", "json",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"glab issue view failed: {result.stderr.strip()}")

        raw = json.loads(result.stdout)
        issue = self._normalize(raw)

        # Fetch comments separately
        comments_cmd = [
            "glab", "issue", "note", "list", str(iid),
            "--repo", self.repo,
            "--output", "json",
        ]
        comments_result = subprocess.run(comments_cmd, capture_output=True, text=True)
        if comments_result.returncode == 0 and comments_result.stdout.strip():
            try:
                raw_comments = json.loads(comments_result.stdout)
                for c in raw_comments:
                    issue.comments.append(IssueComment(
                        author=c.get("author", {}).get("username", ""),
                        body=c.get("body", ""),
                        created=c.get("created_at", ""),
                    ))
                issue.comment_count = len(issue.comments)
            except (json.JSONDecodeError, TypeError):
                pass

        return issue

    def _normalize(self, raw: dict) -> Issue:
        """Convert glab JSON to normalized Issue."""
        iid = raw.get("iid", raw.get("id", 0))
        labels = raw.get("labels", [])
        if isinstance(labels, str):
            labels = [l.strip() for l in labels.split(",") if l.strip()]

        assignees = []
        for a in raw.get("assignees", []):
            if isinstance(a, dict):
                assignees.append(a.get("username", ""))
            else:
                assignees.append(str(a))

        milestone = ""
        ms = raw.get("milestone")
        if ms and isinstance(ms, dict):
            milestone = ms.get("title", "")

        author = ""
        auth = raw.get("author")
        if auth and isinstance(auth, dict):
            author = auth.get("username", "")

        return Issue(
            id=f"GL-{iid}",
            title=raw.get("title", ""),
            url=raw.get("web_url", ""),
            platform=self.platform,
            body=raw.get("description", ""),
            state=raw.get("state", "").lower(),
            labels=labels,
            assignees=assignees,
            milestone=milestone,
            priority=raw.get("priority", "") or "",
            issue_type=raw.get("issue_type", "") or "",
            author=author,
            created=raw.get("created_at", ""),
            updated=raw.get("updated_at", ""),
            closed=raw.get("closed_at", "") or "",
            comment_count=raw.get("user_notes_count", 0),
            raw=raw,
        )
