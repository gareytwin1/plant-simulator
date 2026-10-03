"""A config edit must come with a VERSION bump (T18-5).

The fixture records the config version and a digest of every config document
under config/. When a config file changes the digest moves and this test fails:
bump config/VERSION as app/configversion.py describes (MAJOR if results can
differ, MINOR if additive, PATCH if no behaviour changes), then run
`PYTHONPATH=. python tests/test_config_version_guard.py` to record both.
"""

import hashlib
import json
from pathlib import Path

from app.configversion import CONFIG_VERSION_PATH, read_config_version

CONFIG_DIR = CONFIG_VERSION_PATH.parent
# The documents the app loads. An editor swap file, .DS_Store or backup copy
# dropped under config/ is not config and must not trip the guard.
CONFIG_SUFFIXES = {".yaml", ".yml", ".json"}
FIXTURE = Path(__file__).parent / "fixtures" / "config_version.json"


def config_digest() -> str:
    digest = hashlib.sha256()

    for path in sorted(CONFIG_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in CONFIG_SUFFIXES or path.name.startswith("."):
            continue

        digest.update(path.relative_to(CONFIG_DIR).as_posix().encode())
        digest.update(b"\0")
        # Line endings are normalised so a CRLF checkout hashes the same.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")

    return digest.hexdigest()


def test_config_files_match_the_recorded_version():
    recorded = json.loads(FIXTURE.read_text())
    current = {"version": str(read_config_version()), "digest": config_digest()}

    assert current == recorded, (
        "config/ changed without a version bump (or the fixture is stale). "
        "Bump config/VERSION per app/configversion.py, then run "
        "`PYTHONPATH=. python tests/test_config_version_guard.py` to re-record "
        "tests/fixtures/config_version.json."
    )


if __name__ == "__main__":
    FIXTURE.write_text(
        json.dumps({"version": str(read_config_version()), "digest": config_digest()}, indent=2) + "\n"
    )
