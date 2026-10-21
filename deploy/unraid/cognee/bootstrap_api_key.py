"""Run Cognee migrations, then make sure the default user has our API key.

Cognee API keys are normally created through its API after a login. For a
single-user Unraid setup the key comes from ``COGNEE_API_KEY`` (shared with
the OpenJarvis container). If it is blank, a key is generated once, stored in
``/cognee-data/.api_key`` (mode 600) and printed once.

Keys this script created earlier (name ``unraid``) that no longer match are
removed, so changing ``COGNEE_API_KEY`` rotates the key.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

KEY_FILE = Path(os.environ.get("COGNEE_API_KEY_FILE", "/cognee-data/.api_key"))
KEY_NAME = "unraid"


def log(msg: str) -> None:
    print(f"[cognee-unraid] {msg}", file=sys.stderr, flush=True)


def resolve_key() -> str:
    key = os.environ.get("COGNEE_API_KEY", "").strip()
    if key:
        log("API key: taken from COGNEE_API_KEY")
        return key
    if KEY_FILE.is_file() and KEY_FILE.read_text().strip():
        log(f"API key: loaded from {KEY_FILE}")
        return KEY_FILE.read_text().strip()
    key = secrets.token_hex(32)
    old = os.umask(0o077)
    try:
        KEY_FILE.write_text(key + "\n")
    finally:
        os.umask(old)
    KEY_FILE.chmod(0o600)
    log("=" * 64)
    log(f"Generated Cognee API key (shown once, stored in {KEY_FILE}):")
    log(f"    {key}")
    log("Set the same value as COGNEE_API_KEY in the OpenJarvis container.")
    log("=" * 64)
    return key


async def main() -> int:
    from cognee.modules.migrations.startup import run_migrations

    failed = await run_migrations()
    if failed:
        log("data migrations failed for: " + ", ".join(failed))

    from cognee.infrastructure.databases.relational import get_relational_engine
    from cognee.modules.users.api_key.hash_api_key import prepare_api_key
    from cognee.modules.users.methods import get_default_user
    from cognee.modules.users.models.UserApiKey import UserApiKey
    from sqlalchemy import delete, select

    key = resolve_key()
    stored = prepare_api_key(key)
    user = await get_default_user()
    engine = get_relational_engine()
    async with engine.get_async_session() as session:
        found = (
            await session.execute(select(UserApiKey).filter_by(api_key=stored))
        ).scalar()
        stale = await session.execute(
            delete(UserApiKey).where(
                UserApiKey.user_id == user.id,
                UserApiKey.name == KEY_NAME,
                UserApiKey.api_key != stored,
            )
        )
        if found is None:
            session.add(
                UserApiKey(
                    user_id=user.id,
                    api_key=stored,
                    label=key[:8] + "****",
                    name=KEY_NAME,
                )
            )
        await session.commit()
    if stale.rowcount:
        log(f"removed {stale.rowcount} old unraid key(s)")
    state = "registered" if found is None else "already registered"
    log(f"API key {state} for {user.email}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
