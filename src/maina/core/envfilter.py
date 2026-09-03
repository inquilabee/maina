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
# cannot find its own binary, its runtime data dir, or locale.
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
        "SYSTEMROOT",
    }
)


def filter_env(env: dict[str, str], env_allow: Iterable[str] | None) -> dict[str, str]:
    """Return *env* unchanged, or trimmed to essentials + *env_allow* keys."""
    if env_allow is None:
        return env
    keep = ESSENTIAL_ENV | set(env_allow)
    return {key: value for key, value in env.items() if key in keep}
