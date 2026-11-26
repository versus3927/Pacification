# Pacification

Консольный бомбер с реестром из 121 провайдера, подтверждением перед отправкой и логом каждого запроса.

## Запуск

```bash
python -m pip install -e .
pacification
```

Если скрипт не попал в PATH:

```bash
python -m pacification
```

Или вообще без PATH и без ручной настройки venv:

```powershell
C:\Users\pc\Downloads\Pacification\run.bat
```

`run.bat` сам создаст `.venv`, поставит пакет и откроет меню.

Меню:

```text
1  ЗАПУСК   — номер → подтверждение → отправка
2  Настройки — раунды, задержка, потоки, режим
3  Провайдеры — реестр и причины пропуска
4  Сухой прогон — план без сети
5  Фильтр живых — отключить мертвые эндпоинты
6  Восстановить — включить все провайдеры
7  Монитор — цикл до подтверждения отправки
0  Выход
```

Поток отправки: ввести номер → увидеть нормализованный E.164 и список готовых провайдеров → подтвердить `y` → получить таблицу итогов.

## Ключи провайдеров

```bash
copy .env.example .env
```

`textbelt` без `PACIFICATION_TEXTBELT_KEY` пропускается до отправки и не расходует общий ключ. Остальные провайдеры тоже пропускаются, пока в `.env` или в окружении нет их ключей. Пропуск виден в меню `3` и в выводе перед запуском.

Поддерживаемые группы:

- Textbelt, Twilio SMS/Call, Vonage, SMS.To, MailerSend, Free2SMS, TopSMS, iSMS.gr, 8x8, LOX24 — ключевые шлюзы.
- OTP-эндпоинты для IR, IN, LY, NP и multi — без ключа, с ограничением по региону номера.

## Реестр

Файл: `pacification/providers.json`. Путь можно переопределить:

```bash
set PACIFICATION_PROVIDERS=C:\path\to\providers.json
```

Обновление реестра из публичных источников:

```bash
python tools\build_registry.py
```

Генератор читает Hamed, Alihtt, TBomb и BomBX, приводит `{phone}`, `{target}` и `Replace phonenumber here` к внутренним токенам, убирает дубли по имени, закрепляет проверенные маркеры успеха, выключает найденные мертвые эндпоинты и перезаписывает `pacification/providers.json`.

Самопроверка реестра на тестовых номерах:

```bash
python tools\selfcheck.py
```

С ключом `--prune` проверка дополнительно отключает в реестре все провайдеры, которые не ответили успехом, кроме `textbelt`:

```bash
python tools\selfcheck.py --prune
```

Повторять проверки, пока какой-нибудь провайдер не подтвердит отправку:

```bash
python tools\selfcheck.py --until-success --max-cycles 50
```

Формат одного провайдера:

```json
{
  "name": "example",
  "kind": "sms",
  "method": "POST",
  "url": "https://api.example.test/send",
  "countries": ["RU"],
  "headers": {"X-Client": "pacification"},
  "query": {"source": "cli"},
  "body_type": "json",
  "body": {"to": "{intl}", "text": "Pacification {code}"},
  "auth": {"type": "bearer", "token": "{env:EXAMPLE_KEY}"},
  "timeout": 10.0,
  "success": {"status": [200], "json_path": "ok", "equals": true}
}
```

Поля:

- `kind`: `sms`, `otp` или `call`. Режим `sms` использует `sms` + `otp`, `call` — только `call`, `both` — все три.
- `countries`: список ISO-кодов; пустой список или отсутствие поля — любой регион.
- `body_type`: `json`, `form` или `raw`.
- `auth`: `{"type":"basic","username":"...","password":"..."}` или `{"type":"bearer","token":"..."}`.
- `success`: `status` — список допустимых HTTP-кодов; `json_path` — путь к полю ответа; `equals` — ожидаемое значение; `exists` — поле должно присутствовать; `regex` — совпадение в теле ответа.

Токены подстановки:

```text
{phone} {e164} {intl}       +79991234567
{digits} {cc_local}         79991234567
{cc}                        7
{local} {subscriber} {target} 9991234567
{local0}                    09991234567
{national}                  9991234567
{country}                   RU
{code}                      случайные 6 цифр
{ts}                        UNIX-ms
{ua}                        User-Agent
{env:NAME}                  обязательное значение из окружения
{env:NAME|default}          значение по умолчанию
```

## Фильтр живых

После обычной отправки `1` CLI предлагает отключить провайдеров, ответивших ошибкой. `textbelt` без `PACIFICATION_TEXTBELT_KEY` пропускается заранее и в ошибки не попадает.

Пункт `5` делает один проход по доступным провайдерам и отключает те, что ответили ошибкой. Перед записью реестра создается `providers.bak.json`. Пункт `6` включает все провайдеры обратно из текущего файла. Результаты проверки пишутся в `pacification-health.log`.

Пункт `7` берет весь реестр — сейчас `121` провайдер — без региональных фильтров и повторяет проходы, пока один из них не подтвердит отправку. Ошибки уходят в `pacification-monitor.log`, в консоли остается только heartbeat и строка `УВЕДОМЛЕНИЕ` с именем провайдера. Остановка — `Ctrl+C`.

Публичные OTP-эндпоинты часто умирают — фильтр убирает мертвые записи, не уменьшая исходный реестр. Полное восстановление из источников:

```bash
python tools\build_registry.py
```

## Движок

- `rounds` — число проходов по реестру; порядок провайдеров перемешивается каждый раунд.
- `delay` — задержка между запусками задач.
- `concurrency` — семафор на одновременные HTTP-запросы.
- `rps` — глобальный лимит запросов в секунду.
- `cooldown` / `max_failures` — провайдер уходит в офлайн после повторяющихся ошибок.
- Ответ с `success=false`, `error`, `status=failed` и текстом `captcha/invalid/rate limit` считается ошибкой даже при HTTP 200.
- Сухой прогон строит тот же план, но не делает сетевых запросов.

Лог: `pacification.log` в рабочей директории, CSV с колонками `ts,provider,kind,ok,status,elapsed_ms,detail`.
