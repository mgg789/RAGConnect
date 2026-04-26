# Server Reconfigure Runbook

Этот runbook нужен DevOps для перевода серверного RAGConnect на новую схему:

- `server-gateway` отдаёт расширенный admin UI.
- применение runtime-конфига, backup и restore идут через host-side helper;
- helper работает на хосте рядом с Docker Compose и не требует Docker socket внутри контейнера.

## Что изменилось

Новая серверная схема опирается на три директории в корне репозитория:

- `control/` — очередь команд и результаты для host helper;
- `backups/` — архивы бэкапов памяти;
- `.ragconnect-server-logs/` — серверные runtime/audit/security/apply/backup логи.

В `docker-compose.yml` уже добавлены нужные bind mounts и переменные окружения:

- `./control:/control`
- `./backups:/backups`
- `RAGCONNECT_CONTROL_DIR=/control`
- `RAGCONNECT_BACKUP_DIR=/backups`
- `RAGCONNECT_SERVER_LOG_DIR=/data/logs`

## Предпосылки

На сервере должны быть:

1. Docker и `docker compose`
2. Python 3.11+ на хосте
3. клон этого репозитория
4. заполненный `.env`

## Порядок перенастройки

### 1. Обновить код

```bash
cd /path/to/RAGConnect
git fetch --all
git checkout <branch-or-tag>
git pull
```

### 2. Проверить директории

```bash
mkdir -p control backups .ragconnect-server-logs
```

### 3. Проверить `.env`

Минимально должны быть заданы:

- `OPENAI_API_KEY`
- `OPENAI_API_BASE`
- `LLM_MODEL`
- `EMBEDDING_MODEL`
- `EMBEDDING_DIM`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`

Рекомендованный дефолт модели в этой ветке:

```env
LLM_MODEL=gpt-5.4-mini
```

### 4. Пересобрать и поднять контейнеры

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
curl -fsS http://127.0.0.1:8080/health
```

### 5. Поднять host helper на хосте

Helper должен работать вне контейнера, из корня репозитория.

Вариант через venv:

```bash
cd /path/to/RAGConnect
python3 -m venv .host-helper-venv
. .host-helper-venv/bin/activate
pip install --upgrade pip
pip install -e .
python -m server_gateway.host_helper daemon --repo-root /path/to/RAGConnect
```

Если нужен systemd unit, использовать такой шаблон:

```ini
[Unit]
Description=RAGConnect Host Helper
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/RAGConnect
ExecStart=/path/to/RAGConnect/.host-helper-venv/bin/python -m server_gateway.host_helper daemon --repo-root /path/to/RAGConnect
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

После создания unit:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ragconnect-host-helper
sudo systemctl status ragconnect-host-helper
```

### 6. Проверить helper

```bash
cd /path/to/RAGConnect
. .host-helper-venv/bin/activate
python -m server_gateway.host_helper status --repo-root /path/to/RAGConnect
```

Нужно получить:

- `status=ok`
- `helper_online=true`
- список compose services

### 7. Проверить UI

Открыть:

- `http://<server>:8080/ui/configs`
- `http://<server>:8080/ui/graph`

В `ui/configs` проверить:

1. секцию `Runtime Config`
2. секцию `Model`
3. секцию `Backups`
4. секцию `Logs`
5. `helper_online=true`

## Новый поток смены модели

Теперь менять runtime нужно так:

1. открыть server UI
2. ввести `OPENAI_API_BASE`, `OPENAI_API_KEY`, `LLM_MODEL`
3. нажать `Validate`
4. убедиться, что endpoint reachable и модель подтверждена либо хотя бы нет auth error
5. нажать `Apply`

Ожидаемое поведение:

- UI сохранит runtime config;
- запрос уйдёт в `control/requests`;
- host helper обработает его;
- helper сделает `docker compose up -d --force-recreate lightrag server-gateway`;
- если health не восстановится, helper откатит `.env` и попробует rollback.

Если helper недоступен, UI покажет `saved_not_applied`. Это не успех и не перезапуск.

## Новый поток backup / restore

### Ручной backup

Через UI или CLI:

```bash
cd /path/to/RAGConnect
. .host-helper-venv/bin/activate
python -m server_gateway.host_helper backup --repo-root /path/to/RAGConnect
```

Что попадает в архив:

- `.env`
- данные `/data/lightrag`
- `server_tokens.yaml`, если файл существует
- `manifest.json`

### Restore

```bash
cd /path/to/RAGConnect
. .host-helper-venv/bin/activate
python -m server_gateway.host_helper restore --repo-root /path/to/RAGConnect --artifact <backup-file.zip>
```

После restore нужно проверить:

```bash
docker compose ps
curl -fsS http://127.0.0.1:8080/health
```

## Что проверить после перенастройки

1. `docker compose ps` показывает `lightrag` и `server-gateway` в рабочем состоянии
2. `/health` отвечает `200`
3. в UI виден `helper_online=true`
4. `Validate` модели проходит без auth error
5. `Apply` меняет конфиг и перезапускает сервисы
6. manual backup создаёт zip в `backups/`
7. `Logs` и `Backups` секции отдают данные

## Типовые проблемы

### `helper_online=false`

Причина:

- helper не запущен
- helper запущен не из того `repo_root`
- helper не может писать в `control/state`

Проверка:

```bash
systemctl status ragconnect-host-helper
ls -la /path/to/RAGConnect/control/state
```

### `Apply` зависает или остаётся в `saved_not_applied`

Причина:

- helper offline
- права на запись `.env`
- сломан `docker compose`

Проверка:

```bash
docker compose ps
docker compose logs --tail=100 server-gateway lightrag
```

### Backup не создаётся

Причина:

- helper не может сделать `docker compose exec`
- нет прав на `backups/`

Проверка:

```bash
ls -la /path/to/RAGConnect/backups
docker compose ps
```
