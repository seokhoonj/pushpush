"""Where the routes' secrets are kept.

A route's secret is its bot token or its webhook URL -- the credential that lets a
send speak for the destination. They live in a file only their owner can read,
next to the configuration and well outside any directory that gets synced or
committed. This is the shape `.netrc`, `.pgpass`, and cloud CLI credential files
all take, chosen here for the reason those tools chose it: it never prompts, so a
script, a cron job, or an agent session works the same as a terminal.

The trade is real and worth stating plainly: the file is not encrypted, so it
protects against other users on the machine, not against anything running as you.
What limits the damage is the credential itself -- a bot token is scoped to one
bot and revocable at the service without touching anything else. Store nothing
else here.

JSON rather than TOML on purpose: `tomllib` reads TOML but cannot write it, and
hand-rolling TOML string escaping is exactly what corrupts a webhook URL full of
slashes. `json` does both halves correctly.
"""

import json
import os
import stat
from pathlib import Path

from pushpush.config import config_dir
from pushpush.errors import (
    CredentialsError,
    InsecureCredentialsError,
    MissingSecretError,
)
from pushpush.route import Route

__all__ = [
    "CREDENTIALS_FILE_MODE",
    "CREDENTIALS_PATH_ENV_VAR",
    "SECRET_ENV_VAR",
    "default_credentials_path",
    "delete_secret",
    "resolve_secret",
    "secret_env_suffix",
    "store_secret",
]

SECRET_ENV_VAR = "PUSHPUSH_SECRET"
CREDENTIALS_PATH_ENV_VAR = "PUSHPUSH_CREDENTIALS"

# Owner read/write, nothing for anyone else -- what ssh demands of a private key,
# for the same reason: a secret the group can read is not a secret.
CREDENTIALS_FILE_MODE = 0o600


