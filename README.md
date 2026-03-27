# Armies of Terra Map

Этот проект собирает общую карту сервера из тайлов JourneyMap.

## Что делает скрипт

Скрипт [scripts/merge_journeymap_tiles.py](c:\Users\atfas\Drive\Games\Minecraft\Armies of Terra\WorldMap\armies_of_terra_map\scripts\merge_journeymap_tiles.py) объединяет:

- твои локальные тайлы JourneyMap из папки игры
- архивы или папки с тайлами от других игроков

Неисследованные области в JourneyMap прозрачные, поэтому скрипт:

- закрывает прозрачные участки уже известными данными
- обновляет исследованные пиксели более свежими данными
- после merge автоматически пересобирает `tiles.json` и preview-картинки

Для определения свежести используется время изменения файла (`mtime`).

## Откуда берутся данные

Основной источник по умолчанию:

```text
C:\Users\atfas\curseforge\minecraft\Instances\Armies of Terra\journeymap\data\mp\Armies~of~Terra\overworld\day
```

Дополнительный inbox внутри проекта:

```text
imports\journeymap_inbox
```

В inbox можно класть:

- `.zip` архивы от других игроков
- папки с тайлами `x,y.png`

## Как пользоваться

### 1. Подготовь входящие файлы

Если другой игрок прислал архив, просто положи его в:

```text
imports\journeymap_inbox
```

Если прислал папку с тайлами, положи эту папку туда же.

### 2. Запусти merge

Из корня проекта:

```powershell
python scripts/merge_journeymap_tiles.py
```

Скрипт:

- прочитает твои локальные тайлы
- прочитает все архивы и папки из inbox
- объединит их в папку `tiles`
- обновит `tiles.json`
- пересоберет preview через `build_map_assets.py`
- удалит успешно обработанные архивы и папки из inbox

## Полезные режимы

Проверка без записи:

```powershell
python scripts/merge_journeymap_tiles.py --dry-run
```

Не удалять обработанные архивы и папки из inbox:

```powershell
python scripts/merge_journeymap_tiles.py --keep-inbox
```

Не пересобирать preview:

```powershell
python scripts/merge_journeymap_tiles.py --skip-previews
```

Можно использовать сразу несколько флагов:

```powershell
python scripts/merge_journeymap_tiles.py --dry-run --keep-inbox
```

## Что сохраняется между запусками

Скрипт ведет служебную базу:

```text
data\tile_merge_state.sqlite3
```

Она хранит приоритет пикселей по времени и нужна, чтобы следующий импорт не затер более свежие данные старым тайлом.

Этот файл удалять не нужно.

## Результат работы

После успешного запуска обновляются:

- `tiles\*.png`
- `tiles.json`
- `previews\overview_full.png`
- `previews\overview_4x.jpg`
- `previews\overview_8x.jpg`
- `previews\overview_16x.jpg`
- `previews\overview_meta.json`

## Типовой сценарий

1. Исследуешь карту у себя в игре.
2. Получаешь архивы от других игроков.
3. Кладешь архивы в `imports\journeymap_inbox`.
4. Запускаешь `python scripts/merge_journeymap_tiles.py`.
5. Проверяешь обновленную карту и preview.

## Замечания

- Если входящие архивы уже были обработаны и не нужен повторный импорт, лучше не хранить их в inbox.
- Если хочешь сначала посмотреть, сколько тайлов изменится, используй `--dry-run`.
- Для корректного приоритета свежести у файлов игроков должно быть сохранено их исходное время изменения внутри архива.
