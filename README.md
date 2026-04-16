# RAGConnect

RAGConnect даёт агенту одну личную память и любое количество изолированных проектных памятей.
Основной сценарий использования здесь агентный: пользователь описывает задачу обычным языком, отвечает на короткий набор вопросов, а агент сам настраивает локальную память, MCP и при необходимости сервер.

## Что получает пользователь

- локальную память по умолчанию для личного долгосрочного контекста
- проектную память по `project_label`
- MCP для Codex и Claude Desktop
- опциональный серверный деплой для общей памяти команды
- опциональный автозапуск локальной памяти при входе в Windows

## Рекомендуемый сценарий старта

Открой репозиторий в агенте и напиши:

`Create my own memory here`

Дальше агент должен сам:
1. задать обязательные вопросы из `AGENTS.md` или `CLAUDE.md`
2. поднять локальную память, если она нужна
3. подключить MCP для Codex и или Claude Desktop, если это нужно
4. включить автозапуск локальной памяти, если пользователь этого хочет
5. задеплоить серверную память, если пользователь этого хочет
6. проверить `health`, `write` и `search`
7. подтвердить маршрутизацию: без label в локальную память, с label в проектную

Пользователь не должен вручную открывать терминал или писать команды, если агент может сделать это сам.

## Скрипты для локальной установки

### Windows

- `scripts/windows/install-local-stack.ps1` — основной bootstrap
- `scripts/windows/install-codex-mcp.ps1` — MCP для Codex
- `scripts/windows/install-claude-mcp.ps1` — MCP для Claude Desktop
- `scripts/windows/install-autostart.ps1` — автозапуск через папку Startup
- `scripts/windows/uninstall-autostart.ps1`
- `scripts/windows/start-local-stack.ps1`
- `scripts/windows/stop-local-stack.ps1`

Что делает `install-local-stack.ps1`:
- создаёт `~/.ragconnect` и `.venv`
- ставит проект, LightRAG API и embedding runtime
- пишет `~/.ragconnect/.env` и `client_config.yaml`
- по флагу ставит MCP для Codex или Claude Desktop
- по флагу включает автозапуск

```powershell
powershell -File scripts/windows/install-local-stack.ps1 `
  -RepoRoot "C:\path\to\RAGConnect" `
  -PythonPath "C:\Path\To\python.exe" `
  -InstallCodexMcp `
  -InstallClaudeMcp `
  -EnableAutostart
```

### macOS

- `scripts/macos/install-local-stack.sh` — основной bootstrap
- `scripts/macos/install-codex-mcp.sh` — MCP для Codex
- `scripts/macos/install-claude-mcp.sh` — MCP для Claude Desktop
- `scripts/macos/install-autostart.sh` — автозапуск через LaunchAgent
- `scripts/macos/uninstall-autostart.sh`
- `scripts/macos/start-local-stack.sh`
- `scripts/macos/stop-local-stack.sh`

Что делает `install-local-stack.sh`:
- создаёт `~/.ragconnect` и `.venv`
- ставит проект, LightRAG API и embedding runtime
- пишет `~/.ragconnect/.env` (chmod 600) и `client_config.yaml`
- по флагу ставит MCP для Codex или Claude Desktop
- по флагу включает автозапуск через `~/Library/LaunchAgents/com.ragconnect.local-stack.plist`

```bash
bash scripts/macos/install-local-stack.sh \
  --repo-root /path/to/RAGConnect \
  --api-key sk-... \
  --install-claude-mcp \
  --enable-autostart
```

## Подключение MCP в Codex

Рекомендуемый способ: запускать прямой Python module entrypoint.

Блок для `~/.codex/config.toml`:

```toml
[mcp_servers.ragconnect]
command = "/Users/<you>/.ragconnect/.venv/bin/python3"
args = ["-m", "client_gateway.mcp_server"]
cwd = "/path/to/RAGConnect"
enabled = true

