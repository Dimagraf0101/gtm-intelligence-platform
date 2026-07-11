#!/bin/bash
# Sales Pipeline — one-click installer for macOS
# Double-click this file to install. If macOS blocks it, right-click → Open.

set -e

REPO_URL="https://github.com/gozzkesshell/sales-pipeline/archive/refs/heads/master.zip"
INSTALL_DIR="$HOME/Applications/sales-pipeline"
DESKTOP_LAUNCHER="$HOME/Desktop/Launch Sales Pipeline.command"

# ---------- helpers ----------
dialog() {
  # $1 = message, $2 = title (optional), $3 = icon (note|caution|stop)
  local msg="$1"; local title="${2:-Sales Pipeline Installer}"; local icon="${3:-note}"
  osascript <<EOF
display dialog "$msg" with title "$title" buttons {"OK"} default button "OK" with icon $icon
EOF
}

confirm() {
  local msg="$1"; local title="${2:-Sales Pipeline Installer}"
  osascript <<EOF 2>/dev/null
display dialog "$msg" with title "$title" buttons {"Cancel","Continue"} default button "Continue" with icon note
EOF
}

prompt_text() {
  local msg="$1"; local default="${2:-}"
  osascript <<EOF 2>/dev/null
set T to text returned of (display dialog "$msg" with title "Sales Pipeline Installer" default answer "$default" buttons {"Cancel","OK"} default button "OK" with icon note)
return T
EOF
}

fail() {
  dialog "Install failed:\n\n$1" "Sales Pipeline Installer" stop
  exit 1
}

# ---------- welcome ----------
confirm "This will install the Sales Pipeline skill to:\n\n$INSTALL_DIR\n\nYou'll need:\n  • Claude Code (already installed)\n  • Python 3\n  • A Vayne API token\n\nClick Continue to begin." >/dev/null || exit 0

# ---------- check python3 ----------
if ! command -v python3 >/dev/null 2>&1; then
  dialog "Python 3 is not installed.\n\nI'll open python.org — download and install the latest Python 3, then run this installer again." "Python 3 required" caution
  open "https://www.python.org/downloads/macos/"
  exit 1
fi

# ---------- check claude ----------
if ! command -v claude >/dev/null 2>&1; then
  dialog "Claude Code (the 'claude' command) was not found in your PATH.\n\nMake sure Claude Code is installed and accessible from Terminal, then run this installer again." "Claude Code required" caution
  exit 1
fi

# ---------- download ----------
TMP_DIR="$(mktemp -d)"
cd "$TMP_DIR"

echo "Downloading latest version..."
curl -fsSL -o repo.zip "$REPO_URL" || fail "Could not download from GitHub. Check your internet connection."

echo "Unzipping..."
unzip -q repo.zip || fail "Could not unzip download."

SRC_DIR="$(ls -d sales-clause-skill-* 2>/dev/null | head -n1)"
[ -z "$SRC_DIR" ] && fail "Download did not contain expected folder."

# ---------- backup existing .env ----------
EXISTING_ENV=""
if [ -f "$INSTALL_DIR/.env" ]; then
  EXISTING_ENV="$(cat "$INSTALL_DIR/.env")"
fi

# ---------- install ----------
mkdir -p "$HOME/Applications"
rm -rf "$INSTALL_DIR"
mv "$SRC_DIR" "$INSTALL_DIR"
cd "$INSTALL_DIR"

# restore .env if existed
if [ -n "$EXISTING_ENV" ]; then
  printf '%s\n' "$EXISTING_ENV" > "$INSTALL_DIR/.env"
fi

# ---------- venv + deps ----------
echo "Setting up Python environment..."
python3 -m venv .venv || fail "Could not create Python virtual environment."
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if [ -f requirements.txt ]; then
  pip install --quiet -r requirements.txt || fail "Could not install Python dependencies."
fi

# ---------- .env / token ----------
if [ ! -f "$INSTALL_DIR/.env" ]; then
  TOKEN="$(prompt_text "Paste your Vayne API token:" "" 2>/dev/null || true)"
  if [ -z "$TOKEN" ]; then
    cp .env.example .env 2>/dev/null || true
    dialog "No token entered. A template .env file was created.\n\nEdit it later at:\n$INSTALL_DIR/.env" "Skipped token setup" caution
  else
    cat > "$INSTALL_DIR/.env" <<EOF
VAYNE_API_TOKEN=$TOKEN
SCORE_THRESHOLD=60
EOF
  fi
fi

# ---------- desktop launcher ----------
cat > "$DESKTOP_LAUNCHER" <<EOF
#!/bin/bash
cd "$INSTALL_DIR"
source .venv/bin/activate
clear
echo "=========================================="
echo "  Sales Pipeline — ready"
echo "=========================================="
echo ""
echo "Try one of these commands inside Claude Code:"
echo "  /sales-pipeline <sales-navigator-url>"
echo "  /scrape <sales-navigator-url>"
echo "  /score"
echo "  /segment"
echo ""
exec claude
EOF
chmod +x "$DESKTOP_LAUNCHER"

# ---------- open icp folder ----------
open "$INSTALL_DIR/icp" 2>/dev/null || true

# ---------- done ----------
dialog "✅ Install complete!\n\nA launcher was added to your Desktop:\n  'Launch Sales Pipeline'\n\nNext steps:\n  1. Drop your ICP PDF files into the 'icp' folder that just opened.\n  2. Double-click 'Launch Sales Pipeline' on your Desktop.\n  3. Inside Claude Code, type /sales-pipeline <url>\n\nIf macOS blocks the Desktop launcher the first time, right-click it → Open." "All set"