def default_credentials_path() -> Path:
    """Where pushpush looks for stored secrets.

    `PUSHPUSH_CREDENTIALS` wins; otherwise it sits beside the configuration, at
    `~/.config/pushpush/credentials.json`.
    """
    override = os.environ.get(CREDENTIALS_PATH_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return config_dir() / "credentials.json"


def resolve_secret(route: Route, *, path: Path | None = None) -> str:
    """Find the route's secret.

    Checks the environment first, so a one-off or a container can supply the
    secret without a file, then the credentials file. `PUSHPUSH_SECRET_<ROUTE>`
    is read before the bare `PUSHPUSH_SECRET`: with several routes configured the
    bare name cannot say which one it is for, and answering with it anyway would
    send one destination's token to another's service.

    Raises
    ------
    MissingSecretError
        Neither source has a secret for this route.
    InsecureCredentialsError
        The credentials file is readable by anyone but its owner.
    CredentialsError
        The file exists but is not readable JSON.
    """
    from_env = _load_secret_from_env(route)
    if from_env:
        return from_env
    path = path if path is not None else default_credentials_path()
    # The permission gate belongs here, where a secret is trusted -- not in the
    # loader, which store and delete also go through. Refusing to *read* a loose
    # file is the point; store rewrites it tight.
    if path.exists():
        _check_owner_only_readable(path)
    stored = _load_secret_by_route(path).get(route.name)
    if stored:
        return stored
    raise MissingSecretError(
        f"no secret stored for route {route.name!r}; put its "
        f"{route.provider.name} token or webhook URL in {path} with "
        f"store_secret(route, secret), or set "
        f"{SECRET_ENV_VAR}_{secret_env_suffix(route.name)}"
    )


def store_secret(route: Route, secret: str, *, path: Path | None = None) -> None:
    """Write the route's secret to the credentials file.

    Creates the file owner-readable-only, and leaves any other route's secret in
    place. Surrounding whitespace is dropped -- a token pasted from a browser
    almost always arrives with a trailing newline, and no service wants it.

    Raises
    ------
    CredentialsError
        The secret is empty -- storing it would make `resolve_secret` report "no
        secret stored" for a route that has an entry -- or the existing file is
        not readable JSON, so the other routes' secrets cannot be preserved.
    """
    secret = secret.strip()
    if not secret:
        raise CredentialsError(
            f"refusing to store an empty secret for route {route.name!r}; paste "
            f"the {route.provider.name} token or webhook URL, or call "
            f"delete_secret(route) to remove the entry"
        )
    path = path if path is not None else default_credentials_path()
    secret_by_route = _load_secret_by_route(path)
    secret_by_route[route.name] = secret
    _write_secret_by_route(path, secret_by_route)


def delete_secret(route: Route, *, path: Path | None = None) -> None:
    """Remove the route's secret from the credentials file.

    Does nothing when no secret is stored, so revoking is safe to repeat.

    Raises
    ------
    CredentialsError
        The file exists but is not readable JSON, so the other routes' secrets
        cannot be preserved.
    """
    path = path if path is not None else default_credentials_path()
    secret_by_route = _load_secret_by_route(path)
    if secret_by_route.pop(route.name, None) is None:
        return
    _write_secret_by_route(path, secret_by_route)


def _load_secret_from_env(route: Route) -> str | None:
    """The secret the environment offers for this route, if any.

    `PUSHPUSH_SECRET_ALERTS` beats a bare `PUSHPUSH_SECRET`, because the bare name
    is only unambiguous while one route exists. Anything a shell will not take in
    a variable name folds to `_`, so the name that is read is the name that can
    be exported: a route `team-alerts` is read as `PUSHPUSH_SECRET_TEAM_ALERTS`,
    not the `TEAM-ALERTS` a shell rejects.
    """
    per_route = os.environ.get(f"{SECRET_ENV_VAR}_{secret_env_suffix(route.name)}")
    return per_route or os.environ.get(SECRET_ENV_VAR)


def secret_env_suffix(route_name: str) -> str:
    """The env-var suffix a route name folds to: `team-alerts` -> `TEAM_ALERTS`.

    Anything a shell rejects in a variable name folds to `_`. Two names that fold
    to the same suffix are a collision `load_config` refuses -- otherwise one
    route's `PUSHPUSH_SECRET_*` would answer for the other.
    """
    return "".join(char if char.isalnum() else "_" for char in route_name.upper())


def _load_secret_by_route(path: Path) -> dict[str, str]:
    """Read the store, or an empty one when the file does not exist yet.

    Deliberately does not police the file mode; `resolve_secret` does that where
    it matters.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        # UnicodeDecodeError is a ValueError, not an OSError, so it must be named
        # explicitly -- otherwise a non-UTF-8 file would escape send()'s contract.
        raise CredentialsError(f"cannot read {path}: {err}") from err
    try:
        stored = json.loads(text)
    except json.JSONDecodeError as err:
        raise CredentialsError(f"{path} is not valid JSON: {err}") from err
    if not isinstance(stored, dict) or not all(
        isinstance(value, str) for value in stored.values()
    ):
        raise CredentialsError(f"{path} should map each route name to its secret")
    return stored


def _write_secret_by_route(path: Path, secret_by_route: dict[str, str]) -> None:
    """Write the store: whole, or not at all, and never briefly readable.

    Written to a new file and renamed over the target. Opening the real file with
    `O_TRUNC` would empty it before the new content lands, so a crash between
    would destroy another route's secret; `os.replace` is atomic, so a reader
    sees either the old store or the new one. And a fresh `O_EXCL` file is 0600
    from the moment it exists, where writing into an existing store that had been
    loosened to 0644 would put the secret on disk world-readable first and tighten
    it only after.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(
            staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, CREDENTIALS_FILE_MODE
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as store:
            json.dump(secret_by_route, store, indent=2, ensure_ascii=False)
            store.write("\n")
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _check_owner_only_readable(path: Path) -> None:
    """Refuse a credentials file that other users can read.

    POSIX only, because the mode is only real there. Windows synthesises
    `st_mode` from the read-only attribute alone, so this test would match every
    file and send the reader off to run a `chmod` that Windows does not have. What
    guards the file there is the ACL on the user's profile directory, which is not
    ours to read without a dependency.
    """
    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode
    except OSError as err:
        raise CredentialsError(f"cannot check permissions on {path}: {err}") from err
    if not (mode & (stat.S_IRWXG | stat.S_IRWXO)):
        return
    raise InsecureCredentialsError(
        f"{path} is readable by more than its owner; secrets must not be. "
        f"Fix it with: chmod 600 {path}"
    )
