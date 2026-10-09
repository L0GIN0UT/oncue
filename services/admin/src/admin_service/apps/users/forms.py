"""Формы раздела «Пользователи»."""

from __future__ import annotations

import re

from django import forms

from admin_service.core.forms import MutationForm

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class UserSearchForm(forms.Form):
    """Поиск пользователей identity."""

    q = forms.CharField(label="Поиск", required=False, max_length=64)


class RolesForm(MutationForm):
    """Полный набор ролей пользователя (``PUT /admin/v1/users/{id}/roles``)."""

    roles = forms.CharField(
        label="Роли", max_length=500, help_text="Через запятую: user, moderator"
    )

    def clean_roles(self) -> list[str]:
        roles = sorted({r.strip() for r in self.cleaned_data["roles"].split(",") if r.strip()})
        bad = [r for r in roles if not _ROLE_RE.match(r)]
        if bad:
            raise forms.ValidationError(f"некорректные роли: {', '.join(bad)}")
        if not roles:
            raise forms.ValidationError("нужна хотя бы одна роль")
        return roles
