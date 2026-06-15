#!/usr/bin/env python3
"""
sms_relay.py — Grafana webhook → Twilio SMS relay.

Binds localhost only.  All config read from environment (loaded via the
systemd EnvironmentFile).  Auth: the caller must present the shared secret
in the X-Relay-Secret header; any other request is rejected with 403.
Constant-time comparison prevents timing-based secret oracle.

Logging: structured key=value lines to stderr (captured by journald).
The Twilio Auth Token is never written to any log.

Exit codes:
  0 — clean shutdown (SIGTERM / KeyboardInterrupt)
  1 — startup error (missing env, weak secret, bad bind address, etc.)
"""

import hmac
import http.server
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from http import HTTPStatus
from socketserver import ThreadingMixIn

# ── logging setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s level=%(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("sms_relay")

# ── configuration ──────────────────────────────────────────────────────────────

_REQUIRED_ENV = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM",
    "SMS_RECIPIENTS",
    "WEBHOOK_SHARED_SECRET",
)

# Minimum acceptable secret length — anything shorter is insecure.
_MIN_SECRET_LEN = 32


def _parse_port(value: str) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        log.error(
            "action=startup error=invalid_port value=%r "
            "reason=RELAY_PORT_must_be_an_integer",
            value,
        )
        sys.exit(1)


def _parse_timeout(value: str) -> int:
    try:
        t = int(value)
        if t <= 0:
            raise ValueError("must be positive")
        return t
    except (ValueError, TypeError):
        log.error(
            "action=startup error=invalid_timeout value=%r "
            "reason=RELAY_REQUEST_TIMEOUT_must_be_a_positive_integer",
            value,
        )
        sys.exit(1)


def _load_config() -> dict:
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k, "").strip()]
    if missing:
        log.error("action=startup error=missing_env vars=%s", ",".join(missing))
        sys.exit(1)

    secret = os.environ["WEBHOOK_SHARED_SECRET"]
    if len(secret.strip()) < _MIN_SECRET_LEN:
        log.error(
            "action=startup error=weak_secret "
            "reason=WEBHOOK_SHARED_SECRET_must_be_at_least_%d_chars "
            "got=%d",
            _MIN_SECRET_LEN,
            len(secret),
        )
        sys.exit(1)

    recipients_raw = os.environ["SMS_RECIPIENTS"]
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    if not recipients:
        log.error("action=startup error=empty_SMS_RECIPIENTS")
        sys.exit(1)

    return {
        "account_sid": os.environ["TWILIO_ACCOUNT_SID"],
        # auth_token kept in a plain string; never emitted to logs
        "auth_token": os.environ["TWILIO_AUTH_TOKEN"],
        "from_number": os.environ["TWILIO_FROM"],
        "recipients": recipients,
        "shared_secret": secret,
        "bind_host": os.environ.get("RELAY_BIND", "127.0.0.1"),
        "bind_port": _parse_port(os.environ.get("RELAY_PORT", "9876")),
        "request_timeout": _parse_timeout(
            os.environ.get("RELAY_REQUEST_TIMEOUT", "10")
        ),
    }


# ── Twilio SMS ─────────────────────────────────────────────────────────────────

_TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
_TIMEOUT_SEC = 10
_MAX_ATTEMPTS = 2
_RETRY_BACKOFF_SEC = 2


def _twilio_basic_auth(account_sid: str, auth_token: str) -> str:
    credentials = f"{account_sid}:{auth_token}".encode()
    return "Basic " + b64encode(credentials).decode()


def _send_sms(cfg: dict, to: str, body: str) -> None:
    """Send one SMS via Twilio REST API.  Raises on failure after retries."""
    url = _TWILIO_API.format(sid=cfg["account_sid"])
    payload = urllib.parse.urlencode(
        {"To": to, "From": cfg["from_number"], "Body": body}
    ).encode()
    headers = {
        "Authorization": _twilio_basic_auth(cfg["account_sid"], cfg["auth_token"]),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
                raw = resp.read(4096).decode(errors="replace")
                # Log the Twilio message SID for audit trail — never log the
                # full response body, which could contain sensitive metadata.
                try:
                    sid = json.loads(raw).get("sid", "unknown")
                except (json.JSONDecodeError, AttributeError):
                    sid = "unknown"
                log.info(
                    "action=sms_sent attempt=%d to=%s status=%d sid=%s",
                    attempt,
                    to,
                    resp.status,
                    sid,
                )
                return
        except urllib.error.HTTPError as exc:
            body_text = exc.read(512).decode(errors="replace")
            log.warning(
                "action=sms_failed attempt=%d to=%s http_status=%d detail=%r",
                attempt,
                to,
                exc.code,
                body_text,
            )
            last_error = exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.warning(
                "action=sms_failed attempt=%d to=%s error=%r", attempt, to, str(exc)
            )
            last_error = exc

        if attempt < _MAX_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF_SEC)

    raise RuntimeError(f"SMS delivery failed after {_MAX_ATTEMPTS} attempts: {last_error}")


# ── alert body composition ─────────────────────────────────────────────────────

