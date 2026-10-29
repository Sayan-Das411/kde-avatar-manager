#!/usr/bin/env bash
# install.sh — Install kde-avatar-manager (kdam) for the current user.
#
# Uses a venv with --system-site-packages so that heavy system packages
# (PyQt5, dbus-python, Pillow, etc.) are inherited without needing to
# reinstall them or touch the system Python environment (no PEP 668 issues).
#
# After installation, the source directory can be moved freely.
# All runtime data lives in ~/.kde-avatar-manager/ — independent of where
# the source lives.  If you move the project, just re-run install.sh from
# the new location to refresh the binary.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$HOME/.kde-avatar-manager/venv"
BIN_DIR="$HOME/.local/bin"
WRAPPER="$BIN_DIR/kdam"

if ! python3 -m venv -h &>/dev/null; then
    echo "Error: python3-venv module is missing."
    echo "  On Debian/Ubuntu: sudo apt install python3-venv"
    echo "  On Fedora:        sudo dnf install python3"
    exit 1
fi

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
    echo "Error: Python 3.11 or newer is required."
    echo "  Found: $(python3 --version 2>&1)"
    echo "  Install Python 3.11+ and ensure it is the default 'python3'."
    exit 1
fi

echo "==> Creating/updating virtualenv at $VENV_DIR ..."
python3 -m venv --system-site-packages "$VENV_DIR"

echo "==> Installing/updating kde-avatar-manager in venv ..."
"$VENV_DIR/bin/pip" install --quiet --force-reinstall "$SCRIPT_DIR"

echo "==> Writing launcher at $WRAPPER ..."
mkdir -p "$BIN_DIR"
cat > "$WRAPPER" <<'WRAPPER_EOF'
#!/usr/bin/env bash
exec "$HOME/.kde-avatar-manager/venv/bin/kdam" "$@"
WRAPPER_EOF
chmod +x "$WRAPPER"

# Verify
echo ""
if command -v kdam &>/dev/null; then
    echo "==> kdam is ready:"
    kdam --version
else
    echo "  Warning: ~/.local/bin is not in your PATH."
    echo "  Add to your ~/.bashrc or ~/.zshrc and restart your terminal:"
    echo '      export PATH="$HOME/.local/bin:$PATH"'
    echo "  Then run: kdam --version"
fi

# ── Shell completion ─────────────────────────────────────────────────────────
# Generate a STATIC completion script once at install time so shells don't
# have to spawn a Python process on every startup (which caused ~0.3s delays).
# The file is regenerated on every reinstall, keeping it current.

SHELL_NAME="$(basename "${SHELL:-bash}")"
COMP_DIR="$HOME/.kde-avatar-manager"
MARKER="# kdam shell completion"

mkdir -p "$COMP_DIR"

case "$SHELL_NAME" in
  bash)
    RC_FILE="$HOME/.bashrc"
    COMP_FILE="$COMP_DIR/completion.bash"
    _KDAM_COMPLETE=bash_source "$WRAPPER" > "$COMP_FILE" 2>/dev/null || true
    SOURCE_LINE="source \"$COMP_FILE\"  # kdam"
    ;;
  zsh)
    RC_FILE="$HOME/.zshrc"
    COMP_FILE="$COMP_DIR/completion.zsh"
    _KDAM_COMPLETE=zsh_source "$WRAPPER" > "$COMP_FILE" 2>/dev/null || true
    SOURCE_LINE="source \"$COMP_FILE\"  # kdam"
    ;;
  fish)
    RC_FILE="$HOME/.config/fish/config.fish"
    COMP_FILE="$COMP_DIR/completion.fish"
    _KDAM_COMPLETE=fish_source "$WRAPPER" > "$COMP_FILE" 2>/dev/null || true
    SOURCE_LINE="source \"$COMP_FILE\"  # kdam"
    ;;
  *)
    RC_FILE=""
    COMP_FILE=""
    SOURCE_LINE=""
    ;;
esac

if [[ -n "$RC_FILE" && -n "$COMP_FILE" ]]; then
  # Strip any previous kdam completion lines/markers cleanly
  sed -i '/_KDAM_COMPLETE/d' "$RC_FILE" 2>/dev/null || true
  sed -i '/# kdam/d' "$RC_FILE" 2>/dev/null || true
  sed -i '/completion\.\(bash\|zsh\|fish\)/d' "$RC_FILE" 2>/dev/null || true

  # Trim any trailing blank lines from RC_FILE so they don't accumulate
  sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$RC_FILE" 2>/dev/null || true

  # Append cleanly with exactly one blank line separator
  printf '\n%s\n%s\n' "$MARKER" "$SOURCE_LINE" >> "$RC_FILE"
  echo "==> Added Tab-completion to $RC_FILE (static — no startup delay)"
  echo "    Restart your terminal or run:  source $RC_FILE"
else
  echo "==> Could not detect shell for Tab-completion setup."
  echo "    Run 'kdam completion' for manual instructions."
fi

# Also place in standard XDG bash-completion directory for automatic detection
if [[ "$SHELL_NAME" == "bash" && -f "$COMP_FILE" ]]; then
  XDG_COMP_DIR="$HOME/.local/share/bash-completion/completions"
  mkdir -p "$XDG_COMP_DIR"
  cp "$COMP_FILE" "$XDG_COMP_DIR/kdam"
fi

echo ""
echo "Quick start:"
echo "  kdam dirs add ~/Pictures/Avatars"
echo "  kdam scan"
echo "  kdam configure-all"
echo "  kdam panic set ~/Pictures/my-safe-avatar.jpg"
echo "  kdam enable"
echo ""
echo "Done. Run 'kdam help' for the full command reference."
