from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MicrosoftGraphAuthError(RuntimeError):
    """Raised when Microsoft OAuth authentication cannot produce a token."""


@dataclass(frozen=True)
class GraphToken:
    access_token: str
    account_username: str | None
    acquired_silently: bool


@dataclass
class GraphDeviceAuthorization:
    """In-memory Device Code Flow state for an explicitly user-started login."""

    app: Any
    cache: Any
    cache_path: Path
    flow: dict[str, Any]
    expected_sender: str | None

    @property
    def verification_uri(self) -> str:
        return str(
            self.flow.get("verification_uri")
            or self.flow.get("verification_uri_complete")
            or "https://microsoft.com/devicelogin"
        )

    @property
    def user_code(self) -> str:
        return str(self.flow.get("user_code") or "")


def validate_graph_environment(email_config: dict) -> dict[str, Any]:
    """Validate non-secret Graph settings and return normalized values."""
    client_id = (os.getenv("MICROSOFT_CLIENT_ID") or "").strip()
    recipient = str(email_config.get("recipient", "")).strip()
    expected_sender = str(email_config.get("expected_sender", "")).strip() or None

    graph = email_config.get("graph", {}) or {}
    authority = str(
        graph.get("authority", "https://login.microsoftonline.com/consumers")
    ).strip()
    scopes = graph.get("scopes", ["Mail.Send"])
    endpoint = str(
        graph.get("endpoint", "https://graph.microsoft.com/v1.0/me/sendMail")
    ).strip()
    cache_file = str(
        graph.get("token_cache_file", "config/private/msal_token_cache.json")
    ).strip()
    timeout_seconds = int(graph.get("timeout_seconds", 30))
    save_to_sent_items = bool(graph.get("save_to_sent_items", True))

    missing: list[str] = []
    if not client_id:
        missing.append("MICROSOFT_CLIENT_ID")
    if not recipient or recipient == "your_email@example.com":
        missing.append("config/email.json -> email.recipient")
    if not authority:
        missing.append("config/email.json -> email.graph.authority")
    if not endpoint:
        missing.append("config/email.json -> email.graph.endpoint")
    if not cache_file:
        missing.append("config/email.json -> email.graph.token_cache_file")

    if missing:
        raise MicrosoftGraphAuthError(
            "Missing Microsoft Graph configuration: " + ", ".join(missing)
        )

    if not isinstance(scopes, list) or not scopes or not all(
        isinstance(scope, str) and scope.strip() for scope in scopes
    ):
        raise MicrosoftGraphAuthError(
            "email.graph.scopes must be a non-empty list of strings"
        )

    return {
        "client_id": client_id,
        "recipient": recipient,
        "expected_sender": expected_sender,
        "authority": authority,
        "scopes": [scope.strip() for scope in scopes],
        "endpoint": endpoint,
        "cache_file": cache_file,
        "timeout_seconds": timeout_seconds,
        "save_to_sent_items": save_to_sent_items,
    }


def acquire_graph_access_token(
    settings: dict[str, Any],
    project_root: Path,
    logger: logging.Logger,
    *,
    allow_interactive: bool = False,
) -> GraphToken:
    """Acquire a delegated Microsoft Graph token with a persistent MSAL cache.

    Scheduled and web-triggered report runs must never wait for a person to
    complete Device Code Flow, so interactive authorization is disabled by
    default.  A visible, user-started authorization command can opt in to it.
    """
    app, cache, cache_path = _build_msal_client(settings, project_root, logger)

    expected_sender = settings.get("expected_sender")
    accounts = (
        app.get_accounts(username=expected_sender)
        if expected_sender
        else app.get_accounts()
    )

    result: dict[str, Any] | None = None
    account_username: str | None = None

    if accounts:
        account = accounts[0]
        account_username = account.get("username")
        logger.info(
            "Attempting silent Microsoft token acquisition | Cached account=%s",
            account_username or "unknown",
        )
        result = app.acquire_token_silent(settings["scopes"], account=account)
        if result and "access_token" in result:
            logger.info("Microsoft access token acquired silently")
            _save_cache_if_changed(cache, cache_path, logger)
            _validate_expected_sender(expected_sender, account_username)
            return GraphToken(
                access_token=result["access_token"],
                account_username=account_username,
                acquired_silently=True,
            )
        logger.info("Silent token acquisition did not return an access token")
    else:
        logger.info("No matching Microsoft account found in the local token cache")

    if not allow_interactive:
        logger.warning(
            "Interactive Microsoft authorization is required; skipping it in "
            "this unattended run so report generation can continue."
        )
        raise MicrosoftGraphAuthError(
            "Microsoft authorization requires a visible device login. "
            "Run .\\.venv\\Scripts\\python.exe .\\authenticate_email.py, "
            "then complete the Microsoft sign-in."
        )

    logger.warning(
        "Interactive Microsoft authorization is required. "
        "Complete the device login in this visible terminal."
    )
    authorization = start_graph_device_authorization(
        settings,
        project_root,
        logger,
        app=app,
        cache=cache,
        cache_path=cache_path,
    )
    message = str(authorization.flow.get("message", ""))
    logger.warning(
        "Device authorization required | URL=%s | Code=%s",
        authorization.verification_uri,
        authorization.user_code,
    )
    print("\n" + message + "\n", flush=True)
    return complete_graph_device_authorization(authorization, logger)


