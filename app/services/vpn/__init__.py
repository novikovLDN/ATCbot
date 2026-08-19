"""app.services.vpn — legacy shim после cutover 2026-08.

Samopis-мастер выведен из эксплуатации, весь provisioning идёт через
`app.services.remnawave_*`. Модуль оставлен как no-op fallback для
существующих call-sites (`fast_expiry_cleanup.py`, `logging_helpers.py`).
"""
