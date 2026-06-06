"""
Orchestra CLI — entry point for the orchestration engine.

Commands
--------
    orchestra validate <config>
        Validate a team configuration file and print a summary.

    orchestra run <config> <input> [--run-id ID] [--output-dir PATH]
        Run the engine against an input and display the results.

The CLI uses Rich for formatted output.  Transcripts are written to
.orchestra/ in the current working directory by default.

Provider support
----------------
Teams with provider: fake use DemoModelClient (no API key needed).
All other providers (openai, anthropic, bedrock, ollama, …) are routed
through LiteLLMClient.  Set the appropriate API key environment variable
before running (e.g. OPENAI_API_KEY, ANTHROPIC_API_KEY).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from orchestra.config import TeamConfig, load_config
from orchestra.errors import ConfigError, OrchestraError
from orchestra.schemas import RunState, RunStatus, TurnStatus

app = typer.Typer(
    name="orchestra",
    help="Turn-based multi-agent orchestration engine.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# orchestra validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    config: Path = typer.Argument(..., help="Path to team.yaml"),
) -> None:
    """Validate a team configuration file and print a summary."""
    try:
        team = load_config(config)
    except ConfigError as exc:
        err_console.print(f"[bold red]ERR  Config error:[/bold red] {exc}")
        raise typer.Exit(1)
    except FileNotFoundError:
        err_console.print(f"[bold red]ERR  File not found:[/bold red] {config}")
        raise typer.Exit(1)

    console.print(
        f"[bold green]OK[/bold green]  Config loaded: [cyan]{config}[/cyan]\n"
    )
    _print_team_summary(team)


# ---------------------------------------------------------------------------
# orchestra run
# ---------------------------------------------------------------------------


@app.command()
def run(
    config: Path = typer.Argument(..., help="Path to team.yaml"),
    input_text: str = typer.Argument(
        ..., metavar="INPUT", help="Task or question for the team"
    ),
    run_id: Optional[str] = typer.Option(
        None, "--run-id", help="Override the auto-generated run ID"
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        help="Directory for transcript output (default: .orchestra in cwd)",
    ),
) -> None:
    """Run the orchestration engine against an input and display the results."""
    # ── Load config ────────────────────────────────────────────────────────
    try:
        team = load_config(config)
    except ConfigError as exc:
        err_console.print(f"[bold red]ERR  Config error:[/bold red] {exc}")
        raise typer.Exit(1)
    except FileNotFoundError:
        err_console.print(f"[bold red]ERR  File not found:[/bold red] {config}")
        raise typer.Exit(1)

    # ── Build model client ─────────────────────────────────────────────────
    try:
        model_client = _build_model_client(team)
    except ConfigError as exc:
        err_console.print(f"[bold red]ERR  Provider error:[/bold red] {exc}")
        raise typer.Exit(1)

    # ── Wire up engine ─────────────────────────────────────────────────────
    from orchestra.engine import Engine
    from orchestra.transcript_store import TranscriptStore, generate_run_id

    effective_run_id = run_id or generate_run_id()
    transcript_store = TranscriptStore(output_dir)
    engine = Engine(model_client, transcript_store=transcript_store)

    # ── Print header ───────────────────────────────────────────────────────
    console.print(
        Panel(
            f"[bold]Team:[/bold]         {team.name}\n"
            f"[bold]Run ID:[/bold]       {effective_run_id}\n"
            f"[bold]Entry agent:[/bold]  {team.entry_agent}\n"
            f"[bold]Energy:[/bold]       {team.default_energy} units\n"
            f"[bold]Input:[/bold]        {input_text}",
            title="[bold blue]Orchestra Run[/bold blue]",
            border_style="blue",
        )
    )
    console.print()

    # ── Execute ────────────────────────────────────────────────────────────
    try:
        with console.status("[bold green]Running...[/bold green]", spinner="dots"):
            run_state = engine.run(team, input_text, run_id=effective_run_id)
    except OrchestraError as exc:
        err_console.print(f"\n[bold red]ERR  Run error:[/bold red] {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        err_console.print(f"\n[bold red]ERR  Unexpected error:[/bold red] {exc}")
        raise typer.Exit(1)

    # ── Display results ────────────────────────────────────────────────────
    _print_run_results(run_state)

    if run_state.status != RunStatus.completed:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_model_client(team: TeamConfig):
    """
    Instantiate the appropriate ModelClient for the team's model profiles.

    All fake  → DemoModelClient (no network, no API key required).
    All real  → LiteLLMClient (requires litellm and valid API credentials).
    Mixed     → ConfigError (not supported).
    """
    from orchestra.demo_client import DemoModelClient
    from orchestra.litellm_client import LiteLLMClient

    providers = {profile.provider for profile in team.models.values()}
    fake_providers = providers & {"fake"}
    real_providers = providers - {"fake"}

    if fake_providers and real_providers:
        raise ConfigError(
            f"Mixing 'fake' and real providers in the same team is not supported. "
            f"Real providers found: {sorted(real_providers)}. "
            f"Use either all fake providers (for demos/testing) or all real providers."
        )

    if fake_providers:
        return DemoModelClient(team)

    # All real providers → LiteLLM
    return LiteLLMClient(team.models)


def _print_team_summary(team: TeamConfig) -> None:
    """Render a structured summary of a loaded TeamConfig."""
    # ── Overview ───────────────────────────────────────────────────────────
    console.print("[bold]Team[/bold]")
    console.print(f"  Name:          {team.name}")
    console.print(f"  ID:            {team.id}")
    console.print(f"  Entry agent:   {team.entry_agent}")
    console.print(f"  Energy budget: {team.default_energy} units")
    console.print(f"  Agents:        {len(team.agents)}")
    console.print()

    # ── Agent table ────────────────────────────────────────────────────────
    agents_table = Table(
        show_header=True, header_style="bold", box=None, padding=(0, 2)
    )
    agents_table.add_column("ID", style="cyan")
    agents_table.add_column("Name")
    agents_table.add_column("Flags", style="yellow")
    agents_table.add_column("Model profile")
    agents_table.add_column("Can hand off to")

    for agent in team.agents.values():
        flags = "can-finalize" if agent.can_finalize else ""
        if agent.can_handoff_to is None:
            handoff_to = "[dim]any[/dim]"
        else:
            handoff_to = ", ".join(agent.can_handoff_to)
        agents_table.add_row(
            agent.id,
            agent.name,
            flags,
            agent.model_profile,
            handoff_to,
        )

    console.print("[bold]Agents[/bold]")
    console.print(agents_table)
    console.print()

    # ── Model profile table ────────────────────────────────────────────────
    models_table = Table(
        show_header=True, header_style="bold", box=None, padding=(0, 2)
    )
    models_table.add_column("Profile", style="cyan")
    models_table.add_column("Provider")
    models_table.add_column("Model")

    for name, profile in team.models.items():
        models_table.add_row(name, profile.provider, profile.model)

    console.print("[bold]Models[/bold]")
    console.print(models_table)


def _print_run_results(run_state: RunState) -> None:
    """Render the turn log, summary line, and final answer or error panel."""
    # ── Turn log ───────────────────────────────────────────────────────────
    table = Table(
        show_header=True,
        header_style="bold",
        border_style="dim",
    )
    table.add_column("#", style="dim", justify="right")
    table.add_column("Agent", style="cyan")
    table.add_column("Handoff")
    table.add_column("Recipient")
    table.add_column("Status", justify="center")

    for turn in run_state.turns:
        status_cell = (
            "[green]OK[/green]"
            if turn.status == TurnStatus.completed
            else "[red]ERR[/red]"
        )
        table.add_row(
            str(turn.index),
            turn.agent_id,
            turn.handoff_type or "-",
            turn.recipient or "-",
            status_cell,
        )

    console.print("[bold]Turn Log[/bold]")
    console.print(table)

    # ── Summary ────────────────────────────────────────────────────────────
    status_color = {
        RunStatus.completed: "green",
        RunStatus.failed: "red",
        RunStatus.exhausted: "yellow",
        RunStatus.running: "blue",
    }.get(run_state.status, "white")

    console.print()
    console.print(
        f"  Status:  [{status_color}]{run_state.status.value}[/{status_color}]"
    )
    console.print(f"  Turns:   {len(run_state.turns)}")
    console.print(
        f"  Energy:  {run_state.energy.used} / {run_state.energy.initial} used"
    )

    # ── Final answer / error / exhaustion panel ────────────────────────────
    if run_state.status == RunStatus.completed and run_state.final_answer:
        console.print()
        console.print(
            Panel(
                run_state.final_answer,
                title="[bold green]Final Answer[/bold green]",
                border_style="green",
            )
        )

    elif run_state.status == RunStatus.failed and run_state.error:
        console.print()
        console.print(
            Panel(
                run_state.error,
                title="[bold red]Run Failed[/bold red]",
                border_style="red",
            )
        )

    elif run_state.status == RunStatus.exhausted:
        console.print()
        console.print(
            Panel(
                f"Energy budget of {run_state.energy.initial} units was exhausted "
                f"after {len(run_state.turns)} turn(s). "
                f"Increase default_energy in the team config to allow longer runs.",
                title="[bold yellow]Energy Exhausted[/bold yellow]",
                border_style="yellow",
            )
        )
