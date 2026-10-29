#!/usr/bin/env bash
# uninstall.sh — Remove kde-avatar-manager and its systemd timer.
# This does NOT delete ~/.kde-avatar-manager/ (your images registry and config).
# Delete that directory manually if you want a full clean.

set -euo pipefail

echo "==> Stopping and disabling systemd timer …"
systemctl --user disable --now kde-avatar-rotate.timer 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/kde-avatar-rotate.timer"
rm -f "$HOME/.config/systemd/user/kde-avatar-rotate.service"
systemctl --user daemon-reload 2>/dev/null || true

echo "==> Removing kdam launcher, package, and virtual environment …"
rm -f "$HOME/.local/bin/kdam"
"$HOME/.kde-avatar-manager/venv/bin/pip" uninstall --quiet -y kde-avatar-manager 2>/dev/null || true
rm -rf "$HOME/.kde-avatar-manager/venv"

# ── Remove shell completion from RC files ────────────────────────────────────
COMP_DIR="$HOME/.kde-avatar-manager"
# Remove static completion script files
rm -f "$COMP_DIR/completion.bash" "$COMP_DIR/completion.zsh" "$COMP_DIR/completion.fish"

for RC_FILE in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.config/fish/config.fish"; do
  if [[ -f "$RC_FILE" ]]; then
    # Remove old eval-style lines and new source-style lines
    grep -vF "_KDAM_COMPLETE" "$RC_FILE" \
      | grep -vF "# kdam shell completion" \
      | grep -vF "# kdam" \
      | grep -vF "completion.bash" \
      | grep -vF "completion.zsh" \
      | grep -vF "completion.fish" \
      > "${RC_FILE}.kdam_tmp" \
      && mv "${RC_FILE}.kdam_tmp" "$RC_FILE" \
      && sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$RC_FILE" 2>/dev/null \
      && echo "==> Removed completion from $RC_FILE" \
      || true
  fi
done

# Remove XDG bash-completion file installed by install.sh
rm -f "$HOME/.local/share/bash-completion/completions/kdam"

echo ""
echo "Done. Your data in ~/.kde-avatar-manager/ was NOT deleted."
echo "Remove it manually if you want a full clean:"
echo "  rm -rf ~/.kde-avatar-manager"
