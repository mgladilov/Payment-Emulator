"""Юнит-тесты таблицы сценариев (источник истины поведения)."""
from app import scenarios


def test_suffix_ignores_spaces_and_dashes():
    assert scenarios.suffix_of("4111 1111-1111 0003") == "0003"


def test_resolve_known_suffixes():
    assert scenarios.resolve("4111111111110001").key == "instant_success"
    assert scenarios.resolve("4111111111110002").key == "instant_decline"
    assert scenarios.resolve("4111111111110003").key == "delayed_success"
    assert scenarios.resolve("4111111111110004").key == "delayed_failure"
    assert scenarios.resolve("4111111111110005").key == "timeout_unknown"


def test_resolve_default_for_other_suffix():
    assert scenarios.resolve("4111111111119999").key == "default_success"


def test_instant_scenarios_are_final_immediately():
    assert scenarios.resolve("...0001").initial_status == "success"
    assert scenarios.resolve("...0002").initial_status == "failed"


def test_delayed_scenarios_start_pending():
    for suffix in ("0003", "0004", "0005"):
        sc = scenarios.SCENARIOS[suffix]
        assert sc.initial_status == "pending"
        assert sc.final_status in scenarios.FINAL_STATUSES


def test_configurable_are_only_pending_ones():
    assert set(scenarios.all_configurable()) == {"0003", "0004", "0005"}
