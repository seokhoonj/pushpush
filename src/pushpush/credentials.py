"""Where the routes' secrets are kept.

A route's secret is its bot token or its webhook URL -- the credential that lets a
send speak for the destination. It lives in a file only its owner can read, next to
the configuration and well outside any directory that gets synced or committed. This
is the shape `.netrc`, `.pgpass`, and cloud CLI credential files all take, chosen here
for the reason those tools chose it: it never prompts, so a script, a cron job, or an
agent session works the same as a terminal.

The trade is real and worth stating plainly: the file is not encrypted, so it protects
against other users on the machine, not against anything running as you. What limits
the damage is the credential itself -- a bot token is scoped to one bot and revocable
at the service without touching anything else. Store nothing else here.

The store itself -- its location under `config_dir()`, the 0600 file mode, the atomic
read-modify-write, the env-over-file resolution, and stripping a pasted value's
trailing newline -- is credbox's. This module maps a route onto that store: the file is
keyed by the route's name, while the environment is keyed by the same name folded to a
shell-safe suffix (see `_load_secret_from_env`).
"""

from __future__ import annotations

import os
from functools import lru_cache

from credbox import BlankSecretError, CredBoxError, Credentials, env_var_prefix

from pushpush.errors import CredentialsError, MissingSecretError
from pushpush.route import Route

__all__ = [
    "SECRET_ENV_VAR",
    "delete_secret",
    "resolve_secret",
    "secret_env_suffix",
    "store_secret",
]

# The credbox app for pushpush's own store, ~/.config/pushpush/credentials.json.
# `for_app` (not the bare `Credentials(...)`) makes pushpush embeddable: a host that
# sets PUSHPUSH_STORE_APP / PUSHPUSH_NAMESPACE before importing pushpush redirects the
# binding into the host's own store under a "pushpush" section, no code change here.
_STORE_APP = "pushpush"

SECRET_ENV_VAR = "PUSHPUSH_SECRET"


@lru_cache(maxsize=1)
def _get_store() -> Credentials:
    """pushpush's credential store, built on first use and cached.

    Built lazily rather than at import: `for_app` validates the `PUSHPUSH_STORE_APP` /
    `PUSHPUSH_NAMESPACE` override eagerly, so a malformed one would raise credbox's
    `InvalidAppNameError` -- a foreign type -- and abort `import pushpush` itself, in
    the very host-embedding scenario `for_app` exists to serve. Deferred here, it comes
    back as a `CredentialsError` (a `PushpushError`) at the send/store call site, in the
    documented catch surface. credbox resolves the store path per call, so the cached
    binding still isolates a test that repoints `XDG_CONFIG_HOME`.
    """
    try:
        return Credentials.for_app(_STORE_APP)
    except CredBoxError as err:
        raise CredentialsError(
            f"the pushpush credential store binding is invalid: {err}"
        ) from err


