# Закрытая веб-карта Cloudflare

Проект Pages: `steel-soil-atfaster`. Почта первоначального доступа: `atfasters@gmail.com`.
Access настроен для основного адреса и всех preview-адресов; единственная разрешённая почта — `atfasters@gmail.com`. Вход по One-time PIN проверен 14.10.2026. Перед каждой автоматической публикацией скрипт повторно проверяет анонимные перенаправления на Access.

Карта опубликована 14.10.2026: https://steel-soil-atfaster.pages.dev/. Проверена сборка `cbba5cd67f8540db6c9e`, 3 420 загруженных файлов со служебным `deployment.json`. Адрес публикации: https://01c63dc5.steel-soil-atfaster.pages.dev. После загрузки повторно проверены анонимные обращения к `/`, `/tiles/0,0.png`, `/surface/index.json`, `/colonies.json` на основном и новом адресах — все перенаправляются на Access. Карта визуально проверена в авторизованном Chrome; ошибок консоли не обнаружено.

## Первичная настройка доступа

В Chrome откройте Cloudflare → Workers & Pages → `steel-soil-atfaster` → Settings → Enable access policy. Эта кнопка сначала защищает только предварительные публикации.

В Zero Trust настройте бесплатный план и имя команды, если мастер предложит первичную настройку. В Settings → Authentication → Login methods добавьте One-time PIN. В Access → Applications откройте созданное приложение и задайте правило Allow с единственным адресом `atfasters@gmail.com`. Не выбирайте разрешение для всех почт.

Для защиты основного адреса выполните шаги из официальной инструкции: в приложении предварительных публикаций уберите `*` из Subdomain, сохраните и повторно включите Enable access policy в Pages. Должны существовать два защищённых адреса: `steel-soil-atfaster.pages.dev` и `*.steel-soil-atfaster.pages.dev`.

Официальные инструкции:
- https://developers.cloudflare.com/pages/platform/known-issues/#enable-access-on-your-pagesdev-domain
- https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/

Проверьте вход своей почтой. Неавторизованный запрос к странице, тайлам, данным блоков и границ должен отправляться на Cloudflare Access. При добавлении тиммейтов меняйте список разрешённых почт в Access; локальный `allowed_emails` в JSON — только запись выбранного списка, а не правило Cloudflare.

## Локальная публикация

Wrangler установлен вне проекта: `%LOCALAPPDATA%\steel-soil-cloudflare-tools`. Он авторизован через OAuth с правами `account:read`, `user:read`, `pages:write`; секреты в проект не записываются. Для повторного входа:

```powershell
node "$env:LOCALAPPDATA\steel-soil-cloudflare-tools\node_modules\wrangler\bin\wrangler.js" login --browser=false --scopes account:read user:read pages:write
```

Ссылку входа открывайте в Chrome. Не присылайте пароли, коды или токены в чат.

Настройки находятся в `seasons/steel-soil/cloudflare.json`, исключены из Git. Пока защита не подтверждена, `enabled` и `protection_verified` остаются `false`. После проверки Access выставляются оба значения `true`.

```powershell
python scripts/publish_cloudflare.py
```

Скрипт проверяет контрольные суммы текущей сборки, создаёт отдельную копию веб-сайта во временной папке, исключает ZIP, заменяет ссылку скачивания на прежний файл Google Drive и проверяет Access для основного адреса и wildcard-адреса. Анонимные проверки используют User-Agent `Mozilla/5.0 SteelSoilPublisher/1.0`: стандартный заголовок Python блокируется Cloudflare с ошибкой 1010. Только затем загружается сайт. Исходная сборка и ZIP не меняются.

Wrangler выбран для автоматизации: загрузка через кабинет ограничена 1 000 файлами, через Wrangler — 20 000. Карта содержит 3 419 файлов. Авторизация Wrangler сохраняется локально; если OAuth перестанет работать, публикация остановится и потребуется повторный вход через Chrome.

После включения Cloudflare мерж автоматически публикует карту после доставки ZIP. `--no-publish` по-прежнему отключает GitHub, а `--no-cloudflare` отдельно пропускает Cloudflare. При ошибке Cloudflare исходные ZIP остаются во входящей папке; готовый мерж и резервные копии сохраняются. Публикацию можно повторить отдельной командой без нового мержа.

Перед включением автопубликации скрипт должен успешно пройти проверку всех защищённых адресов. Если пробный wildcard-адрес не отправляет на Access, публикация остановится до исправления правила.

Архив Google Drive имеет собственные права доступа: защита Cloudflare на него не распространяется. Для строгого доступа только доверенных людей разрешите в Drive конкретные Google-аккаунты.
