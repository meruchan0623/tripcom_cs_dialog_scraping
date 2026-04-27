from im_archive_cli.models import SessionRecord
from im_archive_cli.xlsx_io import preview_sessions


def test_preview_sessions_counts() -> None:
    sessions = [
        SessionRecord(session_id="1", cs_name="A"),
        SessionRecord(session_id="2", cs_name="A"),
        SessionRecord(session_id="3", cs_name="B"),
    ]
    preview = preview_sessions(sessions)
    assert preview.total_sessions == 3
    assert preview.total_roles == 2
    assert preview.roles[0].cs_name == "A"
    assert preview.roles[0].count == 2

