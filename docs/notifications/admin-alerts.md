# Автоматические алерты админу (кратко)

[← к оглавлению](README.md)

Не пользовательские уведомления. Все они уходят на `ADMIN_TELEGRAM_ID`, кроме admin_notifier: тот шлёт только Web Push в браузер дашборда.

| Источник | Что и когда | Канал | Анти-спам | Где |
|---|---|---|---|---|
| `admin_alerts.send_alert` | общий вход: категории `payment`, `subscription`, `worker`, `database`, `vpn_api`, `security` | Telegram, 1 ретрай через 2 с | кулдаун на категорию в памяти: 60/120/300/600/300/0 с; `force=True` обходит | `app/services/admin_alerts.py:24-92` |
| ↳ падение итерации воркера | reminders, trial_notifications, fast_expiry, auto_renewal, activation, farm | worker, 5 мин | в кулдауне сообщение **теряется** | `reminders.py:365-366`, `trial_notifications.py:973-974`, `fast_expiry_cleanup.py:411-412`, `auto_renewal.py:588-589`, `activation_worker.py:478-479` |
| ↳ автопродление | ошибка обработки; не удался возврат после ошибки; не отправилось уведомление | payment | 60 с, возврат — force | `auto_renewal.py:276-285, 312-321, 366-375, 464-471` |
| ↳ активация | ошибка БД при пометке активации | subscription | 120 с | `activation_worker.py:161-168, 317-324, 347-354` |
| ↳ watchdog выдачи | `expires_at` выдан больше чем на 8 лет вперёд | security, force | нет | `app/services/subscription_watchdog.py:36, 190-196` |
| ↳ старт бота | не установлен или не подтверждён вебхук | worker, force | нет | `main.py:596-597, 611-612, 631-632` |
| `admin_notifications` | деградированный режим и восстановление БД | Telegram | один раз за процесс, флаги сбрасываются при старте | `admin_notifications.py:21, 30-100`; `main.py:183, 199, 397` |
| `admin_notifications` | «есть pending-активации» (топ-5 старейших) | Telegram | не чаще раза в час | `admin_notifications.py:27, 113-196`; вызов `activation_worker.py:112-125` |
| activation_worker | активация окончательно не удалась (`failed`) | Telegram, Markdown | нет | `activation_worker.py:283-311` |
| healthcheck | БД в деградации, пул None, `SELECT 1` упал, Redis недоступен | Telegram | раз в 10 мин, кулдаун 1 ч | `healthcheck.py:14, 17-31, 33-53, 56-80` |
| admin_notifier | ошибки оплаты (≤1/мин на стадию+провайдера), рассылка завершена, выручка дня пересекла 5…40 тыс. ₽ | **только Web Push** | милстоун раз в МСК-сутки | `app/services/admin_notifier.py:1-14, 33, 167-178, 181-261` |
| purchase_flow | bypass не создался при покупке | Telegram | нет | `app/services/purchase_flow.py:395-410` |
| Platega | выдача прошла, но списание не помечено; прочие ошибки рекуррента | Telegram (`_alert_admin`) | нет | `platega_service.py:1195-1202` и др. |

## Пробелы (коротко)

- **traffic_monitor** не алертит вообще: ошибки только в лог (`app/workers/traffic_monitor.py:68-69, 137-138`).
- **Массовые сбои отправки пользователям** (например, 429 на всех) видны только косвенно, через статистику `automated_notification_sends` в дашборде (`app/services/automated_notifications/helper.py:268-290`), и только для ключей реестра.
- Кулдаун в `send_alert` глотает второй и последующие алерты категории (`admin_alerts.py:58-61`). Потерянные алерты не агрегируются — это отмечено в `docs/audit/00_recon.md` §4.7.
- Три модуля уведомлений админа (`admin_notifications.py`, `admin_alerts.py`, `admin_notifier.py`) — кандидат на объединение (`00_recon.md` §6).
