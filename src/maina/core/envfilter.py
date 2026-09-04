"""Environment-variable allowlisting shared by the runner and the installer.

``env_allow`` on ``Tool.run`` must cover *both* the tool subprocess and the
install-phase subprocesses (npm, mise): a secret in ``os.environ`` should not
reach a third-party ``npm install`` lifecycle script any more than the tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# Always forwarded even when ``env_allow`` is set: without these a subprocess
# cannot find its own binary, its shared libraries, CA bundle, proxy, runtime
# data dir, temp dir, or locale. Matched case-insensitively so Windows spellings
# (``SystemRoot``, ``Path``, ``PathExt``) survive too.
ESSENTIAL_ENV = frozenset(
    {
        "PATH",
        "MISE_DATA_DIR",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "TEMP",
        "TMP",
        # Windows: shell, system dir, executable-extension list.
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        # Shared-library search paths.
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        # TLS trust stores.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        # Proxy configuration (upper- and lower-case are both conventional).
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
    }
)


def filter_env(env: dict[str, str], env_allow: Iterable[str] | None) -> dict[str, str]:
    """Return *env* unchanged, or trimmed to essentials + *env_allow* keys.

    Matching is case-insensitive so a Windows ``SystemRoot`` / ``Path`` is not
    dropped by an all-caps allowlist.
    """
    if env_allow is None:
        return env
    keep = {key.lower() for key in ESSENTIAL_ENV} | {key.lower() for key in env_allow}
    return {key: value for key, value in env.items() if key.lower() in keep}
