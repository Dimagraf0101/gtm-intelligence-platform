# Installing Sales Pipeline on Mac

A step-by-step guide for non-technical users.

## Before you start

You need **three things** on your Mac:

1. **Claude Code** — the `claude` command, available in Terminal.
   (If you don't have it, install it from https://claude.com/claude-code first.)
2. **Python 3** — comes with most Macs; the installer will prompt you if not.
3. **A Vayne API token** — get it from your Vayne account settings.

## Step 1 — Download the installer

1. Go to: https://github.com/gozzkesshell/sales-clause-skill
2. Click the green **Code** button → **Download ZIP**.
3. Open the downloaded ZIP (double-click it in Finder).
4. Inside, find the file called **`install-mac.command`**.

## Step 2 — Run the installer

macOS blocks unsigned scripts the first time, so do this:

1. **Right-click** (or Control-click) `install-mac.command`.
2. Choose **Open** from the menu.
3. When macOS asks "are you sure?", click **Open** again.

A Terminal window will appear and a series of popup dialogs will guide you:

- **Welcome** — click Continue.
- **Vayne token prompt** — paste your Vayne API token and click OK.
- **ICP folder** — Finder opens the `icp` folder. Drop your ICP PDF files in here.
- **Done** — a launcher is placed on your Desktop.

That's it. The installer puts everything in `~/Applications/sales-pipeline`.

## Step 3 — Launch the skill

On your Desktop you'll see **`Launch Sales Pipeline`**.

1. **First time only:** right-click it → **Open** (same Gatekeeper step as the installer).
2. After that, just double-click it whenever you want to use the pipeline.

A Terminal opens with Claude Code running in the project folder. Type any of:

```
/sales-pipeline <sales-navigator-url>
/scrape <sales-navigator-url>
/score
/segment
/post-enrich
```

## Updating to a newer version

Just run `install-mac.command` again. Your `.env` file (with your Vayne token) is preserved.

## Troubleshooting

**"cannot be opened because it is from an unidentified developer"**
→ Right-click the file → Open (not just double-click). Confirm in the dialog.

**"python3: command not found"**
→ The installer opens python.org for you. Install Python 3, then run the installer again.

**"claude: command not found"**
→ Install Claude Code first: https://claude.com/claude-code

**I want to change my Vayne token**
→ Open `~/Applications/sales-pipeline/.env` in TextEdit and edit it.

**I want to start over**
→ Delete `~/Applications/sales-pipeline` and the Desktop launcher, then run the installer again.
