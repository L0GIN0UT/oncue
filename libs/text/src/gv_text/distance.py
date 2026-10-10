"""Расстояние Дамерау-Левенштейна для допуска опечаток при сверке ответов.

Используется полная (неограниченная) версия Лоуренса-Вагнера: вставка, удаление, замена и
перестановка соседних символов, в том числе с правками между ними. В отличие от упрощённой
OSA-версии это метрика: выполняется неравенство треугольника (проверяется тестом), поэтому
допуск «не дальше k правок» ведёт себя предсказуемо.

Сложность O(len(a)·len(b)); длина ответа ограничена API (120 символов), а при заданном
``max_distance`` строки с разницей длин больше порога отбрасываются без расчёта.
"""

from __future__ import annotations


def damerau_levenshtein(a: str, b: str, max_distance: int | None = None) -> int:
    """Расстояние Дамерау-Левенштейна между строками.

    Args:
        a: Первая строка.
        b: Вторая строка.
        max_distance: Порог раннего выхода: если расстояние заведомо больше, возвращается
            ``max_distance + 1`` (точное значение сверх порога не нужно).

    Returns:
        Число правок; при заданном пороге — не больше ``max_distance + 1``.

    Raises:
        ValueError: отрицательный порог.
    """
    if max_distance is not None:
        if max_distance < 0:
            raise ValueError("max_distance не может быть отрицательным")
        if abs(len(a) - len(b)) > max_distance:
            return max_distance + 1
    if a == b:
        return 0
    distance = _full_distance(a, b)
    if max_distance is not None and distance > max_distance:
        return max_distance + 1
    return distance


def _full_distance(a: str, b: str) -> int:
    """Алгоритм Лоуренса-Вагнера с таблицей последних вхождений символов."""
    n, m = len(a), len(b)
    inf = n + m
    # Матрица (n + 2) × (m + 2): нулевые строка и столбец — «бесконечность».
    d = [[inf] * (m + 2) for _ in range(n + 2)]
    for i in range(n + 1):
        d[i + 1][0] = inf
        d[i + 1][1] = i
    for j in range(m + 1):
        d[0][j + 1] = inf
        d[1][j + 1] = j
    last_row: dict[str, int] = {}
    for i in range(1, n + 1):
        last_col = 0
        ca = a[i - 1]
        for j in range(1, m + 1):
            cb = b[j - 1]
            k = last_row.get(cb, 0)
            l_ = last_col
            cost = 0 if ca == cb else 1
            if cost == 0:
                last_col = j
            d[i + 1][j + 1] = min(
                d[i][j] + cost,  # замена или совпадение
                d[i + 1][j] + 1,  # вставка
                d[i][j + 1] + 1,  # удаление
                d[k][l_] + (i - k - 1) + 1 + (j - l_ - 1),  # перестановка
            )
        last_row[ca] = i
    return d[n + 1][m + 1]
