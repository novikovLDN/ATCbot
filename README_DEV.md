# Руководство для разработчиков

Этот документ содержит инструкции по настройке локального окружения для разработки.

## Настройка локального окружения

### 1. Создание виртуального окружения

```bash
python3 -m venv .venv
```

### 2. Активация виртуального окружения

**macOS/Linux:**
```bash
source .venv/bin/activate
```

**Windows:**
```bash
.venv\Scripts\activate
```

### 3. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 4. Настройка IDE (Cursor/VSCode)

После установки зависимостей:

1. Откройте проект в Cursor/VSCode
2. Нажмите `Cmd+Shift+P` (macOS) или `Ctrl+Shift+P` (Windows/Linux)
3. Выберите "Python: Select Interpreter"
4. Выберите интерпретатор из `.venv/bin/python` (или `.venv\Scripts\python.exe` на Windows)
5. Перезагрузите окно: `Cmd+Shift+P` → "Developer: Reload Window"

### 5. Переменные окружения

Создайте файл `.env` в корне проекта (не коммитить в git):

```env
BOT_TOKEN=your_bot_token_here
ADMIN_TELEGRAM_ID=your_admin_id_here
DATABASE_URL=postgresql://user:password@localhost:5432/dbname
TG_PROVIDER_TOKEN=your_provider_token_here
OUTLINE_API_URL=your_outline_api_url_here
```

Или экспортируйте переменные в терминале:

```bash
export BOT_TOKEN="your_bot_token_here"
export ADMIN_TELEGRAM_ID="your_admin_id_here"
export DATABASE_URL="postgresql://user:password@localhost:5432/dbname"
export TG_PROVIDER_TOKEN="your_provider_token_here"
export OUTLINE_API_URL="your_outline_api_url_here"
```

## Устранение проблем с импортами

Если Cursor/Pyright показывает предупреждения о невозможности разрешить импорты:

1. Убедитесь, что виртуальное окружение активировано
2. Проверьте, что все зависимости установлены: `pip list`
3. Перезагрузите окно Cursor: `Cmd+Shift+P` → "Developer: Reload Window"
4. Проверьте, что выбран правильный интерпретатор Python

## Запуск проекта

```bash
python main.py
```

## Структура проекта

- `main.py` - точка входа
- `handlers.py` - обработчики команд и callback'ов
- `database.py` - работа с базой данных
- `config.py` - конфигурация
- `localization.py` - локализация
- `requirements.txt` - зависимости Python

## Дополнительная информация

Для получения помощи по настройке окружения обратитесь к основному README.md.