def _compose_sms_body(payload: dict) -> str:
    """Extract alert info from a Grafana webhook payload and build the SMS text."""
    title = payload.get("title", "")
    state = payload.get("state", "alerting")
    message = payload.get("message", "")
    alerts = payload.get("alerts", [])

    if title:
        subject = title
    elif alerts:
        first = alerts[0]
        subject = first.get("labels", {}).get("alertname", "Alert")
    else:
        subject = "Alert"

    # Keep SMS under 160 chars where possible; truncate gracefully.
    body_parts = [f"[monitrix] {state.upper()}: {subject}"]
    if message:
        body_parts.append(message[:80])
    for alert in alerts[:2]:
        name = alert.get("labels", {}).get("alertname", "")
        status = alert.get("status", "")
        if name:
            body_parts.append(f"  {name}: {status}")

    full = " | ".join(p for p in body_parts if p)
    return full[:400]


# ── HTTP handler ───────────────────────────────────────────────────────────────

# Maximum bytes accepted in a request body.  Requests exceeding this are
# rejected with 413 before the body is read to prevent unbounded allocation.
MAX_BODY = 64 * 1024


class _RelayHandler(http.server.BaseHTTPRequestHandler):
    cfg: dict  # injected by the server factory

    # StreamRequestHandler.setup() calls self.connection.settimeout(self.timeout)
    # before constructing rfile, so this deadline covers the request-line read,
    # header read, AND the Content-Length body read in do_POST.  If the client
    # stalls at any point, a TimeoutError is raised and handle_one_request()
    # closes the connection cleanly.  Set from cfg by the server factory.
    timeout: int  # seconds; overrides BaseHTTPRequestHandler default (None)

    # Suppress the Python version from the Server: response header so it
    # doesn't leak implementation details on every response, including 403s.
    def version_string(self) -> str:  # noqa: N802
        return "monitrix-relay"

    # Silence BaseHTTPRequestHandler's default log_message output;
    # we write structured logs ourselves.
    def log_message(self, fmt, *args):  # noqa: N802
        pass

    def log_request(self, code="-", size="-"):  # noqa: N802
        pass

    def do_POST(self):  # noqa: N802
        if self.path not in ("/alert", "/"):
            self._reply(HTTPStatus.NOT_FOUND, "not found")
            return

        secret = self.headers.get("X-Relay-Secret", "")
        # Constant-time compare prevents a timing oracle on the shared secret.
        if not secret or not hmac.compare_digest(secret, self.cfg["shared_secret"]):
            log.warning(
                "action=auth_rejected remote=%s path=%s", self.client_address[0], self.path
            )
            self._reply(HTTPStatus.FORBIDDEN, "forbidden")
            return

        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except (ValueError, TypeError):
            self._reply(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return

        if length < 0 or length > MAX_BODY:
            self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
            return

        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("action=parse_failed error=%r", str(exc))
            self._reply(HTTPStatus.BAD_REQUEST, "invalid json")
            return

        try:
            sms_body = _compose_sms_body(payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("action=compose_failed error=%r", str(exc))
            self._reply(HTTPStatus.BAD_REQUEST, "malformed payload")
            return

        errors = []
        for recipient in self.cfg["recipients"]:
            try:
                _send_sms(self.cfg, recipient, sms_body)
            except Exception as exc:  # noqa: BLE001
                log.error("action=sms_error recipient=%s error=%r", recipient, str(exc))
                errors.append(recipient)

        if errors:
            log.error("action=dispatch_partial failed_recipients=%s", ",".join(errors))
            self._reply(HTTPStatus.BAD_GATEWAY, f"failed: {','.join(errors)}")
        else:
            log.info(
                "action=dispatch_ok recipients=%d", len(self.cfg["recipients"])
            )
            self._reply(HTTPStatus.OK, "ok")

    def _reply(self, status: HTTPStatus, body: str) -> None:
        encoded = body.encode()
        self.send_response(status.value)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


# ── server factory ─────────────────────────────────────────────────────────────

class _RelayServer(ThreadingMixIn, http.server.HTTPServer):
    # Each inbound connection is handled in its own thread, so a slow or
    # stalled client cannot block delivery of the next alert.
    #
    # daemon_threads=True means a thread stuck on a timed-out slow-loris
    # connection will not prevent clean process exit on SIGTERM — the OS
    # reclaims it when the main thread exits rather than server_close()
    # joining forever.
    daemon_threads = True

    def __init__(self, cfg: dict):
        # Inject both cfg and the per-socket read deadline into the handler
        # class via a one-off subclass so handler instances share no mutable
        # globals and the timeout is fully determined at startup.
        handler = type(
            "Handler",
            (_RelayHandler,),
            {"cfg": cfg, "timeout": cfg["request_timeout"]},
        )
        super().__init__((cfg["bind_host"], cfg["bind_port"]), handler)


# ── entrypoint ─────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = _load_config()

    if cfg["bind_host"] not in ("127.0.0.1", "::1", "localhost"):
        # The design requires localhost-only binding; refuse to start on a
        # public interface.  This is a guard against misconfiguration.
        log.error(
            "action=startup error=unsafe_bind bind_host=%s "
            "reason=relay_must_bind_localhost_only",
            cfg["bind_host"],
        )
        sys.exit(1)

    log.info(
        "action=startup bind=%s port=%d recipients=%d request_timeout=%ds",
        cfg["bind_host"],
        cfg["bind_port"],
        len(cfg["recipients"]),
        cfg["request_timeout"],
    )

    server = _RelayServer(cfg)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("action=shutdown reason=interrupt")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
