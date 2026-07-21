from src.email_sender import evaluate_email_delivery, should_send_email


def result(status):
    return {"decision": {"status": status}}


def test_disabled_email_never_sends():
    config = {
        "enabled": False,
        "triggers": {"REVIEW_BUY": True},
        "debug": {
            "enabled": False,
            "force_send_email": False,
        },
    }
    decision = evaluate_email_delivery(result("REVIEW_BUY"), config)
    assert decision.should_send is False
    assert "email.enabled is false" in decision.reason


def test_trigger_map_controls_status():
    config = {
        "enabled": True,
        "send_mode": "trigger",
        "triggers": {
            "REVIEW_BUY": True,
            "WATCH": False,
        },
        "debug": {
            "enabled": False,
            "force_send_email": False,
        },
    }
    assert should_send_email(result("REVIEW_BUY"), config)
    assert not should_send_email(result("WATCH"), config)
    assert not should_send_email(result("UNKNOWN"), config)


def test_legacy_observe_statuses_use_the_new_observe_trigger():
    config = {"enabled": True, "send_mode": "trigger", "triggers": {"OBSERVE": True}, "debug": {}}
    assert should_send_email(result("WAIT"), config)
    assert should_send_email(result("WATCH"), config)
    assert should_send_email(result("HOLD"), config)


def test_always_mode():
    config = {
        "enabled": True,
        "send_mode": "always",
        "debug": {
            "enabled": False,
            "force_send_email": False,
        },
    }
    decision = evaluate_email_delivery(result("HOLD"), config)
    assert decision.should_send is True
    assert "always" in decision.reason


def test_force_send_bypasses_trigger():
    config = {
        "enabled": True,
        "send_mode": "trigger",
        "triggers": {"HOLD": False},
        "debug": {
            "enabled": True,
            "force_send_email": True,
        },
    }
    decision = evaluate_email_delivery(result("HOLD"), config)
    assert decision.should_send is True
    assert decision.forced is True
    assert "bypassed" in decision.reason


def test_force_send_does_not_override_disabled_email():
    config = {
        "enabled": False,
        "send_mode": "trigger",
        "triggers": {"HOLD": False},
        "debug": {
            "enabled": True,
            "force_send_email": True,
        },
    }
    decision = evaluate_email_delivery(result("HOLD"), config)
    assert decision.should_send is False
