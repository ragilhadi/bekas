"""Textual TUI for bekas."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, Tree

from bekas.formatters import _human_size
from bekas.models import AuditReport
from bekas.plugin import discover_plugins
from bekas.runner import run_audit


class TuiApp(App):  # type: ignore[type-arg]
    """Interactive TUI for bekas."""

    CSS = """
    Screen { align: center middle; }
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
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Plugins", id="plugins-label")
                yield Tree("Plugins", id="plugin-tree")
            with Vertical(id="main"):
                yield Label("Select a plugin to see candidates.", id="detail-label")
                yield DataTable(id="candidate-table")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "bekas"
        self.sub_title = "Audit"
        self.action_refresh()

    def action_refresh(self) -> None:
        plugins = discover_plugins()
        report = run_audit(plugins, serial=False)
        self.audit_report = report
        tree = self.query_one("#plugin-tree", Tree)
        tree.clear()
        tree.root.add("Plugins")
        for pr in report.plugins:
            node = tree.root.add(f"{pr.name} ({pr.candidates_found})")
            for c in pr.candidates:
                node.add_leaf(f"{c.id} — {_human_size(c.size_bytes)}")
        self.query_one("#detail-label", Label).update(
            f"Audit complete: {len(report.plugins)} plugins, {_human_size(report.summary.total_bytes)} reclaimable."
        )

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:  # type: ignore[type-arg]
        label = str(event.node.label)
        # If leaf, show candidate details; if branch, show plugin summary
        table = self.query_one("#candidate-table", DataTable)
        table.clear()
        table.add_columns("ID", "Size", "Confidence", "Reason")
        report = self.audit_report
        if report is None:
            return
        for pr in report.plugins:
            for c in pr.candidates:
                if label.startswith(c.id + " —"):
                    table.add_row(c.id, _human_size(c.size_bytes), c.confidence.value, c.reason)
                    self.query_one("#detail-label", Label).update(f"Selected: {c.id}")
                    return
            # Show all candidates for plugin
            if pr.name in label:
                for c in pr.candidates:
                    table.add_row(c.id, _human_size(c.size_bytes), c.confidence.value, c.reason)
                self.query_one("#detail-label", Label).update(f"Plugin: {pr.name}")
                return

    def action_inspect(self) -> None:
        table = self.query_one("#candidate-table", DataTable)
        cursor = table.cursor_coordinate
        # Textual Coordinate is never None but row can be -1 when empty
        if cursor.row < 0 or cursor.row >= table.row_count:
            return
        try:
            row_key = table.get_row_at(cursor.row)
        except Exception:
            return
        if row_key:
            cid = str(row_key[0])
            self.query_one("#detail-label", Label).update(f"Inspect {cid} (see CLI: bekas inspect {cid})")
