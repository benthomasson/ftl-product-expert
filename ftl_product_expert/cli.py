"""Command-line interface for product expert."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import click

from .llm import check_model_available, invoke, invoke_sync
from .prompts import (
    PROPOSE_BELIEFS_PRODUCT,
    build_explore_prompt,
    build_ingest_prompt,
    build_scan_prompt,
    build_summary_prompt,
)
from .sources import GitHubSource, GitLabSource, Issue, JiraSource
from .topics import (
    Topic,
    add_topics,
    load_queue,
    parse_topics_from_response,
    pending_count,
    pop_at,
    pop_multiple,
    pop_next,
    skip_topic,
)

PROJECT_DIR = ".product-expert"

_RELATIVE_DATE_RE = re.compile(r"(\d+)\s*(day|week|month)s?\s*ago", re.IGNORECASE)


def _parse_since_date(since_str: str) -> datetime:
    """Parse a --since value into a datetime.

    Accepts ISO dates (2026-04-01) or relative strings (1 week ago, 7 days ago).
    """
    m = _RELATIVE_DATE_RE.match(since_str.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit == "day":
            return datetime.now() - timedelta(days=n)
        elif unit == "week":
            return datetime.now() - timedelta(weeks=n)
        elif unit == "month":
            return datetime.now() - timedelta(days=n * 30)
    try:
        return datetime.fromisoformat(since_str.strip())
    except ValueError:
        raise click.BadParameter(
            f"Cannot parse date: {since_str!r}. "
            "Use ISO format (2026-04-01) or relative (7 days ago, 1 week ago)."
        )


def _load_scan_checkpoint(project_dir: str | None = None) -> str | None:
    """Load the last scan timestamp from checkpoint file."""
    pdir = project_dir or str(Path.cwd() / PROJECT_DIR)
    cp = Path(pdir) / "scan-checkpoint.json"
    if cp.is_file():
        data = json.loads(cp.read_text())
        return data.get("timestamp")
    return None


def _save_scan_checkpoint(project_dir: str | None = None) -> None:
    """Save the current timestamp as the scan checkpoint."""
    pdir = project_dir or str(Path.cwd() / PROJECT_DIR)
    cp = Path(pdir) / "scan-checkpoint.json"
    os.makedirs(pdir, exist_ok=True)
    cp.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "project": str(Path.cwd()),
    }, indent=2))


def _parse_issue_updated(updated: str) -> datetime:
    """Parse an issue's updated timestamp to a naive datetime for comparison."""
    if not updated:
        return datetime.min
    cleaned = updated.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt.replace(tzinfo=None)
    except ValueError:
        return datetime.min


# --- Config helpers ---


def _load_config() -> dict | None:
    config_path = Path.cwd() / PROJECT_DIR / "config.json"
    if config_path.is_file():
        return json.loads(config_path.read_text())
    return None


def _save_config(config: dict) -> None:
    config_dir = Path.cwd() / PROJECT_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(config, indent=2))


def _get_project_dir() -> str:
    return str(Path.cwd() / PROJECT_DIR)


# --- Source helpers ---


def _get_source(config: dict) -> GitHubSource | GitLabSource | JiraSource:
    """Create the appropriate source from config."""
    platform = config["platform"]
    if platform == "github":
        return GitHubSource(config["repo"])
    elif platform == "gitlab":
        return GitLabSource(config["repo"])
    elif platform == "jira":
        return JiraSource(
            config["project"],
            url=config.get("jira_url"),
        )
    else:
        raise ValueError(f"Unknown platform: {platform}")


# --- Output helpers ---


def _emit(ctx, text: str) -> None:
    if not ctx.obj.get("quiet"):
        click.echo(text)


