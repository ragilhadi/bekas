"""Unit tests for TUI methods with mocked widgets."""

from unittest.mock import MagicMock, patch

from bekas.models import AuditReport, AuditSummary, Candidate, Confidence, PluginReport, SystemInfo
from bekas.tui import TuiApp


def test_tui_populate_tree_with_report():
    app = TuiApp()
    report = AuditReport(
        audit_id="ad_test",
        started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        duration_ms=1000,
        system=SystemInfo(os="linux", arch="x86_64", free_disk_bytes=1),
        plugins=[
            PluginReport(
                name="fake.plugin",
                candidates_found=1,
                candidates=[
                    Candidate(
                        id="f:1",
                        category="fake.item",
                        size_bytes=1024,
                        path_or_handle="/tmp/fake",
                        confidence=Confidence.SAFE,
                        reason="test",
                    )
                ],
            )
        ],
        summary=AuditSummary(total_candidates=1, total_bytes=1024),
    )
    app.audit_report = report

    mock_tree = MagicMock()
    mock_tree.root = MagicMock()
    mock_label = MagicMock()

    def mock_query_one(selector, widget_type=None):
        if "tree" in str(selector).lower():
            return mock_tree
        return mock_label

    with patch.object(app, "query_one", side_effect=mock_query_one):
        app._populate_tree()
        mock_tree.clear.assert_called_once()
        mock_tree.root.expand.assert_called_once()


def test_tui_on_tree_node_selected_leaf():
    app = TuiApp()
    report = AuditReport(
        audit_id="ad_test",
        started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        duration_ms=1000,
        system=SystemInfo(os="linux", arch="x86_64", free_disk_bytes=1),
        plugins=[
            PluginReport(
                name="fake.plugin",
                candidates_found=1,
                candidates=[
                    Candidate(
                        id="f:1",
                        category="fake.item",
                        size_bytes=1024,
                        path_or_handle="/tmp/fake",
                        confidence=Confidence.SAFE,
                        reason="test",
                    )
                ],
            )
        ],
        summary=AuditSummary(total_candidates=1, total_bytes=1024),
    )
    app.audit_report = report

    mock_table = MagicMock()
    mock_label = MagicMock()
    mock_node = MagicMock()
    mock_node.is_leaf = True
    mock_node.label = "f:1 — 1.0 KB"

    def mock_query_one(selector, widget_type=None):
        if "table" in str(selector).lower():
            return mock_table
        return mock_label

    with patch.object(app, "query_one", side_effect=mock_query_one):
        from textual.widgets import Tree
        event = Tree.NodeSelected(mock_node)
        app.on_tree_node_selected(event)
        mock_table.clear.assert_called()
        mock_table.add_columns.assert_called()
        mock_table.add_row.assert_called()


def test_tui_on_tree_node_selected_branch():
    app = TuiApp()
    report = AuditReport(
        audit_id="ad_test",
        started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        duration_ms=1000,
        system=SystemInfo(os="linux", arch="x86_64", free_disk_bytes=1),
        plugins=[
            PluginReport(
                name="fake.plugin",
                candidates_found=1,
                candidates=[
                    Candidate(
                        id="f:1",
                        category="fake.item",
                        size_bytes=1024,
                        path_or_handle="/tmp/fake",
                        confidence=Confidence.SAFE,
                        reason="test",
                    )
                ],
            )
        ],
        summary=AuditSummary(total_candidates=1, total_bytes=1024),
    )
    app.audit_report = report

    mock_table = MagicMock()
    mock_label = MagicMock()
    mock_node = MagicMock()
    mock_node.is_leaf = False
    mock_node.label = "fake.plugin (1)"

    def mock_query_one(selector, widget_type=None):
        if "table" in str(selector).lower():
            return mock_table
        return mock_label

    with patch.object(app, "query_one", side_effect=mock_query_one):
        from textual.widgets import Tree
        event = Tree.NodeSelected(mock_node)
        app.on_tree_node_selected(event)
        mock_table.clear.assert_called()
        mock_table.add_columns.assert_called()
        mock_table.add_row.assert_called()


def test_tui_action_inspect_without_selection():
    app = TuiApp()
    mock_table = MagicMock()
    mock_table.cursor_coordinate = MagicMock(row=0)
    mock_table.row_count = 0

    with patch.object(app, "query_one", return_value=mock_table):
        app.action_inspect()
        # If row_count is 0, it should early-return without crashing
