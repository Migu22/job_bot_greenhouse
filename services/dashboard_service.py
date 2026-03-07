# Terminal summary renderer.
# Builds rich tables from `data/jobs.csv` so each run ends with a readable
# status overview, including both this-run and all-time totals.

from datetime import datetime

import pandas as pd
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.logger import get_logger

logger = get_logger()
console = Console()


class DashboardService:
    # Print end-of-run totals and rows in formatted tables.

    def _print_table(self, rows: pd.DataFrame, title: str):
        # Render one summary table with stable columns.
        table = Table(title=title, box=box.ROUNDED, show_lines=True)
        table.add_column("Title", style="cyan", max_width=35)
        table.add_column("Company", style="magenta", max_width=25)
        table.add_column("Status", style="green", max_width=12)
        table.add_column("Applied At", style="yellow", max_width=22)

        if rows.empty:
            table.add_row("-", "-", "-", "-")
        else:
            for _, row in rows.iterrows():
                table.add_row(
                    str(row.get("title", "")),
                    str(row.get("company", "")),
                    str(row.get("status", "")),
                    str(row.get("applied_at", "")),
                )

        console.print(table)

    def show_summary(self):
        # Read jobs CSV and render run-aware summary.
        try:
            jobs = pd.read_csv("data/jobs.csv")
            if jobs.empty:
                console.print(
                    Panel(
                        "[yellow]No applications recorded yet.[/yellow]",
                        title="Job Bot Summary",
                    )
                )
                return

            work = jobs.copy()
            work["applied_at_dt"] = pd.to_datetime(work["applied_at"], errors="coerce")

            # Derive this-run window from run completion events.
            this_run = work
            try:
                logs = pd.read_csv("data/logs.csv")
                if not logs.empty and {"event", "timestamp"}.issubset(logs.columns):
                    run_events = logs[
                        logs["event"].isin(["run_complete", "run_complete_dry"])
                    ].copy()
                    run_events["timestamp_dt"] = pd.to_datetime(
                        run_events["timestamp"], errors="coerce"
                    )
                    run_events = run_events.dropna(subset=["timestamp_dt"]).sort_values(
                        "timestamp_dt"
                    )

                    if len(run_events) >= 1:
                        run_end = run_events.iloc[-1]["timestamp_dt"]
                        run_start = (
                            run_events.iloc[-2]["timestamp_dt"]
                            if len(run_events) >= 2
                            else datetime.min
                        )
                        this_run = work[
                            (work["applied_at_dt"] > run_start)
                            & (work["applied_at_dt"] <= run_end)
                        ]
            except Exception:
                # If logs cannot be read, fallback to showing all rows as this-run.
                this_run = work

            self._print_table(this_run, "Job Bot - This Run")

            this_total = len(this_run)
            this_applied = len(this_run[this_run["status"] == "applied"])
            this_dry = len(this_run[this_run["status"] == "dry_run"])
            this_failed = len(this_run[this_run["status"] == "failed"])
            console.print(
                f"[bold]This run:[/bold] total={this_total} "
                f"applied={this_applied} dry_run={this_dry} failed={this_failed}"
            )

            all_total = len(work)
            all_applied = len(work[work["status"] == "applied"])
            all_dry = len(work[work["status"] == "dry_run"])
            all_failed = len(work[work["status"] == "failed"])
            console.print(
                f"[bold]All-time:[/bold] total={all_total} "
                f"applied={all_applied} dry_run={all_dry} failed={all_failed}\n"
            )

        except (FileNotFoundError, pd.errors.EmptyDataError):
            console.print(
                Panel(
                    "[yellow]No applications recorded yet.[/yellow]",
                    title="Job Bot Summary",
                )
            )
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
