#!/bin/bash
# OpenJarvis on Unraid: first-start setup, then exec the server.
# Runs as UID 99 (nobody:users). Never overwrites an existing config or note.
set -euo pipefail

DATA="${OPENJARVIS_HOME:-/data}"
VAULT="${OPENJARVIS_VAULT:-/vault}"
TEMPLATE=/opt/openjarvis-unraid/vault-template
LIB=/opt/openjarvis-unraid/lib

# Unraid convention (same as "Docker Safe New Perms"): files 666, dirs 777,
# so notes stay editable over SMB and from Obsidian. Secrets are chmod 600
# explicitly and /data itself is kept 700.
umask 0000

log() { printf '[openjarvis-unraid] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Pass-through for one-off commands: docker exec / run IMAGE jarvis-backup, bash, ...
case "${1:-serve}" in
    jarvis-backup|bash|sh|python3|python|/*) exec "$@" ;;
esac

log "image commit ${OPENJARVIS_COMMIT:-unknown}; $(jarvis --version 2>/dev/null || echo 'jarvis --version failed')"
log "running as uid=$(id -u) gid=$(id -g), TZ=${TZ:-unset}"

# --- volumes -----------------------------------------------------------------
[ -d "$DATA" ] && [ -w "$DATA" ] || die "$DATA is not writable by uid $(id -u). On Unraid run: chown -R 99:100 /mnt/cache/appdata/openjarvis"
[ -d "$VAULT" ] || die "$VAULT is missing. Map it to your markdown share (for example /mnt/user/TheMachine)."
if ! grep -qs " $VAULT " /proc/self/mountinfo; then
    log "WARNING: $VAULT is not a mounted volume; notes would be lost with the container."
fi
[ -w "$VAULT" ] || die "$VAULT is not writable by uid $(id -u). On Unraid run: chown -R 99:100 /mnt/user/TheMachine"
chmod 700 "$DATA" 2>/dev/null || true
mkdir -p "$DATA/home" "$DATA/custom" "$DATA/logs"

# --- vault skeleton (only into an empty vault) --------------------------------
if [ -z "$(ls -A "$VAULT" 2>/dev/null)" ]; then
    log "empty vault: copying the folder skeleton into $VAULT"
    cp -R "$TEMPLATE"/. "$VAULT"/
    chmod -R a+rwX "$VAULT"
fi
# Memory files must exist even in a vault that was not empty.
for f in SOUL.md MEMORY.md USER.md; do
    [ -e "$VAULT/$f" ] || { cp "$TEMPLATE/$f" "$VAULT/$f"; chmod 666 "$VAULT/$f"; }
done
mkdir -p "$VAULT/personas" "$VAULT/_backups"

# --- memory files live in the vault -------------------------------------------
# memory_manage writes $OPENJARVIS_HOME/MEMORY.md whatever [memory_files] says,
# and named personas are read from $OPENJARVIS_HOME/personas/<name>/.
link_into_vault() {
    local name="$1" target="$VAULT/$1" link="$DATA/$1"
    if [ -L "$link" ]; then
        [ "$(readlink "$link")" = "$target" ] && return 0
        rm -f "$link"
    elif [ -e "$link" ]; then
        local keep="$link.local-$(date +%Y%m%d%H%M%S)"
        log "moving existing $link aside to $keep (the vault copy wins)"
        mv "$link" "$keep"
    fi
    ln -s "$target" "$link"
}
for f in SOUL.md MEMORY.md USER.md personas; do link_into_vault "$f"; done

# --- optional engine host -----------------------------------------------------
if [ -n "${OPENJARVIS_OLLAMA_HOST:-}" ]; then
    export OLLAMA_HOST="$OPENJARVIS_OLLAMA_HOST"
fi

# --- API key ------------------------------------------------------------------
KEY_FILE="$DATA/.api_key"
if [ -n "${OPENJARVIS_API_KEY:-}" ]; then
    log "API key: taken from OPENJARVIS_API_KEY"
elif [ -s "$KEY_FILE" ]; then
    OPENJARVIS_API_KEY="$(cat "$KEY_FILE")"
    log "API key: loaded from $KEY_FILE"
else
    OPENJARVIS_API_KEY="$(python3 -c 'from openjarvis.server.auth_middleware import generate_api_key as g; print(g())')"
    ( umask 0077; printf '%s\n' "$OPENJARVIS_API_KEY" > "$KEY_FILE" )
    chmod 600 "$KEY_FILE"
    log "================================================================"
    log "Generated API key (shown once, stored in $KEY_FILE):"
    log "    $OPENJARVIS_API_KEY"
    log "Paste it in the web UI Settings page, or send it as"
    log "    Authorization: Bearer <key>"
    log "================================================================"
fi
export OPENJARVIS_API_KEY

# --- config (first start only) ------------------------------------------------
if [ ! -e "$DATA/config.toml" ]; then
    log "no $DATA/config.toml: running unattended 'jarvis init --no-analytics'"
    if ! (cd "$DATA" && jarvis init --engine ollama --no-analytics --no-download --no-scan \
            </dev/null >"$DATA/logs/init.log" 2>&1); then
        log "jarvis init failed (see $DATA/logs/init.log); writing the config directly"
        rm -f "$DATA/config.toml"
    fi
    python3 "$LIB/configure.py" --first-start "$DATA/config.toml" "$VAULT"
    chmod 600 "$DATA/config.toml"
else
    python3 "$LIB/configure.py" --check "$DATA/config.toml" "$VAULT"
fi

log "starting: jarvis $*"
exec python3 "$LIB/launch.py" "$@"
