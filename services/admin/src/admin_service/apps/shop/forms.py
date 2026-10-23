"""Формы раздела «Магазин». Поля запросов — по contracts/openapi/progression.json (admin)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django import forms

from admin_service.core.forms import MutationForm

KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
KEY_ERROR = "Латиница в нижнем регистре, цифры и «_», начинается с буквы"
PRICE_MAX = 100_000_000
LEVEL_MAX = 1000
Choices = Sequence[tuple[str, str]]


def _key_field(label: str, *, disabled: bool = False) -> forms.RegexField:
    return forms.RegexField(
        regex=KEY_PATTERN,
        max_length=64,
        label=label,
        error_messages={"invalid": KEY_ERROR},
        disabled=disabled,
    )


def with_choices(form: forms.Form, name: str, choices: Choices | None, current: str = "") -> None:
    """Поле ``name`` — выбор из ``choices`` (из конфига сервиса); без них — ключ текстом.

    Без конфига (progression недоступен) значение проверит сервис. Текущее значение
    редактируемой записи добавляется в список, даже если из конфига его уже убрали.
    """
    if not choices:
        return
    options = list(choices)
    if current and current not in {key for key, _ in options}:
        options.append((current, f"{current} (нет в конфиге)"))
    field = forms.ChoiceField(label=form.fields[name].label, choices=options)
    form.fields[name] = field


class ItemForm(MutationForm):
    """Создание и правка предмета. Ключ после создания неизменяем (сервис: 409)."""

    key = _key_field("Ключ")
    slot = _key_field("Слот")
    rarity = _key_field("Редкость")
    title = forms.CharField(label="Название", max_length=80, strip=True)
    media_kind = forms.ChoiceField(
        label="Вид картинки", choices=[("static", "статичная"), ("animated", "анимированная")]
    )
    level_required = forms.IntegerField(
        label="Нужный уровень", required=False, min_value=1, max_value=LEVEL_MAX
    )
    price = forms.IntegerField(
        label="Цена, Версы", required=False, min_value=1, max_value=PRICE_MAX
    )
    active = forms.BooleanField(label="Активный (виден на витрине; нужна картинка)", required=False)

    def __init__(
        self,
        *args: Any,
        slots: Choices | None = None,
        rarities: Choices | None = None,
        editing: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        current = self.initial if editing else {}
        with_choices(self, "slot", slots, str(current.get("slot") or ""))
        with_choices(self, "rarity", rarities, str(current.get("rarity") or ""))
        if editing:
            self.fields["key"].disabled = True

    def payload(self) -> dict[str, Any]:
        """Тело ``CreateItemRequest``/``UpdateItemRequest`` (вместе с ``reason``)."""
        data = self.cleaned_data
        return {
            "key": data["key"],
            "slot": data["slot"],
            "rarity": data["rarity"],
            "title": data["title"],
            "media_kind": data["media_kind"],
            "level_required": data.get("level_required"),
            "price": data.get("price"),
            "active": bool(data.get("active")),
            "reason": data["reason"],
        }


class ItemMediaForm(MutationForm):
    """Загрузка картинки или animated WebP; размер проверяется до обращения к сервису."""

    file = forms.FileField(label="Файл (PNG, WebP, animated WebP)")

    def __init__(self, *args: Any, limit_bytes: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.limit_bytes = limit_bytes

    def clean_file(self) -> Any:
        upload = self.cleaned_data["file"]
        if upload.size > self.limit_bytes:
            raise forms.ValidationError(
                f"Файл {upload.size} байт больше допустимых {self.limit_bytes} байт"
            )
        return upload


class PackForm(MutationForm):
    """Создание и правка пака (продажа выключена, пока ``billing.enabled=false``)."""

    key = _key_field("Ключ")
    title = forms.CharField(label="Название", max_length=80, strip=True)
    style = _key_field("Стиль")
    item_ids = forms.MultipleChoiceField(label="Предметы (1–50)")
    verses_amount = forms.IntegerField(
        label="Версы в паке", min_value=0, max_value=PRICE_MAX, initial=0
    )
    price_kopecks = forms.IntegerField(label="Цена, копейки", min_value=1, max_value=PRICE_MAX)
    active = forms.BooleanField(label="Активный", required=False)

    def __init__(
        self,
        *args: Any,
        item_choices: Choices = (),
        editing: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["item_ids"].choices = list(item_choices)  # type: ignore[attr-defined]  # MultipleChoiceField
        if editing:
            self.fields["key"].disabled = True

    def clean_item_ids(self) -> list[str]:
        ids: list[str] = self.cleaned_data["item_ids"]
        if not 1 <= len(ids) <= 50:  # noqa: PLR2004 - предел контракта SavePackRequest
            raise forms.ValidationError("В паке от 1 до 50 предметов")
        return ids

    def payload(self) -> dict[str, Any]:
        """Тело ``SavePackRequest``."""
        data = self.cleaned_data
        return {
            "key": data["key"],
            "title": data["title"],
            "style": data["style"],
            "item_ids": list(data["item_ids"]),
            "verses_amount": data["verses_amount"],
            "price_kopecks": data["price_kopecks"],
            "active": bool(data.get("active")),
            "reason": data["reason"],
        }


class PromoForm(MutationForm):
    """Партия промокодов: награда (Версы или предмет), число кодов, лимиты, срок."""

    label = forms.CharField(label="Метка партии", min_length=1, max_length=80, strip=True)
    reward_kind = forms.ChoiceField(
        label="Награда", choices=[("verses", "Версы"), ("item", "Предмет")]
    )
    verses_amount = forms.IntegerField(
        label="Версов", required=False, min_value=1, max_value=PRICE_MAX
    )
    item_id = forms.ChoiceField(label="Предмет", required=False)
    count = forms.IntegerField(label="Сколько кодов", min_value=1, max_value=1000)
    max_uses = forms.IntegerField(label="Погашений на код", min_value=1, max_value=1_000_000)
    per_user_once = forms.BooleanField(label="Один раз на игрока", required=False, initial=True)
    expires_at = forms.DateTimeField(
        label="Действует до (UTC)",
        required=False,
        widget=forms.DateTimeInput({"type": "datetime-local"}),
    )

    def __init__(self, *args: Any, item_choices: Choices = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["item_id"].choices = [("", "—"), *item_choices]  # type: ignore[attr-defined]

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        kind = data.get("reward_kind")
        if kind == "verses" and not data.get("verses_amount"):
            self.add_error("verses_amount", "Укажите число Версов")
        if kind == "item" and not data.get("item_id"):
            self.add_error("item_id", "Выберите предмет")
        return data

    def payload(self) -> dict[str, Any]:
        """Тело ``CreatePromoRequest``."""
        data = self.cleaned_data
        if data["reward_kind"] == "verses":
            reward: dict[str, Any] = {"kind": "verses", "verses_amount": data["verses_amount"]}
        else:
            reward = {"kind": "item", "item_id": data["item_id"]}
        expires = data.get("expires_at")
        return {
            "label": data["label"],
            "reward": reward,
            "count": data["count"],
            "max_uses": data["max_uses"],
            "per_user_once": bool(data.get("per_user_once")),
            "expires_at": expires.isoformat() if expires else None,
            "reason": data["reason"],
        }
