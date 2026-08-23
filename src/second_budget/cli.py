"""``sb`` -- the navigator's command line.

Four verbs, in the order a case actually happens:

    sb check                        is everything wired up
    sb open --narrative FILE        elicit facts, run the budget, show the disagreement
    sb packet --out FILE            render the fair-hearing request
    sb validate [--full]            reproduce the numbers this project claims

``open`` runs the real Strands graph. The facts the model proposes are presented
for confirmation in **one** gate, and nothing enters the budget until a human has
said yes.

``--backend scripted`` runs the whole thing with no credentials and no network,
which is how the test suite runs and how anyone can watch the machinery work
without an AWS account. It is labelled on screen whenever it is used: a demo that
quietly fakes its model is the thing this project argues against.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .control.hooks import BatchConfirmation, HaltWhenFrontierCloses
from .engine.compare import compare
from .engine.constants import OutOfCoverage
from .facts import FactId, FactLedger, Provenance
from .nodes.graph import GraphDidNotConverge, build_graph, run
from .nodes.solver_node import (
    CERTIFICATE_KEY,
    HOUSEHOLD_KEY,
    LEDGER_KEY,
    REFUSAL_KEY,
    RESULT_KEY,
    BudgetSolver,
)

console = Console()

DEFAULT_MODEL = "us.amazon.nova-pro-v1:0"
DEFAULT_REGION = "us-east-1"
CASE_FILE = pathlib.Path(".second-budget-case.json")


# -- model backends ---------------------------------------------------------


def _bedrock(model_id: str, region: str):
    from strands.models.bedrock import BedrockModel

    if "AWS_BEARER_TOKEN_BEDROCK" not in os.environ:
        key = pathlib.Path(__file__).resolve().parents[4] / "zugang" / ".bedrock-key"
        if key.exists():
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = key.read_text(encoding="utf-8").strip()
    os.environ.setdefault("AWS_REGION", region)
    return BedrockModel(model_id=model_id, region_name=region)


def _scripted():
    """A fixed script over the bundled example, so the machinery runs offline."""
    from .models.scripted import ScriptedModel, Turn

    def fact(fact_id: FactId, value, provenance=Provenance.FROM_NARRATIVE):
        return ("record_fact", {
            "fact_id": fact_id.value, "value": value,
            "provenance": provenance.value, "source": "the household's own words",
        })

    return ScriptedModel([
        Turn.tools(
            fact(FactId.HOUSEHOLD_SIZE, 2), fact(FactId.STATE, "Ohio"),
            fact(FactId.BENEFIT_MONTH, "2024-06"), fact(FactId.EARNED_INCOME, 1200.0),
        ),
        Turn.say("recorded what I had so far"),
        Turn.tools(
            fact(FactId.UNEARNED_INCOME, 0.0),
            fact(FactId.ELDERLY_OR_DISABLED, True),
            fact(FactId.CHILD_SUPPORT_PAID, 0.0), fact(FactId.DEPENDENT_CARE, 0.0),
        ),
        Turn.say("recorded what I had so far"),
        Turn.tools(
            fact(FactId.HOMELESS_STATUS, False), fact(FactId.SHELTER_COST, 900.0),
            fact(FactId.UTILITY_ALLOWANCE, 0.0),
            fact(FactId.STATE_DETERMINED_BENEFIT, 210.0),
            fact(FactId.MEDICAL_EXPENSES, 60.0),
        ),
        Turn.say("that is everything the household told me"),
    ])


def _scripted_drafter():
    """The drafter has no tools, so its script must be plain text.

    Handing it the elicitor's script made it call ``record_fact``, which it
    cannot execute. That showed up only as a logged warning while the run still
    produced the right answer -- and a demo that logs warnings it cannot explain
    is not a demo.
    """
    from .models.scripted import ScriptedModel, Turn

    return ScriptedModel([Turn.say(
        "The engine's determination is above. Nothing is added to it here."
    )])


def _models(args):
    if args.backend == "scripted":
        console.print("[yellow]backend: scripted -- no model is being called[/yellow]\n")
        return _scripted(), _scripted_drafter()
    return _bedrock(args.model, args.region), _bedrock(args.model, args.region)


# -- check ------------------------------------------------------------------


def cmd_check(args) -> int:
    import asyncio

    from .engine.constants import for_state
    from .memory.statute_store import StatuteIndexUnhealthy, StatuteStore

    ok = True
    console.print("[bold]second-budget -- readiness[/bold]\n")

    try:
        store = StatuteStore()
        asyncio.run(store.initialize())
        console.print(f"  [green]ok[/green]    regulation index: "
                      f"{len(store.citations())} sections of 7 CFR 273, canary passed")
    except StatuteIndexUnhealthy as exc:
        ok = False
        console.print(f"  [red]FAIL[/red]  regulation index: {exc}")

    try:
        constants = for_state("Ohio")
        console.print(f"  [green]ok[/green]    constants: FY{constants.fiscal_year}, "
                      f"maximum allotment for 2 people = ${constants.max_allotment(2)}")
    except OutOfCoverage as exc:
        ok = False
        console.print(f"  [red]FAIL[/red]  constants: {exc}")

    if args.backend == "bedrock":
        try:
            from strands import Agent

            agent = Agent(model=_bedrock(args.model, args.region), callback_handler=None)
            console.print(f"  [green]ok[/green]    model: {args.model} -> "
                          f"{str(agent('Reply with exactly: OK')).strip()!r}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            console.print(f"  [red]FAIL[/red]  model: {type(exc).__name__}: {str(exc)[:120]}")
    else:
        console.print("  [green]ok[/green]    model: scripted (no credentials needed)")

    console.print()
    console.print("[bold green]READY[/bold green]" if ok else "[bold red]NOT READY[/bold red]")
    return 0 if ok else 1


# -- open -------------------------------------------------------------------


def _confirm(payload: dict, *, assume_yes: bool) -> dict:
    table = Table(title="Confirm these facts before they enter the budget",
                  title_style="bold")
    table.add_column("#", justify="right")
    table.add_column("fact")
    table.add_column("value")
    table.add_column("where it came from")
    for index, item in enumerate(payload["facts"], start=1):
        table.add_row(str(index), str(item["fact_id"]), str(item["value"]),
                      str(item["provenance"]),
                      style="yellow" if item["needs_scrutiny"] else None)
    console.print(table)
    if any(item["needs_scrutiny"] for item in payload["facts"]):
        console.print("[yellow]Rows in yellow were inferred by the model, not stated "
                      "by the household.[/yellow]")

    if assume_yes:
        console.print("[dim]--yes: approving the batch.[/dim]\n")
        return {"rejected": []}

    answer = console.input(
        "\nApprove? [bold]enter[/bold] to accept all, or numbers to reject (e.g. 2 5): "
    ).strip()
    if not answer:
        return {"rejected": []}
    return {"rejected": [
        payload["facts"][int(n) - 1]["fact_id"]
        for n in answer.replace(",", " ").split()
        if n.isdigit() and 1 <= int(n) <= len(payload["facts"])
    ]}


def cmd_open(args) -> int:
    from strands import Agent

    from .nodes.elicitor import build_elicitor

    narrative = pathlib.Path(args.narrative).read_text(encoding="utf-8")
    console.print(Panel(narrative.strip(), title="what the household said",
                        border_style="dim"))

    ledger = FactLedger()
    engine = BudgetSolver()
    elicitor_model, drafter_model = _models(args)

    gate = BatchConfirmation(ledger, navigator=args.navigator)
    halt = HaltWhenFrontierCloses(ledger)
    graph = build_graph(
        elicitor=build_elicitor(elicitor_model, ledger, hooks=[gate, halt]),
        drafter=Agent(model=drafter_model, callback_handler=None,
                      system_prompt="State only what the engine reported."),
        engine=engine,
    )
    invocation_state: dict = {LEDGER_KEY: ledger}

    try:
        run(graph, narrative, invocation_state,
            on_interrupt=lambda interrupt: _confirm(interrupt.reason,
                                                    assume_yes=args.yes))
    except GraphDidNotConverge as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    except OutOfCoverage as exc:
        console.print(Panel(str(exc), title="refused", border_style="red"))
        return 2

    if REFUSAL_KEY in invocation_state:
        console.print(Panel(str(invocation_state[REFUSAL_KEY]), title="refused",
                            border_style="red"))
        return 2

    console.print(f"[dim]{engine.runs} elicitation round(s), {len(ledger)} facts "
                  f"recorded, {gate.gates_paused} confirmation gate(s)[/dim]\n")

    budget = invocation_state[RESULT_KEY]
    household = invocation_state[HOUSEHOLD_KEY]
    stated = int(float(ledger.value(FactId.STATE_DETERMINED_BENEFIT)))

    _show_budget(budget)
    disagreement = compare(household, agency_allotment=stated)
    _show_disagreement(disagreement)

    CASE_FILE.write_text(json.dumps({
        "navigator": args.navigator,
        "derived": disagreement.derived,
        "stated": disagreement.stated_by_agency,
        "facts": {
            fact.id.value: {"value": fact.value, "provenance": fact.provenance.value,
                            "source": fact.source}
            for fact in ledger.entries.values()
        },
    }, indent=2), encoding="utf-8")
    console.print(f"\n[dim]case written to {CASE_FILE} -- "
                  f"run `sb packet --out request.md` to draft the filing[/dim]")
    return 0


def _show_budget(budget) -> None:
    table = Table(title="the budget, stage by stage", title_style="bold")
    table.add_column("stage")
    table.add_column("amount", justify="right")
    table.add_column("regulation")
    for stage in budget.stages:
        table.add_row(stage.name.replace("_", " "), f"${stage.value:,.2f}", stage.cfr)
    console.print(table)


def _show_disagreement(disagreement) -> None:
    if disagreement.agrees:
        console.print(Panel("The notice and the recomputation agree.",
                            border_style="green"))
        return
    whose = ("the household's favour" if disagreement.household_is_owed
             else "the agency's favour")
    console.print(Panel(
        f"The notice says [bold]${disagreement.stated_by_agency:,}[/bold]. "
        f"The engine derives [bold]${disagreement.derived:,}[/bold].\n"
        f"A difference of [bold]${abs(disagreement.gap):,} a month[/bold] in {whose}.",
        title="disagreement", border_style="yellow"))

    if not disagreement.reconciliations:
        console.print("No single input, at any permitted value, explains the difference.")
        return
    console.print("\n[bold]For the notice to be correct, one of these would have to "
                  "be true:[/bold]")
    for line in disagreement.reconciliations:
        console.print(f"  * {line.sentence()}")
    console.print("\n[dim]Inputs that cannot account for the difference at any "
                  "permitted value are omitted rather than shown with an impossible "
                  "figure.[/dim]")


# -- packet -----------------------------------------------------------------


def cmd_packet(args) -> int:
    import asyncio

    from .engine.budget import compute
    from .engine.certificate import certify
    from .engine.constants import for_state
    from .facts import Fact
    from .memory.statute_store import StatuteStore
    from .nodes.solver_node import BudgetSolver
    from .packet.render import render

    if not CASE_FILE.exists():
        console.print(f"[red]no case open. Run `sb open --narrative ...` first.[/red]")
        return 1
    case = json.loads(CASE_FILE.read_text(encoding="utf-8"))

    ledger = FactLedger()
    for fact_id, payload in case["facts"].items():
        ledger.record(Fact(id=FactId(fact_id), value=payload["value"],
                           provenance=Provenance(payload["provenance"]),
                           source=payload.get("source", "")))

    state: dict = {LEDGER_KEY: ledger}
    asyncio.run(BudgetSolver().invoke_async("render", state))
    budget = state[RESULT_KEY]
    household = state[HOUSEHOLD_KEY]
    certificate = state[CERTIFICATE_KEY]
    disagreement = compare(household, agency_allotment=int(case["stated"]))

    store = StatuteStore()
    asyncio.run(store.initialize())

    packet = render(ledger=ledger, budget=budget, disagreement=disagreement,
                    certificate=certificate, store=store,
                    navigator=case.get("navigator", "(navigator)"))

    pathlib.Path(args.out).write_text(packet.markdown, encoding="utf-8")
    console.print(f"[green]written[/green] {args.out}")
    console.print(f"[dim]{len(packet.figures)} figures, all traceable to the engine; "
                  f"{len(packet.quotations)} quotations, all verbatim and re-checked "
                  f"against the statute index[/dim]")
    return 0


# -- validate ---------------------------------------------------------------


def cmd_validate(args) -> int:
    import runpy
    import sys

    modules = ["second_budget.validate.layer_a_allotment"]
    if args.full:
        modules += [
            "second_budget.validate.layer_b_localisation",
            "second_budget.validate.layer_c_constants",
        ]
    for module in modules:
        sys.argv = [module.rsplit(".", 1)[-1]] + ([] if args.full else ["--sample"])
        runpy.run_module(module, run_name="__main__")
        console.print()
    return 0


# -- entry point ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sb", description="An independent second "
                                     "opinion on a household's SNAP determination.")
    parser.add_argument("--backend", choices=("bedrock", "scripted"), default="bedrock")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="is everything wired up").set_defaults(fn=cmd_check)

    opened = sub.add_parser("open", help="elicit facts and compare against the notice")
    opened.add_argument("--narrative", required=True, type=pathlib.Path)
    opened.add_argument("--navigator", default="(navigator)")
    opened.add_argument("--yes", action="store_true",
                        help="approve every fact batch without asking")
    opened.set_defaults(fn=cmd_open)

    packet = sub.add_parser("packet", help="render the fair-hearing request")
    packet.add_argument("--out", default="request.md")
    packet.set_defaults(fn=cmd_packet)

    validated = sub.add_parser("validate", help="reproduce the published numbers")
    validated.add_argument("--full", action="store_true",
                           help="use the whole 44,891-household file, not the sample")
    validated.set_defaults(fn=cmd_validate)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