def start_graph_device_authorization(
    settings: dict[str, Any],
    project_root: Path,
    logger: logging.Logger,
    *,
    app: Any | None = None,
    cache: Any | None = None,
    cache_path: Path | None = None,
) -> GraphDeviceAuthorization:
    """Create a Device Code Flow without waiting for the user to finish it."""
    if app is None or cache is None or cache_path is None:
        app, cache, cache_path = _build_msal_client(settings, project_root, logger)
    flow = app.initiate_device_flow(scopes=settings["scopes"])
    if "user_code" not in flow:
        raise MicrosoftGraphAuthError(
            "Unable to start Microsoft device code flow: "
            + str(flow.get("error_description") or flow)
        )
    return GraphDeviceAuthorization(
        app=app,
        cache=cache,
        cache_path=cache_path,
        flow=dict(flow),
        expected_sender=settings.get("expected_sender"),
    )


def complete_graph_device_authorization(
    authorization: GraphDeviceAuthorization,
    logger: logging.Logger,
) -> GraphToken:
    """Wait for a previously created Device Code Flow and persist its token."""
    result = authorization.app.acquire_token_by_device_flow(authorization.flow)
    _save_cache_if_changed(authorization.cache, authorization.cache_path, logger)
    if not result or "access_token" not in result:
        error = result.get("error") if isinstance(result, dict) else "unknown_error"
        description = result.get("error_description") if isinstance(result, dict) else str(result)
        raise MicrosoftGraphAuthError(
            f"Microsoft authorization failed: {error}: {description}"
        )
    claims = result.get("id_token_claims", {}) or {}
    account_username = (
        claims.get("preferred_username") or claims.get("email") or claims.get("unique_name")
    )
    if not account_username:
        refreshed_accounts = authorization.app.get_accounts()
        if refreshed_accounts:
            account_username = refreshed_accounts[0].get("username")
    _validate_expected_sender(authorization.expected_sender, account_username)
    logger.info(
        "Microsoft access token acquired through device code flow | Account=%s",
        account_username or "unknown",
    )
    return GraphToken(result["access_token"], account_username, acquired_silently=False)


def _build_msal_client(
    settings: dict[str, Any],
    project_root: Path,
    logger: logging.Logger,
) -> tuple[Any, Any, Path]:
    try:
        import msal
    except ImportError as exc:
        raise MicrosoftGraphAuthError(
            "The 'msal' package is not installed. Run: pip install -r requirements.txt"
        ) from exc
    cache_path = _resolve_cache_path(project_root, settings["cache_file"])
    cache = msal.SerializableTokenCache()
    _load_cache(cache, cache_path, logger)
    app = msal.PublicClientApplication(
        client_id=settings["client_id"],
        authority=settings["authority"],
        token_cache=cache,
    )
    return app, cache, cache_path


def _resolve_cache_path(project_root: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    return path if path.is_absolute() else project_root / path


def _load_cache(cache: Any, path: Path, logger: logging.Logger) -> None:
    if not path.exists():
        logger.info("Microsoft token cache does not exist yet: %s", path)
        return
    try:
        cache.deserialize(path.read_text(encoding="utf-8"))
        logger.info("Microsoft token cache loaded: %s", path)
    except Exception as exc:
        raise MicrosoftGraphAuthError(
            f"Unable to read Microsoft token cache {path}: {exc}"
        ) from exc


def _save_cache_if_changed(cache: Any, path: Path, logger: logging.Logger) -> None:
    if not getattr(cache, "has_state_changed", False):
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(cache.serialize(), encoding="utf-8")
    temporary.replace(path)

    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows permissions are controlled mainly through ACLs; failure here
        # should not prevent authentication.
        pass

    logger.info("Microsoft token cache saved: %s", path)


def _validate_expected_sender(
    expected_sender: str | None,
    actual_sender: str | None,
) -> None:
    if not expected_sender or not actual_sender:
        return
    if expected_sender.casefold() != actual_sender.casefold():
        raise MicrosoftGraphAuthError(
            "The signed-in Microsoft account does not match email.expected_sender: "
            f"expected {expected_sender}, got {actual_sender}"
        )