def _create_entry(topic: str, title: str, content: str) -> None:
    try:
        result = subprocess.run(
            ["entry", "create", topic, title, "--content", content],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            click.echo(f"Entry: {result.stdout.strip()}", err=True)
        else:
            result = subprocess.run(
                ["entry", "create", topic, title],
                input=content,
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                click.echo(f"Entry: {result.stdout.strip()}", err=True)
            else:
                click.echo(f"WARN: entry create failed: {result.stderr.strip()}", err=True)
    except FileNotFoundError:
        click.echo("WARN: entry CLI not found. Install with: uv tool install ftl-entry", err=True)


def _enqueue_topics(response: str, source: str, project_dir: str | None = None) -> None:
    new_topics = parse_topics_from_response(response, source=source)
    if new_topics:
        added = add_topics(new_topics, project_dir)
        if added:
            total = pending_count(project_dir)
            click.echo(f"Queued {added} new topic(s) ({total} pending)", err=True)


def _has_reasons() -> bool:
    return shutil.which("reasons") is not None


def _parse_beliefs_from_response(response: str) -> list[dict]:
    section_match = re.search(
        r"#+\s*Beliefs?\s*\n(.*?)(?=\n#|\Z)",
        response, re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return []
    beliefs = []
    pattern = re.compile(r"^[-*]\s+`([^`]+)`\s*(?:—|-|:)\s*(.+)$", re.MULTILINE)
    for match in pattern.finditer(section_match.group(1)):
        beliefs.append({"id": match.group(1), "text": match.group(2).strip()})
    return beliefs


def _report_beliefs(response: str) -> None:
    beliefs = _parse_beliefs_from_response(response)
    if beliefs:
        click.echo(f"Surfaced {len(beliefs)} belief(s):", err=True)
        for b in beliefs[:5]:
            click.echo(f"  {b['id']}: {b['text'][:80]}", err=True)


def _reasons_export():
    beliefs_path = Path("beliefs.md")
    network_path = Path("network.json")
    result = subprocess.run(["reasons", "export-markdown"], capture_output=True, text=True)
    if result.returncode == 0:
        beliefs_path.write_text(result.stdout)
        click.echo(f"Updated {beliefs_path}")
    result = subprocess.run(["reasons", "export"], capture_output=True, text=True)
    if result.returncode == 0:
        network_path.write_text(result.stdout)
        click.echo(f"Updated {network_path}")


# --- CLI ---


@click.group()
@click.version_option(package_name="ftl-product-expert")
@click.option("--quiet", "-q", is_flag=True, default=False,
              help="Suppress output to stdout")
@click.option("--model", "-m", default="claude", help="Model to use (default: claude)")
@click.option("--timeout", "-t", default=300, type=int, help="LLM timeout in seconds")
@click.pass_context
def cli(ctx, quiet, model, timeout):
    """Build expert knowledge bases from product management data."""
    ctx.ensure_object(dict)
    ctx.obj["quiet"] = quiet
    ctx.obj["model"] = model
    ctx.obj["timeout"] = timeout


# --- init ---


@cli.command()
@click.argument("platform", type=click.Choice(["github", "gitlab", "jira"]))
@click.argument("target", type=str)
@click.option("--domain", "-d", default=None, help="One-line product description")
@click.option("--jira-url", default=None, help="Jira base URL (for jira platform)")
def init(platform, target, domain, jira_url):
    """Bootstrap a product-expert knowledge base.

    TARGET is owner/repo for GitHub/GitLab, or project key for Jira.

    Examples:
        product-expert init github owner/repo
        product-expert init gitlab group/project
        product-expert init jira MYPROJ --jira-url https://myco.atlassian.net
    """
    if not domain:
        domain = target

    # Check prerequisites
    for tool in ["entry"]:
        if not shutil.which(tool):
            click.echo(f"Error: {tool} not found on PATH", err=True)
            sys.exit(1)
    if not shutil.which("reasons") and not shutil.which("beliefs"):
        click.echo("Error: neither reasons nor beliefs found on PATH", err=True)
        sys.exit(1)

    # Check platform CLI
    if platform == "github" and not shutil.which("gh"):
        click.echo("Error: gh CLI not found. Install from https://cli.github.com", err=True)
        sys.exit(1)
    if platform == "gitlab" and not shutil.which("glab"):
        click.echo("Error: glab CLI not found.", err=True)
        sys.exit(1)
    if platform == "jira":
        if not jira_url and not os.environ.get("JIRA_URL"):
            click.echo("Error: --jira-url or JIRA_URL env var required for Jira", err=True)
            sys.exit(1)

    # Create project dir
    project_dir = Path.cwd() / PROJECT_DIR
    project_dir.mkdir(parents=True, exist_ok=True)

    # Save config
    config = {
        "platform": platform,
        "domain": domain,
        "created": date.today().isoformat(),
    }
    if platform in ("github", "gitlab"):
        config["repo"] = target
    else:
        config["project"] = target
        config["jira_url"] = jira_url or os.environ.get("JIRA_URL", "")

    _save_config(config)

    # Create entries dir
    Path("entries").mkdir(exist_ok=True)

    # Init belief store
    if _has_reasons():
        if not Path("reasons.db").exists():
            subprocess.run(["reasons", "init"], capture_output=True)
            click.echo("Initialized reasons.db")
        if not Path("beliefs.md").exists():
            _reasons_export()
    elif not Path("beliefs.md").exists():
        subprocess.run(["beliefs", "init"], capture_output=True)
        click.echo("Initialized beliefs.md")

    click.echo(f"\nInitialized product-expert")
    click.echo(f"  Platform: {platform}")
    click.echo(f"  Target:   {target}")
    click.echo(f"  Domain:   {domain}")
    click.echo(f"\nNext: product-expert scan")


# --- scan ---


@cli.command()
@click.option("--state", "-s", default=None,
              help="Issue state filter (default: open/opened)")
@click.option("--labels", "-l", default=None,
              help="Comma-separated labels to filter by")
@click.option("--limit", default=100, type=int,
              help="Max issues per page (default: 100)")
@click.option("--page", default=1, type=int,
              help="Page number for pagination (default: 1)")
@click.option("--all-pages", is_flag=True, default=False,
              help="Auto-paginate through all issues (uses --limit as page size)")
@click.option("--jql", default=None,
              help="Custom JQL query (Jira only)")
@click.option("--since", "since_str", default=None,
              help="Only process issues updated since date (e.g., 2026-04-01, '1 week ago')")
@click.option("--since-last", is_flag=True, default=False,
              help="Only process issues updated since last scan checkpoint")
@click.pass_context
def scan(ctx, state, labels, limit, page, all_pages, jql, since_str, since_last):
    """Scan product issues and create a product-focused overview."""
    config = _load_config()
    if not config:
        click.echo("Not initialized. Run: product-expert init <platform> <target>")
        sys.exit(1)

    model = ctx.obj["model"]
    timeout = ctx.obj["timeout"]
    project_dir = _get_project_dir()

    # Resolve --since / --since-last
    since_date = None
    if since_last:
        ts = _load_scan_checkpoint(project_dir)
        if not ts:
            click.echo("No scan checkpoint found. Run a scan first, or use --since.", err=True)
            sys.exit(1)
        since_date = _parse_since_date(ts)
        click.echo(f"Scanning issues updated since {since_date.isoformat()}", err=True)
    elif since_str:
        since_date = _parse_since_date(since_str)
        click.echo(f"Scanning issues updated since {since_date.isoformat()}", err=True)

    if not check_model_available(model):
        click.echo(f"Error: Model '{model}' CLI not available", err=True)
        sys.exit(1)

    source = _get_source(config)
    label_list = [l.strip() for l in labels.split(",") if l.strip()] if labels else None

    # Set default state per platform
    if state is None:
        state = "opened" if config["platform"] == "gitlab" else "open"
        if config["platform"] == "jira":
            state = None  # Jira uses JQL

    if all_pages:
        current_page = 1
        total_scanned = 0
        while True:
            click.echo(f"\n{'=' * 40}", err=True)
            click.echo(f"Page {current_page}", err=True)
            click.echo(f"{'=' * 40}", err=True)
            count = _scan_page(
                ctx, config, source, model, timeout, project_dir,
                state, label_list, limit, current_page, jql, since_date,
            )
            if count == 0:
                if total_scanned == 0:
                    click.echo("No issues found.")
                else:
                    click.echo(f"\nDone. Scanned {total_scanned} issues across {current_page - 1} pages.", err=True)
                break
            total_scanned += count
            if count < limit:
                click.echo(f"\nDone. Scanned {total_scanned} issues across {current_page} pages.", err=True)
                break
            current_page += 1
    else:
        _scan_page(
            ctx, config, source, model, timeout, project_dir,
            state, label_list, limit, page, jql, since_date,
        )

    _save_scan_checkpoint(project_dir)


def _scan_page(ctx, config, source, model, timeout, project_dir,
               state, label_list, limit, page, jql, since_date=None):
    """Scan a single page of issues. Returns the number of issues fetched."""
    project_name = config.get("repo", config.get("project", "unknown"))
    click.echo(f"Scanning {project_name} (page {page})...", err=True)

    try:
        if config["platform"] == "jira":
            issues = source.list_issues(jql=jql, state=state, labels=label_list, limit=limit, page=page)
        elif config["platform"] == "gitlab":
            issues = source.list_issues(state=state, labels=label_list, limit=limit, page=page)
        else:
            issues = source.list_issues(state=state, labels=label_list, limit=limit)
    except Exception as e:
        click.echo(f"Error fetching issues: {e}", err=True)
        sys.exit(1)

    if not issues:
        return 0

    fetched_count = len(issues)
    if since_date:
        naive_since = since_date.replace(tzinfo=None)
        issues = [i for i in issues if _parse_issue_updated(i.updated) >= naive_since]
        click.echo(f"Fetched {fetched_count} issues, {len(issues)} after --since filter", err=True)
        if not issues:
            return 0
    else:
        click.echo(f"Fetched {len(issues)} issues", err=True)

    # Build prompt
    issues_text = "\n\n".join(issue.to_prompt_text() for issue in issues)

    prompt = build_scan_prompt(
        issues_text=issues_text,
        project_name=project_name,
        platform=config["platform"],
        issue_count=len(issues),
    )

    click.echo(f"Running {model}...", err=True)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Strip URL prefix for entry naming
    short_name = project_name.split("//")[-1] if "//" in project_name else project_name
    safe_name = short_name.replace("/", "-")
    page_suffix = f"-p{page}" if page > 1 else ""
    _create_entry(f"scan-{safe_name}{page_suffix}", f"Scan: {project_name} (page {page})", result)
    _enqueue_topics(result, source=f"scan:{project_name}", project_dir=project_dir)
    _report_beliefs(result)

    # Cache issues for explore
    _cache_issues(issues, project_dir)

    _emit(ctx, result)
    return len(issues)


def _cache_issues(issues: list[Issue], project_dir: str) -> None:
    """Cache fetched issues so explore can reference them without re-fetching."""
    cache_path = os.path.join(project_dir, "issues-cache.json")
    # Merge with existing cache
    data = {}
    if os.path.isfile(cache_path):
        with open(cache_path) as f:
            data = json.load(f)
    for issue in issues:
        data[issue.id] = {
            "id": issue.id,
            "title": issue.title,
            "url": issue.url,
            "platform": issue.platform,
            "body": issue.body,
            "state": issue.state,
            "labels": issue.labels,
            "assignees": issue.assignees,
            "milestone": issue.milestone,
            "priority": issue.priority,
            "issue_type": issue.issue_type,
            "parent": issue.parent,
            "children": issue.children,
            "linked": issue.linked,
            "author": issue.author,
            "created": issue.created,
            "updated": issue.updated,
            "comment_count": issue.comment_count,
        }
    os.makedirs(project_dir, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)


def _load_cached_issues(project_dir: str) -> dict:
    """Load cached issues."""
    cache_path = os.path.join(project_dir, "issues-cache.json")
    if not os.path.isfile(cache_path):
        return {}
    with open(cache_path) as f:
        return json.load(f)


# --- ingest ---


@cli.command()
@click.argument("docs_dir", type=click.Path(exists=True))
@click.option("--glob-pattern", "-g", default="**/*.md",
              help="Glob pattern for files to ingest (default: **/*.md)")
@click.pass_context
def ingest(ctx, docs_dir, glob_pattern):
    """Ingest markdown documents for product analysis.

    Reads markdown files from DOCS_DIR, analyzes each through a product lens,
    creates entries, and queues follow-up topics.

    Examples:
        product-expert ingest ./prds
        product-expert ingest ~/docs/specs -g "*.md"
        product-expert ingest ./research -g "**/*.txt"
    """
    config = _load_config()
    if not config:
        click.echo("Not initialized. Run: product-expert init <platform> <target>")
        sys.exit(1)

    model = ctx.obj["model"]
    timeout = ctx.obj["timeout"]
    project_dir = _get_project_dir()

    if not check_model_available(model):
        click.echo(f"Error: Model '{model}' CLI not available", err=True)
        sys.exit(1)

    docs_path = Path(docs_dir)
    files = sorted(docs_path.glob(glob_pattern))

    if not files:
        click.echo(f"No files matching '{glob_pattern}' in {docs_dir}")
        return

    click.echo(f"Found {len(files)} document(s) to ingest", err=True)

    for i, file_path in enumerate(files):
        click.echo(f"\n{'=' * 40}", err=True)
        click.echo(f"[{i + 1}/{len(files)}] {file_path.name}", err=True)
        click.echo(f"{'=' * 40}", err=True)

        content = file_path.read_text()
        if not content.strip():
            click.echo(f"  Skipping empty file", err=True)
            continue

        if len(content) > 50000:
            content = content[:50000] + "\n\n[Truncated — original was {len(content)} chars]"

        prompt = build_ingest_prompt(
            doc_text=content,
            doc_name=file_path.name,
        )

        click.echo(f"Analyzing with {model}...", err=True)
        try:
            result = asyncio.run(invoke(prompt, model, timeout=timeout))
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            continue

        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", file_path.stem)[:80]
        _create_entry(f"ingest-{safe_name}", f"Ingest: {file_path.name}", result)
        _enqueue_topics(result, source=f"ingest:{file_path.name}", project_dir=project_dir)
        _report_beliefs(result)

        _emit(ctx, result)

    click.echo(f"\nIngested {len(files)} document(s).", err=True)


# --- topics ---


@cli.command()
@click.option("--all", "show_all", is_flag=True, default=False,
              help="Show all topics including done and skipped")
def topics(show_all):
    """Show the exploration queue."""
    queue = load_queue(_get_project_dir())

    if not queue:
        click.echo("No topics queued. Run `product-expert scan` to discover topics.")
        return

    pending = [t for t in queue if t.status == "pending"]
    done = [t for t in queue if t.status == "done"]
    skipped = [t for t in queue if t.status == "skipped"]

    if pending:
        click.echo(f"Pending ({len(pending)}):\n")
        for i, topic in enumerate(pending):
            click.echo(f"  {i}. [{topic.kind}] {topic.target}")
            click.echo(f"     {topic.title}")
            if topic.source:
                click.echo(f"     (from {topic.source})")
            click.echo()
    else:
        click.echo("No pending topics.")

    if show_all:
        if done:
            click.echo(f"Done ({len(done)}):\n")
            for topic in done:
                click.echo(f"  [{topic.kind}] {topic.target} - {topic.title}")
        if skipped:
            click.echo(f"\nSkipped ({len(skipped)}):\n")
            for topic in skipped:
                click.echo(f"  [{topic.kind}] {topic.target} - {topic.title}")

    click.echo(f"\n{len(pending)} pending, {len(done)} done, {len(skipped)} skipped")


# --- explore ---


@cli.command()
@click.option("--skip", "do_skip", is_flag=True, default=False,
              help="Skip the next topic")
@click.option("--pick", "pick_index", type=str, default=None,
              help="Pick topic(s) by index — single (3) or comma-separated (1,3,8)")
@click.option("--loop", "loop_max", type=int, default=None,
              help="Continuously explore up to N topics")
@click.pass_context
def explore(ctx, do_skip, pick_index, loop_max):
    """Explore the next topic in the queue."""
    project_dir = _get_project_dir()

    if loop_max is not None:
        if do_skip or pick_index:
            click.echo("Error: --loop cannot be combined with --skip or --pick", err=True)
            sys.exit(1)
        _explore_loop(ctx, project_dir, loop_max)
        return

    if do_skip:
        if skip_topic(0, project_dir):
            queue = load_queue(project_dir)
            pending = [t for t in queue if t.status == "pending"]
            if pending:
                click.echo(f"Skipped. Next: [{pending[0].kind}] {pending[0].target}")
            else:
                click.echo("Skipped. No more pending topics.")
        else:
            click.echo("Nothing to skip.")
        return

    if pick_index is not None:
        try:
            indices = [int(x.strip()) for x in pick_index.split(",")]
        except ValueError:
            click.echo(f"Error: --pick must be integers, got: {pick_index}", err=True)
            sys.exit(1)
        if len(indices) > 1:
            topic_list = pop_multiple(indices, project_dir)
        else:
            topic_list = [pop_at(indices[0], project_dir)]
    else:
        topic_list = [pop_next(project_dir)]

    valid_topics = [(i, t) for i, t in zip(
        indices if pick_index is not None else [0],
        topic_list,
    ) if t is not None]

    if not valid_topics:
        click.echo("No pending topics. Run `product-expert scan` to discover topics.")
        return

    invalid_count = len(topic_list) - len(valid_topics)
    if invalid_count:
        click.echo(f"Warning: {invalid_count} index(es) out of bounds, skipped.", err=True)

    for seq, (idx, topic) in enumerate(valid_topics):
        if len(valid_topics) > 1:
            click.echo(f"\n{'=' * 40}", err=True)
            click.echo(f"[{seq + 1}/{len(valid_topics)}] Topic #{idx}", err=True)
            click.echo(f"{'=' * 40}", err=True)

        _run_topic(ctx, topic)

    remaining = pending_count(project_dir)
    if remaining:
        click.echo(f"\n{remaining} topic(s) remaining.", err=True)
    else:
        click.echo("\nNo more topics. Exploration complete.", err=True)


def _explore_loop(ctx, project_dir, max_topics):
    """Continuously explore topics up to max_topics."""
    explored = 0
    while explored < max_topics:
        topic = pop_next(project_dir)
        if topic is None:
            if explored == 0:
                click.echo("No pending topics. Run `product-expert scan` to discover topics.")
            else:
                click.echo(f"\nNo more topics after {explored} exploration(s).", err=True)
            return

        explored += 1
        remaining = pending_count(project_dir)
        click.echo(f"\n{'=' * 40}", err=True)
        click.echo(f"[{explored}/{max_topics}] ({remaining} remaining in queue)", err=True)
        click.echo(f"{'=' * 40}", err=True)

        _run_topic(ctx, topic)

    remaining = pending_count(project_dir)
    click.echo(f"\nExplored {explored} topic(s). {remaining} remaining.", err=True)


def _run_topic(ctx, topic: Topic):
    """Explore a single topic."""
    model = ctx.obj["model"]
    timeout = ctx.obj["timeout"]
    project_dir = _get_project_dir()
    config = _load_config()

    if not check_model_available(model):
        click.echo(f"Error: Model '{model}' CLI not available", err=True)
        sys.exit(1)

    click.echo(f"Topic: [{topic.kind}] {topic.target}", err=True)
    click.echo(f"  {topic.title}", err=True)
    click.echo(err=True)

    # Fetch issue details if it's an issue/epic/feature topic
    issue_text = ""
    context_text = ""

    if topic.kind in ("feature", "epic", "user-story") and config:
        try:
            source = _get_source(config)
            issue_id = topic.target
            if config["platform"] == "github":
                num = re.search(r"\d+", issue_id)
                if num:
                    issue = source.get_issue(int(num.group()))
                    issue_text = issue.to_prompt_text()

                    cached = _load_cached_issues(project_dir)
                    related_ids = issue.children + issue.linked
                    if issue.parent:
                        related_ids.append(issue.parent)
                    context_parts = []
                    for rid in related_ids:
                        if rid in cached:
                            ci = cached[rid]
                            context_parts.append(
                                f"### {ci['id']}: {ci['title']}\n"
                                f"- State: {ci['state']}\n"
                                f"- Labels: {', '.join(ci.get('labels', []))}"
                            )
                    if context_parts:
                        context_text = "\n\n".join(context_parts)

            elif config["platform"] == "gitlab":
                num = re.search(r"\d+", issue_id)
                if num:
                    issue = source.get_issue(int(num.group()))
                    issue_text = issue.to_prompt_text()

            elif config["platform"] == "jira":
                issue = source.get_issue(issue_id)
                issue_text = issue.to_prompt_text()

        except Exception as e:
            click.echo(f"Warning: Could not fetch issue {topic.target}: {e}", err=True)

    # If we couldn't fetch, use cached data or the title as context
    if not issue_text:
        cached = _load_cached_issues(project_dir)
        if topic.target in cached:
            ci = cached[topic.target]
            issue_text = (
                f"## {ci['id']}: {ci['title']}\n"
                f"- State: {ci['state']}\n"
                f"- Labels: {', '.join(ci.get('labels', []))}\n"
                f"- Assignees: {', '.join(ci.get('assignees', []))}\n"
            )
            if ci.get("body"):
                issue_text += f"\n### Description\n\n{ci['body']}"
        else:
            issue_text = f"## {topic.target}\n\n{topic.title}"

    prompt = build_explore_prompt(
        issue_text=issue_text,
        context_text=context_text or None,
        question=topic.title,
    )

    click.echo(f"Exploring with {model}...", err=True)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        return

    safe_target = re.sub(r"[^a-zA-Z0-9_-]", "-", topic.target)[:80]
    _create_entry(f"explore-{safe_target}", f"Explore: {topic.target}", result)
    _enqueue_topics(result, source=f"explore:{topic.target}", project_dir=project_dir)
    _report_beliefs(result)

    _emit(ctx, result)


# --- propose-beliefs ---


@cli.command("propose-beliefs")
@click.option("--batch-size", type=int, default=5, help="Entries per LLM batch")
@click.option("--output", default="proposed-beliefs.md", help="Output file")
@click.option("--all", "process_all", is_flag=True, help="Re-process all entries")
@click.option("--auto", "auto_accept", is_flag=True, default=False,
              help="Automatically accept all proposed beliefs (no review step)")
@click.pass_context
def propose_beliefs(ctx, batch_size, output, process_all, auto_accept):
    """Extract candidate beliefs from entries for human review."""
    model = ctx.obj["model"]
    timeout = ctx.obj["timeout"]

    if not check_model_available(model):
        click.echo(f"Error: Model '{model}' CLI not available", err=True)
        sys.exit(1)

    input_dir = Path("entries")
    if not input_dir.exists():
        click.echo("No entries/ directory found. Run explorations first.")
        sys.exit(1)

    entries = sorted(input_dir.rglob("*.md"))
    if not entries:
        click.echo("No .md files found.")
        return

    click.echo(f"Reading {len(entries)} entries...")

    # Batch entries
    batches = []
    current_batch = []
    for entry_path in entries:
        content = entry_path.read_text()
        if len(content) > 10000:
            content = content[:10000] + "\n[Truncated]"
        current_batch.append(f"--- FILE: {entry_path} ---\n{content}")
        if len(current_batch) >= batch_size:
            batches.append("\n\n".join(current_batch))
            current_batch = []
    if current_batch:
        batches.append("\n\n".join(current_batch))

    click.echo(f"Processing {len(batches)} batches...")

    all_proposals = []
    for i, batch_text in enumerate(batches):
        click.echo(f"  Batch {i + 1}/{len(batches)}...")
        prompt = PROPOSE_BELIEFS_PRODUCT.format(entries=batch_text)
        try:
            result = invoke_sync(prompt, model=model, timeout=timeout)
            all_proposals.append(result)
        except Exception as e:
            click.echo(f"  ERROR: {e}")
            continue

    if auto_accept:
        accept_pattern = re.compile(
            r"^### \[?(?:ACCEPT(?:/REJECT)?|REJECT)\]? (\S+)\n(.+?)\n- Source: (.+?)(?:\n|$)",
            re.MULTILINE,
        )
        matches = []
        for proposal in all_proposals:
            matches.extend(accept_pattern.findall(proposal))
        if not matches:
            click.echo("No beliefs extracted from proposals.")
            return
        click.echo(f"\nAuto-accepting {len(matches)} beliefs...")
        _accept_proposals(matches)
        return

    # Write proposals for manual review
    output_path = Path(output)
    with output_path.open("w") as f:
        f.write("# Proposed Beliefs\n\n")
        f.write("Edit each entry: change `[ACCEPT/REJECT]` to `[ACCEPT]` or `[REJECT]`.\n")
        f.write("Then run: `product-expert accept-beliefs`\n\n---\n\n")
        f.write(f"**Generated:** {date.today().isoformat()}\n")
        f.write(f"**Model:** {model}\n\n")
        for proposal in all_proposals:
            f.write(proposal)
            f.write("\n\n")

    click.echo(f"\nWrote {output_path}")
    click.echo("Review the file, then run: product-expert accept-beliefs")


# --- accept-beliefs ---


def _accept_proposals(matches: list[tuple[str, str, str]]) -> tuple[int, int, int]:
    """Import belief proposals into the primary store.

    Returns (added, skipped, failed) counts.
    """
    if _has_reasons():
        added = 0
        skipped = 0
        failed = 0
        for belief_id, claim_text, source in matches:
            result = subprocess.run(
                ["reasons", "add", belief_id, claim_text.strip(),
                 "--source", source.strip()],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                click.echo(f"  Added: {belief_id}")
                added += 1
            else:
                stderr = result.stderr.strip()
                stdout = result.stdout.strip()
                if "already exists" in stderr or "already exists" in stdout:
                    click.echo(f"  EXISTS: {belief_id}")
                    skipped += 1
                else:
                    click.echo(f"  FAIL: {belief_id}: {stderr or stdout}")
                    failed += 1

        click.echo(f"\nAccepted {added} beliefs ({skipped} existing, {failed} failed)")

        if added > 0:
            _reasons_export()
        return added, skipped, failed

    # Fall back to beliefs CLI
    added = 0
    skipped = 0
    failed = 0
    for belief_id, claim_text, source in matches:
        try:
            result = subprocess.run(
                ["beliefs", "add", "--id", belief_id,
                 "--text", claim_text.strip(), "--source", source.strip()],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                click.echo(f"  Added: {belief_id}")
                added += 1
            else:
                stderr = result.stderr.strip()
                if "already exists" in stderr or "already exists" in result.stdout:
                    click.echo(f"  EXISTS: {belief_id}")
                    skipped += 1
                else:
                    click.echo(f"  FAIL: {belief_id}: {stderr or result.stdout.strip()}")
                    failed += 1
        except FileNotFoundError:
            click.echo("ERROR: beliefs CLI not found.")
            sys.exit(1)

    click.echo(f"\nAccepted {added} beliefs ({skipped} existing, {failed} failed)")
    return added, skipped, failed


@cli.command("accept-beliefs")
@click.option("--file", "proposals_file", default="proposed-beliefs.md",
              help="Proposals file")
def accept_beliefs(proposals_file):
    """Import accepted beliefs from proposals file."""
    proposals_path = Path(proposals_file)
    if not proposals_path.exists():
        click.echo(f"Proposals file not found: {proposals_file}")
        sys.exit(1)

    text = proposals_path.read_text()

    pattern = re.compile(
        r"### \[?ACCEPT\]? (\S+)\n"
        r"(.+?)\n"
        r"- Source: (.+?)(?:\n|$)"
    )
    matches = pattern.findall(text)

    if not matches:
        click.echo("No [ACCEPT] entries found.")
        return

    click.echo(f"Found {len(matches)} accepted beliefs")
    _accept_proposals(matches)


# --- derive ---


def _load_network() -> dict:
    """Load network.json (exported from reasons)."""
    network_path = Path("network.json")
    if not network_path.exists():
        if _has_reasons():
            result = subprocess.run(
                ["reasons", "export"], capture_output=True, text=True,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
        return {"nodes": {}}
    return json.loads(network_path.read_text())


def _get_depth(node_id: str, nodes: dict, derived: dict, memo: dict | None = None) -> int:
    """Compute the depth of a node in the reasoning chain."""
    if memo is None:
        memo = {}
    if node_id in memo:
        return memo[node_id]
    if node_id not in derived:
        memo[node_id] = 0
        return 0
    max_d = 0
    for j in derived[node_id].get("justifications", []):
        for a in j.get("antecedents", []):
            max_d = max(max_d, _get_depth(a, nodes, derived, memo))
    memo[node_id] = max_d + 1
    return max_d + 1


def _build_beliefs_section(nodes: dict, derived: dict, max_beliefs: int = 300) -> str:
    """Build a compact beliefs section for the derive prompt."""
    from collections import defaultdict
    lines = []
    in_nodes = {k: v for k, v in nodes.items()
                if v.get("truth_value") == "IN" and k not in derived}
    groups = defaultdict(list)
    for k, v in in_nodes.items():
        prefix = k.split("-")[0] if "-" in k else k
        groups[prefix].append((k, v["text"][:120]))

    count = 0
    for prefix in sorted(groups, key=lambda p: -len(groups[p])):
        if count >= max_beliefs:
            break
        lines.append(f"\n### {prefix} ({len(groups[prefix])} beliefs)")
        for belief_id, text in sorted(groups[prefix]):
            if count >= max_beliefs:
                break
            lines.append(f"- `{belief_id}`: {text}")
            count += 1

    return "\n".join(lines)


def _build_derived_section(nodes: dict, derived: dict) -> str:
    """Build the derived conclusions section for the derive prompt."""
    memo = {}
    lines = []
    for k in sorted(derived, key=lambda x: -_get_depth(x, nodes, derived, memo)):
        depth = _get_depth(k, nodes, derived, memo)
        text = nodes[k]["text"][:150]
        justs = derived[k]["justifications"]
        antes = justs[0].get("antecedents", []) if justs else []
        outlist = justs[0].get("outlist", []) if justs else []
        status = nodes[k].get("truth_value", "?")

        lines.append(f"\n#### [{status}] depth-{depth}: `{k}`")
        lines.append(text)
        lines.append(f"- Antecedents: {', '.join(antes)}")
        if outlist:
            lines.append(f"- Unless: {', '.join(outlist)}")

    return "\n".join(lines) if lines else "(No derived conclusions yet)"


def _parse_derive_proposals(response: str) -> list[dict]:
    """Parse DERIVE and GATE proposals from LLM response."""
    proposals = []
    pattern = re.compile(
        r"### (DERIVE|GATE) (\S+)\n"
        r"(.+?)\n"
        r"- Antecedents: (.+?)\n"
        r"(?:- Unless: (.+?)\n)?"
        r"- Label: (.+?)(?:\n|$)",
    )
    for match in pattern.finditer(response):
        kind = match.group(1)
        proposal = {
            "kind": kind.lower(),
            "id": match.group(2).strip("`"),
            "text": match.group(3).strip(),
            "antecedents": [a.strip().strip("`") for a in match.group(4).split(",")],
            "unless": [u.strip().strip("`") for u in match.group(5).split(",")] if match.group(5) else [],
            "label": match.group(6).strip(),
        }
        proposals.append(proposal)
    return proposals


@cli.command("derive")
@click.option("--output", "-o", default="proposed-derivations.md",
              help="Output file (default: proposed-derivations.md)")
@click.option("--auto", "auto_add", is_flag=True, default=False,
              help="Automatically add proposals to reasons (no review step)")
@click.option("--exhaust", is_flag=True, default=False,
              help="Loop until no new derivations (implies --auto)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be sent to the LLM without invoking it")
@click.option("--budget", type=int, default=300,
              help="Maximum number of beliefs in prompt (default: 300)")
@click.option("--topic", default=None,
              help="Keyword filter — only include beliefs matching these keywords")
@click.option("--max-rounds", type=int, default=10,
              help="Maximum rounds for --exhaust (default: 10)")
@click.pass_context
def derive(ctx, output, auto_add, exhaust, dry_run, budget, topic, max_rounds):
    """Derive deeper reasoning chains from existing beliefs.

    Delegates to `reasons derive` which handles prompt building, LLM
    invocation, proposal validation, and network updates.

    Example:
        product-expert derive              # propose derivations
        product-expert derive --auto       # propose and add automatically
        product-expert derive --exhaust    # loop until no new derivations
    """
    if not _has_reasons():
        click.echo("Error: reasons CLI required. Install with: uv tool install ftl-reasons", err=True)
        sys.exit(1)

    model = ctx.obj["model"]
    timeout = ctx.obj["timeout"]

    cmd = ["reasons", "derive", "-m", model, "--timeout", str(timeout),
           "--budget", str(budget), "-o", output]
    if auto_add or exhaust:
        cmd.append("--auto")
    if exhaust:
        cmd.extend(["--exhaust", "--max-rounds", str(max_rounds)])
    if dry_run:
        cmd.append("--dry-run")
    if topic:
        cmd.extend(["--topic", topic])

    click.echo(f"Running: {' '.join(cmd)}", err=True)
    result = subprocess.run(cmd)

    if result.returncode != 0:
        sys.exit(result.returncode)

    if (auto_add or exhaust) and not dry_run:
        _reasons_export()


# --- generate-summary ---


_NEGATIVE_KEYWORDS = re.compile(
    r"\b(gap|missing|churn|attrition|competitor|delay|regression|blocker|"
    r"complaint|friction|confusion|workaround|debt|deprioritized|blocked|"
    r"stalled|declining|risk|broken|fragile|untested)\b",
    re.IGNORECASE,
)

_CRITICAL_KEYWORDS = re.compile(
    r"\b(revenue|retention|churn|security|compliance|legal|data loss|privacy|"
    r"outage|downtime|SLA|enterprise|competitor)\b",
    re.IGNORECASE,
)


def _find_gated_out_beliefs(nodes: dict) -> list[dict]:
    """Find gated OUT beliefs and their active blockers."""
    results = []
    for nid, node in nodes.items():
        if node.get("truth_value") != "OUT":
            continue
        if node.get("metadata", {}).get("superseded_by"):
            continue
        for j in node.get("justifications", []):
            if not j.get("outlist"):
                continue
            active_blockers = [
                oid for oid in j["outlist"]
                if oid in nodes and nodes[oid].get("truth_value") == "IN"
            ]
            if active_blockers:
                results.append({
                    "id": nid,
                    "text": node.get("text", ""),
                    "blockers": [
                        {"id": bid, "text": nodes[bid].get("text", "")}
                        for bid in active_blockers
                    ],
                })
                break
    return results


def _find_negative_in_beliefs(nodes: dict) -> list[dict]:
    """Find IN beliefs with negative-signal keywords."""
    results = []
    for nid, node in nodes.items():
        if node.get("truth_value") != "IN":
            continue
        text = node.get("text", "")
        if _NEGATIVE_KEYWORDS.search(text):
            results.append({"id": nid, "text": text})
    return results


def _format_gated_section(beliefs: list[dict]) -> str:
    if not beliefs:
        return "_None_\n"
    lines = []
    for b in beliefs:
        lines.append(f"- **{b['id']}**: {b['text']}")
        for blocker in b["blockers"]:
            lines.append(f"  - Blocked by: `{blocker['id']}` — {blocker['text']}")
    return "\n".join(lines) + "\n"


def _format_belief_list(beliefs: list[dict]) -> str:
    if not beliefs:
        return "_None_\n"
    lines = []
    for b in beliefs:
        lines.append(f"- **{b['id']}**: {b['text']}")
    return "\n".join(lines) + "\n"


@cli.command("generate-summary")
@click.option("--snapshot-ids", multiple=True, hidden=True,
              help="Pre-run node IDs (passed by update command)")
@click.pass_context
def generate_summary(ctx, snapshot_ids):
    """Generate a summary entry of belief state (no LLM).

    Highlights gated OUT beliefs, negative IN beliefs,
    critical issues, and statistics.
    """
    network = _load_network()
    nodes = network.get("nodes", {})
    if not nodes:
        click.echo("No beliefs found. Run explorations first.", err=True)
        sys.exit(1)

    pre_run_ids = set(snapshot_ids) if snapshot_ids else set()

    all_gated = _find_gated_out_beliefs(nodes)
    all_negative = _find_negative_in_beliefs(nodes)

    if pre_run_ids:
        new_gated = [b for b in all_gated if b["id"] not in pre_run_ids]
        new_negative = [b for b in all_negative if b["id"] not in pre_run_ids]
    else:
        new_gated = all_gated
        new_negative = all_negative

    critical_gated = [b for b in all_gated if _CRITICAL_KEYWORDS.search(b["text"])
                      or any(_CRITICAL_KEYWORDS.search(bl["text"]) for bl in b["blockers"])]
    critical_negative = [b for b in all_negative if _CRITICAL_KEYWORDS.search(b["text"])]

    total_in = sum(1 for n in nodes.values() if n.get("truth_value") == "IN")
    total_out = sum(1 for n in nodes.values() if n.get("truth_value") == "OUT")
    total_derived = sum(1 for n in nodes.values()
                        if n.get("justifications") and len(n["justifications"]) > 0)

    content = f"## New Gated OUT Beliefs\n\n{_format_gated_section(new_gated)}"
    content += f"\n## New Negative IN Beliefs\n\n{_format_belief_list(new_negative)}"
    content += "\n## Critical Watch List\n\n"

    if critical_gated or critical_negative:
        if critical_gated:
            content += f"### Gated (blocked)\n\n{_format_gated_section(critical_gated)}\n"
        if critical_negative:
            content += f"### Active Issues\n\n{_format_belief_list(critical_negative)}\n"
    else:
        content += "_No critical issues detected._\n"

    content += "\n## Statistics\n\n"
    content += f"- **Total beliefs:** {len(nodes)}\n"
    content += f"- **IN:** {total_in}\n"
    content += f"- **OUT:** {total_out}\n"
    content += f"- **Derived:** {total_derived}\n"
    content += f"- **Gated OUT (all):** {len(all_gated)}\n"
    content += f"- **Negative IN (all):** {len(all_negative)}\n"
    if pre_run_ids:
        content += f"- **New beliefs this run:** {len(nodes) - len(pre_run_ids)}\n"
        content += f"- **New gated OUT:** {len(new_gated)}\n"
        content += f"- **New negative IN:** {len(new_negative)}\n"

    _create_entry("update", "Update Summary", content)
    click.echo(f"\nSummary: {len(new_gated)} new gated OUT, {len(new_negative)} new negative IN, "
               f"{len(critical_gated) + len(critical_negative)} critical", err=True)


# --- summary ---


@cli.command()
@click.pass_context
def summary(ctx):
    """Synthesize a product summary from beliefs."""
    config = _load_config()
    if not config:
        click.echo("Not initialized. Run: product-expert init <platform> <target>")
        sys.exit(1)

    model = ctx.obj["model"]
    timeout = ctx.obj["timeout"]

    if not check_model_available(model):
        click.echo(f"Error: Model '{model}' CLI not available", err=True)
        sys.exit(1)

    # Read beliefs from reasons or beliefs.md
    beliefs_text = ""
    belief_count = 0

    if _has_reasons() and Path("reasons.db").exists():
        result = subprocess.run(["reasons", "list"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            beliefs_text = result.stdout
            belief_count = len([l for l in result.stdout.splitlines() if l.strip()])
    elif Path("beliefs.md").exists():
        beliefs_text = Path("beliefs.md").read_text()
        belief_count = len(re.findall(r"^### \S+", beliefs_text, re.MULTILINE))

    if not beliefs_text or belief_count == 0:
        click.echo("No beliefs found. Run the pipeline first:")
        click.echo("  product-expert scan")
        click.echo("  product-expert propose-beliefs")
        click.echo("  product-expert accept-beliefs")
        sys.exit(1)

    click.echo(f"Summarizing {belief_count} beliefs with {model}...", err=True)

    project_name = config.get("repo", config.get("project", "unknown"))

    prompt = build_summary_prompt(
        beliefs_text=beliefs_text,
        project_name=project_name,
        belief_count=belief_count,
    )

    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    short_name = project_name.split("//")[-1] if "//" in project_name else project_name
    safe_name = short_name.replace("/", "-")
    _create_entry(f"summary-{safe_name}", f"Summary: {project_name}", result)

    _emit(ctx, result)


# --- status ---


@cli.command()
def status():
    """Show product-expert dashboard."""
    config = _load_config()

    click.echo("=== Product Expert Status ===\n")

    if config:
        click.echo(f"Platform: {config.get('platform', 'unknown')}")
        click.echo(f"Target:   {config.get('repo', config.get('project', 'unknown'))}")
        click.echo(f"Domain:   {config.get('domain', 'unknown')}")
        click.echo(f"Created:  {config.get('created', 'unknown')}")
    else:
        click.echo("Not initialized. Run: product-expert init <platform> <target>")
        return

    click.echo()

    # Entries
    entries_dir = Path("entries")
    entry_count = len(list(entries_dir.rglob("*.md"))) if entries_dir.exists() else 0
    click.echo(f"Entries:  {entry_count}")

    # Beliefs
    if _has_reasons() and Path("reasons.db").exists():
        result = subprocess.run(["reasons", "list"], capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            r_in = sum(1 for l in lines if l.strip().startswith("[+]"))
            r_out = sum(1 for l in lines if l.strip().startswith("[-]"))
            click.echo(f"Beliefs:  {r_in} IN, {r_out} OUT")
    else:
        beliefs_path = Path("beliefs.md")
        if beliefs_path.exists():
            text = beliefs_path.read_text()
            b_in = len(re.findall(r"^### \S+ \[IN\]", text, re.MULTILINE))
            click.echo(f"Beliefs:  {b_in} IN")

    # Topics
    project_dir = _get_project_dir()
    queue = load_queue(project_dir)
    pending = sum(1 for t in queue if t.status == "pending")
    done = sum(1 for t in queue if t.status == "done")
    skipped = sum(1 for t in queue if t.status == "skipped")
    click.echo(f"Topics:   {pending} pending, {done} done, {skipped} skipped")

    # Cached issues
    cached = _load_cached_issues(project_dir)
    if cached:
        click.echo(f"Cached:   {len(cached)} issues")

    # Proposals
    proposals_path = Path("proposed-beliefs.md")
    if proposals_path.exists():
        text = proposals_path.read_text()
        total = len(re.findall(r"^### \[(?:ACCEPT|REJECT|ACCEPT/REJECT)\]", text, re.MULTILINE))
        accepted = len(re.findall(r"^### \[ACCEPT\]", text, re.MULTILINE))
        click.echo(f"Proposed: {total} candidates ({accepted} accepted)")


# --- update ---


@cli.command("update")
@click.option("--since", "since_str", default=None,
              help="Only scan issues updated since date (e.g., 2026-04-01, '1 week ago')")
@click.option("--since-last", is_flag=True, default=False,
              help="Only scan issues updated since last scan checkpoint")
@click.option("--limit", default=100, type=int,
              help="Max issues per page for scan (default: 100)")
@click.pass_context
def update(ctx, since_str, since_last, limit):
    """Automated update pipeline: scan, explore, propose, derive, summarize.

    Runs the full pipeline in one command:
      1. scan --all-pages (with optional --since filtering)
      2. explore --loop 1000 (drain pending topics)
      3. propose-beliefs --auto (extract and accept beliefs)
      4. derive --exhaust (compute all logical consequences)
      5. generate-summary (update report entry)

    Example:
        product-expert update --since-last
        product-expert update --since "1 week ago"
    """
    from .caffeinate import hold as _caffeinate
    _caffeinate()

    errors = []

    # Snapshot current node IDs before any changes
    try:
        network = _load_network()
        pre_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        pre_run_ids = set()

    # Step 1: scan
    click.echo("\n=== Step 1: Scan issues ===\n", err=True)
    try:
        ctx.invoke(scan, since_str=since_str, since_last=since_last,
                   all_pages=True, limit=limit)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"scan exited with code {e.code}")
            click.echo(f"WARN: scan failed (exit {e.code}), continuing...", err=True)
    except Exception as e:
        errors.append(f"scan: {e}")
        click.echo(f"WARN: scan failed: {e}, continuing...", err=True)

    # Step 2: explore all pending topics
    click.echo("\n=== Step 2: Explore pending topics ===\n", err=True)
    try:
        ctx.invoke(explore, loop_max=1000)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"explore exited with code {e.code}")
            click.echo(f"WARN: explore failed (exit {e.code}), continuing...", err=True)
    except Exception as e:
        errors.append(f"explore: {e}")
        click.echo(f"WARN: explore failed: {e}, continuing...", err=True)

    # Step 3: propose-beliefs --auto
    click.echo("\n=== Step 3: Propose and accept beliefs ===\n", err=True)
    try:
        ctx.invoke(propose_beliefs, auto_accept=True)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"propose-beliefs exited with code {e.code}")
            click.echo(f"WARN: propose-beliefs failed (exit {e.code}), continuing...", err=True)
    except Exception as e:
        errors.append(f"propose-beliefs: {e}")
        click.echo(f"WARN: propose-beliefs failed: {e}, continuing...", err=True)

    # Step 4: derive --exhaust
    click.echo("\n=== Step 4: Derive (exhaust) ===\n", err=True)
    try:
        ctx.invoke(derive, exhaust=True)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"derive exited with code {e.code}")
            click.echo(f"WARN: derive failed (exit {e.code}), continuing...", err=True)
    except Exception as e:
        errors.append(f"derive: {e}")
        click.echo(f"WARN: derive failed: {e}, continuing...", err=True)

    # Step 5: generate-summary
    click.echo("\n=== Step 5: Generate summary ===\n", err=True)
    try:
        ctx.invoke(generate_summary, snapshot_ids=tuple(pre_run_ids))
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"generate-summary exited with code {e.code}")
    except Exception as e:
        errors.append(f"generate-summary: {e}")
        click.echo(f"WARN: generate-summary failed: {e}", err=True)

    # Final report
    click.echo("\n=== Update complete ===\n", err=True)
    if errors:
        click.echo(f"Completed with {len(errors)} warning(s):", err=True)
        for err in errors:
            click.echo(f"  - {err}", err=True)
    else:
        click.echo("All steps completed successfully.", err=True)


if __name__ == "__main__":
    cli()
