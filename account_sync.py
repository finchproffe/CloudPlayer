import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import requests
from PySide6.QtCore import QThread, Qt, QUrl, QUrlQuery, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dropdown_ui import QDialog

from config import (
    ACCENT_COLOR,
    AUDIO_EXTENSIONS,
    BG_COLOR,
    BUTTON_BG,
    BUTTON_BORDER,
    BUTTON_HOVER,
    CAPTCHA_HTML_PATH,
    DOCS_PATH,
    PANEL_BG,
    PLAYLISTS_PATH,
    SUPABASE_ADMIN_API_KEY,
    SUPABASE_API_KEY,
    SUPABASE_URL,
    TEXT_COLOR,
    TEXT_MUTED,
    TURNSTILE_SECRET_KEY,
    TURNSTILE_SITE_KEY,
    TURNSTILE_VERIFY_URL,
)


class SupabaseError(RuntimeError):
    pass


ACCOUNT_SESSION_PATH = DOCS_PATH / "account.json"


def load_account_session():
    try:
        payload = json.loads(ACCOUNT_SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    user_id = str(payload.get("id") or "").strip()
    username = str(payload.get("username") or "").strip()
    if not user_id or not username:
        return None
    return {"id": user_id, "username": username}


def save_account_session(user):
    user_id = str((user or {}).get("id") or "").strip()
    username = str((user or {}).get("username") or "").strip()
    if not user_id or not username:
        return False
    temporary = ACCOUNT_SESSION_PATH.with_suffix(".tmp")
    try:
        DOCS_PATH.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(
                {"id": user_id, "username": username},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(ACCOUNT_SESSION_PATH)
        return True
    except OSError:
        temporary.unlink(missing_ok=True)
        return False


def clear_account_session():
    try:
        ACCOUNT_SESSION_PATH.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def hash_password(password):
    iterations = 310_000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(iterations),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password, encoded):
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = str(encoded).split(
            "$", 3
        )
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = base64.b64decode(raw_salt, validate=True)
        expected = base64.b64decode(raw_digest, validate=True)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def _response_message(response):
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = (
            payload.get("message")
            or payload.get("details")
            or payload.get("hint")
            or payload.get("code")
        )
        if detail:
            return str(detail)[:400]
    text = str(getattr(response, "text", "") or "").strip()
    return text[:400] or f"HTTP {response.status_code}"


def verify_turnstile(token):
    token = str(token or "").strip()
    if not token:
        raise SupabaseError("Complete the Cloudflare verification.")
    if not TURNSTILE_SECRET_KEY:
        raise SupabaseError("Cloudflare Turnstile is not configured.")
    try:
        response = requests.post(
            TURNSTILE_VERIFY_URL,
            data={
                "secret": TURNSTILE_SECRET_KEY,
                "response": token,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SupabaseError(
            f"Could not verify the Cloudflare challenge: {exc}"
        ) from exc
    if not response.ok:
        raise SupabaseError(
            f"Cloudflare verification failed: {_response_message(response)}"
        )
    try:
        result = response.json()
    except ValueError as exc:
        raise SupabaseError(
            "Cloudflare returned an invalid verification response."
        ) from exc
    if not isinstance(result, dict) or not result.get("success"):
        error_codes = result.get("error-codes", []) if isinstance(result, dict) else []
        detail = ", ".join(str(code) for code in error_codes) or "challenge rejected"
        raise SupabaseError(f"Cloudflare verification failed: {detail}.")
    action = str(result.get("action") or "")
    if action and action != "account":
        raise SupabaseError("Cloudflare verification action did not match.")
    hostname = str(result.get("hostname") or "").casefold()
    if hostname and hostname not in {"localhost", "127.0.0.1"}:
        raise SupabaseError("Cloudflare verification hostname did not match.")
    return True


class _CaptchaRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in {"/", "/captcha.html"}:
            self.send_error(404)
            return
        body = self.server.captcha_content
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


class LocalCaptchaServer:
    def __init__(self):
        self.server = None
        self.thread = None

    def start(self):
        if self.server is not None:
            port = self.server.server_address[1]
            return QUrl(f"http://localhost:{port}/captcha.html")
        if not TURNSTILE_SITE_KEY:
            raise SupabaseError("Cloudflare Turnstile site key is missing.")
        try:
            template = CAPTCHA_HTML_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise SupabaseError(f"Could not load captcha.html: {exc}") from exc
        rendered = template.replace(
            "__TURNSTILE_SITE_KEY_JSON__",
            json.dumps(TURNSTILE_SITE_KEY),
        )
        if rendered == template:
            raise SupabaseError("captcha.html does not contain the site-key marker.")
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _CaptchaRequestHandler,
        )
        server.daemon_threads = True
        server.captcha_content = rendered.encode("utf-8")
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="CloudPlayerCaptcha",
            daemon=True,
        )
        self.server = server
        self.thread = thread
        thread.start()
        return QUrl(
            f"http://localhost:{server.server_address[1]}/captcha.html"
        )

    def stop(self):
        server = self.server
        thread = self.thread
        self.server = None
        self.thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread and thread.is_alive():
            thread.join(timeout=1)


class CaptchaPage(QWebEnginePage):
    token_ready = Signal(str)
    token_failed = Signal(str)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        host = url.host().casefold()
        path = url.path()
        if host in {"localhost", "127.0.0.1"}:
            value = QUrlQuery(url).queryItemValue("value")
            if path == "/captcha-complete":
                self.token_ready.emit(value)
                return False
            if path == "/captcha-expired":
                self.token_failed.emit("Verification expired. Try again.")
                return False
            if path == "/captcha-error":
                self.token_failed.emit(
                    f"Verification error ({value or 'unknown'}). Try again."
                )
                return False
        return super().acceptNavigationRequest(
            url, navigation_type, is_main_frame
        )


def _is_privileged_key(api_key):
    api_key = str(api_key).strip()
    if api_key.startswith("sb_secret_"):
        return True
    if api_key.count(".") != 2:
        return False
    try:
        payload_part = api_key.split(".", 2)[1]
        payload_part += "=" * (-len(payload_part) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(payload_part).decode("utf-8")
        )
        return payload.get("role") == "service_role"
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


class SupabaseClient:
    def __init__(
        self,
        base_url=SUPABASE_URL,
        api_key=SUPABASE_API_KEY,
        admin_api_key=SUPABASE_ADMIN_API_KEY,
    ):
        self.base_url = str(base_url).rstrip("/")
        self.api_key = str(api_key).strip()
        self.admin_api_key = str(admin_api_key).strip()
        self.session = requests.Session()

    def _headers(self, extra=None, *, admin=False):
        api_key = self.admin_api_key if admin else self.api_key
        if not api_key:
            raise SupabaseError(
                "Supabase admin API key is not configured. Set "
                "CLOUDPLAYER_SUPABASE_ADMIN_KEY."
                if admin
                else "Supabase API key is not configured. Set "
                "CLOUDPLAYER_SUPABASE_KEY to the project's anon or "
                "publishable key."
            )
        privileged = _is_privileged_key(api_key)
        if admin and not privileged:
            raise SupabaseError(
                "Supabase deletion requires a secret/service-role key."
            )
        if not admin and privileged:
            raise SupabaseError(
                "Refusing to use a Supabase secret/service-role key in a "
                "desktop application. Configure a publishable or anon key."
            )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "apikey": api_key,
        }
        if api_key.count(".") == 2:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method,
        table,
        *,
        params=None,
        payload=None,
        extra_headers=None,
        admin=False,
    ):
        try:
            response = self.session.request(
                method,
                f"{self.base_url}/rest/v1/{table}",
                params=params,
                json=payload,
                headers=self._headers(extra_headers, admin=admin),
                timeout=25,
            )
        except requests.RequestException as exc:
            raise SupabaseError(f"Could not reach Supabase: {exc}") from exc
        if not response.ok:
            message = _response_message(response)
            if response.status_code in (401, 403):
                message = (
                    f"Supabase access denied: {message}. Check the API key "
                    "and table RLS policies."
                )
            raise SupabaseError(message)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SupabaseError("Supabase returned invalid JSON.") from exc

    def _all_rows(self, table, params, *, admin=False):
        rows = []
        page_size = 1000
        start = 0
        while True:
            page = self._request(
                "GET",
                table,
                params=params,
                extra_headers={
                    "Range": f"{start}-{start + page_size - 1}"
                },
                admin=admin,
            )
            if not isinstance(page, list):
                raise SupabaseError("Unexpected response from Supabase.")
            rows.extend(item for item in page if isinstance(item, dict))
            if len(page) < page_size:
                return rows
            start += page_size

    @staticmethod
    def _validate_credentials(username, password, *, sign_up=False):
        username = str(username).strip()
        password = str(password)
        if len(username) < 3 or len(username) > 64:
            raise SupabaseError("Username must contain 3 to 64 characters.")
        if any(ord(char) < 32 for char in username):
            raise SupabaseError("Username contains unsupported characters.")
        if not re.fullmatch(r"[\w.-]+", username, flags=re.UNICODE):
            raise SupabaseError(
                "Username may contain letters, numbers, dots, dashes, and underscores."
            )
        if not password:
            raise SupabaseError("Enter your password.")
        if sign_up and len(password) < 6:
            raise SupabaseError("Password must contain at least 6 characters.")
        return username, password

    def login(self, username, password):
        username, password = self._validate_credentials(username, password)
        rows = self._request(
            "GET",
            "users",
            params={
                "select": "id,username,password_hash",
                "username": f"eq.{username}",
                "limit": "1",
            },
        )
        if not rows or not verify_password(
            password, rows[0].get("password_hash")
        ):
            raise SupabaseError("Invalid username or password.")
        return {"id": str(rows[0]["id"]), "username": rows[0]["username"]}

    def sign_up(self, username, password):
        username, password = self._validate_credentials(
            username, password, sign_up=True
        )
        existing = self._request(
            "GET",
            "users",
            params={
                "select": "id",
                "username": f"eq.{username}",
                "limit": "1",
            },
        )
        if existing:
            raise SupabaseError("This username is already taken.")
        try:
            rows = self._request(
                "POST",
                "users",
                params={"select": "id,username"},
                payload={
                    "username": username,
                    "password_hash": hash_password(password),
                },
                extra_headers={"Prefer": "return=representation"},
            )
        except SupabaseError as exc:
            if "unique" in str(exc).casefold() or "duplicate" in str(exc).casefold():
                raise SupabaseError("This username is already taken.") from exc
            raise
        if not rows:
            raise SupabaseError("Account was created but could not be loaded.")
        return {"id": str(rows[0]["id"]), "username": rows[0]["username"]}

    def synchronize(self, user_id, tracks):
        clean_tracks = [dict(track) for track in tracks if track.get("url")]
        if not clean_tracks:
            return {"inserted": 0, "already_synced": 0}
        playlist_name = str(clean_tracks[0].get("playlist_name") or "Playlist")
        existing = self._all_rows(
            "user_links",
            {
                "select": "url",
                "user_id": f"eq.{user_id}",
                "playlist_name": f"eq.{playlist_name}",
                "order": "id.asc",
            },
        )
        known_urls = {str(row.get("url") or "") for row in existing}
        pending = []
        seen = set(known_urls)
        for track in clean_tracks:
            url = str(track.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            pending.append(
                {
                    "user_id": str(user_id),
                    "url": url,
                    "artist": str(
                        track.get("artist") or "Unknown Artist"
                    )[:500],
                    "song_title": str(
                        track.get("song_title") or "Unknown Title"
                    )[:500],
                    "duration": track.get("duration"),
                    "playlist_name": str(
                        track.get("playlist_name") or playlist_name
                    )[:500],
                }
            )
        for offset in range(0, len(pending), 200):
            self._request(
                "POST",
                "user_links",
                payload=pending[offset : offset + 200],
                extra_headers={"Prefer": "return=minimal"},
            )
        synchronized_links = self.load_links(user_id)
        return {
            "inserted": len(pending),
            "already_synced": len(clean_tracks) - len(pending),
            "total_synced": len(synchronized_links),
            "links": synchronized_links,
        }

    def load_links(self, user_id, *, admin=False):
        return self._all_rows(
            "user_links",
            {
                "select": (
                    "id,url,artist,song_title,duration,"
                    "playlist_name"
                ),
                "user_id": f"eq.{user_id}",
                "order": "id.asc",
            },
            admin=admin,
        )

    def unsynchronize(self, user_id, link_ids):
        clean_ids = []
        seen = set()
        for value in link_ids or []:
            raw_id = str(value or "").strip()
            if not raw_id:
                continue
            if raw_id.isdecimal():
                link_id = str(int(raw_id))
                if link_id == "0":
                    continue
            else:
                try:
                    link_id = str(uuid.UUID(raw_id))
                except (ValueError, AttributeError):
                    continue
            if link_id not in seen:
                seen.add(link_id)
                clean_ids.append(link_id)
        for offset in range(0, len(clean_ids), 100):
            batch = clean_ids[offset : offset + 100]
            self._request(
                "DELETE",
                "user_links",
                params={
                    "user_id": f"eq.{user_id}",
                    "id": f"in.({','.join(batch)})",
                },
                extra_headers={"Prefer": "return=minimal"},
                admin=True,
            )
        return self.load_links(user_id, admin=True)

    def delete_account(self, user_id):
        user_id = str(user_id or "").strip()
        if not user_id:
            raise SupabaseError("Account ID is missing.")
        self._request(
            "DELETE",
            "user_links",
            params={"user_id": f"eq.{user_id}"},
            extra_headers={"Prefer": "return=minimal"},
            admin=True,
        )
        remaining_links = self._request(
            "GET",
            "user_links",
            params={
                "select": "id",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
            admin=True,
        )
        if remaining_links:
            raise SupabaseError(
                "Supabase did not delete the synchronized tracks."
            )
        self._request(
            "DELETE",
            "users",
            params={"id": f"eq.{user_id}"},
            extra_headers={"Prefer": "return=minimal"},
            admin=True,
        )
        remaining = self._request(
            "GET",
            "users",
            params={
                "select": "id",
                "id": f"eq.{user_id}",
                "limit": "1",
            },
            admin=True,
        )
        if remaining:
            raise SupabaseError(
                "Supabase did not delete the account. Check the users "
                "DELETE policy."
            )
        return True

class CloudRequestWorker(QThread):
    completed = Signal(bool, object)

    def __init__(self, operation, *arguments, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.arguments = arguments

    def run(self):
        try:
            client = SupabaseClient()
            function = getattr(client, self.operation)
            self.completed.emit(True, function(*self.arguments))
        except Exception as exc:
            self.completed.emit(False, str(exc)[:800])


class AccountAuthWorker(QThread):
    completed = Signal(bool, object)

    def __init__(
        self, operation, username, password, captcha_token, parent=None
    ):
        super().__init__(parent)
        self.operation = operation
        self.username = username
        self.password = password
        self.captcha_token = captcha_token

    def run(self):
        try:
            verify_turnstile(self.captcha_token)
            client = SupabaseClient()
            function = getattr(client, self.operation)
            self.completed.emit(
                True,
                function(self.username, self.password),
            )
        except Exception as exc:
            self.completed.emit(False, str(exc)[:800])


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self._busy = False
        self.captcha_token = ""
        self.captcha_server = LocalCaptchaServer()
        self.authenticated_user = None
        self.setWindowTitle("Account")
        self.setMinimumWidth(390)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(12)
        title = QLabel("Login or Sign Up")
        title.setStyleSheet("font-size:22px;font-weight:700")
        subtitle = QLabel("Sign in to synchronize playlists between devices.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px")
        self.username = QLineEdit()
        self.username.setPlaceholderText("Username")
        self.username.setMaxLength(64)
        self.password = QLineEdit()
        self.password.setPlaceholderText("Password")
        self.password.setMaxLength(256)
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(lambda: self._start("login"))

        self.captcha_view = QWebEngineView()
        self.captcha_view.setFixedHeight(78)
        self.captcha_view.setContextMenuPolicy(Qt.NoContextMenu)
        self.captcha_view.setStyleSheet(
            f"background:{BG_COLOR};border:none"
        )
        self.captcha_page = CaptchaPage(self.captcha_view)
        self.captcha_page.setBackgroundColor(QColor(BG_COLOR))
        self.captcha_view.setPage(self.captcha_page)
        self.captcha_page.token_ready.connect(self._captcha_verified)
        self.captcha_page.token_failed.connect(self._captcha_failed)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px")
        buttons = QHBoxLayout()
        self.login_button = QPushButton("Login")
        self.signup_button = QPushButton("Sign Up")
        self.login_button.clicked.connect(lambda: self._start("login"))
        self.signup_button.clicked.connect(lambda: self._start("sign_up"))
        buttons.addWidget(self.login_button)
        buttons.addWidget(self.signup_button)

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addSpacing(4)
        root.addWidget(self.username)
        root.addWidget(self.password)
        root.addSpacing(2)
        root.addWidget(self.captcha_view)
        root.addWidget(self.status)
        root.addLayout(buttons)

        self.finished.connect(self._stop_captcha_server)
        self._update_auth_buttons()
        try:
            self.captcha_view.setUrl(self.captcha_server.start())
            self.status.setText("Complete the verification to continue.")
        except Exception as exc:
            self.captcha_view.hide()
            self.status.setStyleSheet("color:#ef9a9a;font-size:12px")
            self.status.setText(str(exc))

    def _update_auth_buttons(self):
        enabled = not self._busy and bool(self.captcha_token)
        self.login_button.setEnabled(enabled)
        self.signup_button.setEnabled(enabled)

    def _captcha_verified(self, token):
        self.captcha_token = str(token or "").strip()
        if not self.captcha_token:
            self._captcha_failed("Verification did not return a token.")
            return
        self.status.setStyleSheet("color:#81c784;font-size:12px")
        self.status.setText("Verification complete.")
        self._update_auth_buttons()

    def _captcha_failed(self, message):
        self.captcha_token = ""
        self.status.setStyleSheet("color:#ef9a9a;font-size:12px")
        self.status.setText(str(message))
        self._update_auth_buttons()

    def _reset_captcha(self, error_message=None):
        self.captcha_token = ""
        self.captcha_view.reload()
        if error_message:
            self.status.setStyleSheet("color:#ef9a9a;font-size:12px")
            self.status.setText(
                f"{error_message}\nComplete the verification again."
            )
        else:
            self.status.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px")
            self.status.setText("Complete the verification again to continue.")
        self._update_auth_buttons()

    def _stop_captcha_server(self, _result=None):
        self.captcha_server.stop()

    def _set_busy(self, busy):
        self._busy = bool(busy)
        self.username.setEnabled(not busy)
        self.password.setEnabled(not busy)
        self.captcha_view.setEnabled(not busy)
        self._update_auth_buttons()
        if busy:
            self.status.setStyleSheet(f"color:{TEXT_MUTED};font-size:12px")
            self.status.setText("Verifying and connecting to Supabase...")

    def _start(self, operation):
        if self.worker and self.worker.isRunning():
            return
        if not self.captcha_token:
            self._captcha_failed("Complete the verification first.")
            return
        captcha_token = self.captcha_token
        self.captcha_token = ""
        self._set_busy(True)
        worker = AccountAuthWorker(
            operation,
            self.username.text(),
            self.password.text(),
            captcha_token,
            parent=self,
        )
        self.worker = worker
        worker.completed.connect(self._completed)
        worker.finished.connect(
            lambda current=worker: self._worker_finished(current)
        )
        worker.start()

    def _worker_finished(self, worker):
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()

    def _completed(self, ok, result):
        self._set_busy(False)
        if not ok:
            self.password.selectAll()
            self.password.setFocus()
            self._reset_captcha(str(result))
            return
        if self.worker and self.worker.isRunning():
            self.worker.wait(1000)
        self.authenticated_user = dict(result)
        self.accept()

    def reject(self):
        if self.worker and self.worker.isRunning():
            return
        super().reject()


class AccountPanel(QWidget):
    login_requested = Signal()
    synchronize_requested = Signal()
    load_requested = Signal()
    logout_requested = Signal()
    unsync_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.song_count = 0
        self._busy = False
        self.setMinimumWidth(260)
        self.setObjectName("accountPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet(
            f"#accountPanel{{background:{PANEL_BG};border:1px solid "
            f"{BUTTON_BORDER};border-radius:4px}}"
            "#accountStats{background:transparent;border:none}"
            "QListWidget#syncedSongsList{background:transparent;border:none;"
            "outline:0;padding:0}"
            f"QListWidget#syncedSongsList::item{{background:transparent;"
            f"color:{TEXT_COLOR};border:none;padding:6px 2px}}"
            f"QListWidget#syncedSongsList::item:selected{{background:"
            f"{BUTTON_BG};color:{TEXT_COLOR};border-radius:3px}}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 20)
        root.setSpacing(12)
        self.user_label = QLabel("")
        self.user_label.setWordWrap(True)
        self.user_label.setStyleSheet(
            f"color:{TEXT_COLOR};font-size:24px;font-weight:700;"
            "background:transparent;border:none"
        )

        self.stats_widget = QWidget()
        self.stats_widget.setObjectName("accountStats")
        self.stats_widget.setAttribute(Qt.WA_StyledBackground, True)
        stats_layout = QVBoxLayout(self.stats_widget)
        stats_layout.setContentsMargins(0, 8, 0, 8)
        stats_layout.setSpacing(2)
        count_row = QHBoxLayout()
        count_row.setContentsMargins(0, 0, 0, 0)
        count_row.setSpacing(8)
        self.song_count_label = QLabel("0")
        self.song_count_label.setStyleSheet(
            f"color:{TEXT_COLOR};font-size:28px;font-weight:700;"
            "background:transparent;border:none"
        )
        synchronized_label = QLabel("Synchronized")
        synchronized_label.setStyleSheet(
            f"color:{TEXT_COLOR};font-size:28px;font-weight:700;"
            "background:transparent;border:none"
        )
        songs_label = QLabel("Songs")
        songs_label.setStyleSheet(
            f"color:{TEXT_COLOR};font-size:18px;font-weight:700;"
            "background:transparent;border:none"
        )
        count_row.addWidget(self.song_count_label)
        count_row.addWidget(synchronized_label)
        count_row.addStretch()
        stats_layout.addLayout(count_row)
        stats_layout.addWidget(songs_label)

        self.login_button = QPushButton("Login / Sign Up")
        self.login_button.setMinimumHeight(44)
        self.sync_button = QPushButton("Synchronize")
        self.load_button = QPushButton("Load")
        self.logout_button = QPushButton("Log Out")
        self.songs_list = QListWidget()
        self.songs_list.setObjectName("syncedSongsList")
        self.songs_list.setMinimumHeight(110)
        self.songs_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.songs_list.setFocusPolicy(Qt.StrongFocus)
        self.songs_list.setSelectionMode(
            QAbstractItemView.ExtendedSelection
        )
        self.songs_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.songs_list.customContextMenuRequested.connect(
            self._songs_menu
        )
        for button in (self.sync_button, self.load_button):
            button.setMinimumHeight(44)
        self.logout_button.setStyleSheet(
            f"QPushButton{{background:{BUTTON_BG};color:{TEXT_MUTED};border:1px "
            f"solid {BUTTON_BORDER}}}QPushButton:hover{{background:{BUTTON_HOVER};"
            f"color:{TEXT_COLOR};border-color:{ACCENT_COLOR}}}"
        )
        self.sync_button.clicked.connect(
            lambda _checked=False: self.synchronize_requested.emit()
        )
        self.load_button.clicked.connect(
            lambda _checked=False: self.load_requested.emit()
        )
        self.logout_button.clicked.connect(
            lambda _checked=False: self.logout_requested.emit()
        )
        self.login_button.clicked.connect(
            lambda _checked=False: self.login_requested.emit()
        )
        root.addWidget(self.user_label)
        root.addWidget(self.stats_widget)
        root.addSpacing(4)
        root.addWidget(self.login_button)
        root.addWidget(self.sync_button)
        root.addWidget(self.load_button)
        root.addWidget(self.songs_list, 1)
        root.addWidget(self.logout_button)
        self.set_logged_out()

    def set_user(self, user):
        username = str((user or {}).get("username") or "")
        self._set_logged_in_style()
        self.user_label.setText(username)
        self.login_button.hide()
        self.stats_widget.show()
        self.sync_button.show()
        self.load_button.show()
        self.songs_list.show()
        self.logout_button.show()

    def set_logged_out(self):
        self.user_label.setText("Not signed in")
        self.user_label.setStyleSheet(
            f"color:{TEXT_MUTED};font-size:16px;font-weight:700;"
            "background:transparent;border:none"
        )
        self.set_song_count(None)
        self.set_tracks([])
        self.stats_widget.hide()
        self.sync_button.hide()
        self.load_button.hide()
        self.songs_list.hide()
        self.logout_button.hide()
        self.login_button.show()

    def _set_logged_in_style(self):
        self.user_label.setStyleSheet(
            f"color:{TEXT_COLOR};font-size:24px;font-weight:700;"
            "background:transparent;border:none"
        )

    def set_song_count(self, count):
        if count is None:
            self.song_count = 0
            self.song_count_label.setText("0")
            return
        self.song_count = max(0, int(count))
        self.song_count_label.setText(str(self.song_count))

    def set_tracks(self, rows):
        self.songs_list.clear()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get("song_title") or "Unknown Title").strip()
            artist = str(row.get("artist") or "Unknown Artist").strip()
            item = QListWidgetItem(f"{title} - {artist}")
            item.setData(Qt.UserRole, dict(row))
            self.songs_list.addItem(item)

    def _songs_menu(self, position):
        item = self.songs_list.itemAt(position)
        if item is None or self._busy:
            return
        if not item.isSelected():
            self.songs_list.clearSelection()
            item.setSelected(True)
        rows = [
            selected.data(Qt.UserRole)
            for selected in self.songs_list.selectedItems()
            if isinstance(selected.data(Qt.UserRole), dict)
            and selected.data(Qt.UserRole).get("id") is not None
        ]
        if not rows:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{PANEL_BG};color:{TEXT_COLOR};border:1px solid "
            f"{BUTTON_BORDER};padding:4px;font-size:14px;font-weight:700}}"
            "QMenu::item{padding:6px 18px;border-radius:3px}"
            f"QMenu::item:selected{{background:{ACCENT_COLOR};color:#ffffff}}"
        )
        unsync = menu.addAction("Unsync")
        chosen = menu.exec(
            self.songs_list.viewport().mapToGlobal(position)
        )
        if chosen is unsync:
            self.unsync_requested.emit(rows)

    def set_busy(self, busy):
        self._busy = bool(busy)
        self.sync_button.setEnabled(not busy)
        self.load_button.setEnabled(not busy)
        self.logout_button.setEnabled(not busy)
        self.songs_list.setEnabled(not busy)


def duration_seconds(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return max(0, round(value))
    text = str(value).strip()
    try:
        return max(0, round(float(text)))
    except ValueError:
        pass
    parts = text.split(":")
    if not parts or len(parts) > 3:
        return None
    try:
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
        return max(0, round(total))
    except ValueError:
        return None


def collect_playlist_tracks(playlist_name):
    songs_path = PLAYLISTS_PATH / str(playlist_name) / "songs"
    tracks = []
    without_url = 0
    if not songs_path.is_dir():
        return tracks, without_url
    for audio_path in sorted(
        songs_path.iterdir(), key=lambda path: path.name.casefold()
    ):
        if (
            not audio_path.is_file()
            or audio_path.suffix.lower() not in AUDIO_EXTENSIONS
        ):
            continue
        sidecar = {}
        try:
            metadata_path = audio_path.with_suffix(".json")
            if metadata_path.is_file():
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    sidecar = payload
        except (OSError, ValueError):
            pass
        url = str(
            sidecar.get("source_url") or sidecar.get("download_url") or ""
        ).strip()
        if not url.startswith(("http://", "https://")):
            without_url += 1
            continue
        fallback_artist = "Unknown Artist"
        fallback_title = audio_path.stem
        if " - " in audio_path.stem:
            fallback_artist, fallback_title = audio_path.stem.split(" - ", 1)
        tracks.append(
            {
                "url": url,
                "artist": sidecar.get("artist") or fallback_artist,
                "song_title": sidecar.get("title") or fallback_title,
                "duration": duration_seconds(
                    sidecar.get("duration_seconds")
                    or sidecar.get("duration")
                ),
                "playlist_name": str(playlist_name),
            }
        )
    return tracks, without_url


def local_playlist_urls(playlist_name):
    tracks, _without_url = collect_playlist_tracks(playlist_name)
    return {str(track["url"]) for track in tracks}
