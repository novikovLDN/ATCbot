# Xray Core Management API

FastAPI сервер для управления пользователями Xray Core (VLESS + REALITY).

## Установка

1. Установите зависимости:
```bash
pip install -r requirements.txt
```

2. Скопируйте `.env.example` в `.env` и настройте переменные окружения:
```bash
cp .env.example .env
nano .env
```

3. Убедитесь, что:
   - Xray установлен и работает
   - Путь к `config.json` указан правильно
   - У сервера есть права на чтение/запись `config.json`
   - У сервера есть права на выполнение `systemctl restart xray`

## Запуск

### Разработка
```bash
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

### Production (через systemd)
Создайте файл `/etc/systemd/system/xray-api.service`:

```ini
[Unit]
Description=Xray Core Management API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/path/to/xray_api
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=/path/to/xray_api/.env
ExecStart=/usr/local/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Затем:
```bash
sudo systemctl daemon-reload
sudo systemctl enable xray-api
sudo systemctl start xray-api
```

## API Эндпоинты

### POST /add-user
Добавить нового пользователя.

**Заголовки:**
- `X-API-Key: your_api_key`

**Ответ:**
```json
{
  "uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "vless_link": "vless://..."
}
```

### POST /remove-user
Удалить пользователя.

**Заголовки:**
- `X-API-Key: your_api_key`

**Тело запроса:**
```json
{
  "uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

**Ответ:**
```json
{
  "status": "ok"
}
```

### GET /health
Проверка здоровья сервера (не требует API-ключа).

**Ответ:**
```json
{
  "status": "ok"
}
```

## Безопасность

- Сервер работает только на `127.0.0.1` (локально)
- Все запросы (кроме `/health`) требуют валидный API-ключ в заголовке `X-API-Key`
- Используйте сильный API-ключ в `.env`

## Логирование

Логи выводятся в stdout с уровнем INFO. Для production рекомендуется настроить ротацию логов через systemd или другой механизм.

