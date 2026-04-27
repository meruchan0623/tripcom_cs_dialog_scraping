from im_archive_cli.models import SessionRecord
from im_archive_cli.state import dedupe_sessions, unique_roles


def test_dedupe_sessions_by_session_id() -> None:
    sessions = [
        SessionRecord(session_id="s1", cs_name="A"),
        SessionRecord(session_id="s1", cs_name="B"),
        SessionRecord(session_id="s2", cs_name="B"),
    ]
    deduped = dedupe_sessions(sessions)
    assert len(deduped) == 2
    assert [x.session_id for x in deduped] == ["s1", "s2"]


def test_unique_roles_sorted() -> None:
    sessions = [
        SessionRecord(session_id="s1", cs_name="Bob"),
        SessionRecord(session_id="s2", cs_name="Alice"),
        SessionRecord(session_id="s3", cs_name="Bob"),
    ]
    assert unique_roles(sessions) == ["Alice", "Bob"]

