"""HTTP API media_pipeline: публичный (/v1), внутренний (/internal/v1), админский (/admin/v1).

Роутеры получают готовые use-cases: сборка с адаптерами — в ``main.py`` (composition root),
потому что api и infrastructure — независимые слои (import-linter).
"""
