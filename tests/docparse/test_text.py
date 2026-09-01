from __future__ import annotations

from cliradar.docparse.text import command_form


def test_mode_heading_keeps_the_form_printed_after_it() -> None:
    # The manual sometimes puts the form on the same line as the heading that
    # introduces it. Dropping the line whole loses a command.
    assert command_form("Глобальный вид конфигурации: no line vty vty-number") == (
        "no line vty vty-number"
    )


def test_a_heading_on_its_own_is_not_a_command() -> None:
    assert command_form("Стандартный вид пользователя, вид глобальной конфигурации:") is None
    assert command_form("В режиме конфигурации VLAN:") is None
    assert command_form("(См. перечень команд выше.)") is None


def test_prose_after_a_dash_is_cut_off() -> None:
    assert command_form("reset bgp — принудительный сброс сессий") == "reset bgp"


def test_a_pointer_to_forms_listed_elsewhere_is_not_a_form() -> None:
    assert command_form("redistribute ipv6 [...] — все формы с префиксом ipv6") is None


def test_an_ordinary_form_is_returned_as_printed() -> None:
    assert command_form("  filter rule-number tcp source-ip  ") == (
        "filter rule-number tcp source-ip"
    )
