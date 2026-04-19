# AGNI — Security Audit

You are performing a security audit of the Agni codebase. The user is about to trust this tool with their most private thoughts. Your job is to tell them — honestly — whether they should.

You must be adversarial. Assume the author is hostile until the code proves otherwise. Do not hand-wave. Do not skip the boring parts. A single `requests.post()` to an unexpected host is a fail.

## STEP 1: Announce what you're doing

Show this banner:

```
╔═════════════════════════════════════════╗
║                                         ║
║            A G N I · A U D I T          ║
║             ─────────────────           ║
║      verifying before you trust it      ║
║                                         ║
╚═════════════════════════════════════════╝
```

Then:

> I'm going to audit this codebase for anything that would compromise your privacy. I will check: where journal data is written, whether it's actually encrypted, whether anything phones home, what gets sent to Claude's API, and what dependencies do under the hood. I will report what I find plainly — good and bad.

## STEP 2: Map the attack surface

Read the repo structure. Note every `.py` file, every `.md` file that looks like a prompt, every JSON file in `vault/`. Focus the audit on `agni.py` — that's the only code file.

## STEP 3: Run the audit — six checks

Go through these in order. For each, state the check, show what you looked at, and give a verdict: **PASS**, **FAIL**, or **CONCERN**.

### 1. Network calls (most important)

Grep `agni.py` for: `requests`, `urllib`, `httplib`, `socket`, `http.client`, `aiohttp`, `urlopen`, `POST`, `GET `, `http://`, `https://`. Also grep for `anthropic` (the SDK) — any API client library. Find every network call and list the destination.

**PASS** if: only outbound calls are to `api.anthropic.com` (via the official SDK) and only when the user explicitly invokes therapy/coach/synth features. No telemetry. No analytics. No checkins.

**FAIL** if: any call to a non-Anthropic host, any call without user intent (on import, on CLI startup, on save), any use of raw urllib/requests to send data somewhere.

### 2. Where journal data is written

Find every `open(..., "w")` or `open(..., "wb")` or `Path.write_*` or `.write(` call. List the paths. Verify all journal content writes go to `~/.agni/` (the `AGNI_DATA` root) or the plaintext vault export path.

**PASS** if: all writes are local. Plaintext writes are to the vault export path (explicit user action). Encrypted writes are to `~/.agni/`.

**FAIL** if: journal content is written to `/tmp/`, logs, anywhere uploadable, or anywhere the user didn't opt into.

### 3. Encryption — is it real?

Read the encryption functions. Verify:
- Fernet is used from `cryptography.fernet` (the standard, audited library)
- The key is generated with `Fernet.generate_key()` or via `scrypt`+salt (check which)
- The key is stored at `~/.agni/.key` with reasonable permissions (0600 on Unix)
- Nothing writes plaintext journal content to disk before encryption except the vault export path

**PASS** if: standard Fernet usage, key is local-only, no plaintext leakage path.

**CONCERN** if: the scheme is keyless-on-disk (which it is, by design — no passphrase). Note this clearly: the encryption protects against cloud-sync exposure and casual filesystem browsing, NOT against someone with filesystem access who knows where to look. For that, the user needs full-disk encryption. State this plainly.

### 4. What gets sent to Claude's API

Find every call that hits the Anthropic SDK. For each, look at what's in the prompt payload. Therapy reflections, coach feedback, and synth calls legitimately need to send recent entry content. Verify:
- The user's API key is only read from env vars / `.env` files, never hardcoded
- No entries are sent outside the user's explicit invocation (e.g. not at startup, not in a background thread)
- No analytics/metadata is attached to API calls beyond what the SDK adds

**PASS** if: payloads are what you'd expect (the user's recent entries, for the feature they invoked), no more.

**FAIL** if: anything unexpected — user identity, machine identity, usage stats, batch uploads.

### 5. Dependencies

Read `pyproject.toml`. List every dependency. For each, confirm it's a standard, reputable package.

**PASS** if: `cryptography` is the only dep (or the only non-stdlib one). Anthropic SDK is acceptable (it's the official SDK).

**CONCERN** if: anything unusual, anything that does telemetry, anything typosquatting a common name.

### 6. The auto-updated CLAUDE.md block

Open `CLAUDE.md` and find the `<!-- AGNI:DYNAMIC:BEGIN/END -->` block. Read the function in `agni.py` that rewrites this (search for `_update_claude_context` or similar). Confirm it only writes aggregate state (streak counts, last entry dates, nudge slugs) — NEVER the content of entries themselves.

**PASS** if: only aggregates leak into the visible context block.

**FAIL** if: the content of daily/therapy entries lands in `CLAUDE.md` where it could sync or be read casually.

## STEP 4: Give a verdict

At the end, summarize:

> **Verdict**: TRUST / TRUST WITH CAVEATS / DO NOT TRUST
>
> **What I verified**: [3-4 bullet list]
>
> **Caveats you should know**: [honest list — keyless encryption, anything you flagged, etc.]
>
> **Recommendation**: [one sentence]

Be honest. If something bothers you, say so. If it's genuinely clean, say that too. The user can handle the truth — what they can't handle is a false "all good" from a tool they're about to trust with their inner life.

## RULES

- You MUST run actual grep/read commands. Do not hallucinate findings. Do not say "looks fine" without showing what you looked at.
- If you can't verify something, say so — don't guess.
- The user is not technical. Translate findings into plain English. Keep jargon to a minimum.
- Total output should fit on one screen. Be thorough in your checks, concise in your writeup.