def resolve_secret(route: Route) -> str:
    """Find the route's secret: the environment first (so a one-off or a container
    needs no file), then the stored credentials file.

    `PUSHPUSH_SECRET_<ROUTE>` is read before the bare `PUSHPUSH_SECRET` -- with several
    routes configured the bare name cannot say which one it is for, and answering with
    it anyway would send one destination's token to another's service (see
    `_load_secret_from_env`).

    Raises
    ------
    MissingSecretError
        Neither the environment nor the file has a secret for this route.
    CredentialsError
        The credential store binding is invalid, the store could not be read, or the
        stored value holds whitespace or a control character (which would break the
        request URL or a header).
    """
    try:
        # override carries pushpush's own env resolution (the per-route
        # PUSHPUSH_SECRET_* name, then the bare PUSHPUSH_SECRET) and wins. After it,
        # credbox probes an env var named exactly the route -- and a route name is a
        # lowercase word, a valid shell variable, so a stray `alerts=...` in the
        # environment would be read as the alerts secret. It fails loud (a wrong token
        # is refused by the service), not silently, but it is why a route should not be
        # named after a variable that might already be in the environment.
        secret = _get_store().secret(
            route.name, override=_load_secret_from_env(route)
        )
    except CredBoxError as err:
        raise CredentialsError(
            f"the pushpush credential store could not be read: {err}"
        ) from err
    if secret is not None:
        revealed = secret.reveal()
        # A token or webhook URL goes into a request URL (Telegram) or an Authorization
        # header (Slack); a stray space or control character there raises a foreign
        # `InvalidURL`/`ValueError` that echoes the secret in its message. Reject it
        # here, WITHOUT putting the value in the error, as a clean CredentialsError.
        if any(ch.isspace() or not ch.isprintable() for ch in revealed):
            raise CredentialsError(
                f"the stored secret for route {route.name!r} contains whitespace or a "
                f"control character, which cannot go in a request URL or header; "
                f"re-store it without stray characters"
            )
        return revealed
    raise MissingSecretError(
        f"no secret stored for route {route.name!r}; put its "
        f"{route.provider.name} token or webhook URL in the store with "
        f"store_secret(route, secret), or set "
        f"{SECRET_ENV_VAR}_{secret_env_suffix(route.name)}"
    )


def store_secret(route: Route, secret: str) -> None:
    """Store the route's secret in the credentials file (created owner-readable only,
    at mode 0600), leaving any other route's secret in place. A pasted value's
    surrounding whitespace is stripped by the store.

    Raises
    ------
    CredentialsError
        The secret is empty (stored, it would read back as "no secret stored" -- worse
        than storing nothing), or the store could not be read or written.
    """
    try:
        _get_store().set(route.name, value=secret)
    except BlankSecretError as err:  # credbox refuses a blank/whitespace-only value
        raise CredentialsError(
            f"refusing to store an empty secret for route {route.name!r}; paste the "
            f"{route.provider.name} token or webhook URL, or call "
            f"delete_secret(route) to remove the entry"
        ) from err
    except CredBoxError as err:
        raise CredentialsError(
            f"could not store the secret for route {route.name!r}: {err}"
        ) from err


def delete_secret(route: Route) -> None:
    """Remove the route's secret from the credentials file; a no-op when none is
    stored, so revoking is safe to repeat.

    Raises
    ------
    CredentialsError
        The store could not be written (propagated from the store).
    """
    try:
        _get_store().unset(route.name)
    except CredBoxError as err:
        raise CredentialsError(
            f"could not remove the secret for route {route.name!r}: {err}"
        ) from err


def _load_secret_from_env(route: Route) -> str | None:
    """The secret the environment offers for this route, if any.

    `PUSHPUSH_SECRET_ALERTS` beats a bare `PUSHPUSH_SECRET`, because the bare name is
    only unambiguous while one route exists. Reading the bare name first, with several
    routes configured, would hand one exported token to whichever service was asked --
    a disclosure, not an inconvenience. Anything a shell will not take in a variable
    name folds to `_`, so the name that is read is the name that can be exported: a
    route `team-alerts` is read as `PUSHPUSH_SECRET_TEAM_ALERTS`, not the `TEAM-ALERTS`
    a shell rejects.
    """
    per_route = os.environ.get(f"{SECRET_ENV_VAR}_{secret_env_suffix(route.name)}")
    return per_route or os.environ.get(SECRET_ENV_VAR)


def secret_env_suffix(route_name: str) -> str:
    """The env-var suffix a route name folds to: `team-alerts` -> `TEAM_ALERTS`.

    credbox's canonical fold: every character a shell rejects in a variable name
    (anything outside ASCII `[A-Za-z0-9]`) becomes `_`, so the name that is read is one
    a shell can actually export -- a non-ASCII route name folds to underscores rather
    than to itself, which no shell would take. Two names that fold to the same suffix
    are a collision `load_config` refuses (it uses credbox's
    `colliding_env_var_prefixes`, the same fold), otherwise one route's
    `PUSHPUSH_SECRET_*` would answer for the other.
    """
    return env_var_prefix(route_name)
