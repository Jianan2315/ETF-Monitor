import pytest

from src.config_loader import validate_email_config


BASE_GRAPH = {
    "authority": "https://login.microsoftonline.com/consumers",
    "scopes": ["Mail.Send"],
    "endpoint": "https://graph.microsoft.com/v1.0/me/sendMail",
    "token_cache_file": "config/private/msal_token_cache.json",
    "timeout_seconds": 30,
}


def test_valid_graph_email_config():
    validate_email_config(
        {
            "enabled": True,
            "provider": "microsoft_graph",
            "send_mode": "trigger",
            "triggers": {"WATCH": True},
            "debug": {
                "enabled": False,
                "force_send_email": False,
                "verbose_logging": True,
            },
            "graph": BASE_GRAPH,
        }
    )


def test_invalid_provider():
    with pytest.raises(ValueError):
        validate_email_config(
            {
                "enabled": True,
                "provider": "smtp",
                "send_mode": "trigger",
                "triggers": {},
                "graph": BASE_GRAPH,
            }
        )


def test_invalid_send_mode():
    with pytest.raises(ValueError):
        validate_email_config(
            {
                "enabled": True,
                "provider": "microsoft_graph",
                "send_mode": "sometimes",
                "triggers": {},
                "graph": BASE_GRAPH,
            }
        )


def test_invalid_debug_value():
    with pytest.raises(ValueError):
        validate_email_config(
            {
                "enabled": True,
                "provider": "microsoft_graph",
                "send_mode": "trigger",
                "triggers": {},
                "debug": {"force_send_email": "yes"},
                "graph": BASE_GRAPH,
            }
        )


def test_missing_graph_endpoint():
    invalid_graph = {**BASE_GRAPH, "endpoint": ""}
    with pytest.raises(ValueError):
        validate_email_config(
            {
                "enabled": True,
                "provider": "microsoft_graph",
                "send_mode": "trigger",
                "triggers": {},
                "graph": invalid_graph,
            }
        )
