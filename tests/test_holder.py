"""Юнит-тесты детерминированной генерации ФИО."""
from app.holder import holder_name


def test_deterministic():
    assert holder_name("4111111111110001") == holder_name("4111111111110001")


def test_different_requisites_may_differ():
    names = {holder_name(f"41111111111100{i:02d}") for i in range(20)}
    assert len(names) > 1  # не константа


def test_shape_is_three_parts():
    parts = holder_name("4111111111110001").split()
    assert len(parts) == 3  # Фамилия Имя Отчество


def test_ignores_non_digits():
    assert holder_name("4111 1111 1111 0001") == holder_name("4111111111110001")
