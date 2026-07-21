# CloudPlayer

[Русский](#русский) · [English](#english)

CloudPlayer is a desktop music player for Windows built with Python, PySide6 and Qt Multimedia. It combines local playlists, SoundCloud and YouTube Music search, preview streaming, lyrics, recommendations, cloud synchronization, keyboard-first navigation and synchronized listening rooms in one application.

---

# Русский

## О программе

CloudPlayer предназначен для локальной музыкальной библиотеки, но не ограничивается файлами на компьютере. Внутри приложения можно:

- создавать и оформлять плейлисты;
- искать музыку в SoundCloud и YouTube Music;
- прослушивать Demo до скачивания;
- скачивать один или несколько выбранных треков;
- добавлять музыку по ссылке или из локального файла;
- получать тексты песен;
- управлять приложением почти полностью с клавиатуры;
- синхронно слушать музыку вместе с другими пользователями;
- синхронизировать доступные ссылки на треки через аккаунт;
- получать обновления через GitHub Releases.

CloudPlayer хранит библиотеку локально в `Documents/CloudPlayer` и не требует аккаунта для обычного воспроизведения, плейлистов и добавления локальных файлов.

## Основные возможности

### Библиотека и плейлисты

- Создание, открытие, переименование и удаление плейлистов.
- Собственная обложка плейлиста.
- Возврат к автоматически найденной обложке.
- Множественное выделение плейлистов и треков.
- Перестановка треков перетаскиванием.
- Отмена последней перестановки через `Ctrl+Z`.
- Автоматическое сохранение порядка и метаданных.
- Продолжение воспроизведения после выхода из плейлиста.
- Корректная остановка, если удалён играющий трек или его плейлист.
- Контекстные действия для одного или нескольких выделенных треков.

### Add Song: SoundCloud и YouTube Music

Окно **Add Song** поддерживает два источника:

- **SoundCloud**;
- **YouTube Music**.

По умолчанию включён только SoundCloud. Источники переключаются через кнопку с `filter.svg`.

В выпадающем меню:

- выбранный источник отмечается `check.svg`;
- можно включить один источник или оба;
- отключить все источники одновременно нельзя;
- последний выбор сохраняется в `Documents/CloudPlayer/settings.json`.

YouTube Music ищется не через обычную выдачу YouTube, а в категории песен. CloudPlayer дополнительно:

- убирает точные дубликаты;
- повышает результаты, лучше совпадающие с запросом;
- понижает `slowed`, `reverb`, `nightcore`, `karaoke`, `cover`, `remix`, `sped up` и похожие версии, если пользователь не указал это в запросе;
- помечает источник результата как `SC` или `YTM`;
- объединяет результаты двух источников в один список.

### Demo перед скачиванием

У каждого результата справа находится белая подпись **Demo** и кнопка `play.svg` / `pause.svg`.

Demo:

- не добавляет трек в библиотеку;
- запускается до скачивания;
- использует небольшую стартовую буферизацию;
- продолжает загружать аудио по чанкам во время воспроизведения;
- поддерживает потоковые и HLS-источники;
- останавливает предыдущее Demo при выборе другого;
- прекращает загрузку при закрытии Add Song или запуске нового поиска;
- автоматически использует текущую громкость основного плеера;
- повторяет состояние mute основного плеера;
- сразу реагирует на изменение громкости.

Клавиатурой в списке результатов:

- `↑` / `↓` — выбрать трек;
- `Space` — включить или поставить Demo на паузу;
- `Enter` — скачать выбранный результат;
- `Ctrl+↑` / `Ctrl+↓` — расширить множественное выделение;
- после множественного выделения `Enter` скачивает выбранные треки.

### Другие способы добавления музыки

- Добавление полной ссылки SoundCloud.
- Добавление полной ссылки YouTube или YouTube Music.
- Добавление локального аудиофайла.
- Фоновая загрузка с прогрессом.
- Преобразование аудио через `yt-dlp` и FFmpeg.
- Добавление рекомендаций непосредственно в плейлист.

Поддерживаемые локальные форматы:

```text
.mp3  .wav  .m4a  .flac  .ogg  .opus  .webm
```

## Управление треками

Контекстное меню трека позволяет:

- запустить воспроизведение;
- переименовать трек;
- создать копию вместе с метаданными и обложкой;
- удалить один или несколько треков;
- показать и скопировать путь к файлу.

Контекстное меню обложки позволяет:

- открыть изображение в полном размере;
- сохранить его в файл;
- скопировать изображение;
- плавно масштабировать колёсиком;
- перемещать увеличенное изображение перетаскиванием.

## Плеер

- Play/Pause.
- Предыдущий и следующий трек.
- Перемотка нажатием или перетаскиванием по timeline.
- Текущее время, длительность и прогресс сетевого буфера.
- Громкость ползунком или точным числом.
- Плавное включение и выключение звука.
- Сохранение громкости между запусками.
- Repeat.
- Shuffle с устойчивой случайной очередью.
- Автоматический переход к следующему треку.
- Название, исполнитель, обложка и текст песни.
- Получение и локальное кеширование текстов через Genius.

## Очередь

- Кнопка очереди расположена первой в блоке управления.
- Показываются следующие пять треков.
- Поддерживаются обычный порядок, Shuffle Mode и очередь Listen Together.
- Треки можно переставлять перетаскиванием.
- Изменение очереди не переписывает сохранённый порядок плейлиста.

## Listen Together

- Создание комнаты.
- Подключение по адресу и порту.
- Поддержка доменов, IPv4, IPv6 и полного URL.
- Отдельный внешний и локальный порт.
- Синхронизация:
  - play;
  - pause;
  - seek;
  - repeat;
  - выбора трека;
  - перехода к следующему треку.
- Передача отсутствующих треков между участниками.
- Адаптивный сетевой буфер.
- Возобновление прерванной передачи после переподключения.
- Список участников, страна и задержка.
- Опциональная TURN-конфигурация.

Для подключения извне локальной сети может понадобиться проброс выбранного порта на роутере и разрешение в firewall.

## Аккаунт и облачная синхронизация

- Регистрация и вход.
- Cloudflare Turnstile.
- Сохранение локальной сессии.
- Синхронизация исходных ссылок на доступные для загрузки треки через Supabase.
- Загрузка синхронизированных треков в локальный плейлист.
- Удаление отдельных записей из облачной синхронизации.
- Выход из аккаунта.
- Полное удаление аккаунта.

Локальные файлы без исходной ссылки не загружаются в облако и пропускаются во время синхронизации.

## Полное управление с клавиатуры

CloudPlayer поддерживает клавиатурную навигацию почти по всему интерфейсу.

Индикатор фокуса:

- скрыт при обычной работе мышью;
- появляется после начала клавиатурной навигации;
- исчезает после клика, двойного клика, прокрутки или касания.

Обычная работа `Tab` не заменяется нестандартной системой. Приложение использует нативный порядок фокуса Qt.

### Навигация

| Действие | Клавиша |
| --- | --- |
| Следующий элемент или блок | `Tab` |
| Предыдущий элемент или блок | `Shift+Tab` |
| Перемещение внутри списка или меню | `↑` `↓` `←` `→` |
| Выполнить выбранное действие | `Enter` |
| Закрыть меню, диалог или отменить действие | `Esc` |
| Вернуться на предыдущий экран | `Backspace` |
| Первый элемент списка | `Home` |
| Последний элемент списка | `End` |
| Быстрая прокрутка вверх | `PageUp` |
| Быстрая прокрутка вниз | `PageDown` |

### Подтверждения

Во всех диалогах подтверждения:

| Действие | Клавиша |
| --- | --- |
| `Yes` / `OK` / подтверждение | `Enter` |
| `No` / `Cancel` / отказ | `Esc` |

Это работает независимо от того, какая кнопка получила визуальный фокус.

### Контекстное меню

| Действие | Клавиша |
| --- | --- |
| Открыть контекстное меню выбранного элемента | одиночный `Right Ctrl` |
| Резервное сочетание контекстного меню | `Shift+F10` |
| Перемещение по пунктам | `↑` / `↓` |
| Выполнить пункт | `Enter` |
| Закрыть меню | `Esc` |

Контекстное меню поддерживается для трека, плейлиста, текста, обложки и других элементов, для которых предусмотрены действия.

Одиночное нажатие `Fn` обычно не передаётся Windows-приложению, поэтому основная клавиша контекстного меню — правый `Ctrl`.

### Плейлисты и библиотека

| Действие | Клавиша |
| --- | --- |
| Открыть Add Song в текущем плейлисте | `Alt` |
| Автоматически перейти в поле поиска Add Song | после `Alt` |
| Новый плейлист | `Ctrl+N` |
| Перейти в поиск | `Ctrl+F` |
| Выбрать всё в поддерживаемом списке | `Ctrl+A` |
| Удалить выбранный трек или плейлист | `Delete` |
| Отменить последнюю перестановку треков | `Ctrl+Z` |
| Запустить выбранный трек | `Ctrl+Enter` |
| Поставить выбранный трек следующим | `Shift+Enter` |
| Добавить следующий трек к выделению | `Ctrl+↓` |
| Добавить предыдущий трек к выделению | `Ctrl+↑` |

`Ctrl+↑` и `Ctrl+↓` работают в основном списке треков и в результатах Add Song. Уже выделенные элементы не сбрасываются.

### Управление воспроизведением

| Действие | Клавиша |
| --- | --- |
| Переключить Repeat | `F5` |
| Переключить Shuffle | `F6` |
| Предыдущий трек | `F7` |
| Play/Pause | `F8` |
| Следующий трек | `F9` |
| Mute/Unmute | `F10` |
| Уменьшить громкость | `F11` |
| Увеличить громкость | `F12` |
| Play/Pause внутри CloudPlayer | `Space` |

`Space` не перехватывается во время ввода текста. В списке результатов Add Song он контекстно управляет Demo.

На ноутбуках и компактных клавиатурах может понадобиться `Fn+F5` … `Fn+F12`. CloudPlayer получает итоговый сигнал `F5` … `F12`; работа `Fn` зависит от клавиатуры и её режима.

### Быстрый переход по разделам

| Раздел | Клавиша |
| --- | --- |
| Главная / библиотека | `Ctrl+1` |
| Плейлисты | `Ctrl+2` |
| Поиск | `Ctrl+3` |
| Очередь | `Ctrl+4` |
| Listen Together | `Ctrl+5` |
| Настройки | `Ctrl+6` |
| Текущий трек | `Ctrl+0` |

### Глобальные media keys

В Windows глобально поддерживаются:

- `F7` — предыдущий трек;
- `F8` — Play/Pause;
- `F9` — следующий трек;
- аппаратные клавиши Previous, Play/Pause и Next.

Глобальные клавиши сначала используют нативный Win32 backend, затем библиотеку `keyboard` как резервный вариант.

## Настройка биндов

Все пользовательские сочетания находятся в:

```text
Documents/CloudPlayer/binds.cfg
```

Если файла нет, CloudPlayer создаёт его автоматически с настройками по умолчанию.

В настройках приложения доступен раздел **Keyboard**:

- отображается путь к `binds.cfg`;
- доступна кнопка сброса;
- сброс записывается после нажатия **Save**;
- **Cancel** не изменяет файл;
- новые значения применяются без перезапуска.

Пример структуры `binds.cfg`:

```ini
focus_next = Tab
focus_previous = Shift+Tab
navigate_up = Up
navigate_down = Down
navigate_left = Left
navigate_right = Right
activate = Enter
cancel = Escape

context_menu = RightCtrl
context_menu_fallback = Shift+F10

repeat = F5
shuffle = F6
previous_track = F7
play_pause = F8
next_track = F9
mute = F10
volume_down = F11
volume_up = F12

playlist_add_song = Alt
demo_selected = Space
multi_select_up = Ctrl+Up
multi_select_down = Ctrl+Down
```

Названия параметров в установленной версии могут включать дополнительные действия. Для получения полного актуального шаблона удалите `binds.cfg` или используйте **Reset Bindings** в настройках.

## Интерфейс

- Диалоги открываются внутри главного окна по центру.
- Выбор и сохранение файлов используют системный Проводник.
- У диалогов есть отдельный header и кнопка `exit.svg`.
- Диалоги можно перемещать за верхнюю панель.
- Плавные fade-анимации.
- Инерционный скролл.
- Стилизованные scrollbar.
- Настраиваемый accent-цвет.
- Анимированная `check.svg`.
- Debug Console:
  - stdout;
  - stderr;
  - Python logging;
  - warnings;
  - Qt logs;
  - необработанные исключения.
- Ссылки GitHub и Telegram.
- Кнопка поддержки с копированием адреса пожертвования.

Строки FFmpeg вида `Input #0`, `hls`, `data000.m4s` во время Demo означают открытие потокового аудио и сами по себе не являются ошибкой.

## Быстрый запуск из исходников

Рекомендуется Windows 10/11 и актуальная версия Python 3.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

Для наиболее чистого поиска YouTube Music установите дополнительную зависимость, если она вынесена в отдельный файл проекта:

```powershell
python -m pip install -r requirements-youtube-music.txt
```

Основные зависимости:

- `PySide6`;
- `qasync`;
- `yt-dlp`;
- `pypresence`;
- `keyboard`;
- `requests`;
- `ytmusicapi` для каталожного поиска YouTube Music.

`ffmpeg.exe` находится в корне проекта. Другой путь можно указать через переменную окружения:

```text
CLOUDPLAYER_FFMPEG
```

## Первый запуск и базовое использование

1. Запустите `CloudPlayer.exe` или `python main.py`.
2. Создайте плейлист через интерфейс или `Ctrl+N`.
3. Откройте плейлист.
4. Нажмите **Add Song** или клавишу `Alt`.
5. Введите запрос.
6. При необходимости откройте фильтр и включите YouTube Music.
7. Нажмите **Search**.
8. Выберите результат стрелками.
9. Нажмите `Space`, чтобы прослушать Demo.
10. Нажмите `Enter`, чтобы скачать выбранный трек.
11. Для нескольких треков используйте `Ctrl+↑` / `Ctrl+↓`, затем `Enter`.
12. После загрузки трек появится в текущем плейлисте.

Для локального файла используйте **Add Local File**. Для ссылки используйте **Add From URL**.

## Сборка автообновления

Автоматическая установка работает в собранной Windows-версии. `CloudPlayer.exe` должен быть самодостаточной one-file сборкой, поскольку updater заменяет именно этот файл.

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean --noconfirm CloudPlayerUpdater.spec
```

В каждый GitHub Release нужно загрузить два asset-файла с точными именами:

- `CloudPlayer.exe`;
- `CloudPlayerUpdater.exe`.

GitHub должен предоставить SHA-256 digest для обоих файлов.

Процесс обновления:

1. CloudPlayer скачивает и проверяет файлы.
2. Пользователь подтверждает `Update and Restart`.
3. Updater ждёт полного закрытия CloudPlayer.
4. Текущий файл сохраняется как `CloudPlayer.exe.old`.
5. Новая версия устанавливается и запускается с одноразовым token.
6. После появления главного окна новая версия подтверждает успешный запуск.
7. Если новая версия завершится слишком рано, updater восстановит старую.

Журнал:

```text
Documents/CloudPlayer/update.log
```

## Настройка интеграций

Локальные плейлисты и воспроизведение работают без внешних API-ключей.

Для дополнительных функций скопируйте:

```powershell
Copy-Item keys.example.json keys.json
```

| Поле | Назначение |
| --- | --- |
| `genius_client_id` | Genius API client ID |
| `genius_client_secret` | Genius API client secret |
| `genius_access_token` | Получение текстов песен |
| `discord_client_id` | Discord Rich Presence |
| `supabase_url` | URL проекта Supabase |
| `supabase_api_key` | Публичный/anon ключ Supabase |
| `supabase_admin_api_key` | Административные операции аккаунта |
| `turnstile_site_key` | Публичный ключ Cloudflare Turnstile |
| `turnstile_secret_key` | Серверная проверка Turnstile |
| `turnstile_verify_url` | Endpoint проверки Turnstile |
| `turn_urls` | TURN URL |
| `turn_username` | Имя пользователя TURN |
| `turn_password` | Пароль TURN |

Вместо `keys.json` можно использовать переменные окружения из `config.py`.

Другой файл ключей:

```text
CLOUDPLAYER_KEYS_FILE
```

`keys.json` должен находиться в `.gitignore`.

Не публикуйте секретные ключи. Supabase service-role/admin key нельзя безопасно хранить в публичной desktop-сборке. Для production административные операции следует вынести на доверенный backend, RPC или Edge Function.

## Где хранятся данные

```text
Documents/CloudPlayer/
├── account.json
├── settings.json
├── binds.cfg
├── update_state.json
├── update.log
├── downloads/
│   ├── CloudPlayer.update.exe
│   └── CloudPlayerUpdater.exe
├── playlists/
│   ├── Playlist Name.json
│   └── Playlist Name/
│       └── songs/
└── temp/
    ├── lyrics/
    └── preview/
```

- `settings.json` — accent-цвет, громкость, Debug Console, источники поиска и другие параметры.
- `binds.cfg` — пользовательские сочетания клавиш.
- `account.json` — локальная сессия.
- `playlists` — аудио, обложки, метаданные и порядок треков.
- `downloads` — проверенные файлы обновления.
- `update.log` — журнал обновления и отката.
- `temp/lyrics` — кеш текстов.
- `temp/preview` — временные данные Demo, очищаемые приложением.

Удаление трека или плейлиста через интерфейс удаляет соответствующие локальные файлы.

## Возможные проблемы

### Не работает скачивание

Проверьте:

- доступность интернета;
- актуальность `yt-dlp`;
- наличие FFmpeg;
- путь `CLOUDPLAYER_FFMPEG`;
- доступность SoundCloud или YouTube Music.

### YouTube Music показывает мало результатов

Установите `ytmusicapi` или файл `requirements-youtube-music.txt`. Без него CloudPlayer может использовать резервный поиск через `yt-dlp`, который менее точен.

### Demo долго запускается

Причины могут быть связаны с:

- медленным соединением;
- HLS-потоком SoundCloud;
- временной задержкой получения прямой ссылки;
- блокировкой источника;
- работой FFmpeg backend.

Demo использует прогрессивную буферизацию и начинает воспроизведение после накопления стартового фрагмента.

### В Debug Console много строк FFmpeg

Сообщения о `hls`, `aac`, `mp3`, `Input #0`, `data000.m4s` обычно являются информационными. Ошибкой следует считать сообщения, после которых Demo или воспроизведение действительно не запускается.

### Нет текстов песен

Проверьте Genius-параметры в `keys.json` или переменных окружения.

### Нет Discord Rich Presence

Проверьте:

- `discord_client_id`;
- запущен ли Discord;
- разрешена ли активность приложений в Discord.

### Не работают глобальные клавиши

Windows может отказать в регистрации сочетания, если его уже использует другое приложение. На некоторых клавиатурах F-клавиши требуют переключения Fn Lock.

### Не подключается Listen Together

Проверьте:

- адрес;
- порт;
- firewall;
- port forwarding;
- TURN-конфигурацию.

### Не работает облачная синхронизация

Проверьте Supabase и Cloudflare Turnstile.

---

# English

## About

CloudPlayer is a Windows desktop music player that combines a local library with SoundCloud and YouTube Music search, preview streaming, keyboard-first navigation, lyrics, cloud synchronization and synchronized listening rooms.

An account is not required for local playlists, local files or ordinary playback.

## Main features

### Library and playlists

- Create, open, rename and remove playlists.
- Set a custom playlist cover.
- Restore an automatically detected cover.
- Select multiple playlists and tracks.
- Reorder tracks with drag and drop.
- Undo the latest reorder with `Ctrl+Z`.
- Persist playlist order and metadata automatically.
- Keep music playing after leaving a playlist.
- Stop safely when the playing track or playlist is deleted.
- Run context actions on one or multiple selected tracks.

### Add Song: SoundCloud and YouTube Music

The **Add Song** dialog supports:

- **SoundCloud**;
- **YouTube Music**.

SoundCloud is enabled by default. Use the `filter.svg` button to change sources.

The source menu:

- marks enabled sources with `check.svg`;
- allows one or both sources;
- does not allow all sources to be disabled;
- saves the latest selection in `Documents/CloudPlayer/settings.json`.

YouTube Music uses a songs-focused search instead of ordinary YouTube results. CloudPlayer also:

- removes exact duplicates;
- ranks stronger title and artist matches higher;
- lowers `slowed`, `reverb`, `nightcore`, `karaoke`, `cover`, `remix`, `sped up` and similar variants unless the query requests them;
- labels results as `SC` or `YTM`;
- merges both providers into one result list.

### Demo preview

Each search result includes a white **Demo** label and a `play.svg` / `pause.svg` button.

Demo preview:

- does not add the track to the library;
- starts before downloading;
- uses a small startup buffer;
- keeps downloading in chunks while playing;
- supports progressive and HLS sources;
- stops the previous preview when another result starts;
- cancels when Add Song closes or a new search begins;
- follows the main player volume;
- follows the main mute state;
- reacts immediately to volume changes.

Keyboard controls in the result list:

- `Up` / `Down` — move through results;
- `Space` — play or pause Demo;
- `Enter` — download the selected result;
- `Ctrl+Up` / `Ctrl+Down` — extend multi-selection;
- `Enter` downloads all selected results.

### Other ways to add music

- Add a complete SoundCloud URL.
- Add a complete YouTube or YouTube Music URL.
- Add local audio files.
- Download in the background with progress.
- Retrieve and convert audio through `yt-dlp` and FFmpeg.
- Add recommendations directly to a playlist.

Supported local formats:

```text
.mp3  .wav  .m4a  .flac  .ogg  .opus  .webm
```

## Track management

The track context menu can:

- start playback;
- rename a track;
- duplicate a track with metadata and cover sidecars;
- remove one or multiple tracks;
- show and copy the file path.

The cover context menu can:

- open the image at full size;
- save it;
- copy it;
- zoom smoothly with the mouse wheel;
- pan by dragging.

## Player

- Play/Pause.
- Previous and next.
- Click or drag the timeline to seek.
- Current time, duration and network buffer progress.
- Slider or exact numeric volume.
- Smooth mute and unmute.
- Persisted volume.
- Repeat.
- Stable shuffle queue.
- Automatic transition to the next track.
- Title, artist, cover and lyrics.
- Genius lyrics retrieval and local caching.

## Queue

- Queue is the first playback control.
- Shows the next five tracks.
- Supports normal order, Shuffle Mode and Listen Together.
- Allows drag-and-drop reordering.
- Does not rewrite the saved playlist order.

## Listen Together

- Host a room.
- Join by address and port.
- Domain, IPv4, IPv6 and full URL support.
- Separate public and local ports.
- Synchronize play, pause, seek, repeat, selection and skipping.
- Transfer missing tracks.
- Adaptive buffering.
- Resume interrupted transfers after reconnecting.
- Participant list with country and latency.
- Optional TURN configuration.

Internet hosting may require firewall permission and port forwarding.

## Account and cloud sync

- Sign up and log in.
- Cloudflare Turnstile.
- Persisted local session.
- Synchronize downloadable source links through Supabase.
- Download synchronized tracks into a local playlist.
- Remove individual cloud entries.
- Log out.
- Permanently delete the account.

Local files without a source URL are skipped during cloud synchronization.

## Keyboard-first control

CloudPlayer supports keyboard navigation across most of the interface.

The focus indicator:

- remains hidden while using the mouse;
- appears when keyboard navigation begins;
- hides after clicking, double-clicking, scrolling or touching.

CloudPlayer keeps the native Qt `Tab` focus order.

### Navigation

| Action | Key |
| --- | --- |
| Next control or section | `Tab` |
| Previous control or section | `Shift+Tab` |
| Navigate inside a list or menu | Arrow keys |
| Activate | `Enter` |
| Close, cancel or go back from a popup | `Esc` |
| Return to the previous screen | `Backspace` |
| First list item | `Home` |
| Last list item | `End` |
| Fast scroll up | `PageUp` |
| Fast scroll down | `PageDown` |

### Confirmation dialogs

| Action | Key |
| --- | --- |
| Yes / OK / confirm | `Enter` |
| No / Cancel | `Esc` |

This behavior does not depend on which button currently owns visual focus.

### Context menus

| Action | Key |
| --- | --- |
| Open the selected item's context menu | single `Right Ctrl` |
| Fallback context menu shortcut | `Shift+F10` |
| Move through menu items | `Up` / `Down` |
| Run the selected command | `Enter` |
| Close the menu | `Esc` |

A standalone `Fn` press is usually not exposed to Windows applications, so Right Ctrl is the primary context-menu key.

### Playlists and library

| Action | Key |
| --- | --- |
| Open Add Song in the current playlist | `Alt` |
| Focus the Add Song search field | automatic after `Alt` |
| Create a playlist | `Ctrl+N` |
| Focus search | `Ctrl+F` |
| Select all in a supported list | `Ctrl+A` |
| Delete selected tracks or playlists | `Delete` |
| Undo the latest reorder | `Ctrl+Z` |
| Play the selected track | `Ctrl+Enter` |
| Queue the selected track next | `Shift+Enter` |
| Extend selection downward | `Ctrl+Down` |
| Extend selection upward | `Ctrl+Up` |

`Ctrl+Up` and `Ctrl+Down` work in playlist track lists and Add Song search results.

### Playback

| Action | Key |
| --- | --- |
| Toggle Repeat | `F5` |
| Toggle Shuffle | `F6` |
| Previous track | `F7` |
| Play/Pause | `F8` |
| Next track | `F9` |
| Mute/Unmute | `F10` |
| Volume down | `F11` |
| Volume up | `F12` |
| Play/Pause inside CloudPlayer | `Space` |

`Space` is not intercepted while typing. In Add Song results it controls Demo preview.

Compact keyboards may require `Fn+F5` through `Fn+F12`. CloudPlayer receives the final F-key event; Fn behavior depends on the keyboard.

### Quick sections

| Section | Key |
| --- | --- |
| Home / library | `Ctrl+1` |
| Playlists | `Ctrl+2` |
| Search | `Ctrl+3` |
| Queue | `Ctrl+4` |
| Listen Together | `Ctrl+5` |
| Settings | `Ctrl+6` |
| Current track | `Ctrl+0` |

### Global media keys

Windows global controls:

- `F7` — previous;
- `F8` — Play/Pause;
- `F9` — next;
- hardware Previous, Play/Pause and Next keys.

The native Win32 backend is used first, with the `keyboard` package as fallback.

## Custom key bindings

Bindings are stored at:

```text
Documents/CloudPlayer/binds.cfg
```

CloudPlayer creates the file automatically when it is missing.

The **Keyboard** section in Settings:

- shows the file path;
- can reset bindings;
- writes the reset only after **Save**;
- leaves the file unchanged after **Cancel**;
- applies new defaults without restarting.

Example:

```ini
focus_next = Tab
focus_previous = Shift+Tab
navigate_up = Up
navigate_down = Down
navigate_left = Left
navigate_right = Right
activate = Enter
cancel = Escape

context_menu = RightCtrl
context_menu_fallback = Shift+F10

repeat = F5
shuffle = F6
previous_track = F7
play_pause = F8
next_track = F9
mute = F10
volume_down = F11
volume_up = F12

playlist_add_song = Alt
demo_selected = Space
multi_select_up = Ctrl+Up
multi_select_down = Ctrl+Down
```

The installed build may include more action names. Use **Reset Bindings** to regenerate the complete current template.

## Interface

- Centered in-app dialogs.
- Native file picker for file selection and saving.
- Dedicated dialog header and `exit.svg`.
- Draggable dialogs constrained to the app window.
- Smooth fade animations.
- Inertial scrolling.
- Styled scrollbars.
- Configurable accent color.
- Animated `check.svg`.
- Optional Debug Console with stdout, stderr, Python logs, warnings, Qt logs and uncaught exceptions.
- GitHub and Telegram links.
- Support button with a copyable donation address.

FFmpeg lines such as `Input #0`, `hls` or `data000.m4s` during Demo usually describe streaming activity and are not automatically errors.

## Quick start from source

Windows 10/11 and a current Python 3 release are recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

For the cleanest YouTube Music catalog search, install the optional dependency when the project keeps it in a separate file:

```powershell
python -m pip install -r requirements-youtube-music.txt
```

Main dependencies:

- `PySide6`;
- `qasync`;
- `yt-dlp`;
- `pypresence`;
- `keyboard`;
- `requests`;
- `ytmusicapi`.

`ffmpeg.exe` is located in the project root. Use another executable through:

```text
CLOUDPLAYER_FFMPEG
```

## First-run workflow

1. Start `CloudPlayer.exe` or `python main.py`.
2. Create a playlist with the UI or `Ctrl+N`.
3. Open the playlist.
4. Press **Add Song** or `Alt`.
5. Type a query.
6. Enable YouTube Music through the filter when needed.
7. Press **Search**.
8. Select a result with the arrow keys.
9. Press `Space` to preview it.
10. Press `Enter` to download it.
11. Use `Ctrl+Up` / `Ctrl+Down` and then `Enter` for multiple tracks.
12. The downloaded tracks appear in the current playlist.

Use **Add Local File** for local audio and **Add From URL** for a direct link.

## Building automatic updates

Automatic installation is available in the packaged Windows build. `CloudPlayer.exe` must be a self-contained one-file executable.

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean --noconfirm CloudPlayerUpdater.spec
```

Upload these exact assets to every GitHub Release:

- `CloudPlayer.exe`;
- `CloudPlayerUpdater.exe`.

GitHub must provide SHA-256 digests for both files.

Update flow:

1. CloudPlayer downloads and verifies the files.
2. The user confirms `Update and Restart`.
3. The updater waits for CloudPlayer to exit.
4. The previous executable is saved as `CloudPlayer.exe.old`.
5. The new build is installed and started with a one-time token.
6. The new version confirms startup after the main window appears.
7. If it exits too early, the updater restores the previous build.

Log file:

```text
Documents/CloudPlayer/update.log
```

## Integration configuration

Local playlists and playback do not require external API credentials.

```powershell
Copy-Item keys.example.json keys.json
```

| Field | Purpose |
| --- | --- |
| `genius_client_id` | Genius API client ID |
| `genius_client_secret` | Genius API client secret |
| `genius_access_token` | Genius lyrics |
| `discord_client_id` | Discord Rich Presence |
| `supabase_url` | Supabase project URL |
| `supabase_api_key` | Supabase public/anon key |
| `supabase_admin_api_key` | Administrative account operations |
| `turnstile_site_key` | Cloudflare Turnstile site key |
| `turnstile_secret_key` | Server-side Turnstile verification |
| `turnstile_verify_url` | Turnstile verification endpoint |
| `turn_urls` | TURN URLs |
| `turn_username` | TURN username |
| `turn_password` | TURN password |

Environment variables from `config.py` can be used instead of `keys.json`.

Alternative credentials file:

```text
CLOUDPLAYER_KEYS_FILE
```

Keep `keys.json` in `.gitignore`.

Never publish secret credentials. A Supabase service-role/admin key cannot be stored safely in a public desktop build. Move administrative operations to a trusted backend, RPC or Edge Function for production.

## Data locations

```text
Documents/CloudPlayer/
├── account.json
├── settings.json
├── binds.cfg
├── update_state.json
├── update.log
├── downloads/
│   ├── CloudPlayer.update.exe
│   └── CloudPlayerUpdater.exe
├── playlists/
│   ├── Playlist Name.json
│   └── Playlist Name/
│       └── songs/
└── temp/
    ├── lyrics/
    └── preview/
```

- `settings.json` — accent color, volume, Debug Console state, search sources and other settings.
- `binds.cfg` — custom key bindings.
- `account.json` — local account session.
- `playlists` — audio, covers, metadata and saved track order.
- `downloads` — verified updater files.
- `update.log` — update and rollback events.
- `temp/lyrics` — lyrics cache.
- `temp/preview` — temporary Demo data cleared by the application.

Removing a track or playlist from the UI removes its local files.

## Troubleshooting

### Downloads fail

Check:

- internet access;
- current `yt-dlp`;
- FFmpeg availability;
- `CLOUDPLAYER_FFMPEG`;
- SoundCloud or YouTube Music availability.

### YouTube Music returns too few results

Install `ytmusicapi` or `requirements-youtube-music.txt`. Without it, CloudPlayer may use a less precise `yt-dlp` fallback.

### Demo starts slowly

Possible causes:

- slow internet;
- SoundCloud HLS startup;
- delay while resolving the direct media URL;
- source throttling;
- FFmpeg backend startup.

Demo uses progressive buffering and begins after a small startup fragment is available.

### Debug Console shows many FFmpeg lines

Messages containing `hls`, `aac`, `mp3`, `Input #0` or `data000.m4s` are often informational. Treat them as an error only when preview or playback actually fails.

### Lyrics are unavailable

Verify Genius settings in `keys.json` or environment variables.

### Discord Rich Presence is unavailable

Verify:

- `discord_client_id`;
- that Discord is running;
- that activity sharing is enabled.

### Global shortcuts do not work

Windows may reject a shortcut already registered by another application. Some keyboards also require Fn Lock for ordinary F-key behavior.

### Listen Together cannot connect

Verify:

- address;
- port;
- firewall;
- port forwarding;
- TURN configuration.

### Cloud sync is unavailable

Verify Supabase and Cloudflare Turnstile.

## Author

FinchProffe · 2026
