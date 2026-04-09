"""Jira issue source using the REST API."""

from __future__ import annotations

import os

import requests

from .models import Issue, IssueComment


class JiraSource:
    """Fetch issues from Jira via the REST API.

    Authentication via environment variables:
        JIRA_URL: Base URL (e.g., https://mycompany.atlassian.net)
        JIRA_USER: Email address
        JIRA_TOKEN: API token
    """

    def __init__(
        self,
        project: str,
        url: str | None = None,
        user: str | None = None,
        token: str | None = None,
    ):
        self.project = project
        self.base_url = (url or os.environ.get("JIRA_URL", "")).rstrip("/")
        self.user = user or os.environ.get("JIRA_USER", "")
        self.token = token or os.environ.get("JIRA_TOKEN", "")
        self.platform = "jira"

        if not self.base_url:
            raise ValueError("JIRA_URL not set. Set env var or pass url=")
        if not self.user or not self.token:
            raise ValueError("JIRA_USER and JIRA_TOKEN must be set")

    def _get(self, path: str, params: dict | None = None) -> dict:
        """Make authenticated GET request to Jira API."""
        url = f"{self.base_url}/rest/api/3/{path.lstrip('/')}"
        resp = requests.get(
            url,
            params=params,
            auth=(self.user, self.token),
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def list_issues(
        self,
        jql: str | None = None,
        state: str | None = None,
        labels: list[str] | None = None,
        limit: int = 100,
    ) -> list[Issue]:
        """List issues from the project.

        Args:
            jql: Custom JQL query. If not set, builds one from project/state/labels.
            state: Filter by status category (e.g., "To Do", "In Progress", "Done")
            labels: Filter by labels
            limit: Max results
        """
        if jql is None:
            parts = [f'project = "{self.project}"']
            if state:
                parts.append(f'statusCategory = "{state}"')
            if labels:
                label_clause = " AND ".join(f'labels = "{l}"' for l in labels)
                parts.append(f"({label_clause})")
            jql = " AND ".join(parts) + " ORDER BY updated DESC"

        data = self._get("search", params={
            "jql": jql,
            "maxResults": limit,
            "fields": "summary,description,status,labels,assignee,reporter,"
                      "priority,issuetype,parent,issuelinks,created,updated,"
                      "resolutiondate,comment,fixVersions",
        })

        return [self._normalize(raw) for raw in data.get("issues", [])]

    def get_issue(self, key: str) -> Issue:
        """Get a single issue with full details."""
        data = self._get(f"issue/{key}", params={
            "fields": "summary,description,status,labels,assignee,reporter,"
                      "priority,issuetype,parent,issuelinks,created,updated,"
                      "resolutiondate,comment,fixVersions,subtasks",
            "expand": "renderedFields",
        })
        return self._normalize(data)

    def _normalize(self, raw: dict) -> Issue:
        """Convert Jira JSON to normalized Issue."""
        fields = raw.get("fields", {})
        key = raw.get("key", "")

        # Assignee
        assignees = []
        assignee = fields.get("assignee")
        if assignee:
            assignees.append(
                assignee.get("displayName", assignee.get("emailAddress", ""))
            )

        # Author/reporter
        author = ""
        reporter = fields.get("reporter")
        if reporter:
            author = reporter.get("displayName", reporter.get("emailAddress", ""))

        # Priority
        priority = ""
        pri = fields.get("priority")
        if pri:
            priority = pri.get("name", "").lower()

        # Issue type
        issue_type = ""
        itype = fields.get("issuetype")
        if itype:
            issue_type = itype.get("name", "").lower()

        # State
        state = ""
        status = fields.get("status")
        if status:
            state = status.get("name", "").lower()

        # Parent
        parent = ""
        parent_field = fields.get("parent")
        if parent_field:
            parent = parent_field.get("key", "")

        # Children (subtasks)
        children = []
        for sub in fields.get("subtasks", []):
            children.append(sub.get("key", ""))

        # Linked issues
        linked = []
        for link in fields.get("issuelinks", []):
            if "outwardIssue" in link:
                linked.append(link["outwardIssue"].get("key", ""))
            if "inwardIssue" in link:
                linked.append(link["inwardIssue"].get("key", ""))

        # Description — Jira uses ADF, try rendered or fall back to raw
        body = ""
        desc = fields.get("description")
        if isinstance(desc, str):
            body = desc
        elif isinstance(desc, dict):
            # Atlassian Document Format — extract text content
            body = self._extract_adf_text(desc)

        # Milestone (fix versions)
        milestone = ""
        fix_versions = fields.get("fixVersions", [])
        if fix_versions:
            milestone = fix_versions[0].get("name", "")

        # Comments
        comments = []
        comment_data = fields.get("comment", {})
        for c in comment_data.get("comments", []):
            c_body = c.get("body", "")
            if isinstance(c_body, dict):
                c_body = self._extract_adf_text(c_body)
            c_author = c.get("author", {})
            comments.append(IssueComment(
                author=c_author.get("displayName", c_author.get("emailAddress", "")),
                body=c_body,
                created=c.get("created", ""),
            ))

        return Issue(
            id=key,
            title=fields.get("summary", ""),
            url=f"{self.base_url}/browse/{key}",
            platform=self.platform,
            body=body,
            state=state,
            labels=fields.get("labels", []),
            assignees=assignees,
            milestone=milestone,
            priority=priority,
            issue_type=issue_type,
            parent=parent,
            children=children,
            linked=linked,
            author=author,
            created=fields.get("created", ""),
            updated=fields.get("updated", ""),
            closed=fields.get("resolutiondate", "") or "",
            comments=comments,
            comment_count=comment_data.get("total", len(comments)),
            raw=raw,
        )

    def _extract_adf_text(self, doc: dict) -> str:
        """Extract plain text from Atlassian Document Format."""
        parts = []

        def _walk(node):
            if isinstance(node, str):
                parts.append(node)
                return
            if isinstance(node, dict):
                if node.get("type") == "text":
                    parts.append(node.get("text", ""))
                for child in node.get("content", []):
                    _walk(child)

        _walk(doc)
        return "\n".join(parts) if parts else ""
