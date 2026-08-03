from src.email_sender import evaluate_email_delivery


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
            "OBSERVE": False,
        },
        "debug": {
            "enabled": False,
            "force_send_email": False,
        },
    }
    assert evaluate_email_delivery(result("REVIEW_BUY"), config).should_send
    assert not evaluate_email_delivery(result("OBSERVE"), config).should_send
    assert not evaluate_email_delivery(result("UNKNOWN"), config).should_send


def test_always_mode():
    config = {
        "enabled": True,
        "send_mode": "always",
        "debug": {
            "enabled": False,
            "force_send_email": False,
        },
    }
    decision = evaluate_email_delivery(result("OBSERVE"), config)
    assert decision.should_send is True
    assert "always" in decision.reason


def test_force_send_bypasses_trigger():
    config = {
        "enabled": True,
        "send_mode": "trigger",
        "triggers": {"OBSERVE": False},
        "debug": {
            "enabled": True,
            "force_send_email": True,
        },
    }
    decision = evaluate_email_delivery(result("OBSERVE"), config)
    assert decision.should_send is True
    assert decision.forced is True
    assert "bypassed" in decision.reason


def test_force_send_does_not_override_disabled_email():
    config = {
        "enabled": False,
        "send_mode": "trigger",
        "triggers": {"OBSERVE": False},
        "debug": {
            "enabled": True,
            "force_send_email": True,
        },
    }
    decision = evaluate_email_delivery(result("OBSERVE"), config)
    assert decision.should_send is False
