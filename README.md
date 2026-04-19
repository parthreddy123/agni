# Agni — Fire Journal

An encrypted journaling copilot. Daily practice, therapy, performance psychology, and targeted exercises — guided by Claude, stored locally, encrypted at rest.

```
╔═════════════════════════════════════════╗
║                                         ║
║                 A G N I                 ║
║             ─────────────────           ║
║        ▲ fanning your inner fire        ║
║                                         ║
╚═════════════════════════════════════════╝
```

## What is it

Agni is a journal that knows you. You talk, it listens. It notices when you drift, nudges you on the days you need it, and helps you go deeper when something is stuck. Everything stays on your laptop, encrypted. No accounts, no cloud, no passwords to lose.

It has six modes:

- **Daily** — a 5-minute structured journal (on my mind / gratitude / intentions / affirmation) or free-form brain dump
- **Therapy** — Claude reads your recent entries, opens with what it noticed, hears you out, reflects back the thing underneath
- **Exercises** — 19 single-session practices across 5 categories, plus the ability to build custom ones
- **Profile** — a 36-question enneagram test that becomes the lens for everything else
- **Dashboard** — streak, 90-day dot grid, current state
- **Vault** — plaintext export to Obsidian / OneNote / VS Code so you can browse by date in a proper tabbed viewer

## Install

Agni is designed as a **Claude Code copilot**. The CLI is the encrypted storage layer; Claude drives the conversation.

```bash
# 1. Clone + install
git clone https://github.com/parthreddy123/agni.git
cd agni
pip install -e .

# 2. Initialize local encryption
agni init

# 3. Install the /agni copilot (this is the default experience)
mkdir -p ~/.claude/commands
cp .claude/commands/agni.md ~/.claude/commands/
```

Now in any Claude Code session, type `/agni`. Claude reads your state, picks the right mode, and drives the conversation. You just talk.

## Prefer the raw CLI?

The subcommands work standalone — skip step 3 above:

```bash
agni daily              # 5-minute structured journal
agni profile test       # 36-question enneagram (10 min, do this once)
agni streak             # 90-day dot grid + stats
agni read 2026-04-15    # show a specific day
agni vault sync         # export to ~/Documents/agni-vault for Obsidian
```

But copilot mode is where Agni comes alive.

## The exercise library

19 single-session practices across five categories. Each one is in `vault/exercises/<category>/<slug>.json` — fully editable, easily extensible.

**CORE** — single-session essentials
- `fear_inventory` — name what you are avoiding and face one
- `sunday_reset` — weekly audit: worked / drifted / reset
- `five_year_letter` — a letter from your future self
- `morning_ignition` — 90-second daily activator

**MOTIVATION**
- `inner_motivation` — find the real fuel under your stated goal

**PERFORMANCE** — for big identity / career moments
- `chip_bank` — build a mental archive of doubters (Kobe)
- `unsatisfiable_standard` — define a version of you always ahead (Brady)
- `expanded_mission` — make this chapter feel as big as it is (Djokovic)
- `identity_anchor` — make coasting feel like self-betrayal (Serena)

**REPARENTING** — family-of-origin work, inner child, self-esteem
- `ei_pattern_map` — map the parent patterns you still carry
- `sentence_completion` — 20-stem rapid-fire, bypass defenses
- `inner_child_first_contact` — letter to and from the child within
- `grief_inventory` — grieve what you didn't get
- `reparenting_letter` — write as the parent you needed

**INNER GAME** — peak-performance mental work
- `mirror_work` — ten minutes spoken, not written
- `release_ritual` — name and release one attachment
- `mental_rehearsal` — visualize a specific upcoming moment
- `pressure_reframe` — pressure as privilege
- `language_audit` — replace fear language with excellence language

Plus a `custom/` folder where Claude builds new ones for you on request.

## How storage works

- Entries live in `~/.agni/` as encrypted `.jrnl` files (Fernet, AES-128-CBC + HMAC)
- The encryption key is at `~/.agni/.key` — auto-generated on first run, no passphrase
- This protects against OneDrive sync exposure, casual filesystem browsing, lost laptops with disk encryption on
- It does NOT protect against someone with filesystem access who knows what they're looking for. That's the design — no passphrase to forget
- For full encryption with a passphrase, use full-disk encryption on your machine

## Browse your entries

Run `agni vault sync` to export everything as plain markdown to `~/Documents/agni-vault/`:

```
agni-vault/
├── Daily/2026-04-15.md
├── Therapy/2026-04-15.md
├── Exercises/<category>/<slug>/<date>.md
└── README.md
```

Open the folder in Obsidian (recommended — Daily Notes plugin gives you a calendar, graph view connects entries, search works across everything). Or OneNote, VS Code, or any markdown editor.

The exported folder is plaintext — wipe it with `agni vault clean` when you're done.

## How copilot mode works

With the `/agni` slash command installed:

- Type `/agni` and Claude opens with a banner, reads your state, and picks a mode based on what's needed
- On weekends, if you haven't done therapy in a while, Claude opens proactively with a check-in
- After 10+ days without an exercise, Claude recommends one by name based on your enneagram type and recent entries
- Claude can build new custom exercises tailored to your situation, with your permission

Claude needs an `ANTHROPIC_API_KEY` set in your environment for the API calls inside the CLI (therapy reflections, coach feedback, synthesis). The Claude Code experience itself uses your existing Claude Code subscription.

## Configuration

Environment variables:
- `ANTHROPIC_API_KEY` — for therapy reflections and coach feedback
- `AGNI_DATA` — override data root (default `~/.agni/`)
- `AGNI_VAULT_PATH` — override plaintext export path (default `~/Documents/agni-vault/`)
- `EDITOR` — used for long-form input in the legacy interactive commands (default `notepad` on Windows)

## Commands

```
agni init                         first-run setup
agni daily [--evening]            structured journal
agni therapy                      live therapy session (interactive)
agni warroom                      legacy 4-session arc with synthesis
agni exercise list                show all exercises
agni exercise run <slug>          run an exercise
agni exercise history <slug>      past responses for an exercise
agni exercise create --slug X     create a custom exercise (stdin JSON)
agni profile test                 enneagram test
agni profile show                 your saved profile
agni read [date]                  view a day's entry
agni streak                       90-day dot grid
agni status                       quick state snapshot
agni vault sync                   export to ~/Documents/agni-vault
agni vault open                   sync + open the folder
agni vault clean                  wipe the plaintext export
agni list                         all warroom runs
agni state                        dump state as JSON (for the copilot)
agni save <kind>                  ingest JSON from stdin (for the copilot)
```

## Author

Developed by Parth Reddy.

## License

MIT
