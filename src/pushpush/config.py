"""The machine-local config directory, and reading the routes off disk.

`config_dir()` resolves the directory pushpush keeps on the machine through credbox's
own `config_dir`, the same resolver credbox uses for the `0600` `credentials.json`
beside the `config.toml` here -- so by default, and on every platform, the two files
share one directory by construction rather than by two resolvers agreeing. The one
override that parts them is deliberate: `PUSHPUSH_STORE_APP` redirects only the
*store* (to fold a messaging setup into a host program's shared credentials), leaving
this non-secret config where it is. There is no data or state directory -- pushpush
sends and forgets, writing nothing durable to relocate.

The directory lives outside any checkout because a messaging setup is a property
of the machine, not of a project -- and because a project directory is exactly the
kind of place that gets synced to a cloud drive or committed by accident. Secrets
are not in this file; they are in `credentials`. What lives here is the non-secret
half: which service each route uses and where it points.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from credbox import CredBoxError, colliding_env_var_prefixes
from credbox import config_dir as credbox_config_dir

from pushpush.credentials import SECRET_ENV_VAR
from pushpush.errors import ConfigError, UnknownRouteError
from pushpush.provider import resolve_provider
from pushpush.route import Route

__all__ = ["Config", "config_dir", "default_config_path", "load_config"]

CONFIG_PATH_ENV_VAR = "PUSHPUSH_CONFIG"


@dataclass(frozen=True, slots=True)
class Config:
    """Everything pushpush needs that is not a secret.

    Attributes
    ----------
    default_route
        Name of the route `send` uses when the caller names none.
    route_by_name
        Every configured route, read-only.
    """

    default_route: str
    route_by_name: Mapping[str, Route]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "route_by_name", MappingProxyType(dict(self.route_by_name))
        )

    def resolve_route(self, name: str | None = None) -> Route:
        """Look up a route by name, or the default when `name` is None.

        Raises
        ------
        UnknownRouteError
        """
        wanted = name or self.default_route
        try:
            return self.route_by_name[wanted]
        except KeyError as err:
            known = ", ".join(sorted(self.route_by_name))
            raise UnknownRouteError(
                f"no route named {wanted!r} in the configuration; it has: {known}"
            ) from err


def config_dir() -> Path:
    """pushpush's directory on the machine: `config.toml` and the `0600`
    `credentials.json` beside it.

    Delegated to credbox's `config_dir`, the same resolver credbox uses for the
    credential store, so by default and on every platform the two files share one
    directory by construction rather than by two resolvers agreeing. By default that
    is `$XDG_CONFIG_HOME/pushpush` when that holds an absolute path, else
    `~/.config/pushpush` (the git / ssh / aws convention; a relative, blank, or
    unresolvable `XDG_CONFIG_HOME` is ignored per the XDG spec). It follows credbox's
    layout, so a machine or host that puts credbox on the native layout
    (`CREDBOX_LAYOUT=native`) moves the config to the OS-native dir -- but the store
    moves with it, so the two stay together. The config *file* keeps its own per-file
    override, `PUSHPUSH_CONFIG` (see `default_config_path`).

    Raises
    ------
    ConfigError
        No home directory can be found for the `~/.config` fallback (HOME unset and
        the process's uid has no passwd entry -- an arbitrary-uid container). credbox
        raises this as a `CredBoxError`; it is translated so it stays inside the
        `PushpushError` catch surface `send` documents.
    """
    try:
        return credbox_config_dir("pushpush")
    except CredBoxError as err:
        raise ConfigError(
            f"cannot locate the pushpush config directory: {err}"
        ) from err


def default_config_path() -> Path:
    """Where pushpush looks for its configuration.

    `PUSHPUSH_CONFIG` wins; otherwise `config.toml` in `config_dir()`,
    `~/.config/pushpush/config.toml`.

    Raises
    ------
    ConfigError
        `PUSHPUSH_CONFIG` names a path with an unresolvable `~user`, or -- when it is
        unset and the `config_dir()` fallback is used -- no home directory can be found.
    """
    override = os.environ.get(CONFIG_PATH_ENV_VAR)
    if override:
        return _expand_named_path(override, source=CONFIG_PATH_ENV_VAR)
    return config_dir() / "config.toml"


def load_config(path: Path | str | None = None) -> Config:
    """Read and validate the configuration file.

    Raises
    ------
    ConfigError
        The file is missing, is not valid TOML, omits something required, or the
        given path has an unresolvable `~user`.
    UnknownProviderError
        A route names a provider pushpush does not know.
    """
    path = (
        _expand_named_path(path, source="the config path")
        if path is not None
        else default_config_path()
    )
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise ConfigError(
            f"no configuration at {path}; create it with a [routes.<name>] table "
            f"naming a provider"
        ) from err
    except OSError as err:
        raise ConfigError(f"cannot read configuration at {path}: {err}") from err
    except UnicodeDecodeError as err:
        # A ValueError, not an OSError, so it needs its own clause -- otherwise a
        # non-UTF-8 config would escape send()'s documented catch.
        raise ConfigError(f"{path} is not valid UTF-8: {err}") from err
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path} is not valid TOML: {err}") from err
    return _as_config(document, path=path)


def _expand_named_path(value: str | Path, *, source: str) -> Path:
    """Expand `~` in a caller-named path, turning an unresolvable `~user` into a
    ConfigError.

    Unlike `config_dir`, which silently ignores an unusable `XDG_CONFIG_HOME`, a
    path the caller named explicitly must not be silently dropped -- a typo should
    surface, not be swallowed. A `~user` that names nobody makes `Path.expanduser`
    raise RuntimeError, which would bypass the ConfigError catch surface `send`
    documents; convert it.
    """
    try:
        return Path(value).expanduser()
    except RuntimeError as err:
        raise ConfigError(f"{source} {value!r} names no home directory") from err


def _as_config(document: dict[str, Any], *, path: Path) -> Config:
    routes = document.get("routes")
    if not isinstance(routes, dict) or not routes:
        raise ConfigError(f"{path} defines no routes; add a [routes.<name>] table")
    route_by_name = {
        name: _as_route(name, table, path=path) for name, table in routes.items()
    }
    _reject_env_name_collisions(route_by_name, path=path)
    default_route = document.get("default_route")
    if default_route is None:
        if len(route_by_name) > 1:
            known = ", ".join(sorted(route_by_name))
            raise ConfigError(
                f"{path} has several routes ({known}) but no default_route; name "
                f"the one send should use by default"
            )
        default_route = next(iter(route_by_name))
    if default_route not in route_by_name:
        known = ", ".join(sorted(route_by_name))
        raise ConfigError(
            f"{path} sets default_route = {default_route!r}, which is not a "
            f"configured route; it has: {known}"
        )
    return Config(default_route=default_route, route_by_name=route_by_name)


def _reject_env_name_collisions(
    route_by_name: dict[str, Route], *, path: Path
) -> None:
    """Refuse two route names that fold to the same secret env-var suffix.

    `resolve_secret` reads a per-route override from `PUSHPUSH_SECRET_<suffix>`, where
    the suffix is credbox's canonical env-var fold of the route name. Two names that
    fold to one suffix (`a-b` and `a_b`, or two non-ASCII names that both fold to the
    same underscores) would share one variable, so a secret set for one route would
    answer for the other -- exactly the wrong-destination hazard the per-route naming
    exists to prevent. Detected with credbox's own `colliding_env_var_prefixes`, so it
    is the same fold `resolve_secret` reads by, and caught at load time.
    """
    collisions = colliding_env_var_prefixes(route_by_name)
    if collisions:
        prefix, names = min(collisions.items())
        raise ConfigError(
            f"{path}: routes {' and '.join(map(repr, names))} both map to the "
            f"environment variable {SECRET_ENV_VAR}_{prefix}; rename one so a "
            f"per-route secret cannot reach the wrong destination"
        )


def _as_route(name: str, table: object, *, path: Path) -> Route:
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [routes.{name}] must be a table")
    if "provider" not in table:
        raise ConfigError(f"{path}: [routes.{name}] is missing 'provider'")
    # Check the type, not just presence: `provider = 12345` parses fine and then
    # detonates inside resolve_provider with an unhelpful message, far from the
    # config file that is the actual fault.
    if not isinstance(table["provider"], str):
        raise ConfigError(
            f"{path}: [routes.{name}].provider must be a string, not "
            f"{type(table['provider']).__name__}"
        )
    destination = table.get("destination")
    if destination is not None and not isinstance(destination, str):
        raise ConfigError(
            f"{path}: [routes.{name}].destination must be a string, not "
            f"{type(destination).__name__}"
        )
    return Route(
        name        = name,
        provider    = resolve_provider(table["provider"]),
        destination = destination,
    )
