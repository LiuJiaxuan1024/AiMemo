#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
NO_PATH_UPDATE=0
QUIET=0

for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=1
      ;;
    --no-path-update)
      NO_PATH_UPDATE=1
      ;;
    --quiet)
      QUIET=1
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/register-aimemo.sh [--dry-run] [--no-path-update] [--quiet]

Registers the global aimemo command for macOS/Linux by writing a user-local
wrapper to ~/.local/bin/aimemo and, unless disabled, adding that directory to
the user's shell startup file.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AIMEMO_SCRIPT="$SCRIPT_DIR/aimemo.sh"

if [[ ! -f "$AIMEMO_SCRIPT" ]]; then
  echo "Expected command router not found: $AIMEMO_SCRIPT" >&2
  exit 1
fi

resolve_home() {
  if [[ -n "${HOME:-}" && "$HOME" != "/" ]]; then
    printf '%s\n' "$HOME"
    return 0
  fi
  getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6
}

HOME_DIR="$(resolve_home)"
if [[ -z "$HOME_DIR" ]]; then
  echo "Could not resolve HOME." >&2
  exit 1
fi

BIN_DIR="${AIMEMO_BIN_DIR:-$HOME_DIR/.local/bin}"
WRAPPER_PATH="$BIN_DIR/aimemo"
PATH_BLOCK_BEGIN="# >>> AiMemo PATH >>>"
PATH_BLOCK_END="# <<< AiMemo PATH <<<"

choose_profile_files() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"
  case "$shell_name" in
    zsh)
      printf '%s\n' "$HOME_DIR/.zshrc"
      printf '%s\n' "$HOME_DIR/.zprofile"
      ;;
    bash)
      printf '%s\n' "$HOME_DIR/.bashrc"
      printf '%s\n' "$HOME_DIR/.profile"
      ;;
    *)
      printf '%s\n' "$HOME_DIR/.profile"
      ;;
  esac
}

path_list_contains() {
  local target="$1"
  local entry
  IFS=':' read -r -a entries <<< "${PATH:-}"
  for entry in "${entries[@]}"; do
    [[ "$entry" == "$target" ]] && return 0
  done
  return 1
}

write_path_block() {
  local profile_path="$1"
  local block
  block="$(cat <<EOF
$PATH_BLOCK_BEGIN
export PATH="$BIN_DIR:\$PATH"
$PATH_BLOCK_END
EOF
)"

  if [[ -f "$profile_path" ]] && grep -Fq "$PATH_BLOCK_BEGIN" "$profile_path"; then
    [[ "$QUIET" -eq 1 ]] || echo "[OK] PATH block already exists in $profile_path"
    return 0
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    [[ "$QUIET" -eq 1 ]] || echo "[dry-run] Would update shell profile: $profile_path"
    return 0
  fi

  mkdir -p "$(dirname "$profile_path")"
  {
    [[ -f "$profile_path" ]] && tail -c 1 "$profile_path" 2>/dev/null | grep -q . && printf '\n'
    printf '%s\n' "$block"
  } >> "$profile_path"
  [[ "$QUIET" -eq 1 ]] || echo "[OK] Updated shell profile: $profile_path"
}

WRAPPER_CONTENT="$(cat <<EOF
#!/usr/bin/env bash
exec "$AIMEMO_SCRIPT" "\$@"
EOF
)"

if [[ "$QUIET" -ne 1 ]]; then
  echo "AiMemo command registration"
  echo "Repository: $REPO_ROOT"
  echo "Command router: $AIMEMO_SCRIPT"
  echo "Global wrapper: $WRAPPER_PATH"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  [[ "$QUIET" -eq 1 ]] || echo "[dry-run] Would create directory: $BIN_DIR"
  [[ "$QUIET" -eq 1 ]] || echo "[dry-run] Would write wrapper: $WRAPPER_PATH"
else
  mkdir -p "$BIN_DIR"
  printf '%s\n' "$WRAPPER_CONTENT" > "$WRAPPER_PATH"
  chmod +x "$WRAPPER_PATH"
  [[ "$QUIET" -eq 1 ]] || echo "[OK] Wrote $WRAPPER_PATH"
fi

if [[ "$NO_PATH_UPDATE" -eq 1 ]]; then
  [[ "$QUIET" -eq 1 ]] || echo "[SKIP] PATH update skipped by --no-path-update."
elif path_list_contains "$BIN_DIR"; then
  [[ "$QUIET" -eq 1 ]] || echo "[OK] AiMemo bin directory is already in PATH."
else
  while IFS= read -r profile_file; do
    write_path_block "$profile_file"
  done < <(choose_profile_files)
  [[ "$QUIET" -eq 1 ]] || echo "Restart terminals that were already open before running this registration."
fi

if [[ "$QUIET" -ne 1 ]]; then
  echo
  echo "Try:"
  echo "  aimemo doctor"
  echo "  aimemo start --no-desktop"
fi
