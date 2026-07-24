"""Reading the routes off disk.

The file lives under the XDG config directory rather than beside the code, because
a messaging setup is a property of the machine, not of a checkout -- and because a
project directory is exactly the kind of place that gets synced to a cloud drive
or committed by accident. Secrets are not here; they are in `credentials`. What
lives here is the non-secret half: which service each route uses and where it
points.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pushpush.credentials import SECRET_ENV_VAR, _env_suffix
from pushpush.errors import ConfigError, UnknownRouteError
from pushpush.provider import resolve_provider
from pushpush.route import Route

__all__ = ["Config", "default_config_path", "load_config"]

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


def default_config_path() -> Path:
    """Where pushpush looks for its configuration.

    `PUSHPUSH_CONFIG` wins; otherwise the XDG location,
    `~/.config/pushpush/config.toml`.
    """
    override = os.environ.get(CONFIG_PATH_ENV_VAR)
    if override:
        return Path(override).expanduser()
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg_home).expanduser() if xdg_home else Path.home() / ".config"
    return config_home / "pushpush" / "config.toml"


def load_config(path: Path | str | None = None) -> Config:
    """Read and validate the configuration file.

    Raises
    ------
    ConfigError
        The file is missing, is not valid TOML, or omits something required.
    UnknownProviderError
        A route names a provider pushpush does not know.
    """
    path = Path(path).expanduser() if path is not None else default_config_path()
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise ConfigError(
            f"no configuration at {path}; create it with a [routes.<name>] table "
            f"naming a provider"
        ) from err
    except OSError as err:
        raise ConfigError(f"cannot read configuration at {path}: {err}") from err
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path} is not valid TOML: {err}") from err
    return _as_config(document, path=path)


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

    `resolve_secret` reads a per-route override from `PUSHPUSH_SECRET_<suffix>`,
    where the suffix folds every non-alphanumeric to `_`. Two names that fold to
    one suffix (`a-b` and `a_b`) would share one variable, so a secret set for one
    route would answer for the other -- exactly the wrong-destination hazard the
    per-route naming exists to prevent. Catch it at load time.
    """
    name_by_suffix: dict[str, str] = {}
    for name in route_by_name:
        suffix = _env_suffix(name)
        clash = name_by_suffix.get(suffix)
        if clash is not None:
            raise ConfigError(
                f"{path}: routes {clash!r} and {name!r} both map to the "
                f"environment variable {SECRET_ENV_VAR}_{suffix}; rename one so a "
                f"per-route secret cannot reach the wrong destination"
            )
        name_by_suffix[suffix] = name


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
