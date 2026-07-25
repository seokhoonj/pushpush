"""Storing, finding, and guarding the routes' secrets."""

import os
import stat
from pathlib import Path

import pytest

from pushpush import (
    TELEGRAM,
    CredentialsError,
    InsecureCredentialsError,
    MissingSecretError,
    Route,
)
from pushpush.credentials import (
    default_credentials_path,
    delete_secret,
    resolve_secret,
    store_secret,
)

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


def test_route_name_with_hyphen_folds_to_underscore(config_dir, monkeypatch):
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


@pytest.mark.skipif(os.name != "posix", reason="file mode is only real on POSIX")
def test_stored_file_is_owner_only(config_dir):
    store_secret(ALERTS, "token")
    mode = default_credentials_path().stat().st_mode
    assert not (mode & (stat.S_IRWXG | stat.S_IRWXO))


@pytest.mark.skipif(os.name != "posix", reason="file mode is only real on POSIX")
def test_world_readable_file_is_refused(config_dir):
    store_secret(ALERTS, "token")
    path = default_credentials_path()
    path.chmod(0o644)
    with pytest.raises(InsecureCredentialsError):
        resolve_secret(ALERTS)


def test_non_json_file_is_reported(config_dir):
    path = default_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    with pytest.raises(CredentialsError, match="not valid JSON"):
        resolve_secret(ALERTS)


def _write_store(config_dir, text: str):
    path = default_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def test_json_that_is_not_an_object_is_reported(config_dir):
    _write_store(config_dir, "[]")
    with pytest.raises(CredentialsError, match="route name"):
        resolve_secret(ALERTS)


def test_non_string_secret_value_is_reported(config_dir):
    _write_store(config_dir, '{"alerts": 123}')
    with pytest.raises(CredentialsError, match="route name"):
        resolve_secret(ALERTS)


def test_unreadable_store_is_reported(config_dir):
    path = default_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()  # read_text -> IsADirectoryError (OSError)
    if os.name == "posix":
        path.chmod(0o700)  # owner-only so the permission gate passes first
    with pytest.raises(CredentialsError, match="cannot read"):
        resolve_secret(ALERTS)


def test_non_utf8_store_is_reported(config_dir):
    path = default_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe")
    if os.name == "posix":
        path.chmod(0o600)
    with pytest.raises(CredentialsError, match="cannot read"):
        resolve_secret(ALERTS)


def test_failed_atomic_replace_leaves_the_store_intact(config_dir, monkeypatch):
    store_secret(ALERTS, "original-token")

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr("pushpush.credentials.os.replace", fail_replace)
    other = Route(name="team", provider=TELEGRAM, destination="9")
    with pytest.raises(OSError, match="replace failed"):
        store_secret(other, "new-token")

    assert resolve_secret(ALERTS) == "original-token"  # old store untouched
    staged = list(default_credentials_path().parent.glob("*.tmp"))
    assert staged == []  # the staged temp was cleaned up


def test_a_relative_xdg_home_never_places_the_secret_under_the_cwd(monkeypatch):
    monkeypatch.delenv("PUSHPUSH_CREDENTIALS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    assert default_credentials_path() == (
        Path.home() / ".config" / "pushpush" / "credentials.json"
    )


def test_credentials_override_with_unresolvable_tilde_user_is_credentials_error(
    monkeypatch,
):
    # An explicit PUSHPUSH_CREDENTIALS is not silently dropped; the unresolvable
    # `~user` surfaces as CredentialsError, not a bare RuntimeError.
    monkeypatch.setenv("PUSHPUSH_CREDENTIALS", "~nosuchuser_zzz/credentials.json")
    with pytest.raises(CredentialsError, match="names no home directory"):
        default_credentials_path()
