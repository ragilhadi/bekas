"""Textual TUI for bekas."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, Tree

from bekas.formatters import _human_size
from bekas.models import AuditReport
from bekas.plugin import discover_plugins
from bekas.runner import run_audit


class TuiApp(App):  # type: ignore[type-arg]
    """Interactive TUI for bekas.

    Displays a tree of plugins and their candidates on the left,
    and candidate details in a table on the right.

    Attributes:
        audit_report: The most recent audit report, or None before the first run.
    """

    CSS = """
    Screen { align: left top; }
    #sidebar { width: 30%; height: 100%; border: solid green; }
    #main { width: 70%; height: 100%; border: solid blue; }
    .candidate { padding: 1 2; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh Audit"),
        ("i", "inspect", "Inspect"),
    ]

    audit_report: reactive[AuditReport | None] = reactive(None)

    def compose(self) -> ComposeResult:
        """Build the UI layout.

        Returns:
            ComposeResult yielding the widget tree.
        """
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Plugins", id="plugins-label")
                yield Tree("Plugins", id="plugin-tree")
            with Vertical(id="main"):
                yield Label("Select a plugin to see candidates.", id="detail-label")
                yield DataTable(id="candidate-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the TUI, set up the table, and trigger the first audit."""
        self.title = "bekas"
        self.sub_title = "Audit"
        table = self.query_one("#candidate-table", DataTable)
        table.add_columns("ID", "Size", "Confidence", "Reason")
        self.action_refresh()

    def action_refresh(self) -> None:
        """Run a fresh audit in the background and repopulate the plugin tree."""
        self.query_one("#detail-label", Label).update("Running audit...")
        self.run_worker(self._do_refresh, exclusive=True)

    async def _do_refresh(self) -> None:
        """Background worker that performs the audit without blocking the UI."""
        plugins = discover_plugins()
        report = await asyncio.to_thread(run_audit, plugins, serial=False)
        self.audit_report = report
        self._populate_tree()

    def _populate_tree(self) -> None:
        """Populate the plugin tree from the current audit report."""
        report = self.audit_report
        if report is None:
            return
        tree = self.query_one("#plugin-tree", Tree)
        tree.clear()
        for pr in report.plugins:
            if not pr.candidates:
                continue
            node = tree.root.add(f"{pr.name} ({pr.candidates_found})")
            for c in pr.candidates:
                node.add_leaf(f"{c.id} — {_human_size(c.size_bytes)}")
        tree.root.expand()
        active_plugins = sum(1 for pr in report.plugins if pr.candidates)
        self.query_one("#detail-label", Label).update(
            f"Audit complete: {active_plugins} plugins, {_human_size(report.summary.total_bytes)} reclaimable."
        )

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:  # type: ignore[type-arg]
        """Handle selection of a tree node and update the details table.

        Args:
            event: Tree node selection event.
        """
        label = str(event.node.label)
        table = self.query_one("#candidate-table", DataTable)
        table.clear()
        table.add_columns("ID", "Size", "Confidence", "Reason")
        report = self.audit_report
        if report is None:
            return

        # Leaf nodes represent individual candidates
        if event.node.is_leaf:
            for pr in report.plugins:
                for c in pr.candidates:
                    if label.startswith(c.id + " —"):
                        table.add_row(
                            c.id,
                            _human_size(c.size_bytes),
                            c.confidence.value,
                            c.reason,
                        )
                        self.query_one("#detail-label", Label).update(f"Selected: {c.id}")
                        return
            return

        # Branch nodes represent plugins
        for pr in report.plugins:
            if pr.name in label:
                for c in pr.candidates:
                    table.add_row(
                        c.id,
                        _human_size(c.size_bytes),
                        c.confidence.value,
                        c.reason,
                    )
                self.query_one("#detail-label", Label).update(f"Plugin: {pr.name}")
                return

        # Root selected — show all candidates
        if label == "Plugins":
            for pr in report.plugins:
                for c in pr.candidates:
                    table.add_row(
                        c.id,
                        _human_size(c.size_bytes),
                        c.confidence.value,
                        c.reason,
                    )
            self.query_one("#detail-label", Label).update("All candidates")

    def action_inspect(self) -> None:
        """Display an inspection hint for the currently selected candidate."""
        table = self.query_one("#candidate-table", DataTable)
        cursor = table.cursor_coordinate
        if cursor.row < 0 or cursor.row >= table.row_count:
            return
        try:
            row_key = table.get_row_at(cursor.row)
        except Exception:
            return
        if row_key:
            cid = str(row_key[0])
            self.query_one("#detail-label", Label).update(f"Inspect {cid} (see CLI: bekas inspect {cid})")
