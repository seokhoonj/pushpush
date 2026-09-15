"""Storing, finding, and reporting the routes' secrets.

pushpush maps a route onto credbox's store; the 0600 file mode, the atomic write, the
whitespace strip, and the store format are credbox's own (and its own tests). What
pushpush owns -- and what these tests pin -- is the route-name keying, the
`PUSHPUSH_SECRET[_<ROUTE>]` env resolution with its disclosure-safe precedence, and the
wrapping of a store failure onto pushpush's own error hierarchy.
"""

import pytest

from pushpush import (
    TELEGRAM,
    CredentialsError,
    MissingSecretError,
    Route,
)
from pushpush.credentials import delete_secret, resolve_secret, store_secret

ALERTS = Route(name="alerts", provider=TELEGRAM, destination="123")


def test_store_then_resolve(config_dir):
    store_secret(ALERTS, "bot-token-value")
    assert resolve_secret(ALERTS) == "bot-token-value"


def test_stored_secret_is_stripped(config_dir):
    store_secret(ALERTS, "  token-with-space\n")
    assert resolve_secret(ALERTS) == "token-with-space"


def test_empty_secret_is_refused(config_dir):
    with pytest.raises(CredentialsError):
        store_secret(ALERTS, "   ")


def test_missing_secret_is_reported(config_dir):
    with pytest.raises(MissingSecretError, match="alerts"):
        resolve_secret(ALERTS)


def test_per_route_env_beats_the_file(config_dir, monkeypatch):
    store_secret(ALERTS, "from-file")
    monkeypatch.setenv("PUSHPUSH_SECRET_ALERTS", "from-env")
    assert resolve_secret(ALERTS) == "from-env"


def test_bare_env_is_used_when_no_per_route(config_dir, monkeypatch):
    monkeypatch.setenv("PUSHPUSH_SECRET", "bare-env")
    assert resolve_secret(ALERTS) == "bare-env"


def test_per_route_env_beats_bare_env(config_dir, monkeypatch):
    monkeypatch.setenv("PUSHPUSH_SECRET", "bare-env")
    monkeypatch.setenv("PUSHPUSH_SECRET_ALERTS", "per-route-env")
    assert resolve_secret(ALERTS) == "per-route-env"


def test_route_name_with_hyphen_folds_to_underscore_env(config_dir, monkeypatch):
    route = Route(name="team-alerts", provider=TELEGRAM, destination="1")
    monkeypatch.setenv("PUSHPUSH_SECRET_TEAM_ALERTS", "folded")
    assert resolve_secret(route) == "folded"


def test_delete_removes_the_secret(config_dir):
    store_secret(ALERTS, "token")
    delete_secret(ALERTS)
    with pytest.raises(MissingSecretError):
        resolve_secret(ALERTS)


def test_delete_is_idempotent(config_dir):
    delete_secret(ALERTS)  # nothing stored; must not raise
    delete_secret(ALERTS)


def test_store_preserves_other_routes(config_dir):
    other = Route(name="team", provider=TELEGRAM, destination="9")
    store_secret(ALERTS, "alerts-token")
    store_secret(other, "team-token")
    assert resolve_secret(ALERTS) == "alerts-token"
    assert resolve_secret(other) == "team-token"


def test_a_store_read_failure_is_wrapped_as_credentials_error(config_dir, monkeypatch):
    """A failure inside the credbox store surfaces on pushpush's own hierarchy, so a
    caller catching `CredentialsError` (or `PushpushError`) still catches it."""
    from credbox import CredBoxError

    def boom(*args, **kwargs):
        raise CredBoxError("store unreadable")

    monkeypatch.setattr("pushpush.credentials._store.secret", boom)
    with pytest.raises(CredentialsError, match="could not be read"):
        resolve_secret(ALERTS)
