import pytest

from src.config_loader import validate_email_config


def test_valid_email_debug_config():
    validate_email_config(
        {
            "enabled": True,
            "send_mode": "trigger",
            "triggers": {"WATCH": True},
            "debug": {
                "enabled": False,
                "force_send_email": False,
                "verbose_logging": True,
            },
        }
    )


def test_invalid_send_mode():
    with pytest.raises(ValueError):
        validate_email_config(
            {
                "enabled": True,
                "send_mode": "sometimes",
                "triggers": {},
            }
        )


def test_invalid_debug_value():
    with pytest.raises(ValueError):
        validate_email_config(
            {
                "enabled": True,
                "send_mode": "trigger",
                "triggers": {},
                "debug": {"force_send_email": "yes"},
            }
        )
