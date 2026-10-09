"""Value objects и политики домена identity."""

from __future__ import annotations

import pytest
from identity_service.domain import (
    ConsentRequirement,
    DisplayName,
    EmailAddress,
    Nickname,
    PasswordPolicy,
    missing_consents,
    parse_consent_requirements,
)

from gv_core import InvalidInputError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("neo_2026", "neo_2026"),
        ("Нео.Матрица-1", "Нео.Матрица-1"),
        ("  trim  ", "trim"),
        ("ｆｕｌｌ", "full"),  # NFKC: полноширинные символы
        ("abc", "abc"),
        ("a" * 24, "a" * 24),
    ],
)
def test_nickname_valid(raw: str, expected: str) -> None:
    assert Nickname.parse(raw).value == expected


@pytest.mark.parametrize("raw", ["ab", "a" * 25, "with space", "emoji😀", "a@b", "~deleted~", ""])
def test_nickname_invalid(raw: str) -> None:
    with pytest.raises(InvalidInputError) as info:
        Nickname.parse(raw)
    assert info.value.code == "user.nickname_invalid"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Neo@Example.COM", "neo@example.com"),
        ("user@пример.рф", "user@xn--e1afmkfd.xn--p1ai"),
        (" a.b+tag@mail.ru ", "a.b+tag@mail.ru"),
    ],
)
def test_email_normalization(raw: str, expected: str) -> None:
    assert EmailAddress.parse(raw).value == expected


@pytest.mark.parametrize("raw", ["no-at", "a@", "@b.ru", "a b@c.ru", "x" * 300 + "@a.ru"])
def test_email_invalid(raw: str) -> None:
    with pytest.raises(InvalidInputError) as info:
        EmailAddress.parse(raw)
    assert info.value.code == "user.email_invalid"


def test_email_is_not_repr_leaked() -> None:
    # ПДн не должны случайно попасть в логи через repr объекта.
    assert "neo" not in repr(EmailAddress.parse("neo@example.com"))


def test_display_name() -> None:
    assert DisplayName.parse("  Нео  ").value == "Нео"
    for bad in ("", "   ", "x" * 41, "a\x00b"):
        with pytest.raises(InvalidInputError):
            DisplayName.parse(bad)


POLICY = PasswordPolicy(banned=frozenset({"qwertyuiop", "1234567890"}))


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("short", "too_short"),
        ("x" * 129, "too_long"),
        ("QwertyUiop", "too_common"),
        ("1234567890", "too_common"),
        ("neo_matrix_2026", "matches_login"),
    ],
)
def test_password_policy_rejects(password: str, reason: str) -> None:
    with pytest.raises(InvalidInputError) as info:
        POLICY.check(password, nickname="neo_matrix_2026", email="neo@example.com")
    assert info.value.code == "auth.password_weak"
    assert info.value.details == {"reason": reason}


def test_password_policy_accepts_long_unique() -> None:
    POLICY.check("correct horse battery", nickname="neo", email="neo@example.com")


def test_consent_requirements() -> None:
    required = parse_consent_requirements("terms:2026-10, privacy:2026-10,age_18:1")
    assert required == (
        ConsentRequirement("terms", "2026-10"),
        ConsentRequirement("privacy", "2026-10"),
        ConsentRequirement("age_18", "1"),
    )
    granted = [ConsentRequirement("terms", "2026-10"), ConsentRequirement("privacy", "2025-01")]
    assert missing_consents(required, granted) == (
        ConsentRequirement("privacy", "2026-10"),
        ConsentRequirement("age_18", "1"),
    )
    assert parse_consent_requirements("") == ()
    with pytest.raises(ValueError, match="согласи"):
        parse_consent_requirements("terms")
