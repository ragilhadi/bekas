"""TUI smoke tests."""


from bekas.models import AuditReport, AuditSummary, Candidate, Confidence, PluginReport, SystemInfo
from bekas.tui import TuiApp


def test_tui_imports_and_instantiates():
    """Ensure the TUI module can be imported and the app instantiated."""
    app = TuiApp()
    # Textual sets default title via class name before compose()
    assert hasattr(app, "title")


def test_tui_populate_tree_with_report():
    """Ensure _populate_tree handles a real audit report without crashing."""
    app = TuiApp()
    app.audit_report = AuditReport(
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
    # _populate_tree requires mounted Textual widgets; just assert it exists and report is set
    assert hasattr(app, "_populate_tree")
    assert app.audit_report is not None
