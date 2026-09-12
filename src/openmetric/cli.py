"""``openmetric`` command line.

One deliberate omission: there is no ``--api-key`` flag anywhere. Secrets are
entered at a hidden prompt or read from an environment variable, so they never
land in your shell history, your terminal scrollback, or a CI log.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import select

from . import __version__, analytics, crypto, pricing
from . import blindspots as blindspots_mod
from .config import get_settings, reset_settings_cache
from .db import init_db, reset_engine, session_scope
from .models import Credential, Project, Provider, RequestEvent, UseCase, VirtualKey

console = Console()
app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="One gateway for every API key, with per-project cost visibility.",
)
project_app = typer.Typer(no_args_is_help=True, help="Manage projects.")
key_app = typer.Typer(no_args_is_help=True, help="Manage provider API keys.")
vkey_app = typer.Typer(no_args_is_help=True, help="Manage OpenMetric virtual keys.")
provider_app = typer.Typer(no_args_is_help=True, help="Manage providers.")
price_app = typer.Typer(no_args_is_help=True, help="Manage price overrides.")
app.add_typer(project_app, name="project")
app.add_typer(key_app, name="key")
app.add_typer(vkey_app, name="vkey")
app.add_typer(provider_app, name="provider")
app.add_typer(price_app, name="price")


def _bootstrap() -> None:
    from .app import sync_builtin_providers

    init_db()
    sync_builtin_providers()


def _money(value: float) -> str:
    return f"${value:,.4f}" if 0 < value < 0.01 else f"${value:,.2f}"


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #


@app.command()
def init(
    env_file: Path = typer.Option(Path(".env"), help="Where to write configuration."),
    force: bool = typer.Option(False, help="Overwrite an existing encryption key."),
) -> None:
    """Create .env with a fresh encryption key, then build the database."""
    existing = env_file.read_text() if env_file.exists() else ""
    has_key = any(
        line.startswith("OPENMETRIC_SECRET_KEY=") and line.split("=", 1)[1].strip()
        for line in existing.splitlines()
    )
    if has_key and not force:
        console.print(f"[yellow]{env_file} already has an encryption key. Keeping it.[/yellow]")
    else:
        if has_key and force:
            console.print(
                "[red]Overwriting the key makes every stored credential undecryptable.[/red]"
            )
            typer.confirm("Continue?", abort=True)
        key = crypto.generate_secret_key()
        lines = [
            line for line in existing.splitlines() if not line.startswith("OPENMETRIC_SECRET_KEY=")
        ]
        lines.append(f"OPENMETRIC_SECRET_KEY={key}")
        env_file.write_text("\n".join(lines).strip() + "\n")
        try:
            env_file.chmod(0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
        console.print(f"[green]Wrote a new encryption key to {env_file} (mode 600).[/green]")

    os.environ["OPENMETRIC_ENV_FILE"] = str(env_file)
    reset_settings_cache()
    reset_engine()
    _bootstrap()
    console.print(
        Panel.fit(
            f"Database ready at [bold]{get_settings().database_url}[/bold]\n\n"
            "Next:\n"
            "  1. [bold]openmetric key add openrouter --label personal[/bold]\n"
            "  2. [bold]openmetric vkey create --name my-app --project my-app[/bold]\n"
            "  3. [bold]openmetric serve[/bold]\n\n"
            f"[yellow]{env_file} holds your encryption key. "
            f"It is gitignored - keep it that way.[/yellow]",
            title="OpenMetric is set up",
        )
    )


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind address."),
    port: int | None = typer.Option(None, help="Port."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (development)."),
) -> None:
    """Run the gateway and dashboard."""
    import uvicorn

    settings = get_settings()
    bind_host = host or settings.host
    bind_port = port or settings.port
    if bind_host not in {"127.0.0.1", "localhost"} and not settings.admin_token:
        console.print(
            "[yellow]Warning: binding beyond localhost with no OPENMETRIC_ADMIN_TOKEN set. "
            "Anyone who can reach this port can use your keys and read your usage.[/yellow]"
        )
    _bootstrap()
    console.print(f"[green]Dashboard:[/green] http://{bind_host}:{bind_port}/")
    console.print(f"[green]OpenAI-compatible base URL:[/green] http://{bind_host}:{bind_port}/v1")
    uvicorn.run("openmetric.app:app", host=bind_host, port=bind_port, reload=reload)


@app.command()
def demo(
    events: int = typer.Option(4000, help="How many synthetic events to generate."),
    days: int = typer.Option(45, help="Spread them over this many days."),
) -> None:
    """Fill the database with synthetic traffic so you can explore the dashboard."""
    from .seed import seed_demo

    if not get_settings().secret_key:
        console.print("[red]Run `openmetric init` first.[/red]")
        raise typer.Exit(1)
    result = seed_demo(events=events, days=days)
    console.print(
        f"[green]Generated {result['events']:,} synthetic events "
        f"across {result['projects']} projects.[/green] None of it is real data."
    )
    console.print("Run [bold]openmetric serve[/bold] and open the dashboard.")


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"openmetric {__version__}")


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #


@project_app.command("add")
def project_add(
    slug: str,
    name: str = typer.Option("", help="Human-readable name."),
    budget: float | None = typer.Option(None, help="Monthly budget in USD."),
) -> None:
    """Create a project."""
    _bootstrap()
    with session_scope() as session:
        if session.scalar(select(Project).where(Project.slug == slug)):
            console.print(f"[yellow]Project '{slug}' already exists.[/yellow]")
            raise typer.Exit(1)
        session.add(
            Project(
                slug=slug, name=name or slug.replace("-", " ").title(), monthly_budget_usd=budget
            )
        )
    console.print(f"[green]Created project '{slug}'.[/green]")


@project_app.command("list")
def project_list() -> None:
    """List projects with month-to-date spend."""
    _bootstrap()
    table = Table(title="Projects")
    for column in ("Slug", "Name", "MTD spend", "Budget", "Status"):
        table.add_column(column)
    with session_scope() as session:
        budgets = {b["project"]: b for b in analytics.budget_status(session, analytics.Filters())}
        for project in session.scalars(select(Project).order_by(Project.slug)):
            info = budgets.get(project.slug, {})
            spend = info.get("month_to_date_usd", 0.0)
            budget = project.monthly_budget_usd
            status = (
                "archived"
                if project.archived
                else ("OVER BUDGET" if info.get("over_budget") else "ok")
            )
            table.add_row(
                project.slug,
                project.name,
                _money(spend),
                _money(budget) if budget else "-",
                f"[red]{status}[/red]" if status == "OVER BUDGET" else status,
            )
    console.print(table)


@project_app.command("archive")
def project_archive(slug: str) -> None:
    """Archive a project (its history is kept)."""
    _bootstrap()
    with session_scope() as session:
        project = session.scalar(select(Project).where(Project.slug == slug))
        if project is None:
            console.print(f"[red]No project '{slug}'.[/red]")
            raise typer.Exit(1)
        project.archived = True
    console.print(f"[green]Archived '{slug}'.[/green]")


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


@provider_app.command("list")
def provider_list() -> None:
    """List configured providers."""
    _bootstrap()
    table = Table(title="Providers")
    for column in ("Slug", "Kind", "Base URL", "Auth", "Keys", "Env var"):
        table.add_column(column)
    with session_scope() as session:
        for provider in session.scalars(select(Provider).order_by(Provider.slug)):
            count = len([c for c in provider.credentials if c.active])
            table.add_row(
                provider.slug,
                provider.kind,
                provider.base_url,
                provider.auth_style,
                str(count),
                provider.env_var or "-",
            )
    console.print(table)


@provider_app.command("add")
def provider_add(
    slug: str,
    base_url: str = typer.Option(..., help="e.g. https://api.example.com/v1"),
    kind: str = typer.Option("other", help="llm | scrape | search | other"),
    auth_style: str = typer.Option("bearer", help="bearer | header | query | basic"),
    auth_header: str = typer.Option("Authorization", help="Header name for --auth-style header."),
    auth_query_param: str = typer.Option("", help="Query param for --auth-style query."),
) -> None:
    """Add a provider that is not in the built-in catalog."""
    _bootstrap()
    with session_scope() as session:
        if session.scalar(select(Provider).where(Provider.slug == slug)):
            console.print(f"[yellow]Provider '{slug}' already exists.[/yellow]")
            raise typer.Exit(1)
        session.add(
            Provider(
                slug=slug,
                name=slug.title(),
                base_url=base_url.rstrip("/"),
                kind=kind,
                auth_style=auth_style,
                auth_header=auth_header,
                auth_query_param=auth_query_param or None,
            )
        )
    console.print(f"[green]Added provider '{slug}'.[/green]")


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #


@key_app.command("add")
def key_add(
    provider: str,
    label: str = typer.Option(
        ..., help="A name you will recognise, e.g. 'openrouter-client-work'."
    ),
    project: str = typer.Option("", help="Bind this key to one project."),
    from_env: str = typer.Option(
        "", help="Read the key from this environment variable instead of prompting."
    ),
    notes: str = typer.Option("", help="Free-text note, e.g. which account it belongs to."),
) -> None:
    """Store a provider API key, encrypted.

    The key is never passed as a command line argument: it is either typed at a
    hidden prompt or read from an environment variable.
    """
    _bootstrap()
    if not get_settings().secret_key:
        console.print("[red]No encryption key. Run `openmetric init` first.[/red]")
        raise typer.Exit(1)

    with session_scope() as session:
        provider_row = session.scalar(select(Provider).where(Provider.slug == provider.lower()))
        if provider_row is None:
            console.print(
                f"[red]No provider '{provider}'.[/red] See `openmetric provider list`, "
                f"or add it with `openmetric provider add`."
            )
            raise typer.Exit(1)
        env_var = from_env or provider_row.env_var

        secret = os.environ.get(env_var, "").strip() if env_var else ""
        if secret:
            console.print(f"Read the key from ${env_var}.")
        else:
            secret = typer.prompt(f"{provider} API key", hide_input=True).strip()
        if not secret:
            console.print("[red]Empty key.[/red]")
            raise typer.Exit(1)

        project_row = None
        if project:
            project_row = session.scalar(select(Project).where(Project.slug == project))
            if project_row is None:
                console.print(f"[red]No project '{project}'. Create it first.[/red]")
                raise typer.Exit(1)

        existing = session.scalar(
            select(Credential).where(
                Credential.provider_id == provider_row.id, Credential.label == label
            )
        )
        if existing:
            console.print(
                f"[yellow]A key labelled '{label}' already exists for {provider}.[/yellow]"
            )
            raise typer.Exit(1)

        duplicate = session.scalar(
            select(Credential).where(Credential.key_fingerprint == crypto.fingerprint(secret))
        )
        if duplicate:
            console.print(
                f"[yellow]Note: this is the same key you already stored as "
                f"'{duplicate.label}'.[/yellow]"
            )

        session.add(
            Credential(
                provider_id=provider_row.id,
                project_id=project_row.id if project_row else None,
                label=label,
                encrypted_secret=crypto.encrypt(secret),
                key_hint=crypto.hint(secret),
                key_fingerprint=crypto.fingerprint(secret),
                notes=notes,
            )
        )
        hint = crypto.hint(secret)
    console.print(f"[green]Stored '{label}' ({hint}) for {provider}, encrypted at rest.[/green]")


@key_app.command("list")
def key_list() -> None:
    """List stored keys. Only hints are ever shown."""
    _bootstrap()
    table = Table(title="Provider keys")
    for column in ("Label", "Provider", "Project", "Key", "Active", "Last used"):
        table.add_column(column)
    with session_scope() as session:
        for credential in session.scalars(select(Credential).order_by(Credential.label)):
            table.add_row(
                credential.label,
                credential.provider.slug if credential.provider else "?",
                credential.project.slug if credential.project else "[dim]shared[/dim]",
                credential.key_hint,
                "yes" if credential.active else "[dim]no[/dim]",
                credential.last_used_at.strftime("%Y-%m-%d")
                if credential.last_used_at
                else "[yellow]never[/yellow]",
            )
    console.print(table)


@key_app.command("deactivate")
def key_deactivate(label: str) -> None:
    """Stop routing through a key without deleting its history."""
    _bootstrap()
    with session_scope() as session:
        credential = session.scalar(select(Credential).where(Credential.label == label))
        if credential is None:
            console.print(f"[red]No key '{label}'.[/red]")
            raise typer.Exit(1)
        credential.active = False
    console.print(f"[green]Deactivated '{label}'.[/green]")


# --------------------------------------------------------------------------- #
# Virtual keys
# --------------------------------------------------------------------------- #


@vkey_app.command("create")
def vkey_create(
    name: str = typer.Option(..., help="What this key is for."),
    project: str = typer.Option("", help="Tag every call from this key with a project."),
    use_case: str = typer.Option("", help="Default use case for this key."),
    credential: str = typer.Option("", help="Pin the key to one provider credential."),
) -> None:
    """Issue a virtual key for an application to use instead of a provider key."""
    _bootstrap()
    with session_scope() as session:
        project_row = use_case_row = credential_row = None
        if project:
            project_row = session.scalar(select(Project).where(Project.slug == project))
            if project_row is None:
                project_row = Project(slug=project, name=project.replace("-", " ").title())
                session.add(project_row)
                session.flush()
        if use_case:
            use_case_row = session.scalar(select(UseCase).where(UseCase.slug == use_case))
            if use_case_row is None:
                use_case_row = UseCase(slug=use_case, name=use_case.replace("-", " ").title())
                session.add(use_case_row)
                session.flush()
        if credential:
            credential_row = session.scalar(
                select(Credential).where(Credential.label == credential)
            )
            if credential_row is None:
                console.print(f"[red]No credential '{credential}'.[/red]")
                raise typer.Exit(1)

        token = crypto.new_virtual_key()
        session.add(
            VirtualKey(
                name=name,
                token_hash=crypto.hash_virtual_key(token),
                token_hint=f"...{token[-6:]}",
                project_id=project_row.id if project_row else None,
                use_case_id=use_case_row.id if use_case_row else None,
                credential_id=credential_row.id if credential_row else None,
            )
        )

    settings = get_settings()
    console.print(
        Panel.fit(
            f"[bold green]{token}[/bold green]\n\n"
            "Shown once - it is stored hashed and cannot be recovered.\n\n"
            "Use it exactly where a provider key used to go:\n"
            f"  [dim]OPENAI_BASE_URL=http://{settings.host}:{settings.port}/v1[/dim]\n"
            f"  [dim]OPENAI_API_KEY={token[:12]}...[/dim]",
            title=f"Virtual key '{name}'",
        )
    )


@vkey_app.command("list")
def vkey_list() -> None:
    """List issued virtual keys."""
    _bootstrap()
    table = Table(title="Virtual keys")
    for column in ("Name", "Token", "Project", "Use case", "Credential", "Active", "Last used"):
        table.add_column(column)
    with session_scope() as session:
        for key in session.scalars(select(VirtualKey).order_by(VirtualKey.name)):
            table.add_row(
                key.name,
                key.token_hint,
                key.project.slug if key.project else "-",
                key.use_case.slug if key.use_case else "-",
                key.credential.label if key.credential else "[dim]auto[/dim]",
                "yes" if key.active else "[dim]no[/dim]",
                key.last_used_at.strftime("%Y-%m-%d") if key.last_used_at else "never",
            )
    console.print(table)


@vkey_app.command("revoke")
def vkey_revoke(name: str) -> None:
    """Revoke a virtual key."""
    _bootstrap()
    with session_scope() as session:
        key = session.scalar(select(VirtualKey).where(VirtualKey.name == name))
        if key is None:
            console.print(f"[red]No virtual key '{name}'.[/red]")
            raise typer.Exit(1)
        key.active = False
    console.print(f"[green]Revoked '{name}'.[/green]")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


@app.command()
def report(
    by: str = typer.Option("project", help=f"Group by: {', '.join(analytics.DIMENSIONS)}"),
    days: int = typer.Option(30, help="Look back this many days."),
    project: str = typer.Option("", help="Filter to one project."),
    use_case: str = typer.Option("", help="Filter to one use case."),
    provider: str = typer.Option("", help="Filter to one provider."),
    limit: int = typer.Option(20, help="Rows to show."),
) -> None:
    """Usage and cost, grouped however you want it."""
    _bootstrap()
    if by not in analytics.DIMENSIONS:
        console.print(
            f"[red]Unknown dimension '{by}'. Try: {', '.join(analytics.DIMENSIONS)}[/red]"
        )
        raise typer.Exit(1)

    filters = analytics.Filters(
        days=days, project=project or None, use_case=use_case or None, provider=provider or None
    )
    with session_scope() as session:
        totals = analytics.summary(session, filters)
        rows = analytics.group(session, filters, by=by, limit=limit)

    table = Table(title=f"Last {days} days by {by}")
    for column in ("", "Requests", "Cost", "Tokens", "Errors", "Avg ms"):
        table.add_column(column, justify="right" if column else "left")
    for row in rows:
        table.add_row(
            row["label"],
            f"{row['requests']:,}",
            _money(row["cost_usd"]),
            f"{row['total_tokens']:,}",
            f"{row['errors']:,}" if not row["errors"] else f"[red]{row['errors']:,}[/red]",
            f"{row['avg_latency_ms']:,.0f}",
        )
    console.print(table)
    console.print(
        f"[bold]{totals['requests']:,} requests - {_money(totals['cost_usd'])} - "
        f"{totals['total_tokens']:,} tokens - p95 {totals['p95_latency_ms']:,}ms[/bold]"
    )
    if totals["unpriced_requests"]:
        console.print(
            f"[yellow]{totals['unpriced_requests']:,} of those requests have no price attached. "
            f"Run `openmetric blindspots`.[/yellow]"
        )


@app.command()
def blindspots(
    days: int = typer.Option(30, help="Look back this many days."),
    idle_days: int = typer.Option(30, help="Flag keys unused for this long."),
) -> None:
    """What your usage data is not telling you."""
    _bootstrap()
    with session_scope() as session:
        result = blindspots_mod.run_all(session, analytics.Filters(days=days), idle_days)

    if not result["findings"]:
        console.print("[green]No blindspots found. Everything is tagged and priced.[/green]")
        return

    colors = {"high": "red", "warn": "yellow", "info": "cyan"}
    counts = result["counts"]
    console.print(
        f"[bold]{counts['high']} high, {counts['warn']} warning, {counts['info']} info[/bold]\n"
    )
    for finding in result["findings"]:
        color = colors.get(finding["severity"], "white")
        console.print(
            Panel(
                f"{finding['detail']}\n\n[dim]Fix:[/dim] {finding['action']}",
                title=f"[{color}]{finding['severity'].upper()}[/{color}]  {finding['title']}",
                border_style=color,
            )
        )


@price_app.command("set")
def price_set(
    provider: str = typer.Argument("", help="Provider slug, for per-request pricing."),
    per_request: float | None = typer.Option(None, help="USD per request for this provider."),
    model: str = typer.Option("", help="Model name, for token pricing."),
    input_rate: float | None = typer.Option(None, "--input", help="USD per 1M input tokens."),
    output_rate: float | None = typer.Option(None, "--output", help="USD per 1M output tokens."),
) -> None:
    """Record what something actually costs you, so it stops showing as $0."""
    if not provider and not model:
        console.print("[red]Give a provider (with --per-request) or a --model (with rates).[/red]")
        raise typer.Exit(1)
    path = pricing.set_local_price(
        provider=provider or None,
        per_request=per_request,
        model=model or None,
        input_rate=input_rate,
        output_rate=output_rate,
    )
    console.print(f"[green]Saved to {path}.[/green] New calls will be priced with it.")
    console.print("[dim]Existing events keep the price they were recorded with.[/dim]")


@price_app.command("show")
def price_show(model: str = typer.Argument(..., help="Model name to look up.")) -> None:
    """Show the rate OpenMetric would apply to a model."""
    rate = pricing.find_token_rate(model)
    if rate is None:
        console.print(
            f"[yellow]No rate known for '{model}'.[/yellow] "
            f"Add one: openmetric price set --model {model} --input X --output Y"
        )
        raise typer.Exit(1)
    console.print(
        f"[bold]{model}[/bold]: ${rate.get('input', 0):.4f} in / "
        f"${rate.get('output', 0):.4f} out, per 1M tokens"
    )


@app.command()
def export(
    out: Path = typer.Option(Path("openmetric-export.csv"), help="Output file."),
    days: int = typer.Option(90, help="Look back this many days."),
) -> None:
    """Export raw events to CSV. Contains no keys and no prompt text."""
    import csv

    _bootstrap()
    with session_scope() as session:
        rows = analytics.recent_events(session, analytics.Filters(days=days), limit=200_000)
    if not rows:
        console.print("[yellow]No events in that window.[/yellow]")
        raise typer.Exit(0)
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    console.print(f"[green]Wrote {len(rows):,} events to {out}.[/green]")


@app.command()
def prune(
    days: int = typer.Option(..., help="Delete events older than this many days."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Delete old events."""
    from datetime import datetime, timedelta, timezone

    _bootstrap()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as session:
        doomed = session.scalars(select(RequestEvent).where(RequestEvent.created_at < cutoff)).all()
        if not doomed:
            console.print("[green]Nothing to prune.[/green]")
            return
        if not yes:
            typer.confirm(f"Delete {len(doomed):,} events older than {days} days?", abort=True)
        for event in doomed:
            session.delete(event)
    console.print(f"[green]Deleted {len(doomed):,} events.[/green]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
