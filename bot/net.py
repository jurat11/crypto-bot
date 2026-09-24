"""TLS settings shared by every HTTPS and websocket call.

Python from python.org on macOS ships without trusted root certificates until
"Install Certificates.command" is run, so every HTTPS call fails with
CERTIFICATE_VERIFY_FAILED. When the system has no certificate store, fall back
to the certifi bundle (installed by requirements.txt). Verification is never
turned off.
"""
import os
import ssl

_ctx = None


def _system_has_certs():
    paths = ssl.get_default_verify_paths()
    if paths.cafile and os.path.exists(paths.cafile):
        return True
    return bool(paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath))


def ssl_context():
    global _ctx
    if _ctx is None:
        ctx = ssl.create_default_context()
        if not _system_has_certs() and not os.getenv("SSL_CERT_FILE"):
            try:
                import certifi
                ctx.load_verify_locations(certifi.where())
            except ImportError:
                pass
        _ctx = ctx
    return _ctx
