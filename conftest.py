import base64
import hashlib
import os

os.environ.setdefault("DATABASE_URL", "postgres://parkshare@localhost/parkshare_test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use-only")
os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("ALLOWED_HOSTS", "*")


# Ensure PII_ENCRYPTION_KEY is a valid 32-byte url-safe base64-encoded Fernet key.
# When running tests the caller may pass a plain string (e.g. the CLI smoke-test
# command uses `PII_ENCRYPTION_KEY=test-pii-key-32chars-minimum!`).  Fernet
# requires exactly 32 raw bytes encoded as url-safe base64.  If the value already
# decodes to 32 bytes we leave it alone; otherwise we derive a deterministic 32-byte
# key from it via SHA-256 so Django/encrypted-model-fields can initialise.
def _ensure_fernet_key(raw: str) -> str:
    try:
        decoded = base64.urlsafe_b64decode(raw + "==")  # pad liberally
        if len(decoded) == 32:
            return raw  # already valid
    except Exception:
        pass
    # Derive a 32-byte key deterministically from the raw string
    derived = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(derived).decode()


_pii_key_raw = os.environ.get(
    "PII_ENCRYPTION_KEY", "dGVzdC1rZXktZG8tbm90LXVzZS1pbi1wcm9kISEhISE="
)
os.environ["PII_ENCRYPTION_KEY"] = _ensure_fernet_key(_pii_key_raw)
