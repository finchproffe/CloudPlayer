# CloudPlayer

[Русский](#русский) · [English](#english)

CloudPlayer is a desktop music player built with Python, PySide6 and Qt Multimedia. It combines local playlists, SoundCloud downloads, lyrics, recommendations, cloud synchronization and synchronized listening rooms in one interface.

## Русский

### Возможности

#### Библиотека и плейлисты

- Создание, открытие, переименование и удаление плейлистов.
- Выбор собственной обложки плейлиста и возврат к автоматически найденной обложке.
- Множественное выделение плейлистов и треков.
- Изменение порядка треков перетаскиванием.
- Отмена последнего изменения порядка через `Ctrl+Z`.
- Автоматическое сохранение порядка и метаданных плейлиста.
- Продолжение воспроизведения после выхода из плейлиста или перехода в другой плейлист.
- Остановка воспроизведения при удалении именно играющего трека или его плейлиста.

#### Добавление и поиск музыки

- Поиск треков SoundCloud из главного меню.
- Поиск SoundCloud непосредственно в окне `Add Song`.
- Загрузка одного или нескольких выбранных результатов.
- Добавление трека по полной ссылке SoundCloud.
- Добавление локальных аудиофайлов.
- Фоновая загрузка с отображением прогресса.
- Использование `yt-dlp` и FFmpeg для получения и преобразования аудио.
- Рекомендации с возможностью сразу добавить найденный трек в плейлист.

Поддерживаемые локальные форматы: `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`, `.opus` и `.webm`.

#### Управление треками

Контекстное меню трека позволяет:

- запустить воспроизведение;
- переименовать трек;
- создать копию вместе с дополнительными файлами метаданных и обложки;
- удалить один или несколько треков;
- посмотреть и скопировать путь к файлу.

Контекстное меню обложки позволяет открыть её в полном размере, сохранить в файл или скопировать в буфер обмена. В полноразмерном просмотре колёсико изменяет масштаб, а перетаскивание перемещает изображение.

#### Плеер

- Play/Pause, предыдущий и следующий трек.
- Перемотка нажатием или перетаскиванием по timeline.
- Отображение текущего времени, длительности и прогресса сетевого буфера.
- Регулировка громкости ползунком или точным числом.
- Быстрое включение и выключение звука.
- Сохранение выбранной громкости между запусками.
- Repeat с плавной анимацией черты состояния.
- Shuffle с устойчивой очередью случайных треков.
- Автоматический переход к следующему треку после завершения текущего.
- Отображение названия, исполнителя, обложки и текста песни.
- Получение и локальное кеширование текстов через Genius.

#### Очередь

- Кнопка очереди расположена первой в блоке управления.
- Показываются следующие пять треков.
- Поддерживаются обычный порядок, Shuffle Mode и очередь Listen Together.
- В обычном и shuffle-режиме треки можно переставлять перетаскиванием.
- Изменение очереди не переписывает сохранённый порядок самого плейлиста.

#### Listen Together

- Создание собственной комнаты или подключение по адресу и порту.
- Поддержка доменов, IPv4, IPv6 и полного URL.
- Отдельный внешний и локальный порт для сервера.
- Синхронизация play, pause, seek, repeat, выбора и пропуска треков.
- Передача отсутствующих треков между участниками.
- Адаптивный сетевой буфер и отображение загруженной части на timeline.
- Возобновление прерванной передачи после переподключения.
- Список участников комнаты, страна и задержка.
- Опциональная конфигурация TURN-серверов.

Для подключения извне локальной сети может потребоваться проброс выбранного порта на роутере.

#### Аккаунт и облачная синхронизация

- Регистрация и вход в аккаунт.
- Проверка Cloudflare Turnstile.
- Сохранение локальной сессии.
- Синхронизация ссылок на загружаемые треки через Supabase.
- Загрузка синхронизированных треков в локальный плейлист.
- Удаление отдельных треков из облачной синхронизации.
- Выход и полное удаление аккаунта.

Локальные файлы без исходной ссылки не загружаются в облако и пропускаются при синхронизации.

#### Системное управление и интеграции

- Глобальные клавиши `F7`, `F8`, `F9`: предыдущий трек, play/pause, следующий трек.
- Поддержка аппаратных клавиш Previous, Play/Pause и Next.
- Windows Thumbnail Toolbar под превью приложения на панели задач.
- Кнопки Previous, Play/Pause и Next в Thumbnail Toolbar.
- Discord Rich Presence с текущим треком, исполнителем, обложкой и состоянием паузы.
- Проверка обновлений через GitHub Releases.
- Проверка размера и SHA-256 перед сохранением обновления.

Thumbnail Toolbar доступен только в Windows. Глобальные media keys в Windows сначала используют нативный Win32 backend, затем библиотеку `keyboard` как резервный вариант.

#### Интерфейс

- Все диалоги открываются внутри главного окна по центру экрана приложения.
- Отдельный верхний header с названием и кнопкой `exit.svg`.
- Перетаскивание диалогов за верхний header с ограничением границами приложения.
- Плавные fade-анимации без искусственного ограничения частоты кадров.
- Одинаковый плавный инерционный скролл во всех списках, текстовых областях и файловых диалогах.
- Стилизованные вертикальные и горизонтальные scrollbar без стандартных стрелок.
- Настраиваемый основной accent-цвет.
- Опциональная Debug Console с stdout, stderr, предупреждениями, Python/Qt-логами и необработанными исключениями.
- Ссылки GitHub и Telegram показываются во внутреннем меню и могут быть скопированы.
- Кнопка поддержки показывает адрес пожертвования и позволяет скопировать его.

### Быстрый запуск

Рекомендуется Windows 10/11 и актуальная версия Python 3.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

Основные зависимости:

- `PySide6`;
- `qasync`;
- `yt-dlp`;
- `pypresence`;
- `keyboard`;
- `requests`.

Файл `ffmpeg.exe` уже находится в корне проекта. Другой путь можно указать через переменную окружения `CLOUDPLAYER_FFMPEG`.

### Настройка интеграций

Локальные плейлисты и воспроизведение работают без внешних API-ключей. Для дополнительных функций скопируйте `keys.example.json` в `keys.json` и заполните только нужные значения:

```powershell
Copy-Item keys.example.json keys.json
```

| Поле | Назначение |
| --- | --- |
| `genius_client_id` | Genius API client ID |
| `genius_client_secret` | Genius API client secret |
| `genius_access_token` | Получение текстов песен через Genius |
| `discord_client_id` | Discord Rich Presence |
| `supabase_url` | URL проекта Supabase |
| `supabase_api_key` | Публичный/anon ключ Supabase |
| `supabase_admin_api_key` | Административные операции аккаунта |
| `turnstile_site_key` | Публичный ключ Cloudflare Turnstile |
| `turnstile_secret_key` | Серверная проверка Turnstile |
| `turnstile_verify_url` | Endpoint проверки Turnstile |
| `turn_urls` | Список TURN URL |
| `turn_username` | Имя пользователя TURN |
| `turn_password` | Пароль TURN |

Вместо `keys.json` можно использовать переменные окружения, перечисленные в `config.py`. Путь к другому файлу ключей задаётся через `CLOUDPLAYER_KEYS_FILE`.

`keys.json` добавлен в `.gitignore`. Не публикуйте секретные ключи. В публичной desktop-сборке нельзя безопасно хранить Supabase service-role/admin key: для production вынесите административные операции на доверенный backend или Supabase RPC/Edge Function.

### Где хранятся данные

CloudPlayer создаёт каталог:

```text
Documents/CloudPlayer/
├── account.json
├── settings.json
├── update_state.json
├── downloads/
├── playlists/
│   ├── Playlist Name.json
│   └── Playlist Name/
│       └── songs/
└── temp/
    └── lyrics/
```

- `settings.json` хранит accent-цвет, громкость и состояние Debug Console.
- `account.json` хранит локальную сессию аккаунта.
- `playlists` содержит аудиофайлы, обложки, sidecar-метаданные и порядок треков.
- `downloads` содержит загруженное обновление `CloudPlayer.exe`.
- `temp/lyrics` содержит кеш текстов песен и временные файлы.

Удаление плейлиста или трека из интерфейса удаляет соответствующие локальные файлы.

### Управление

| Действие | Управление |
| --- | --- |
| Воспроизвести трек | Двойной клик по треку или `Play` в контекстном меню |
| Изменить порядок плейлиста | Перетащить трек в списке |
| Отменить перестановку | `Ctrl+Z` внутри плейлиста |
| Изменить очередь | Открыть очередь и перетащить строку |
| Предыдущий трек | `F7` или media key Previous |
| Play/Pause | `F8` или media key Play/Pause |
| Следующий трек | `F9` или media key Next |
| Точная громкость | Нажать кнопку с процентом громкости |
| Перетащить диалог | Потянуть за его верхний header |
| Закрыть диалог | Нажать кнопку с `exit.svg` или затемнённый фон |

### Возможные проблемы

- Если загрузка или преобразование трека не работает, проверьте доступность FFmpeg и `yt-dlp`.
- Если тексты не загружаются, проверьте параметры Genius в `keys.json` или переменных окружения.
- Если Discord Rich Presence отсутствует, проверьте `discord_client_id` и запущенный Discord-клиент.
- Если глобальные клавиши заняты другим приложением, Windows может отказать в их регистрации.
- Если участники не могут подключиться к комнате, проверьте адрес, порт, firewall, port forwarding и TURN-конфигурацию.
- Если облачная синхронизация недоступна, проверьте Supabase и Cloudflare Turnstile.

---

## English

### Features

#### Library and playlists

- Create, open, rename and remove playlists.
- Set a custom playlist cover or restore the automatically detected cover.
- Select multiple playlists and tracks.
- Reorder tracks with drag and drop.
- Undo the latest playlist reorder with `Ctrl+Z`.
- Automatically persist playlist order and metadata.
- Keep music playing after leaving a playlist or opening another playlist.
- Stop playback when the currently playing track or its playlist is deleted.

#### Adding and discovering music

- Search SoundCloud from the home screen.
- Search SoundCloud inside the `Add Song` dialog.
- Download one or multiple selected results.
- Add a track from a complete SoundCloud URL.
- Add local audio files.
- Download in the background with progress reporting.
- Use `yt-dlp` and FFmpeg for audio retrieval and conversion.
- Browse recommendations and add a recommended track directly to a playlist.

Supported local formats: `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`, `.opus` and `.webm`.

#### Track management

The track context menu can:

- start playback;
- rename a track;
- duplicate a track together with its metadata and cover sidecars;
- delete one or multiple tracks;
- display and copy the file path.

The cover context menu can open the image at full size, save it to a file or copy it to the clipboard. In the full-size viewer, use the mouse wheel to zoom and drag to pan the image.

#### Player

- Play/Pause, previous and next controls.
- Click or drag on the timeline to seek.
- Current time, duration and network-buffer progress.
- Slider-based or exact numeric volume control.
- Quick mute and unmute.
- Persisted volume between launches.
- Repeat with a smooth animated state slash.
- Shuffle with a stable randomized queue.
- Automatic transition to the next track when playback ends.
- Track title, artist, cover and lyrics display.
- Genius lyrics retrieval and local caching.

#### Queue

- The queue button is the first playback control.
- Displays the next five tracks.
- Supports normal order, Shuffle Mode and Listen Together queues.
- Tracks can be reordered with drag and drop in normal and shuffle modes.
- Queue changes do not rewrite the saved playlist order.

#### Listen Together

- Host a room or connect with an address and port.
- Domain, IPv4, IPv6 and full URL support.
- Separate public and local server ports.
- Synchronized play, pause, seek, repeat, selection and track skipping.
- Transfer missing tracks between participants.
- Adaptive network buffering with loaded progress on the timeline.
- Resume interrupted transfers after reconnecting.
- Room roster with participant country and latency.
- Optional TURN server configuration.

Hosting outside the local network may require forwarding the selected port on the router.

#### Account and cloud synchronization

- Account sign-up and login.
- Cloudflare Turnstile verification.
- Persisted local account session.
- Synchronize downloadable track links through Supabase.
- Download synchronized tracks into a local playlist.
- Remove individual tracks from cloud synchronization.
- Log out or permanently delete the account.

Local files without an original downloadable URL are skipped during synchronization.

#### System controls and integrations

- Global `F7`, `F8`, `F9` shortcuts for previous, play/pause and next.
- Hardware Previous, Play/Pause and Next media-key support.
- Windows Thumbnail Toolbar under the taskbar preview.
- Previous, Play/Pause and Next Thumbnail Toolbar buttons.
- Discord Rich Presence with track, artist, cover and paused state.
- GitHub Releases update checks.
- Size and SHA-256 verification before an update is saved.

Thumbnail Toolbar is available only on Windows. Global media keys use the native Win32 backend first and the `keyboard` package as a fallback.

#### Interface

- Every dialog opens as a centered overlay inside the main window.
- Dedicated top header with the dialog title and `exit.svg` button.
- Drag dialogs by their header while keeping them inside the application.
- Smooth fade animations without an artificial frame-rate limit.
- The same inertial scrolling behavior in lists, text areas and file dialogs.
- Styled vertical and horizontal scrollbars without native arrow buttons.
- Configurable primary accent color.
- Optional Debug Console with stdout, stderr, warnings, Python/Qt logs and uncaught exceptions.
- GitHub and Telegram links are displayed in an internal menu and can be copied.
- The support button displays the donation address and lets you copy it.

### Quick start

Windows 10/11 and a current Python 3 release are recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

Main dependencies:

- `PySide6`;
- `qasync`;
- `yt-dlp`;
- `pypresence`;
- `keyboard`;
- `requests`.

`ffmpeg.exe` is included in the project root. Set `CLOUDPLAYER_FFMPEG` to use a different executable.

### Integration configuration

Local playlists and playback do not require external API credentials. For optional integrations, copy `keys.example.json` to `keys.json` and fill only the values you need:

```powershell
Copy-Item keys.example.json keys.json
```

| Field | Purpose |
| --- | --- |
| `genius_client_id` | Genius API client ID |
| `genius_client_secret` | Genius API client secret |
| `genius_access_token` | Genius lyrics retrieval |
| `discord_client_id` | Discord Rich Presence |
| `supabase_url` | Supabase project URL |
| `supabase_api_key` | Supabase public/anon key |
| `supabase_admin_api_key` | Administrative account operations |
| `turnstile_site_key` | Cloudflare Turnstile public site key |
| `turnstile_secret_key` | Server-side Turnstile verification |
| `turnstile_verify_url` | Turnstile verification endpoint |
| `turn_urls` | TURN URL list |
| `turn_username` | TURN username |
| `turn_password` | TURN password |

Environment variables listed in `config.py` can be used instead of `keys.json`. Use `CLOUDPLAYER_KEYS_FILE` to select another credentials file.

`keys.json` is included in `.gitignore`. Never publish secret credentials. A Supabase service-role/admin key cannot be stored safely in a public desktop build; move administrative operations to a trusted backend or Supabase RPC/Edge Function for production.

### Data locations

CloudPlayer creates the following directory:

```text
Documents/CloudPlayer/
├── account.json
├── settings.json
├── update_state.json
├── downloads/
├── playlists/
│   ├── Playlist Name.json
│   └── Playlist Name/
│       └── songs/
└── temp/
    └── lyrics/
```

- `settings.json` stores the accent color, volume and Debug Console state.
- `account.json` stores the local account session.
- `playlists` contains audio files, covers, sidecar metadata and track order.
- `downloads` contains the downloaded `CloudPlayer.exe` update.
- `temp/lyrics` contains cached lyrics and temporary files.

Removing a playlist or track through the interface deletes its corresponding local files.

### Controls

| Action | Control |
| --- | --- |
| Play a track | Double-click it or choose `Play` from its context menu |
| Reorder a playlist | Drag a track in the playlist |
| Undo a reorder | Press `Ctrl+Z` inside the playlist |
| Reorder the queue | Open the queue and drag a row |
| Previous track | `F7` or the Previous media key |
| Play/Pause | `F8` or the Play/Pause media key |
| Next track | `F9` or the Next media key |
| Exact volume | Click the volume percentage button |
| Move a dialog | Drag its top header |
| Close a dialog | Click `exit.svg` or the dimmed backdrop |

### Troubleshooting

- If downloading or conversion fails, verify that FFmpeg and `yt-dlp` are available.
- If lyrics do not load, verify the Genius settings in `keys.json` or the environment.
- If Discord Rich Presence is missing, verify `discord_client_id` and make sure the Discord client is running.
- Windows can reject global shortcuts when another application has already registered them.
- If participants cannot join a room, verify the address, port, firewall, port forwarding and TURN configuration.
- If cloud synchronization is unavailable, verify the Supabase and Cloudflare Turnstile configuration.

## Author

FinchProffe · 2025