[mcp_servers.ragconnect.env]
PYTHONPATH = "/path/to/RAGConnect"
RAGCONNECT_CONFIG_PATH = "/Users/<you>/.ragconnect/client_config.yaml"
RAGCONNECT_PROMPTS_DIR = "/path/to/RAGConnect/config/prompts"
```

Скрипты `install-codex-mcp.ps1` / `install-codex-mcp.sh` пишут этот блок автоматически.

## Подключение MCP в Claude Desktop

Для Claude Desktop используется тот же module entrypoint: `python -m client_gateway.mcp_server`.
Скрипты `install-claude-mcp.ps1` / `install-claude-mcp.sh` автоматически обновляют `claude_desktop_config.json`.

## Автозапуск локальной памяти

Если пользователь отвечает "да" на вопрос про автозапуск, агент включает его сам:
- **Windows**: кладёт `.cmd`-файл в папку `Startup` (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`)
- **macOS**: создаёт LaunchAgent plist в `~/Library/LaunchAgents/` и сразу загружает его

Оба варианта не требуют прав администратора.

## Сниппет для проектного `AGENTS.md` или `CLAUDE.md`

Для проекта, который должен пользоваться проектной памятью, скопируй один из файлов и замени `LABEL_HERE`:

- `config/AGENTS.md.example`
- `config/CLAUDE.md.example`

Этот сниппет сообщает агенту:
- какой `project_label` использовать
- что память надо воспринимать как рабочую память, а не как вспомогательный инструмент
- когда искать в памяти перед ответом
- когда обязательно писать результат обратно
- когда использовать локальную память без label

## Docker-деплой серверной памяти

### Быстрый путь

1. Скопировать `.env.example` в `.env`.
2. Заполнить `OPENAI_API_KEY` и `RAGCONNECT_ADMIN_PASSWORD`.
3. Запустить `docker compose up -d`.
4. Создать write-token:

```bash
docker compose exec server-gateway ragconnect-server token create --role write --description "Initial user"
```

### Что включает текущий Docker-стек

- LightRAG с OpenAI-compatible binding
- локальный embedding proxy внутри контейнера LightRAG
- дефолтную embedding-модель `intfloat/multilingual-e5-small`
- проектный gateway с token auth

То есть Docker-конфигурация повторяет рабочую схему, которая уже была проверена на живом окружении.

## Модель памяти

- без `project_label` -> локальная личная память
- с `project_label="some-project"` -> общая проектная память этого проекта
- личные заметки нужно держать локально
- проектные знания нужно держать в проектной памяти

## MCP-промпты

Промпты, которые задают поведение агента по памяти, лежат здесь:

- `config/prompts/global.md`
- `config/prompts/rules.md`

Они специально написаны так, чтобы агент воспринимал память как свою внешнюю долговременную память и пользовался ей проактивно.

## Итоговый список вопросов, которые должен задать агент

1. Нужна только локальная память или ещё и проектная память на своём сервере?
2. Если нужен сервер, какие SSH-параметры подключения использовать?
3. Установлен ли Docker на текущей машине?
4. Если нужен сервер, есть ли sudo-пароль или passwordless sudo?
5. Есть ли домен для сервера?
6. Если домен есть, настроены ли уже DNS A/AAAA записи?
7. Какой Git URL использовать на сервере?
8. Какую ветку или тег деплоить?
9. Откуда брать `OPENAI_API_KEY`?
10. Используется стандартный OpenAI endpoint или совместимый?
11. Если endpoint совместимый, какой `OPENAI_API_BASE`?
12. Нужны ли кастомные `LLM_MODEL` и `EMBEDDING_MODEL`?
13. Нужна локальная память, проектная память или обе?
14. Какой `memory-label` нужен этому проекту?
15. Какой URL у Server Gateway?
16. Какой `tok_...` использовать для проектной памяти?
17. Должны ли запросы без label идти в локальную память?
18. Нужен ли `remote_only_mode=true`?
19. Нужна ли строгая маршрутизация без fallback?
20. Нужно ли автоматически настроить MCP для Codex, Claude Desktop или обоих?
21. Нужен ли автозапуск локальной памяти при входе в систему?

## Технические заметки

- локальные embeddings по умолчанию: `intfloat/multilingual-e5-small`, размерность `384`
- прямой Codex MCP entrypoint: `python -m client_gateway.mcp_server`
- `pyproject.toml` использует `setuptools.build_meta`, поэтому editable install работает штатно
- текущий Windows-автозапуск сделан через папку `Startup`
