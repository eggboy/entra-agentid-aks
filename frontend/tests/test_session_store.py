"""Tests for the server-side session store."""

import time
from unittest.mock import patch

from app.session_store import (
    _SESSION_TTL,
    COOKIE_NAME,
    create_session,
    delete_session,
    get_session,
    set_session_data,
)


class TestSessionStore:
    """Tests for session CRUD operations."""

    def test_create_and_get_session(self):
        """Created session is retrievable by signed cookie."""
        _sid, signed = create_session()
        session = get_session(signed)
        assert session is not None
        assert "_created" in session

    def test_set_and_get_data(self):
        """Data written to session is readable."""
        _sid, signed = create_session()
        set_session_data(signed, "user", {"name": "Alice"})

        session = get_session(signed)
        assert session["user"]["name"] == "Alice"

    def test_overwrite_data(self):
        """Setting the same key overwrites the previous value."""
        _sid, signed = create_session()
        set_session_data(signed, "key", "value1")
        set_session_data(signed, "key", "value2")

        session = get_session(signed)
        assert session["key"] == "value2"

    def test_delete_session(self):
        """Deleted session is no longer retrievable."""
        _sid, signed = create_session()
        delete_session(signed)

        assert get_session(signed) is None

    def test_delete_none_is_safe(self):
        """Deleting None does not raise."""
        delete_session(None)

    def test_get_none_returns_none(self):
        """Getting None returns None."""
        assert get_session(None) is None

    def test_tampered_cookie_returns_none(self):
        """Tampered/invalid signed cookie returns None."""
        assert get_session("tampered.invalid.cookie") is None

    def test_expired_session_returns_none(self):
        """Session past TTL returns None."""
        _sid, signed = create_session()

        # Fast-forward past TTL
        with patch("app.session_store.time") as mock_time:
            mock_time.time.return_value = time.time() + _SESSION_TTL + 1
            assert get_session(signed) is None

    def test_session_within_ttl_returns_data(self):
        """Session within TTL is still accessible."""
        _sid, signed = create_session()
        set_session_data(signed, "key", "value")

        session = get_session(signed)
        assert session is not None
        assert session["key"] == "value"

    def test_set_data_with_invalid_cookie_is_safe(self):
        """Setting data with invalid cookie does not raise."""
        set_session_data("invalid.cookie", "key", "value")

    def test_cookie_name_is_exported(self):
        """COOKIE_NAME is a non-empty string."""
        assert isinstance(COOKIE_NAME, str)
        assert len(COOKIE_NAME) > 0

    def test_multiple_sessions_isolated(self):
        """Different sessions have independent data."""
        _, signed1 = create_session()
        _, signed2 = create_session()

        set_session_data(signed1, "user", "Alice")
        set_session_data(signed2, "user", "Bob")

        assert get_session(signed1)["user"] == "Alice"
        assert get_session(signed2)["user"] == "Bob"
