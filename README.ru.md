# capcut-mcp

**Собирайте и редактируйте проекты CapCut 9.3 из Claude Desktop, OpenCode или любого MCP-клиента.**
Разработано [Спектрикой](https://spectrika.ru) — AI-ассистентом, который отвечает на вопросы о любом видео с таймкодами.

[English](README.md) | [Русский](README.ru.md)

[![CI](https://github.com/spectrika/capcut-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/spectrika/capcut-mcp/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

capcut-mcp — сервер [Model Context Protocol](https://modelcontextprotocol.io), упакованный в образ Docker.
Ваш AI-клиент запускает его командой `docker run`, общается с ним через stdio, а сервер пишет папки проектов
CapCut прямо в директорию проектов CapCut. Перезапустите CapCut — и проект уже там: клипы, субтитры,
эффекты, готовые к правке и экспорту.

Он также умеет **открывать проекты, которые вы отредактировали руками** в CapCut, применять вырезки, изменение
скорости, отключение звука, правку субтитров и области размытия, не трогая остальное, и записывать результат
обратно — по умолчанию в копию.

Внутри контейнера работает закреплённый коммит движка с открытым исходным кодом
[sun-guannan/VectCutAPI](https://github.com/sun-guannan/VectCutAPI) (Apache-2.0) плюс тонкая обёртка
(`entry.py`, `timeline_edit.py`), которая делает вывод совместимым с CapCut 9.3, усиливает обработку входных
данных движка и добавляет инструменты для работы с существующими проектами.

## Что он делает

22 инструмента, версия обёртки 0.3.0:

| Группа | Инструменты |
|---|---|
| Новый черновик | `create_draft`, `add_video`, `add_audio`, `add_image`, `add_text`, `add_subtitle` (файл SRT, URL или текст инлайн), `add_effect`, `add_sticker`, `add_video_keyframe`, `get_video_duration`, `save_draft` |
| Существующие проекты | `list_projects`, `open_project` (копия или на месте), `describe_draft` |
| Правки таймлайна | `delete_range`, `retime_range`, `set_segment`, `edit_text`, `set_track` |
| Размытие и маски | `blur_region`, `edit_mask`; геометрия маски в `add_video`/`add_image`, `effect`/`effect_strength` в `add_video` |
| Сервисные | `capcut_healthcheck` |

Параметры всех инструментов — в разделе [Справочник инструментов](#справочник-инструментов).

Чего он не делает: не рендерит и не экспортирует видео, не управляет запущенным CapCut. Экспорт остаётся
ручным: откройте проект в CapCut и нажмите Export. Сегменты с ключевыми кадрами, кривыми скорости или
переходами не режутся (вы получите понятную ошибку; делайте такие вырезки в CapCut).

## Требования

| Компонент | Примечания |
|---|---|
| Windows 11 с Docker Desktop | Docker Desktop должен быть **запущен до** старта MCP-клиента (клиент сам выполняет `docker run`) |
| CapCut для Windows **9.3.0** | бесплатной версии достаточно; CapCut Pro нужен только если вы используете Pro-ресурсы по ID |
| MCP-клиент | Claude Desktop, установленный из `.exe` (сборка из Microsoft Store не может достучаться до Docker), OpenCode Desktop/CLI или любой stdio MCP-клиент |
| Интернет | только для медиа, заданного через http(s) URL; локальные файлы читаются из подключённой папки media |
| WSL2 или другая Linux-оболочка, Python 3.10+ | только для запуска `build.sh` и тестов; в Windows включите Docker Desktop → Settings → Resources → WSL integration для вашего дистрибутива, либо собирайте из PowerShell (ниже) |

macOS пока не поддерживается: обёртка переписывает пути Windows и ищет структуру кеша CapCut для Windows.
Pull request'ы приветствуются.

## Установка

```bash
git clone https://github.com/spectrika/capcut-mcp.git
cd capcut-mcp
./build.sh            # builds capcut-mcp:<version> and tags it capcut-mcp:local
```

`build.sh` — тонкая обёртка вокруг `docker build`. Без Linux-оболочки выполните эквивалент из PowerShell в
папке репозитория:

```powershell
docker build --build-arg WRAPPER_VERSION=0.3.0 -t capcut-mcp:0.3.0 .
docker tag capcut-mcp:0.3.0 capcut-mcp:local
```

Образ — это `python:3.12-slim` + ffmpeg + встроенный движок; он работает от непривилегированного пользователя
и не открывает порты.

Необязательные проверки перед подключением клиента:

```bash
python3 test_timeline.py      # timeline operations, no Docker needed
python3 smoke_test.py         # drives the container end to end in scratch folders; expects SMOKE PASSED
```

## Подключение клиента

Оба клиента запускают одну и ту же команду. Замените `<you>` на имя вашего пользователя Windows и выберите
папку media (любую папку с видео, аудио, изображениями и файлами SRT, которые вы хотите использовать). В JSON
каждый обратный слэш нужно дублировать, а путь с пробелами остаётся единым элементом массива: Docker
запускается напрямую, без шелла.
Медиа можно указывать как `/media/clip.mp4`, либо по его Windows-пути `C:\...\clip.mp4`; оба варианта
принимаются.

Тома и переменные:

| Аргумент | Значение |
|---|---|
| `-v "<projects dir>:/drafts"` | Папка проектов CapCut, **единственное** место, куда пишет контейнер. В Windows это `C:\Users\<you>\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft` |
| `-v "<media dir>:/media:ro"` | локальные медиа для `add_video`, `add_audio`, `add_image`, `add_subtitle`; только для чтения |
| `-e CAPCUT_DRAFT_ROOT=<projects dir>` | путь Windows для `/drafts`; записывается в файл проекта вместо `/drafts` и используется для поиска кеша эффектов CapCut для размытия |
| `-e VECTCUT_MEDIA_WIN_ROOT=<media dir>` | путь Windows для `/media`, чтобы можно было передавать `C:\...\clip.mp4` как есть |
| `--memory 2g --pids-limit 256 --init` | лимиты ресурсов; при желании настройте по-своему |

Не добавляйте `--name`: фиксированное имя вместе с `--rm` приводит к ошибке «name already in use» при
перезапуске сервера клиентом.

### Claude Desktop

Отредактируйте `%APPDATA%\Claude\claude_desktop_config.json` (полный пример в
[docs/claude_desktop.example.json](docs/claude_desktop.example.json)):

```json
{
  "mcpServers": {
    "capcut": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--init", "--label", "app=capcut-mcp",
        "-v", "C:\\Users\\<you>\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft:/drafts",
        "-v", "C:\\Users\\<you>\\Videos:/media:ro",
        "-e", "CAPCUT_DRAFT_ROOT=C:\\Users\\<you>\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft",
        "-e", "VECTCUT_MEDIA_WIN_ROOT=C:\\Users\\<you>\\Videos",
        "--memory", "2g", "--pids-limit", "256",
        "capcut-mcp:local"]
    }
  }
}
```

Полностью закройте Claude Desktop (правый клик по иконке в трее → Quit; закрытие окна лишь сворачивает
приложение) и запустите снова. В списке инструментов должен появиться `capcut` с 22 инструментами.
Логи: `%APPDATA%\Claude\logs\mcp-server-capcut.log`.

### OpenCode Desktop и CLI

OpenCode читает MCP-серверы из своего конфигурационного файла: глобального
`~/.config/opencode/opencode.json` (в Windows `%USERPROFILE%\.config\opencode\opencode.json`) или проектного
`opencode.json` рядом с вашим проектом. Приложение Desktop и CLI используют этот файл совместно. Добавьте
сервер под ключом `mcp` (полный пример в [docs/opencode.example.json](docs/opencode.example.json)):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "capcut": {
      "type": "local",
      "command": ["docker", "run", "-i", "--rm", "--init", "--label", "app=capcut-mcp",
        "-v", "C:\\Users\\<you>\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft:/drafts",
        "-v", "C:\\Users\\<you>\\Videos:/media:ro",
        "-e", "CAPCUT_DRAFT_ROOT=C:\\Users\\<you>\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft",
        "-e", "VECTCUT_MEDIA_WIN_ROOT=C:\\Users\\<you>\\Videos",
        "--memory", "2g", "--pids-limit", "256",
        "capcut-mcp:local"],
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

`type: "local"` означает, что OpenCode запускает команду и общается по MCP через stdio, точно так же, как
Claude Desktop. `timeout` (в миллисекундах) — время, которое OpenCode ждёт список инструментов; стандартных
5 секунд может не хватить для холодного `docker run`, поэтому в примере указано 30 секунд. Перезапустите OpenCode Desktop
после редактирования файла. Чтобы проверить, выполните `opencode mcp list` в терминале или спросите в чате:
«вызови инструмент capcut_healthcheck и покажи каждую проверку». Имена инструментов идут с префиксом имени
сервера (`capcut_...`). Справка: [opencode.ai/docs/mcp-servers](https://opencode.ai/docs/mcp-servers/).

### Другие MCP-клиенты

Любой клиент, способный запускать stdio-сервер, работает с той же командной строкой `docker run ...`
(Claude Code CLI, Cursor, Cline и другие). Сервер реализует MCP `initialize`, `tools/list` и `tools/call`.

## Первая команда

После перезапуска клиента вставьте это в чат:

```
Используй MCP-сервер capcut. Проведи полную проверку и отчитайся по шагам:
1) вызови capcut_healthcheck и покажи каждую проверку (ok/fail с деталями);
2) выведи список инструментов capcut (их должно быть 22);
3) вызови get_video_duration для /media/<your clip>.mp4 и скажи мне длительность;
4) создай черновик 1080x1920, добавь заголовок "MCP test" на 0-3 с, добавь видео
   /media/<your clip>.mp4 целиком (end = его длительность), сохрани его с помощью save_draft, draft_name "mcp-test";
5) выведи Windows-путь папки проекта и что мне нужно сделать, чтобы увидеть его в CapCut.
Если шаг не удался, остановись и покажи текст ошибки.
```

Ожидаемый результат: каждая критическая проверка `ok`, путь вида `C:\Users\<you>\...\com.lveditor.draft\mcp-test`,
и после перезапуска CapCut проект `mcp-test` открывается с клипом и заголовком.

## Работа с существующими проектами

Порядок действий всегда один: `list_projects` → `open_project` → `describe_draft` → правки таймлайна →
`add_*` → `save_draft`.

- **`open_project(name, mode)`**. `mode="copy"` (по умолчанию) дублирует папку проекта так же, как это делает
  собственная функция CapCut «Copy» (включая медиа, тот же внутренний `id`, новый `draft_id` в
  `draft_meta_info.json`) под именем `<name>-mcp` или `copy_name`; существующие имена никогда не
  перезаписываются, вы получите `name (2)`. `mode="in_place"` редактирует сам проект. **Закройте проект в
  CapCut перед сохранением `save_draft` на месте**; самый безопасный вариант — закрыть CapCut целиком:
  автосохранение CapCut перезапишет правки, а изменения, которые CapCut внёс после `open_project`, отклоняются
  проверкой «changed on disk».
- **Всё, что записал CapCut, сохраняется как есть**: треки, сегменты, эффекты, маски, анимации, ключевые
  кадры, служебные ключи (`platform`, `relationships`, `config`...). Проект не пересобирается через объектную
  модель движка; JSON проносится через обработку без изменений, меняются только запрошенные поля.
- **`describe_draft`** возвращает индексы треков и сегментов, секунды таймлайна, скорости, громкость, текст
  субтитров, маски и эффекты. Все инструменты редактирования обращаются к сегментам по этим индексам; все
  времена — в секундах таймлайна.
- **Правки таймлайна** (`delete_range`, `retime_range`, `set_segment`, `edit_text`, `set_track`,
  `blur_region`, `edit_mask`) нужно применять **до первого `add_*`** в одном черновике (иначе — переоткройте
  проект). По умолчанию `delete_range` и `retime_range` действуют сразу на всех треках, поэтому оверлеи,
  стикеры и субтитры остаются синхронизированы с базовым треком. Несколько вырезок применяйте **с конца
  таймлайна назад** (сначала более поздний диапазон), чтобы более ранние времена не сдвигались у вас из-под
  рук. Клипы на границе разрезаются; новая половина получает собственные копии материалов
  сегмента (скорость, маска, эффекты), при этом исходный видеофайл остаётся общим. Скорость записывается и в
  сегмент, и в его материал `speeds`.
- **Новый контент** (`add_video`, `add_text`, `add_subtitle`, ...) всегда попадает на **новые треки** в конец
  списка: не указывайте `track_name` (получите `text_mcp_1`, `video_mcp_1`, ...) либо повторно используйте
  имя, созданное в той же сессии, чтобы складывать сегменты на одном треке. Имена существующих треков проекта
  отклоняются. `add_video_keyframe` работает только с треками, созданными в текущей сессии.
- **Размытие.** `blur_region(track, segment, center_x, center_y, width, height, strength, start_sec, end_sec)`
  повторяет ручной рецепт: копия клипа на оверлейном треке `blur_mcp` (вставляется сразу после последнего
  видеотрека) + прямоугольная маска + эффект «Blur». Единицы измерения — как в CapCut: центр от −1 до 1
  относительно центра клипа (`+y` — вверх), ширина и высота — как доли клипа; `center_y=-0.6, height=0.4`
  размывает нижнюю часть кадра. `edit_mask` перемещает существующую маску (например, чтобы оставить чёткой
  полосу прогресса плеера). `add_effect(effect_type="Blur")` размывает весь кадр на диапазоне (трек
  эффектов), а `add_video(..., mask_type, mask_center_x/y, mask_size, mask_rect_width, effect="Blur")`
  добавляет новый клип с маской и эффектом.
- **`save_draft`** для открытого проекта пишет на месте: новые медиа копируются в `<project>/assets/`,
  обновляются `draft_content.json` и его зеркала (`.bak`, `template-2.tmp`, `Timelines/<id>/*`), а также
  `draft_meta_info.json` (`tm_draft_modified`, `tm_duration`) и `draft_settings`. Перед записью сохраняются
  резервные копии `draft_content.json.vectcut-bak-<timestamp>` и
  `draft_meta_info.json.vectcut-bak-<timestamp>` (последние 3). `draft_name` игнорируется для открытых
  проектов.
- **Отмена**: переименуйте `draft_content.json.vectcut-bak-<timestamp>` обратно в `draft_content.json`, когда
  CapCut закрыт; CapCut перезапишет зеркала при следующем сохранении.
- После `save_draft` перезапустите CapCut (или откройте и закройте любой проект): копия появится в списке,
  правки на месте будут видны при открытии проекта.
- Материалы, оставшиеся без сегментов после удаления, остаются в `materials` (CapCut это допускает; файл
  получается немного больше).

### Пример промпта: редактирование копии проекта

```
Используй MCP-сервер capcut. Мы работаем с существующим проектом, не удаляя ничего из исходника.
1) list_projects; open_project name="my-project", mode="copy", copy_name="my-project-v2";
2) describe_draft: покажи треки с индексами и сегменты основного видеотрека с временами;
   все времена ниже — секунды таймлайна;
3) вырезки, строго справа налево: delete_range 80.6-81.4, 45-47, 14.5-25;
   ускорения между вырезками: retime_range 63-73 x1.5, 25-33 x1.6, 6-11.5 x1.5
   (сначала более поздние диапазоны, чтобы более ранние времена не сдвигались);
4) set_track на основном видеотреке с mute=true; затем set_segment volume=0 для оставшихся видеоклипов;
5) edit_text: перемести первую субтитровую надпись на transform_y 0.86; измени текст последней надписи на "Меню: все ваши видео";
6) blur_region на клипе, где показан браузер: center_y -0.6, height 0.4, strength 50, на всю его длину;
7) add_subtitle с инлайн-SRT (времена на новом таймлайне), полужирный шрифт, font_size 10, font_color #000000,
   background_color #FFD200, background_alpha 1, transform_y 0.67, track_name "captions";
8) save_draft; выведи путь проекта, длительность, новые треки и имя файла резервной копии.
Если шаг не удался, остановись и покажи полную ошибку.
```

## Справочник инструментов

Все 22 инструмента, сгруппированные по этапам работы. Параметры, отмеченные `*`, обязательны; время — в
секундах; позиции и геометрия маски — в единицах CapCut (−1..1 от центра, `+y` вверх); размеры — доли клипа.
Каждый инструмент, принимающий `draft_id`, ожидает id, возвращённый `create_draft` или `open_project`.
Медиа-аргументы (`video_url`, `audio_url`, `image_url`, `srt_path`) принимают публичный http(s) URL, путь
внутри `/media/` или тот же файл по его Windows-пути, если задан `VECTCUT_MEDIA_WIN_ROOT`.

### Сервисные

| Инструмент | Что делает | Параметры |
|---|---|---|
| `capcut_healthcheck` | Самопроверка контейнера: профиль черновика, точки монтирования `/drafts` и `/media`, `CAPCUT_DRAFT_ROOT`, ffprobe, исходящий доступ в интернет, стейдж-папка. Вызывайте первым в новой сессии. | нет |
| `get_video_duration` | Длительность медиафайла в секундах (ffprobe). | `video_url*` |

### Новые черновики

| Инструмент | Что делает | Параметры |
|---|---|---|
| `create_draft` | Новый пустой черновик в памяти; возвращает `draft_id`. | `width` (1080), `height` (1920) |
| `add_video` | Добавляет видеоклип. `start`/`end` вырезают часть источника, `target_start` размещает клип на таймлайне. Опционально — маска и эффект для клипа. | `video_url*`, `draft_id*`, `start` (0), `end`, `target_start` (0), `width`, `height`, `transform_x`, `transform_y`, `scale_x`, `scale_y`, `speed` (1), `volume` (1), `track_name` (`main`), `transition`, `transition_duration` (0.5), `background_blur` (1–4), `mask_type` (`rectangle`, `circle`, `linear`, `mirror`, `star`), `mask_center_x`, `mask_center_y`, `mask_size` (height fraction), `mask_rect_width`, `mask_rotation`, `mask_feather` (0–100), `mask_round_corner` (0–100), `mask_invert`, `effect` (e.g. `Blur`), `effect_strength` (0–100) |
| `add_audio` | Добавляет аудиоклип. | `audio_url*`, `draft_id*`, `start` (0), `end`, `target_start` (0), `volume` (1), `speed` (1), `track_name` (`audio_main`) |
| `add_image` | Добавляет статичное изображение на `start`–`end` секунд таймлайна, с опциональными анимациями, переходом и маской. | `image_url*`, `draft_id*`, `start` (0), `end` (3), `width`, `height`, `transform_x`, `transform_y`, `scale_x`, `scale_y`, `track_name` (`main`), `intro_animation`, `outro_animation`, `transition`, `mask_*` (same as `add_video`) |
| `add_text` | Добавляет текстовый оверлей с опциональной тенью, фоном и стилями по диапазонам. | `text*`, `start*`, `end*`, `draft_id*`, `font_color` (`#ffffff`), `font_size` (24), `shadow_enabled`, `shadow_color`, `shadow_alpha` (0.8), `shadow_angle` (315), `shadow_distance` (5), `shadow_smoothing`, `background_color`, `background_alpha` (1), `background_style` (0), `background_round_radius`, `text_styles` (list), `track_name` |
| `add_subtitle` | Добавляет субтитры из файла SRT, URL или инлайн-текста SRT, по одному оформленному сегменту на реплику. | `srt_path*`, `draft_id*`, `track_name` (`subtitle`), `time_offset` (0), `font`, `font_size` (8), `font_color` (`#FFFFFF`), `bold`, `italic`, `underline`, `border_width`, `border_color`, `background_color`, `background_alpha` (0), `transform_x` (0), `transform_y` (−0.8) |
| `add_effect` | Эффект на весь кадр сцены на отдельном треке эффектов (например, `Blur`). | `effect_type*`, `draft_id*`, `start` (0), `end` (3), `track_name` (`effect_01`), `params` (list of numbers) |
| `add_sticker` | Добавляет стикер по resource id CapCut. | `resource_id*`, `draft_id*`, `start*`, `end*`, `transform_x`, `transform_y`, `scale_x`, `scale_y`, `alpha` (1), `rotation` (0), `track_name` (`sticker_main`) |
| `add_video_keyframe` | Анимация по ключевым кадрам на клипе трека, созданного в этой сессии (позиция, масштаб, поворот, альфа, цвет, громкость). Одиночный или батч-режим. | `draft_id*`, `track_name` (`main`), `property_type`, `time` (0), `value`; batch: `property_types`, `times`, `values` |
| `save_draft` | Записывает черновик на диск. Для нового черновика: создаёт папку проекта, копирует или скачивает медиа, заполняет метаданные CapCut. Для открытого проекта: записывает правки обратно (резервная копия сохраняется). Вызывайте последним; чтобы увидеть результат, перезапустите CapCut. | `draft_id*`, `draft_name` (new drafts only) |

`width`/`height` в инструментах `add_*` — это размер холста, используемый для размещения, по умолчанию 1080×1920.

### Существующие проекты

| Инструмент | Что делает | Параметры |
|---|---|---|
| `list_projects` | Проекты в папке CapCut с именем, временем изменения, длительностью и размером медиа. | нет |
| `open_project` | Открывает проект для редактирования. `copy` дублирует папку так же, как собственная функция CapCut «Copy», и редактирует копию; `in_place` редактирует оригинал. Возвращает `draft_id` и сводку по таймлайну. | `name*` (folder name from `list_projects`), `mode` (`copy`, `in_place`), `copy_name` (default `<name>-mcp`) |
| `describe_draft` | Сводка по таймлайну: треки с индексами, сегменты с start/end, типом, скоростью, громкостью, текстом, масками, эффектами. Эти индексы используются инструментами редактирования. | `draft_id*` |

### Правки таймлайна

Только для проекта, открытого через `open_project`, и только до первого вызова `add_*` на нём.

| Инструмент | Что делает | Параметры |
|---|---|---|
| `delete_range` | Волновое удаление (ripple-delete) `[start_sec, end_sec)` на всех треках (или на указанных): клипы на границах разрезаются, последующий контент сдвигается влево. Применяйте более поздние диапазоны первыми. | `draft_id*`, `start_sec*`, `end_sec*`, `tracks` (list of indices) |
| `retime_range` | Меняет скорость всего внутри диапазона (`speed=2` — вдвое быстрее); границы разрезаются, последующий контент сдвигается. | `draft_id*`, `start_sec*`, `end_sec*`, `speed*` (0.05–100), `tracks` |
| `set_segment` | Редактирует один клип: громкость (0 = без звука), скорость, обрезка по новым start/end таймлайна, или удаление. `ripple` сдвигает только последующие клипы того же трека. | `draft_id*`, `track*`, `segment*`, `volume` (0–10), `speed`, `start_sec`, `end_sec`, `delete`, `ripple` |
| `edit_text` | Редактирует существующий текстовый оверлей: текст (стиль сохраняется), тайминг, позицию, равномерный масштаб. | `draft_id*`, `track*`, `segment*`, `text`, `start_sec`, `end_sec`, `transform_x`, `transform_y`, `scale` |
| `set_track` | Отключает звук у всех клипов трека (или восстанавливает) либо удаляет трек. | `draft_id*`, `track*`, `mute`, `delete` |

### Размытие и маски

| Инструмент | Что делает | Параметры |
|---|---|---|
| `blur_region` | Размывает прямоугольную область существующего клипа так же, как это делается в CapCut вручную: копия клипа на оверлейном треке `blur_mcp` с прямоугольной маской и эффектом «Blur». | `draft_id*`, `track*`, `segment*`, `center_x*`, `center_y*`, `width*`, `height*`, `strength` (50), `feather` (0–1), `round_corner` (0–1), `invert`, `start_sec`, `end_sec` |
| `edit_mask` | Изменяет маску существующего клипа (включая оверлей, созданный `blur_region`). | `draft_id*`, `track*`, `segment*`, `center_x`, `center_y`, `width`, `height`, `feather`, `round_corner`, `invert` |

Результаты — в формате JSON: `success`, `result` (например, `draft_id`, `track_name`, `duration_sec`, изменённые треки) или `error`
с сообщением, объясняющим, что нужно исправить.

## Ограничения и безопасность

- Черновики и открытые проекты живут в памяти контейнера до `save_draft`; контейнер живёт столько, сколько
  клиент его держит запущенным. После перезапуска клиента несохранённая работа теряется, а старые `draft_id`
  становятся недействительными.
- `save_draft` может занимать десятки секунд (скачивание медиа + ffprobe). Кладите крупные файлы в папку
  media и указывайте их как `/media/...` вместо URL; файлы копируются в папку проекта, потому что CapCut
  нужны локальные копии. Копирование большого проекта (`open_project` в режиме copy) занимает секунды на
  каждые сто мегабайт.
- Сам движок небезопасно открывать наружу: HTTP-сервер без авторизации, `rmtree` по произвольному `draft_id`,
  SSRF и чтение локальных файлов. Обёртка закрывает эти дыры:
  - нет HTTP-сервера, нет портов, только stdio;
  - `draft_id` должен соответствовать шаблону `dfd_cat_<ts>_<hex8>` и существовать в памяти, поэтому `rmtree`
    движка может задеть только `/drafts/dfd_cat_*` (новые черновики) и стейдж-папку контейнера;
  - имя проекта в `open_project` — это ровно одна папка внутри `/drafts` (без `/`, `\` или ведущей точки),
    содержащая `draft_content.json`;
  - для открытых проектов контейнер пишет только `<project>/{draft_content.json, mirrors,
    draft_meta_info.json, draft_settings, assets/**, *.vectcut-bak-*}` и новую папку копии; ничто внутри
    `/drafts` не удаляется;
  - URL: http(s) только на публичные IP (loopback, LAN и link-local отклоняются после резолвинга DNS);
    локальные пути — только внутри `/media` (только для чтения); другие схемы (`file:`, `concat:`, ...)
    отклоняются;
  - аргументы фильтруются по `inputSchema` каждого инструмента (протащить `draft_folder` не получится);
  - контейнер без root, `--memory 2g --pids-limit 256`, единственный доступный на запись том;
  - `is_upload_draft: false`, никаких облачных учётных данных, никакой телеметрии в коде.
- Pro-ресурсы (эффекты, стикеры, шрифты по ID из платного каталога) заставляют CapCut запросить Pro при
  экспорте; используйте бесплатные. Эффект «Blur» и прямоугольная маска бесплатны.
- Папки Windows, смонтированные в контейнер, не поддерживают chmod/utime; обёртка не копирует метаданные
  файлов (`shutil.copystat`), содержимое файлов копируется полностью.
- Формат проекта CapCut не документирован и меняется между версиями. Проверено на 9.3.0.3970. После
  обновления CapCut запустите первую команду снова и откройте копию реального проекта. Блок `platform`,
  который пишется в новые черновики, берётся из `platform_capcut_9.json` (синтетические ID устройства, не
  взятые с какой-либо реальной машины). Чтобы вместо этого использовать блок из одного из ваших собственных
  черновиков, смонтируйте его и укажите обёртке путь к нему:
  `-v C:\path\platform.json:/app/my_platform.json:ro -e VECTCUT_PLATFORM_FILE=/app/my_platform.json`.
- Единицы измерения: время — в секундах; позиции и геометрия маски — в координатах CapCut −1..1; размеры —
  доли клипа. Сегменты с ключевыми кадрами, кривыми скорости или переходами инструменты редактирования
  отклоняют.

## Проверено с

| Компонент | Версия |
|---|---|
| CapCut для Windows | 9.3.0.3970 |
| Windows | 11 |
| Docker Desktop | 29.x |
| Claude Desktop | текущая сборка `.exe` (новые черновики и отредактированные копии открываются в CapCut) |
| OpenCode Desktop | настроено по официальной документации; нами пока не проверено end-to-end |
| Движок | VectCutAPI `d14e7074`, без изменений |

## Версионирование и откат

- Версия обёртки — это `WRAPPER_VERSION` в `entry.py` (semver: patch для фиксов, minor для новых
  инструментов или поведения, major для несовместимых схем инструментов или конфигурации). Она возвращается
  в `initialize` и `capcut_healthcheck`. История: `CHANGELOG.md`, git-теги `v<version>`.
- `./build.sh` собирает `capcut-mcp:<version>` (OCI-метки: version, git sha, engine sha) и переуказывает
  `capcut-mcp:local` на него, так что конфиги клиентов никогда не меняются.
- Откат: `docker tag capcut-mcp:<old version> capcut-mcp:local` и перезапустите клиент.
  `docker image ls capcut-mcp` покажет, что у вас есть.

## Обновление upstream

```bash
cd /tmp && git clone https://github.com/sun-guannan/VectCutAPI.git && cd VectCutAPI
git archive --format=tar.gz --prefix=VectCutAPI/ -o <repo>/vendor/VectCutAPI-<sha8>.tar.gz <sha>
# Dockerfile: ARG VECTCUT_SHA=<sha> and the archive name in ADD; if upstream requirements changed:
cd <repo> && uv pip compile --python-version 3.12 --generate-hashes requirements.in -o requirements.lock
./build.sh && python3 test_timeline.py && python3 smoke_test.py
```

Текущий архив — это `git archive` коммита `d14e70749c9331424ab816a402bb45417c50cf68`
(sha256 `ce1bbbf193060b3c65b1826968420492aa8157f9a846f76ebb31dd7c33e4c707`). После обновления заново проверьте
модули upstream, на которые опирается обёртка: `draft_profiles.py` (сигнатура `DraftProfile`),
`save_draft_impl.py` (`draft_folder`, `get_task_status`), `mcp_server.py` (`TOOLS`, `execute_tool`),
`pyJianYingDraft/track.py` (`Track.__init__`, `Track_type`), `pyJianYingDraft/script_file.py`
(`Script_file.__init__`, `materials.export_json`).

## Troubleshooting

| Симптом | Что делать |
|---|---|
| Клиент не показывает сервер `capcut` / ошибка при старте | Запущен ли Docker Desktop? Показывает ли `docker image ls capcut-mcp` тег `local`? Лог Claude Desktop: `%APPDATA%\Claude\logs\mcp-server-capcut.log`; OpenCode: `opencode mcp list` |
| Меньше 22 инструментов | Клиент не был перезапущен после `./build.sh` (контейнер запускается вместе с клиентом) |
| `capcut_healthcheck`: `drafts_mount` fail | Проверьте строку `-v` с путём `com.lveditor.draft` (один элемент массива, удвоенные обратные слэши) и что Docker Desktop имеет доступ к `C:\Users` (Settings → Resources → File sharing) |
| `capcut_draft_root` fail | Добавьте `-e CAPCUT_DRAFT_ROOT=...` |
| `internet_egress` fail | Медиа по URL не будет работать; локальные файлы из `/media` будут |
| Проект не появляется в CapCut | Полностью перезапустите CapCut; проверьте, что папка находится внутри `com.lveditor.draft` и содержит `draft_content.json` и `draft_meta_info.json` |
| CapCut говорит, что медиа отсутствует | Пути в `draft_content.json` должны начинаться с `C:/Users/<you>/.../com.lveditor.draft/<project>/assets/` или с плейсхолдера CapCut; `materials_failed` в результате `save_draft` перечисляет, что не удалось скачать |
| `open_project`: `project ... not found` | Используйте имя папки точно как в `list_projects` (папка, а не `draft_name`); скрытые папки, такие как `.recycle_bin`, недоступны |
| `save_draft`: `changed on disk since open_project` | CapCut изменил файл (проект был открыт) или другой `draft_id` того же проекта сохранился первым. Закройте проект в CapCut, снова вызовите `open_project`, повторите правки |
| `track_name ... already exists in the project` | Новые сегменты идут только на новые треки: убирайте `track_name` или выбирайте новое имя |
| `timeline edits must be applied before add_*` | Порядок: сначала `delete_range` / `retime_range` / ..., потом `add_*`; иначе переоткройте проект |
| `segment ... has keyframes / curve speed / transition` | Такие сегменты не режутся; делайте это в CapCut или выбирайте границы на краях сегмента |
| CapCut перезаписал правки MCP | Проект был открыт во время `save_draft`. Отмена: `draft_content.json.vectcut-bak-<timestamp>` → `draft_content.json` |
| Размытие не отображается в CapCut | Проверьте в `describe_draft`, что у сегмента есть `effects: ["Blur"]` и маска; если CapCut не может найти ресурс эффекта, откройте один раз любой эффект «Blur» в CapCut, чтобы он попал в кеш, и повторите |
| `name already in use` | Удалите `--name` из аргументов |
| Медленный `save_draft` / таймаут | Кладите крупные файлы в `/media` вместо URL; проверьте `took_sec` в результате |
| `save_draft`: `Operation not permitted` при копировании | Точки монтирования Windows отказывают в chmod/utime; обёртка это игнорирует, так что если вы видите эту ошибку — запущен старый образ: пересоберите |
| В CapCut появился пустой проект `dfd_cat_*` | Остаток неудавшегося `save_draft` нового черновика; удалите папку (отмечена как `leftover` в `list_projects`) |
| Обновление CapCut сломало открытие | Скопируйте блок `platform` из свежего реального черновика в `platform_capcut_9.json`, обновите `CONTENT_VERSION`/`NEW_VERSION` в `entry.py`, пересоберите; повторно запустите `test_timeline.py` с `VECTCUT_REAL_FIXTURE` на новом файле |

## Структура проекта

```
Dockerfile               image: python:3.12-slim + ffmpeg + vendored engine + entry.py + timeline_edit.py
build.sh                 build with the version tag and re-point capcut-mcp:local
entry.py                 stdio wrapper (profile, validation, new drafts, open/save projects, healthcheck)
timeline_edit.py         timeline operations on existing projects (pure functions over JSON)
config.json              engine config inside the container
platform_capcut_9.json   platform block for CapCut 9.3.0 drafts (anonymised)
template_capcut_9/       project folder template (CapCut 9.x / Jianying 10.x layout)
vendor/                  git archive of the engine at the pinned commit
requirements.in/.lock    Python dependencies (lock with hashes)
test_timeline.py         host-side tests for timeline_edit (+ a real draft_content.json via VECTCUT_REAL_FIXTURE)
smoke_test.py            end-to-end test of the image without CapCut; --drafts-dir for a real folder, --open-existing NAME for a live copy
docs/                    example client configs
.github/workflows/       CI: timeline tests, docker build + smoke test
CHANGELOG.md             version history
CONTRIBUTING.md          настройка окружения разработки, тесты, правила внесения изменений
SECURITY.md              от чего защищает обёртка и как сообщить о проблеме
LICENSE, NOTICE          Apache-2.0 и указание авторства сторонних компонентов
```

## О Спектрике

[Спектрика](https://spectrika.ru) — AI-ассистент для видео: отправьте ей ссылку, и она транскрибирует видео,
отвечает на вопросы о нём с цитатами по таймкодам и делает саммари в Telegram, MAX или на сайте.
capcut-mcp выросла из нашего собственного процесса монтажа, где обучающие видео собираются и перемонтируются
из чата с AI. Issues и pull request'ы приветствуются; см. [CONTRIBUTING.md](CONTRIBUTING.md) и
[SECURITY.md](SECURITY.md).

## Лицензия

Apache License 2.0, см. [LICENSE](LICENSE) и [NOTICE](NOTICE). Встроенный движок VectCutAPI и библиотека
pyJianYingDraft также распространяются под Apache-2.0. CapCut и Jianying — торговые марки их
правообладателей; этот проект не аффилирован с ByteDance или CapCut и не одобрен ими.
