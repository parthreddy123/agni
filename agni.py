#!/usr/bin/env python3
"""
AGNI — Fire Journal
====================
Encrypted daily journal + war-room performance sessions with Claude coaching.

Commands:
  agni init                   First-run passphrase setup
  agni unlock / lock          Session cache control
  agni daily [--evening]      5-minute journal (morning or evening)
  agni therapy                Freeform Claude therapy entry
  agni warroom                Show schedule + status
  agni warroom run            Autopilot: full 4-session arc
  agni warroom step <1-4>     Run one session
  agni warroom synth          Generate affirmations from completed sessions
  agni warroom status         Show war-room progress
  agni warroom show <what>    kobe | brady | djokovic | serena | synth
  agni read [date]            Paginated book view
  agni status                 Streak + flow diagram
  agni list                   All war-room runs

Requires: cryptography
"""

import argparse
import base64
import getpass
import io
import json
import os
import re
import sys
import tempfile
import shutil
import subprocess
import textwrap
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
except ImportError:
    print("ERROR: cryptography not installed. Run: pip install cryptography")
    sys.exit(1)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Paths ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
VAULT_DIR = SCRIPT_DIR / "vault"
EXERCISES_DIR = VAULT_DIR / "exercises"
ROOT = Path(os.environ.get("AGNI_DATA", str(Path.home() / ".agni")))
DAILY_DIR = ROOT / "daily"
THERAPY_DIR = ROOT / "therapy"
WARROOM_DIR = ROOT / "warroom"
KEY_FILE = ROOT / ".key"
SALT_FILE = ROOT / ".salt"            # legacy, unused in keyless mode
VERIFIER_FILE = ROOT / ".verifier"    # legacy, unused in keyless mode
PROFILE_FILE = ROOT / "profile.jrnl"
SESSION_CACHE = Path(tempfile.gettempdir()) / f"agni-session-{os.getlogin() if hasattr(os, 'getlogin') else 'u'}"

# ── Colors (amber, ported from anvil) ────────────────────────────────────
A   = "\033[38;2;245;166;35m"
AD  = "\033[38;2;155;106;20m"
AB  = "\033[38;2;255;208;128m"
AW  = "\033[38;2;239;239;239m"
AF  = "\033[38;2;245;166;35m\033[1m"
DK  = "\033[38;2;58;58;58m"
DM  = "\033[2m"
B   = "\033[1m"
R   = "\033[0m"
RED = "\033[38;2;239;68;68m"
GRN = "\033[38;2;34;197;94m"

if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

# ── Banner ───────────────────────────────────────────────────────────────
AGNI_BANNER = f"""
  {A}╔═════════════════════════════════════════╗
  ║                                         ║
  ║                 {AW}{B}A G N I{R}{A}                 ║
  ║            {AD}─────────────────{A}            ║
  ║        {AB}▲{A} {AD}fanning your inner fire{A}        ║
  ║                                         ║
  ╚═════════════════════════════════════════╝{R}
"""

AGNI_MINI = f"  {DK}[{R} {AB}▲{R} {DK}]{R} {AW}{B}AGNI{R}"

# ── Narration ────────────────────────────────────────────────────────────

def _narrate(msg, style="info"):
    prefix = {
        "info":  f"{A}  ▸ ",
        "step":  f"{AF}  ◆ ",
        "done":  f"{GRN}  ✓ ",
        "warn":  f"{RED}  ⚠ ",
        "data":  f"{AD}    ",
        "head":  f"\n  {AW}{B}",
        "dim":   f"{DK}    ",
        "forge": f"{AB}  ⚒ ",
        "fire":  f"{AB}  ▲ ",
    }
    p = prefix.get(style, f"{A}  ")
    print(f"{p}{msg}{R}")


def _rule(width=55):
    print(f"  {A}{'─' * width}{R}")


def _strip_html(s):
    return re.sub(r"<[^>]+>", "", s) if s else s


def _wrap(text, width=70, indent="    "):
    text = _strip_html(text)
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        wrapped = textwrap.fill(para, width=width, initial_indent=indent, subsequent_indent=indent)
        lines.append(wrapped)
    return "\n".join(lines)


# ── Crypto ───────────────────────────────────────────────────────────────

def _ensure_key_file() -> bytes:
    """Return key bytes. Generate random key on first run."""
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    ROOT.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except Exception:
        pass
    return key


def _load_cached_key():
    """Legacy compat — in keyless mode, just returns the key file if it exists."""
    return _ensure_key_file() if KEY_FILE.exists() else None


def _get_key(allow_cache=True) -> bytes:
    if not KEY_FILE.exists() and not SALT_FILE.exists():
        _narrate("agni is not initialized. run: agni init", "warn")
        sys.exit(1)
    return _ensure_key_file()


def _encrypt(data: dict, key: bytes) -> bytes:
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return Fernet(key).encrypt(raw)


def _decrypt(blob: bytes, key: bytes) -> dict:
    raw = Fernet(key).decrypt(blob)
    return json.loads(raw.decode("utf-8"))


def save_jrnl(path: Path, data: dict, key: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_encrypt(data, key))


def load_jrnl(path: Path, key: bytes) -> dict:
    return _decrypt(path.read_bytes(), key)


# ── Claude ───────────────────────────────────────────────────────────────

def _load_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    for env_path in [SCRIPT_DIR / ".env", ROOT / ".env", Path.home() / ".env"]:
        if key:
            break
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return key


def _load_system_prompts():
    return json.loads((VAULT_DIR / "system_prompts.json").read_text(encoding="utf-8"))


def _claude(system: str, user: str, model: str = "claude-sonnet-4-5", max_tokens: int = 2048) -> str:
    api_key = _load_api_key()
    if not api_key:
        return "[no ANTHROPIC_API_KEY — skipping Claude response]"
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    _narrate("forging response...", "forge")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result.get("content", [{}])[0].get("text", "")
    except urllib.error.HTTPError as e:
        return f"[API error {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}]"
    except Exception as e:
        return f"[error: {e}]"


# ── Editor ───────────────────────────────────────────────────────────────

def _edit_text(prefill: str = "", header_comment: str = "") -> str:
    """Open $EDITOR for long-form input. Returns the body (strips comment header)."""
    editor = os.environ.get("EDITOR")
    if not editor:
        editor = "notepad" if os.name == "nt" else "vi"

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        if header_comment:
            for line in header_comment.splitlines():
                f.write(f"# {line}\n")
            f.write("#\n# --- write below this line. lines starting with # are ignored. ---\n\n")
        if prefill:
            f.write(prefill)
        tmp_path = f.name

    try:
        subprocess.call([editor, tmp_path])
        with open(tmp_path, "r", encoding="utf-8") as f:
            raw = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    body_lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(body_lines).strip()


def _prompt_line(label: str) -> str:
    try:
        return input(f"  {A}▸{R} {label}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


# ── Flow diagram ─────────────────────────────────────────────────────────

FLOW_LABELS = ["DAILY", " THRPY", " KOBE ", "BRADY", " DJKV ", "SRNA ", "SYNTH"]

def _agni_flow(active=None, done=None):
    done = done or []
    def node(i):
        lbl = FLOW_LABELS[i]
        if i == active:
            return f"{A}▸{lbl}◂{R}"
        if i in done:
            return f"{A} {lbl} {R}"
        return f"{DK} {'·' * len(lbl)} {R}"
    nodes = "".join([node(i) + (f"{A}─{R}" if i < len(FLOW_LABELS) - 1 else "") for i in range(len(FLOW_LABELS))])
    print()
    print(f"  {nodes}")
    print()


# ── Commands ─────────────────────────────────────────────────────────────

def cmd_init(args):
    print(AGNI_BANNER)
    if KEY_FILE.exists():
        _narrate("agni is already initialized.", "warn")
        _narrate(f"key at {KEY_FILE}", "dim")
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    _narrate("setting up keyless encrypted journal", "step")
    print(f"  {AD}a random key will be generated and stored alongside your entries.{R}")
    print(f"  {AD}no passphrase to remember. no passphrase to lose.{R}")
    print(f"  {AD}files are encrypted on disk — safe from onedrive sync and casual browsing.{R}")
    print(f"  {AD}NOT safe from someone with filesystem access who knows what they're looking for.{R}")
    print()
    _ensure_key_file()
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    THERAPY_DIR.mkdir(parents=True, exist_ok=True)
    WARROOM_DIR.mkdir(parents=True, exist_ok=True)
    _narrate("agni initialized.", "done")
    _narrate(f"journal root: {ROOT}", "dim")
    _narrate("next: agni daily", "fire")


def cmd_unlock(args):
    _ensure_key_file()
    _narrate("keyless mode — already unlocked.", "done")


def cmd_lock(args):
    _narrate("keyless mode — no session to lock. delete ~/.agni/.key to wipe access.", "dim")


def cmd_daily(args):
    key = _get_key()
    prompts_file = VAULT_DIR / "daily_prompts.json"
    prompts = json.loads(prompts_file.read_text(encoding="utf-8"))

    hour = datetime.now().hour
    evening = args.evening or hour >= 18
    mode = "evening" if evening else "morning"
    today = date.today().isoformat()
    path = DAILY_DIR / f"{today}.jrnl"

    entry = {}
    if path.exists():
        try:
            entry = load_jrnl(path, key)
        except Exception:
            entry = {}

    print(AGNI_BANNER)
    print(f"  {AW}{B}{mode.upper()} · {datetime.now().strftime('%A · %B %d · %Y')}{R}")
    _rule()
    print()

    for prompt in prompts[mode]:
        print(f"  {AB}{prompt['label']}{R}")
        print(f"  {DK}{prompt['hint']}{R}")
        print()
        responses = []
        for i in range(prompt["count"]):
            tag = f"  {A}{i+1}.{R} " if prompt["count"] > 1 else f"  {A}▸{R} "
            try:
                val = input(tag).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                val = ""
            if val:
                responses.append(val)
        entry.setdefault(mode, {})[prompt["key"]] = responses
        print()

    entry.setdefault("meta", {})["updated"] = datetime.now().isoformat()
    save_jrnl(path, entry, key)
    _rule()
    _narrate(f"saved · {path.name}", "done")
    _narrate("the fire is fed. see you tomorrow.", "fire")


def _load_recent_context(key, days=21, max_entries=20):
    parts = []
    cutoff = date.today() - timedelta(days=days)
    if DAILY_DIR.exists():
        for p in sorted(DAILY_DIR.glob("*.jrnl"), reverse=True):
            try:
                d = datetime.fromisoformat(p.stem).date()
                if d < cutoff:
                    break
                entry = load_jrnl(p, key)
                m = entry.get("morning", {})
                e = entry.get("evening", {})
                bits = [f"## Daily · {p.stem}"]
                if m.get("on_my_mind"):
                    omm = m["on_my_mind"]
                    bits.append("on my mind: " + (omm[0] if isinstance(omm, list) else omm))
                if m.get("gratitude"):
                    bits.append("grateful: " + " / ".join(m["gratitude"]))
                if m.get("intentions"):
                    bits.append("intended: " + " / ".join(m["intentions"]))
                if m.get("affirmation"):
                    bits.append("affirmed: " + " / ".join(m["affirmation"]))
                if e.get("amazing"):
                    bits.append("amazing: " + " / ".join(e["amazing"]))
                if e.get("better"):
                    bits.append("better: " + " / ".join(e["better"]))
                parts.append("\n".join(bits))
            except Exception:
                continue
    if THERAPY_DIR.exists():
        for p in sorted(THERAPY_DIR.glob("*.jrnl"), reverse=True):
            try:
                d = datetime.fromisoformat(p.stem).date()
                if d < cutoff:
                    break
                entry = load_jrnl(p, key)
                for s in entry.get("sessions", [])[-2:]:
                    bits = [f"## Therapy · {p.stem} · {s.get('time','')}"]
                    if s.get("entry"):
                        bits.append(f"they wrote: {s['entry']}")
                    if s.get("reflection"):
                        bits.append(f"you said: {s['reflection']}")
                    parts.append("\n".join(bits))
            except Exception:
                continue
    return "\n\n".join(parts[:max_entries])


def cmd_therapy(args):
    key = _get_key()
    print(AGNI_BANNER)
    _narrate("therapy · checking in", "step")
    print(f"  {AD}reading your recent entries...{R}")
    print()

    context = _load_recent_context(key)
    sys_prompts = _load_system_prompts()

    if not context.strip():
        opening = ("This is your first session with me. I don't have anything to read yet. "
                   "So let's start where every first session starts: what's actually taking up space in your head right now?")
    else:
        opening = _claude(sys_prompts["therapy_checkin"], context, max_tokens=1024)

    print()
    _rule()
    print(f"  {AB}{B}CHECK-IN{R}")
    print()
    print(_wrap(opening, indent="  "))
    print()
    _rule()
    print()

    response = _edit_text(
        header_comment="agni therapy · respond to what's above. no structure. just write."
    )
    if not response:
        _narrate("nothing written. session saved as check-in only.", "dim")
        today = date.today().isoformat()
        path = THERAPY_DIR / f"{today}.jrnl"
        entries = load_jrnl(path, key) if path.exists() else {}
        entries.setdefault("sessions", []).append({
            "time": datetime.now().strftime("%H%M"),
            "checkin": opening,
            "entry": "",
            "reflection": "",
        })
        save_jrnl(path, entries, key)
        return

    reflect_msg = (f"Your opening check-in to them:\n\n{opening}\n\n"
                   f"---\n\nWhat they wrote back:\n\n{response}")
    reflection = _claude(sys_prompts["therapy_reflect"], reflect_msg, max_tokens=1024)

    print()
    _rule()
    print(f"  {AB}{B}REFLECTION{R}")
    print()
    print(_wrap(reflection, indent="  "))
    print()
    _rule()

    today = date.today().isoformat()
    stamp = datetime.now().strftime("%H%M")
    path = THERAPY_DIR / f"{today}.jrnl"
    entries = {}
    if path.exists():
        try:
            entries = load_jrnl(path, key)
        except Exception:
            entries = {}
    entries.setdefault("sessions", []).append({
        "time": stamp,
        "checkin": opening,
        "entry": response,
        "reflection": reflection,
    })
    save_jrnl(path, entries, key)
    _narrate(f"saved · {path.name}", "done")
    _narrate("sit with it. come back tomorrow.", "fire")


# ── War Room ─────────────────────────────────────────────────────────────

def _load_sessions():
    return json.loads((VAULT_DIR / "sessions.json").read_text(encoding="utf-8"))


def _warroom_latest_run():
    if not WARROOM_DIR.exists():
        return None
    runs = sorted([p for p in WARROOM_DIR.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)
    return runs[0] if runs else None


def _new_run_dir():
    WARROOM_DIR.mkdir(parents=True, exist_ok=True)
    n = 1
    while (WARROOM_DIR / f"run-{n:03d}").exists():
        n += 1
    d = WARROOM_DIR / f"run-{n:03d}"
    d.mkdir()
    return d


def _print_session_header(session):
    print()
    _rule(60)
    print(f"  {AF}SESSION {session['number']} · {session['tag']}{R}")
    print(f"  {AW}{B}{session['title']}{R}")
    _rule(60)
    print()
    print(_wrap(session["intro"], indent="  "))
    print()
    print(f"  {AD}{session['quote']['text']}{R}")
    print(f"  {AD}  {session['quote']['cite']}{R}")
    print()


def _print_exercise(ex):
    print()
    print(f"  {AB}{B}{ex['id']} · {ex['title']}{R}")
    print(f"  {DK}{ex['sub']}{R}")
    print()
    for para in ex["prompt"]:
        print(_wrap(para, indent="  "))
        print()
    print(f"  {A}guidance:{R}")
    for g in ex["guidance"]:
        print(f"  {A}{g['num']}{R}  {AW}{g['title']}{R}")
        print(_wrap(g["body"], indent="      "))
        print()
    if ex.get("watch_out"):
        print(f"  {RED}⚠ watch out:{R}")
        print(_wrap(ex["watch_out"], indent="      "))
        print()
    if ex.get("tip"):
        print(f"  {AB}▲ tip:{R}")
        print(_wrap(ex["tip"], indent="      "))
        print()


def _run_exercise(session, ex, key, run_dir, sys_prompts):
    _print_exercise(ex)
    header = f"session {session['number']} · exercise {ex['id']}: {ex['title']}\n\n"
    header += "prompt:\n" + "\n".join("  " + _strip_html(p) for p in ex["prompt"])
    response = _edit_text(header_comment=header)
    if not response:
        _narrate("empty response. skipped.", "dim")
        return None

    feedback = None
    print()
    try:
        want = input(f"  {A}▸{R} get coach feedback on this? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        want = ""
    if want == "y":
        user_msg = f"Exercise: \"{ex['title']}\"\n\nPrompt given:\n{' '.join(ex['prompt'])}\n\nPerson's response:\n{response}"
        feedback = _claude(sys_prompts["coach"], user_msg)
        print()
        _rule()
        print(f"  {AB}{B}COACH{R}")
        print()
        print(_wrap(feedback, indent="  "))
        print()
        _rule()

    data = {"session_id": session["id"], "exercise_id": ex["id"], "title": ex["title"],
            "response": response, "feedback": feedback,
            "timestamp": datetime.now().isoformat()}
    fname = f"s{session['id']}_{ex['id'].replace('.', '_')}.jrnl"
    save_jrnl(run_dir / fname, data, key)
    _narrate(f"saved · {fname}", "done")
    return data


def cmd_warroom(args):
    sub = args.warroom_cmd
    if sub in (None, "status"):
        _warroom_status()
        return
    if sub == "run":
        _warroom_run_all(args)
        return
    if sub == "step":
        _warroom_step(args)
        return
    if sub == "synth":
        _warroom_synth(args)
        return
    if sub == "show":
        _warroom_show(args)
        return
    _narrate(f"unknown warroom subcommand: {sub}", "warn")


def _warroom_status():
    print(AGNI_BANNER)
    data = _load_sessions()
    print(f"  {AW}{B}WAR ROOM · performance psychology arc{R}")
    _rule(60)
    print()
    for block in data["schedule"]:
        sid = block.get("session_id")
        marker = f"{A}▸{R}" if sid else f"{DK}·{R}"
        print(f"  {marker} {AW}{block['time']:<12}{R} {A}{block['name']:<32}{R} {DK}{block['dur']}{R}")
    print()
    _rule(60)
    run = _warroom_latest_run()
    if run:
        _narrate(f"latest run: {run.name}", "dim")
        files = sorted(run.glob("s*_*.jrnl"))
        print(f"  {DK}    exercises saved: {len(files)}{R}")
    else:
        _narrate("no runs yet. start with: agni warroom run", "dim")
    print()


def _warroom_run_all(args):
    key = _get_key()
    sys_prompts = _load_system_prompts()
    data = _load_sessions()
    run_dir = _new_run_dir()
    _narrate(f"new run: {run_dir.name}", "step")
    for session in data["sessions"]:
        _print_session_header(session)
        try:
            go = input(f"  {A}▸{R} begin session {session['number']}? [Y/n/skip] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            go = "n"
        if go == "n":
            _narrate("stopping here. progress saved.", "dim")
            return
        if go == "skip":
            continue
        for ex in session["exercises"]:
            _run_exercise(session, ex, key, run_dir, sys_prompts)
    _narrate("all four sessions complete.", "done")
    _narrate("run synthesis: agni warroom synth", "fire")


def _warroom_step(args):
    key = _get_key()
    sys_prompts = _load_system_prompts()
    data = _load_sessions()
    try:
        n = int(args.step)
    except (ValueError, TypeError):
        _narrate("step must be 1-4", "warn")
        return
    if n < 1 or n > 4:
        _narrate("step must be 1-4", "warn")
        return
    run_dir = _warroom_latest_run() or _new_run_dir()
    _narrate(f"run: {run_dir.name}", "dim")
    session = data["sessions"][n - 1]
    _print_session_header(session)
    for ex in session["exercises"]:
        _run_exercise(session, ex, key, run_dir, sys_prompts)


def _warroom_synth(args):
    key = _get_key()
    sys_prompts = _load_system_prompts()
    run_dir = _warroom_latest_run()
    if not run_dir:
        _narrate("no runs found. start with: agni warroom run", "warn")
        return
    files = sorted(run_dir.glob("s*_*.jrnl"))
    if not files:
        _narrate("no exercises completed in latest run.", "warn")
        return
    _narrate(f"synthesizing from {len(files)} exercises in {run_dir.name}", "forge")
    compiled = []
    for f in files:
        try:
            d = load_jrnl(f, key)
            compiled.append(f"## {d.get('title', f.stem)}\n\n{d.get('response', '')}")
        except Exception as e:
            _narrate(f"skipping {f.name}: {e}", "warn")
    user_msg = "\n\n---\n\n".join(compiled)
    result = _claude(sys_prompts["synth"], user_msg, max_tokens=2048)
    print()
    _rule()
    print(f"  {AF}FIVE AFFIRMATIONS{R}")
    print()
    print(_wrap(result, indent="  "))
    print()
    _rule()
    out_path = run_dir / "synth.jrnl"
    save_jrnl(out_path, {"affirmations": result, "timestamp": datetime.now().isoformat()}, key)
    _narrate(f"saved · {out_path.name}", "done")


def _warroom_show(args):
    key = _get_key()
    run_dir = _warroom_latest_run()
    if not run_dir:
        _narrate("no runs.", "warn")
        return
    which = (args.what or "").lower()
    session_map = {"kobe": 1, "brady": 2, "djokovic": 3, "serena": 4, "synth": None}
    if which not in session_map:
        _narrate("show: kobe | brady | djokovic | serena | synth", "warn")
        return
    if which == "synth":
        synth_path = run_dir / "synth.jrnl"
        if not synth_path.exists():
            _narrate("no synthesis yet. run: agni warroom synth", "warn")
            return
        d = load_jrnl(synth_path, key)
        print()
        _rule()
        print(f"  {AF}AFFIRMATIONS{R}")
        print()
        print(_wrap(d.get("affirmations", ""), indent="  "))
        print()
        _rule()
        return
    sid = session_map[which]
    files = sorted(run_dir.glob(f"s{sid}_*.jrnl"))
    if not files:
        _narrate(f"nothing saved for {which}.", "warn")
        return
    data = _load_sessions()
    session = data["sessions"][sid - 1]
    _print_session_header(session)
    for f in files:
        d = load_jrnl(f, key)
        print()
        print(f"  {AB}{B}{d.get('exercise_id', '')} · {d.get('title', '')}{R}")
        _rule()
        print(_wrap(d.get("response", ""), indent="  "))
        if d.get("feedback"):
            print()
            print(f"  {AD}— coach —{R}")
            print(_wrap(d["feedback"], indent="  "))
        print()


# ── Read / Status / List ─────────────────────────────────────────────────

def cmd_read(args):
    key = _get_key()
    target = args.date
    if target:
        path = DAILY_DIR / f"{target}.jrnl"
        if not path.exists():
            _narrate(f"no entry for {target}.", "warn")
            return
        _render_daily(path, key)
        return
    entries = sorted(DAILY_DIR.glob("*.jrnl"), reverse=True) if DAILY_DIR.exists() else []
    if not entries:
        _narrate("no entries yet. start with: agni daily", "dim")
        return
    print(AGNI_BANNER)
    print(f"  {AW}{B}THE JOURNAL{R}")
    _rule(60)
    print()
    for p in entries[:20]:
        try:
            d = load_jrnl(p, key)
            parts = []
            m = d.get("morning", {})
            e = d.get("evening", {})
            if m.get("gratitude"):
                parts.append(f"gratitude: {m['gratitude'][0][:40]}")
            if e.get("amazing"):
                parts.append(f"amazing: {e['amazing'][0][:40]}")
            summary = " · ".join(parts) or "(empty)"
            print(f"  {A}▸{R} {AW}{p.stem}{R}  {DK}{summary}{R}")
        except Exception as ex:
            print(f"  {RED}⚠{R} {p.stem}  {DK}[decrypt failed: {ex}]{R}")
    print()
    _narrate("open one with: agni read <YYYY-MM-DD>", "dim")


def _render_daily(path: Path, key: bytes):
    d = load_jrnl(path, key)
    dt = datetime.fromisoformat(path.stem)
    print()
    print(f"  {A}╔{'═' * 58}╗{R}")
    print(f"  {A}║{R}{' ' * 58}{A}║{R}")
    title = dt.strftime("%A · %B %d · %Y")
    pad = (58 - len(title)) // 2
    print(f"  {A}║{R}{' ' * pad}{AW}{B}{title}{R}{' ' * (58 - pad - len(title))}{A}║{R}")
    print(f"  {A}║{R}{' ' * 58}{A}║{R}")
    print(f"  {A}╚{'═' * 58}╝{R}")
    print()
    m = d.get("morning", {})
    e = d.get("evening", {})
    freeform = d.get("freeform")
    if freeform:
        print(f"  {AB}{B}FREE-FORM{R}")
        print()
        text = freeform if isinstance(freeform, str) else "\n\n".join(freeform)
        print(_wrap(text, indent="    "))
        print()
    if m:
        print(f"  {AB}{B}MORNING{R}")
        print()
        if m.get("on_my_mind"):
            print(f"  {A}on my mind{R}")
            omm = m["on_my_mind"]
            if isinstance(omm, list):
                for item in omm:
                    print(f"    {A}▸{R} {AW}{item}{R}")
            else:
                print(f"    {A}▸{R} {AW}{omm}{R}")
            print()
        if m.get("gratitude"):
            print(f"  {A}grateful for{R}")
            for item in m["gratitude"]:
                print(f"    {A}▸{R} {AW}{item}{R}")
            print()
        if m.get("intentions"):
            print(f"  {A}would make today great{R}")
            for item in m["intentions"]:
                print(f"    {A}▸{R} {AW}{item}{R}")
            print()
        if m.get("affirmation"):
            print(f"  {A}affirmation{R}")
            print(f"    {AB}{m['affirmation'][0]}{R}")
            print()
    if e:
        print(f"  {AB}{B}EVENING{R}")
        print()
        if e.get("amazing"):
            print(f"  {A}amazing today{R}")
            for item in e["amazing"]:
                print(f"    {A}▸{R} {AW}{item}{R}")
            print()
        if e.get("better"):
            print(f"  {A}could have been better{R}")
            print(f"    {AW}{e['better'][0]}{R}")
            print()


def cmd_status(args):
    print(AGNI_BANNER)
    key = None
    if KEY_FILE.exists():
        try:
            key = _get_key()
        except SystemExit:
            return
    streak, last = _compute_streak(key) if key else (0, None)
    run = _warroom_latest_run()
    done_sessions = set()
    active_step = None
    if run and key:
        for i in range(1, 5):
            if list(run.glob(f"s{i}_*.jrnl")):
                done_sessions.add(1 + i)  # offset by daily/therapy nodes
        if (run / "synth.jrnl").exists():
            done_sessions.add(6)

    done_flow = []
    if last == date.today().isoformat():
        done_flow.append(0)
    done_flow.extend(sorted(done_sessions))
    _agni_flow(active=active_step, done=done_flow)
    _rule()
    print(f"  {A}▸ Streak:{R} {AW}{streak} days{R}")
    print(f"  {A}▸ Last entry:{R} {AW}{last or '—'}{R}")
    if run:
        files = sorted(run.glob("s*_*.jrnl"))
        print(f"  {A}▸ War room:{R} {AW}{run.name} · {len(files)} exercises saved{R}")
    else:
        print(f"  {A}▸ War room:{R} {DK}not started{R}")
    _rule()
    print()


def _compute_streak(key):
    if not DAILY_DIR.exists():
        return 0, None
    files = sorted(DAILY_DIR.glob("*.jrnl"), reverse=True)
    if not files:
        return 0, None
    last = files[0].stem
    streak = 0
    d = date.today()
    for _ in range(400):
        p = DAILY_DIR / f"{d.isoformat()}.jrnl"
        if p.exists():
            streak += 1
            d -= timedelta(days=1)
        else:
            if d == date.today():
                d -= timedelta(days=1)
                continue
            break
    return streak, last


# ── Streak ──────────────────────────────────────────────────────────────

def _streak_stats(entries_set):
    today = date.today()
    d = today if today in entries_set else today - timedelta(days=1)
    streak = 0
    while d in entries_set:
        streak += 1
        d -= timedelta(days=1)
    longest = 0
    cur = 0
    prev = None
    for dd in sorted(entries_set):
        if prev and (dd - prev).days == 1:
            cur += 1
        else:
            cur = 1
        longest = max(longest, cur)
        prev = dd
    return streak, longest


def cmd_streak(args):
    if not KEY_FILE.exists():
        _narrate("agni not initialized. run: agni init", "warn")
        return
    print(AGNI_BANNER)
    entries = set()
    if DAILY_DIR.exists():
        for p in DAILY_DIR.glob("*.jrnl"):
            try:
                entries.add(datetime.fromisoformat(p.stem).date())
            except Exception:
                continue
    total = len(entries)
    streak, longest = _streak_stats(entries)
    last_30 = sum(1 for i in range(30) if (date.today() - timedelta(days=i)) in entries)
    pct_30 = int(last_30 / 30 * 100) if entries else 0

    print(f"  {AW}{B}STREAK · consistency{R}")
    _rule(60)
    print()
    print(f"  {A}▸ Current streak:{R}  {AW}{B}{streak}{R} {A}days{R}")
    print(f"  {A}▸ Longest streak:{R}  {AW}{longest}{R} {A}days{R}")
    print(f"  {A}▸ Total entries:{R}   {AW}{total}{R}")
    print(f"  {A}▸ Last 30 days:{R}    {AW}{last_30}/30{R}  {DK}({pct_30}%){R}")
    print()
    _rule(60)
    print()
    print(f"  {AW}{B}Last 90 days{R}   {DK}● = showed up · · = didn't{R}")
    print()

    days = [date.today() - timedelta(days=89 - i) for i in range(90)]
    current_month = None
    line_parts = []
    for d in days:
        if d.month != current_month:
            if line_parts:
                print("  " + "".join(line_parts))
                line_parts = []
            current_month = d.month
            line_parts.append(f"{AD}{d.strftime('%b')}{R}  ")
        dot = f"{AB}●{R}" if d in entries else f"{DK}·{R}"
        line_parts.append(f"{dot} ")
    if line_parts:
        print("  " + "".join(line_parts))
    print()
    if streak == 0 and total > 0:
        _narrate("streak broken. today is still available.", "dim")
    elif streak > 0:
        _narrate("the fire is lit.", "fire")
    print()


# ── Exercise library ────────────────────────────────────────────────────

def _load_exercise_index():
    result = []
    if EXERCISES_DIR.exists():
        for f in sorted(EXERCISES_DIR.rglob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                slug = f.relative_to(EXERCISES_DIR).with_suffix("").as_posix()
                result.append((slug, data))
            except Exception:
                continue
    return result


def cmd_exercise(args):
    sub = args.exercise_cmd
    if sub in (None, "list"):
        _exercise_list()
        return
    if sub == "run":
        _exercise_run(args)
        return
    if sub == "history":
        _exercise_history(args)
        return
    if sub == "create":
        _exercise_create(args)
        return
    _narrate(f"unknown exercise subcommand: {sub}", "warn")


def _exercise_create(args):
    slug = args.slug
    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _narrate(f"invalid JSON: {e}", "warn")
        sys.exit(1)
    if not data.get("title") or not data.get("exercises"):
        _narrate("exercise JSON must have 'title' and 'exercises' fields", "warn")
        sys.exit(1)
    custom_dir = EXERCISES_DIR / "custom"
    custom_dir.mkdir(parents=True, exist_ok=True)
    out = custom_dir / f"{slug}.json"
    if out.exists() and not args.force:
        _narrate(f"custom/{slug}.json already exists. use --force to overwrite.", "warn")
        sys.exit(1)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _narrate(f"created exercise: custom/{slug}", "done")
    print(json.dumps({"created": str(out), "slug": f"custom/{slug}"}))


def _exercise_list():
    print(AGNI_BANNER)
    items = _load_exercise_index()
    if not items:
        _narrate("no exercises found in vault/exercises/", "warn")
        return
    print(f"  {AW}{B}EXERCISE LIBRARY{R}")
    _rule(60)
    print()
    for slug, data in items:
        print(f"  {A}▸{R} {AW}{B}{slug}{R}  {DK}{data.get('tag','')}{R}")
        intro = _strip_html(data.get("intro", ""))
        if intro:
            short = intro[:110] + ("..." if len(intro) > 110 else "")
            print(f"     {DK}{short}{R}")
        print()
    _narrate("run with: agni exercise run <slug>", "dim")


def _exercise_run(args):
    key = _get_key()
    sys_prompts = _load_system_prompts()
    slug = args.slug
    path = EXERCISES_DIR / f"{slug}.json"
    if not path.exists():
        _narrate(f"no exercise: {slug}", "warn")
        avail = [s for s, _ in _load_exercise_index()]
        if avail:
            _narrate(f"available: {', '.join(avail)}", "dim")
        return
    session = json.loads(path.read_text(encoding="utf-8"))
    _print_session_header(session)

    out_dir = ROOT / "exercises" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    stamp = datetime.now().strftime("%H%M")
    out_path = out_dir / f"{today}_{stamp}.jrnl"

    responses = []
    for ex in session.get("exercises", []):
        data = _run_standalone_ex(ex, key, sys_prompts)
        if data:
            responses.append(data)

    if responses:
        save_jrnl(out_path, {
            "slug": slug,
            "title": session.get("title", ""),
            "timestamp": datetime.now().isoformat(),
            "responses": responses,
        }, key)
        _narrate(f"saved · {out_path.name}", "done")


def _run_standalone_ex(ex, key, sys_prompts):
    _print_exercise(ex)
    header = f"exercise: {ex.get('title','')}\n\nprompt:\n" + "\n".join(
        "  " + _strip_html(p) for p in ex.get("prompt", [])
    )
    response = _edit_text(header_comment=header)
    if not response:
        _narrate("empty. skipped.", "dim")
        return None
    feedback = None
    print()
    try:
        want = input(f"  {A}▸{R} get coach feedback? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        want = ""
    if want == "y":
        user_msg = (f"Exercise: \"{ex.get('title','')}\"\n\nPrompt:\n"
                    f"{' '.join(ex.get('prompt', []))}\n\nResponse:\n{response}")
        feedback = _claude(sys_prompts["coach"], user_msg)
        print()
        _rule()
        print(f"  {AB}{B}COACH{R}")
        print()
        print(_wrap(feedback, indent="  "))
        print()
        _rule()
    return {
        "exercise_id": ex.get("id", ""),
        "title": ex.get("title", ""),
        "response": response,
        "feedback": feedback,
        "timestamp": datetime.now().isoformat(),
    }


def _exercise_history(args):
    key = _get_key()
    slug = args.slug
    out_dir = ROOT / "exercises" / slug
    if not out_dir.exists() or not any(out_dir.iterdir()):
        _narrate(f"no history for {slug}", "dim")
        return
    print()
    print(f"  {AW}{B}HISTORY · {slug}{R}")
    _rule(60)
    for f in sorted(out_dir.glob("*.jrnl"), reverse=True):
        try:
            d = load_jrnl(f, key)
            print()
            print(f"  {A}▸ {f.stem}{R}")
            for r in d.get("responses", []):
                print(f"    {AB}{r.get('title','')}{R}")
                print(_wrap(r.get("response", ""), indent="      "))
                if r.get("feedback"):
                    print(f"      {AD}— coach —{R}")
                    print(_wrap(r["feedback"], indent="      "))
        except Exception as e:
            print(f"  {RED}⚠{R} {f.stem} [{e}]")
    print()


# ── Vault export (plaintext for Obsidian / OneNote / VS Code) ──────────

def _vault_path(args):
    if getattr(args, "path", None):
        return Path(args.path)
    env = os.environ.get("AGNI_VAULT_PATH")
    if env:
        return Path(env)
    return Path.home() / "Documents" / "agni-vault"


def _daily_to_md(stem, d):
    dt = datetime.fromisoformat(stem)
    out = [f"---\ntype: daily\ndate: {stem}\nday: {dt.strftime('%A')}\n---\n"]
    out.append(f"# {dt.strftime('%A · %B %d · %Y')}\n")
    freeform = d.get("freeform")
    if freeform:
        out.append("## Free-form\n")
        out.append(freeform if isinstance(freeform, str) else "\n\n".join(freeform))
        out.append("")
    m = d.get("morning", {})
    e = d.get("evening", {})
    if m:
        out.append("## Morning\n")
        if m.get("on_my_mind"):
            out.append("### On my mind")
            omm = m["on_my_mind"]
            if isinstance(omm, list):
                for i in omm:
                    out.append(f"- {i}")
            else:
                out.append(f"- {omm}")
            out.append("")
        if m.get("gratitude"):
            out.append("### Grateful for")
            for i in m["gratitude"]:
                out.append(f"- {i}")
            out.append("")
        if m.get("intentions"):
            out.append("### Would make today great")
            for i in m["intentions"]:
                out.append(f"- {i}")
            out.append("")
        if m.get("affirmation"):
            out.append("### Affirmation")
            for i in m["affirmation"]:
                out.append(f"> {i}")
            out.append("")
    if e:
        out.append("## Evening\n")
        if e.get("amazing"):
            out.append("### Amazing today")
            for i in e["amazing"]:
                out.append(f"- {i}")
            out.append("")
        if e.get("better"):
            out.append("### Could have been better")
            for i in e["better"]:
                out.append(f"- {i}")
            out.append("")
    return "\n".join(out)


def _therapy_to_md(stem, d):
    dt = datetime.fromisoformat(stem)
    out = [f"---\ntype: therapy\ndate: {stem}\n---\n"]
    out.append(f"# Therapy · {dt.strftime('%A · %B %d · %Y')}\n")
    for i, s in enumerate(d.get("sessions", []), 1):
        out.append(f"## Session {i} — {s.get('time','')}\n")
        if s.get("checkin"):
            out.append("### Check-in")
            out.append(s["checkin"])
            out.append("")
        if s.get("entry"):
            out.append("### What I wrote")
            out.append(s["entry"])
            out.append("")
        if s.get("reflection"):
            out.append("### Reflection")
            out.append(s["reflection"])
            out.append("")
    return "\n".join(out)


def _warroom_ex_to_md(d):
    out = [f"---\ntype: warroom\nsession: {d.get('session_id','')}\nexercise: {d.get('exercise_id','')}\n---\n"]
    out.append(f"# {d.get('title','')}\n")
    out.append(f"*{d.get('timestamp','')}*\n")
    if d.get("response"):
        out.append("## Response\n")
        out.append(d["response"])
        out.append("")
    if d.get("feedback"):
        out.append("## Coach Feedback\n")
        out.append(d["feedback"])
        out.append("")
    return "\n".join(out)


def _exercise_to_md(d):
    out = [f"---\ntype: exercise\nslug: {d.get('slug','')}\ntimestamp: {d.get('timestamp','')}\n---\n"]
    out.append(f"# {d.get('title','')}\n")
    for r in d.get("responses", []):
        out.append(f"## {r.get('title','')}\n")
        out.append(r.get("response", ""))
        out.append("")
        if r.get("feedback"):
            out.append("### Coach\n")
            out.append(r["feedback"])
            out.append("")
    return "\n".join(out)


def cmd_vault(args):
    sub = args.vault_cmd
    if sub in (None, "sync"):
        _vault_sync(args)
        return
    if sub == "open":
        _vault_sync(args)
        _vault_open(args)
        return
    if sub == "clean":
        _vault_clean(args)
        return
    _narrate(f"unknown vault subcommand: {sub}", "warn")


def _vault_sync(args):
    key = _get_key()
    vault = _vault_path(args)
    vault.mkdir(parents=True, exist_ok=True)

    (vault / "README.md").write_text(
        "# Agni Vault\n\n"
        "This folder contains **plaintext exports** of your encrypted agni journal.\n\n"
        "- Regenerate:  `python agni.py vault sync`\n"
        "- Open:        `python agni.py vault open`  (launches folder)\n"
        "- Wipe:        `python agni.py vault clean`\n\n"
        "## How to browse it\n"
        "- **Obsidian** (recommended): open this folder as a vault. Use Daily Notes plugin for calendar view, graph view for connections across entries.\n"
        "- **OneNote**: use File → Import → Markdown, or paste entries into sections by date.\n"
        "- **VS Code**: open the folder, use the outline view and tabs.\n"
        "- **Any markdown editor**: works.\n\n"
        "## Warning\n"
        "Anything in this folder is unencrypted. If you sync it to cloud storage, it is no longer protected. The encrypted originals live in `~/.agni/` — those are the canonical entries. This folder is a read-only viewer.\n",
        encoding="utf-8",
    )

    stats = {"daily": 0, "therapy": 0, "warroom": 0, "exercises": 0}

    if DAILY_DIR.exists():
        daily_dir = vault / "Daily"
        daily_dir.mkdir(exist_ok=True)
        for p in DAILY_DIR.glob("*.jrnl"):
            try:
                d = load_jrnl(p, key)
                (daily_dir / f"{p.stem}.md").write_text(_daily_to_md(p.stem, d), encoding="utf-8")
                stats["daily"] += 1
            except Exception as e:
                _narrate(f"skip daily/{p.name}: {e}", "warn")

    if THERAPY_DIR.exists():
        ther_dir = vault / "Therapy"
        ther_dir.mkdir(exist_ok=True)
        for p in THERAPY_DIR.glob("*.jrnl"):
            try:
                d = load_jrnl(p, key)
                (ther_dir / f"{p.stem}.md").write_text(_therapy_to_md(p.stem, d), encoding="utf-8")
                stats["therapy"] += 1
            except Exception as e:
                _narrate(f"skip therapy/{p.name}: {e}", "warn")

    if WARROOM_DIR.exists():
        for run in WARROOM_DIR.iterdir():
            if not run.is_dir():
                continue
            run_out = vault / "Warroom" / run.name
            run_out.mkdir(parents=True, exist_ok=True)
            for f in sorted(run.glob("*.jrnl")):
                try:
                    d = load_jrnl(f, key)
                    (run_out / f"{f.stem}.md").write_text(_warroom_ex_to_md(d), encoding="utf-8")
                    stats["warroom"] += 1
                except Exception as e:
                    _narrate(f"skip warroom/{run.name}/{f.name}: {e}", "warn")

    ex_root = ROOT / "exercises"
    if ex_root.exists():
        for slug_dir in ex_root.iterdir():
            if not slug_dir.is_dir():
                continue
            slug_out = vault / "Exercises" / slug_dir.name
            slug_out.mkdir(parents=True, exist_ok=True)
            for f in sorted(slug_dir.glob("*.jrnl")):
                try:
                    d = load_jrnl(f, key)
                    (slug_out / f"{f.stem}.md").write_text(_exercise_to_md(d), encoding="utf-8")
                    stats["exercises"] += 1
                except Exception as e:
                    _narrate(f"skip exercises/{slug_dir.name}/{f.name}: {e}", "warn")

    print()
    _narrate(f"synced → {vault}", "done")
    _narrate(f"daily: {stats['daily']}  therapy: {stats['therapy']}  warroom: {stats['warroom']}  exercises: {stats['exercises']}", "data")
    print()
    _narrate("open in obsidian: point a vault at the folder above", "dim")
    _narrate("plaintext — wipe when done: agni vault clean", "warn")


def _vault_open(args):
    vault = _vault_path(args)
    if not vault.exists():
        _narrate("no vault. run: agni vault sync", "warn")
        return
    try:
        if os.name == "nt":
            os.startfile(str(vault))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(vault)])
        else:
            subprocess.Popen(["xdg-open", str(vault)])
        _narrate(f"opened {vault}", "done")
    except Exception as e:
        _narrate(f"could not open: {e}", "warn")


def _vault_clean(args):
    vault = _vault_path(args)
    if not vault.exists():
        _narrate("nothing to clean.", "dim")
        return
    try:
        want = input(f"  {RED}⚠{R} delete all plaintext in {vault}? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        want = ""
    if want != "y":
        _narrate("cancelled.", "dim")
        return
    try:
        shutil.rmtree(vault)
        _narrate(f"wiped {vault}", "done")
    except Exception as e:
        _narrate(f"could not wipe: {e}", "warn")


def cmd_list(args):
    if not WARROOM_DIR.exists() or not any(WARROOM_DIR.iterdir()):
        _narrate("no war-room runs.", "dim")
        return
    print()
    print(f"  {AW}{B}WAR ROOM RUNS{R}")
    _rule()
    for run in sorted(WARROOM_DIR.iterdir()):
        if not run.is_dir():
            continue
        n = len(list(run.glob("s*_*.jrnl")))
        synth = "✓" if (run / "synth.jrnl").exists() else "·"
        print(f"  {A}▸{R} {AW}{run.name}{R}  {DK}{n} exercises  synth:{synth}{R}")
    print()


# ── Profile (enneagram) ─────────────────────────────────────────────────

def _load_enneagram():
    return json.loads((VAULT_DIR / "enneagram.json").read_text(encoding="utf-8"))


def _load_profile(key):
    if not PROFILE_FILE.exists():
        return None
    try:
        return load_jrnl(PROFILE_FILE, key)
    except Exception:
        return None


def cmd_profile(args):
    sub = args.profile_cmd
    if sub == "test":
        _profile_test()
    elif sub == "show":
        _profile_show()
    elif sub == "clear":
        _profile_clear()
    else:
        _profile_show()


def _profile_test():
    key = _get_key()
    test_data = json.loads((VAULT_DIR / "enneagram_test.json").read_text(encoding="utf-8"))
    types_data = _load_enneagram()

    print(AGNI_BANNER)
    print(f"  {AW}{B}ENNEAGRAM TEST{R}")
    _rule(60)
    print()
    print(_wrap(test_data["instructions"], indent="  "))
    print()
    print(f"  {AD}scale: 1 = strongly disagree · 5 = strongly agree{R}")
    print()
    _rule(60)

    scores = {str(i): 0 for i in range(1, 10)}
    answers = []
    for q in test_data["questions"]:
        print()
        print(f"  {AB}{q['id']}/36{R}  {AW}{q['text']}{R}")
        while True:
            try:
                raw = input(f"     {A}▸{R} 1-5: ").strip()
            except (EOFError, KeyboardInterrupt):
                _narrate("test cancelled.", "dim")
                return
            if raw in ("1", "2", "3", "4", "5"):
                val = int(raw)
                break
            print(f"     {RED}enter a number 1-5{R}")
        scores[str(q["type"])] += val
        answers.append({"id": q["id"], "type": q["type"], "value": val})

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    top_type = int(ranked[0][0])
    second = int(ranked[1][0])

    print()
    _rule(60)
    print(f"  {AF}RESULTS{R}")
    _rule(60)
    print()
    print(f"  {A}Your top three:{R}")
    for i, (t, s) in enumerate(ranked[:3]):
        info = types_data["types"][t]
        print(f"     {AB}{i+1}.{R} {AW}{B}Type {t} · {info['name']}{R}  {DK}({s}/20){R}")
    print()

    for t in [str(top_type), str(second)]:
        info = types_data["types"][t]
        print(f"  {AB}{B}Type {t} · {info['name']}{R}")
        print(_wrap(info["description"], indent="  "))
        print(f"  {AD}core fear:{R} {info['core_fear']}")
        print(f"  {AD}core desire:{R} {info['core_desire']}")
        print()

    try:
        confirm = input(f"  {A}▸{R} does Type {top_type} feel right? [Y/n/<number 1-9>]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        confirm = ""

    if confirm.isdigit() and 1 <= int(confirm) <= 9:
        top_type = int(confirm)
    elif confirm == "n":
        try:
            alt = input(f"  {A}▸{R} which type fits better (1-9)? ").strip()
            if alt.isdigit() and 1 <= int(alt) <= 9:
                top_type = int(alt)
        except (EOFError, KeyboardInterrupt):
            pass

    left_wing = top_type - 1 if top_type > 1 else 9
    right_wing = top_type + 1 if top_type < 9 else 1
    wing = left_wing if scores[str(left_wing)] >= scores[str(right_wing)] else right_wing

    profile = {
        "type": top_type,
        "wing": wing,
        "scores": scores,
        "answers": answers,
        "taken_date": date.today().isoformat(),
        "user_confirmed": True,
    }
    save_jrnl(PROFILE_FILE, profile, key)

    final = types_data["types"][str(top_type)]
    print()
    _rule(60)
    print(f"  {AF}Your type: {top_type}w{wing} · {final['name']}{R}")
    _rule(60)
    print()
    print(_wrap(final["description"], indent="  "))
    print()
    print(f"  {A}key patterns to watch:{R}")
    for p in final.get("key_patterns", []):
        print(f"    {A}▸{R} {p}")
    print()
    print(f"  {A}recommended exercises for your type:{R}")
    for slug in final.get("recommended_exercises", []):
        print(f"    {AB}▲{R} {slug}")
    print()
    _narrate("profile saved.", "done")
    _narrate("therapy will now take your type into account.", "fire")


def _profile_show():
    if not PROFILE_FILE.exists():
        _narrate("no profile. run: agni profile test", "dim")
        return
    key = _get_key()
    profile = _load_profile(key)
    if not profile:
        _narrate("could not read profile.", "warn")
        return
    types_data = _load_enneagram()
    t = profile.get("type")
    w = profile.get("wing")
    info = types_data["types"].get(str(t), {})

    print(AGNI_BANNER)
    print(f"  {AW}{B}YOUR PROFILE · Type {t}w{w}{R}")
    _rule(60)
    print()
    print(f"  {AB}{info.get('name','')}{R}")
    print(_wrap(info.get("description", ""), indent="  "))
    print()
    print(f"  {A}core fear:{R}    {info.get('core_fear','')}")
    print(f"  {A}core desire:{R}  {info.get('core_desire','')}")
    print(f"  {A}passion:{R}      {info.get('passion','')}")
    print(f"  {A}virtue:{R}       {info.get('virtue','')}")
    print()
    print(f"  {A}stress arrow → type {info.get('stress_arrow','?')}{R}")
    print(_wrap(info.get("stress_description", ""), indent="    "))
    print()
    print(f"  {A}growth arrow → type {info.get('growth_arrow','?')}{R}")
    print(_wrap(info.get("growth_description", ""), indent="    "))
    print()
    print(f"  {A}key patterns:{R}")
    for p in info.get("key_patterns", []):
        print(f"    {A}▸{R} {p}")
    print()
    print(f"  {A}recommended exercises:{R}")
    for slug in info.get("recommended_exercises", []):
        print(f"    {AB}▲{R} {slug}")
    print()
    print(f"  {DK}taken: {profile.get('taken_date', '—')}{R}")
    print()


def _profile_clear():
    if not PROFILE_FILE.exists():
        _narrate("no profile to clear.", "dim")
        return
    try:
        confirm = input(f"  {RED}⚠{R} delete your enneagram profile? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        confirm = ""
    if confirm != "y":
        _narrate("cancelled.", "dim")
        return
    PROFILE_FILE.unlink()
    _narrate("profile cleared.", "done")


# ── Copilot primitives: state + save ────────────────────────────────────

def cmd_state(args):
    """Emit current state as JSON for the copilot to read programmatically."""
    entries = set()
    if DAILY_DIR.exists():
        for p in DAILY_DIR.glob("*.jrnl"):
            try:
                entries.add(datetime.fromisoformat(p.stem).date())
            except Exception:
                continue
    streak, longest = _streak_stats(entries) if entries else (0, 0)
    last_daily = max(entries).isoformat() if entries else None

    last_therapy = None
    if THERAPY_DIR.exists():
        ther = sorted(THERAPY_DIR.glob("*.jrnl"), reverse=True)
        if ther:
            last_therapy = ther[0].stem

    latest_run = _warroom_latest_run()
    warroom = None
    if latest_run:
        warroom = {
            "run": latest_run.name,
            "exercises": len(list(latest_run.glob("s*_*.jrnl"))),
            "synth": (latest_run / "synth.jrnl").exists(),
        }

    exercises = {}
    last_exercise_date = None
    ex_root = ROOT / "exercises"
    if ex_root.exists():
        for slug_dir in sorted(ex_root.iterdir()):
            if not slug_dir.is_dir():
                continue
            files = sorted(slug_dir.glob("*.jrnl"), reverse=True)
            if files:
                last_str = files[0].stem.split("_")[0]
                exercises[slug_dir.name] = {"last": last_str, "count": len(files)}
                try:
                    d = datetime.fromisoformat(last_str).date()
                    if last_exercise_date is None or d > last_exercise_date:
                        last_exercise_date = d
                except Exception:
                    pass

    last_ther_date = None
    if last_therapy:
        try:
            last_ther_date = datetime.fromisoformat(last_therapy).date()
        except Exception:
            pass

    today_blocks = set()
    today_path = DAILY_DIR / f"{date.today().isoformat()}.jrnl"
    if today_path.exists():
        try:
            cached = _load_cached_key()
            if cached:
                td = _decrypt(today_path.read_bytes(), cached)
                if td.get("morning"):
                    today_blocks.add("morning")
                if td.get("evening"):
                    today_blocks.add("evening")
                if td.get("freeform"):
                    today_blocks.add("freeform")
        except Exception:
            pass

    nudges = _compute_nudges(entries, last_ther_date, last_exercise_date, today_blocks)

    profile_info = None
    cached = _load_cached_key()
    if cached and PROFILE_FILE.exists():
        try:
            pr = _decrypt(PROFILE_FILE.read_bytes(), cached)
            tdata = _load_enneagram()["types"].get(str(pr.get("type")), {})
            profile_info = {
                "type": pr.get("type"),
                "wing": pr.get("wing"),
                "name": tdata.get("name"),
                "core_fear": tdata.get("core_fear"),
                "core_desire": tdata.get("core_desire"),
                "key_patterns": tdata.get("key_patterns", []),
                "recommended_exercises": tdata.get("recommended_exercises", []),
                "taken_date": pr.get("taken_date"),
            }
        except Exception:
            profile_info = {"status": "locked"}
    elif PROFILE_FILE.exists():
        profile_info = {"status": "locked — run agni unlock to surface type"}
    else:
        profile_info = None

    state = {
        "initialized": KEY_FILE.exists(),
        "today": date.today().isoformat(),
        "weekday": date.today().strftime("%A"),
        "streak": {"current": streak, "longest": longest, "total": len(entries)},
        "last_daily": last_daily,
        "last_therapy": last_therapy,
        "warroom": warroom,
        "exercises": exercises,
        "profile": profile_info,
        "nudges": [{"tag": t, "hint": h} for t, h in nudges],
    }
    print(json.dumps(state, indent=2))


def cmd_save(args):
    """Ingest a JSON blob (from --file or stdin) and save it encrypted under the right path."""
    key = _get_key()
    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _narrate(f"invalid JSON: {e}", "warn")
        sys.exit(1)

    today = date.today().isoformat()
    stamp = datetime.now().strftime("%H%M")

    if args.kind == "profile":
        data.setdefault("taken_date", today)
        save_jrnl(PROFILE_FILE, data, key)
        path = PROFILE_FILE
        print(json.dumps({"saved": str(path), "kind": "profile"}))
        return

    if args.kind == "daily":
        path = DAILY_DIR / f"{today}.jrnl"
        existing = load_jrnl(path, key) if path.exists() else {}
        existing.update(data)
        existing.setdefault("meta", {})["updated"] = datetime.now().isoformat()
        save_jrnl(path, existing, key)
    elif args.kind == "therapy":
        path = THERAPY_DIR / f"{today}.jrnl"
        existing = load_jrnl(path, key) if path.exists() else {"sessions": []}
        data.setdefault("time", stamp)
        existing.setdefault("sessions", []).append(data)
        save_jrnl(path, existing, key)
    elif args.kind == "warroom":
        if not args.run:
            _narrate("--run required (run-NNN or 'new')", "warn")
            sys.exit(1)
        run_dir = _new_run_dir() if args.run == "new" else (WARROOM_DIR / args.run)
        run_dir.mkdir(parents=True, exist_ok=True)
        ex_id = data.get("exercise_id", "entry")
        sid = data.get("session_id", "x")
        fname = f"s{sid}_{str(ex_id).replace('.', '_')}.jrnl"
        data.setdefault("timestamp", datetime.now().isoformat())
        save_jrnl(run_dir / fname, data, key)
        path = run_dir / fname
    elif args.kind == "exercise":
        if not args.slug:
            _narrate("--slug required for exercise", "warn")
            sys.exit(1)
        out_dir = ROOT / "exercises" / args.slug
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{today}_{stamp}.jrnl"
        data.setdefault("slug", args.slug)
        data.setdefault("timestamp", datetime.now().isoformat())
        save_jrnl(path, data, key)
    else:
        _narrate(f"unknown kind: {args.kind}", "warn")
        sys.exit(1)

    print(json.dumps({"saved": str(path), "kind": args.kind}))


# ── Dynamic context update ──────────────────────────────────────────────

DYNAMIC_START = "<!-- AGNI:DYNAMIC:BEGIN -->"
DYNAMIC_END = "<!-- AGNI:DYNAMIC:END -->"


def _compute_nudges(entries_set, last_therapy, last_exercise_date, today_blocks=None):
    nudges = []
    today = date.today()
    weekday = today.weekday()  # 0=Mon, 6=Sun
    hour = datetime.now().hour
    today_blocks = today_blocks or set()
    has_morning = "morning" in today_blocks
    has_evening = "evening" in today_blocks

    if hour < 12 and not has_morning:
        nudges.append(("morning_due", "It is morning local time and they have not done the morning 5-minute journal yet. Open with morning mode."))
    elif hour >= 18 and has_morning and not has_evening:
        nudges.append(("evening_due", "It is evening local time, they did the morning journal but have not closed out with the evening check-in. Offer evening mode."))
    elif hour >= 18 and not has_morning and not has_evening:
        nudges.append(("daily_due", "It is evening and nothing has been logged today at all. Offer the evening journal as a quick recovery — better to do half than nothing."))
    elif 12 <= hour < 18 and not has_morning:
        nudges.append(("morning_due", "Past noon and the morning journal still has not happened. Offer it gently — late is better than never."))

    therapy_stale = (last_therapy is None) or ((today - last_therapy).days >= 6)
    is_weekend = weekday in (5, 6)
    if is_weekend and therapy_stale:
        nudges.append(("therapy_weekend", "It's the weekend and they haven't done therapy in at least 6 days. Poke them gently toward therapist mode — name a specific thing from their recent daily entries if you can see one."))

    if last_exercise_date is None or (today - last_exercise_date).days >= 10:
        nudges.append(("exercise_stale", "It's been 10+ days since any standalone exercise. Offer exercise mode — pick ONE slug based on their recent state, never a menu dump."))

    entries_this_week = sum(1 for i in range(7) if (today - timedelta(days=i)) in entries_set)
    if entries_this_week <= 2 and len(entries_set) > 5:
        nudges.append(("low_consistency", "Only 2 or fewer daily entries in the last 7 days. This is a drift signal — name it gently and pick the simplest re-entry (daily mode, one prompt)."))

    return nudges


def _update_claude_context():
    claude_md = SCRIPT_DIR / "CLAUDE.md"
    if not claude_md.exists():
        return

    # Gather metadata (no decryption needed — filenames only)
    entries = set()
    if DAILY_DIR.exists():
        for p in DAILY_DIR.glob("*.jrnl"):
            try:
                entries.add(datetime.fromisoformat(p.stem).date())
            except Exception:
                continue

    streak, longest = _streak_stats(entries) if entries else (0, 0)
    total = len(entries)
    last_daily = max(entries) if entries else None

    last_therapy = None
    if THERAPY_DIR.exists():
        ther_files = sorted(THERAPY_DIR.glob("*.jrnl"), reverse=True)
        if ther_files:
            try:
                last_therapy = datetime.fromisoformat(ther_files[0].stem).date()
            except Exception:
                pass

    latest_run = _warroom_latest_run()
    warroom_line = "no runs"
    if latest_run:
        n = len(list(latest_run.glob("s*_*.jrnl")))
        synth = (latest_run / "synth.jrnl").exists()
        warroom_line = f"{latest_run.name} · {n} exercises · synth {'✓' if synth else '—'}"

    exercise_lines = []
    last_exercise_date = None
    ex_root = ROOT / "exercises"
    if ex_root.exists():
        for slug_dir in sorted(ex_root.iterdir()):
            if not slug_dir.is_dir():
                continue
            files = sorted(slug_dir.glob("*.jrnl"), reverse=True)
            if files:
                last_date_str = files[0].stem.split("_")[0]
                try:
                    d = datetime.fromisoformat(last_date_str).date()
                    if last_exercise_date is None or d > last_exercise_date:
                        last_exercise_date = d
                except Exception:
                    pass
                exercise_lines.append(f"  - `{slug_dir.name}` — last run {last_date_str} · {len(files)} total")

    today_blocks = set()
    today_path = DAILY_DIR / f"{date.today().isoformat()}.jrnl"
    if today_path.exists() and cached_key:
        try:
            td = _decrypt(today_path.read_bytes(), cached_key)
            if td.get("morning"):
                today_blocks.add("morning")
            if td.get("evening"):
                today_blocks.add("evening")
            if td.get("freeform"):
                today_blocks.add("freeform")
        except Exception:
            pass

    nudges = _compute_nudges(entries, last_therapy, last_exercise_date, today_blocks)

    today = date.today()
    weekday_name = today.strftime("%A")

    lines = [DYNAMIC_START, "", "## Current State (auto-updated)", "",
             f"*Refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M')} · {weekday_name}*", ""]
    # Profile — only readable with cached key (type and wing are not secret, but rest is)
    profile_line = "not taken"
    profile_patterns = []
    profile_exercises = []
    cached_key = _load_cached_key()
    if cached_key and PROFILE_FILE.exists():
        try:
            pr = _decrypt(PROFILE_FILE.read_bytes(), cached_key)
            tdata = _load_enneagram()["types"].get(str(pr.get("type")), {})
            profile_line = f"Type {pr.get('type')}w{pr.get('wing')} · {tdata.get('name','')}"
            profile_patterns = tdata.get("key_patterns", [])
            profile_exercises = tdata.get("recommended_exercises", [])
        except Exception:
            profile_line = "locked"
    elif PROFILE_FILE.exists():
        profile_line = "locked (run `agni unlock`)"

    lines.append(f"- **Profile**: {profile_line}")
    if profile_patterns:
        lines.append("  - *key patterns to watch:* " + "; ".join(profile_patterns[:3]))
    if profile_exercises:
        lines.append("  - *recommended for type:* " + ", ".join(profile_exercises))
    lines.append(f"- **Streak**: current {streak} days · longest {longest} days · total {total} entries")
    lines.append(f"- **Last daily**: {last_daily.isoformat() if last_daily else 'never'}")
    lines.append(f"- **Last therapy**: {last_therapy.isoformat() if last_therapy else 'never'}")
    lines.append(f"- **War room**: {warroom_line}")
    if exercise_lines:
        lines.append("- **Exercises run**:")
        lines.extend(exercise_lines)
    else:
        lines.append("- **Exercises run**: none")
    lines.append("")

    if nudges:
        lines.append("### Nudges (act on these)")
        lines.append("")
        for tag, desc in nudges:
            lines.append(f"- **`{tag}`** — {desc}")
        lines.append("")
    else:
        lines.append("### Nudges")
        lines.append("")
        lines.append("None. User is on track across all modes.")
        lines.append("")

    lines.append("*This block is auto-regenerated by agni.py after every command. Do not edit by hand.*")
    lines.append("")
    lines.append(DYNAMIC_END)
    new_block = "\n".join(lines)

    try:
        content = claude_md.read_text(encoding="utf-8")
    except Exception:
        return
    if DYNAMIC_START in content and DYNAMIC_END in content:
        before = content.split(DYNAMIC_START)[0]
        after = content.split(DYNAMIC_END)[-1]
        new_content = before + new_block + after
    else:
        new_content = content.rstrip() + "\n\n---\n\n" + new_block + "\n"
    try:
        claude_md.write_text(new_content, encoding="utf-8")
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(prog="agni", description="Fire journal")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init")
    sub.add_parser("unlock")
    sub.add_parser("lock")

    d = sub.add_parser("daily")
    d.add_argument("--evening", action="store_true")

    sub.add_parser("therapy")

    wr = sub.add_parser("warroom")
    wr_sub = wr.add_subparsers(dest="warroom_cmd")
    wr_sub.add_parser("status")
    wr_sub.add_parser("run")
    wr_step = wr_sub.add_parser("step")
    wr_step.add_argument("step")
    wr_sub.add_parser("synth")
    wr_show = wr_sub.add_parser("show")
    wr_show.add_argument("what")

    rd = sub.add_parser("read")
    rd.add_argument("date", nargs="?")

    sub.add_parser("status")
    sub.add_parser("streak")
    sub.add_parser("list")

    ex = sub.add_parser("exercise")
    ex_sub = ex.add_subparsers(dest="exercise_cmd")
    ex_sub.add_parser("list")
    ex_run = ex_sub.add_parser("run")
    ex_run.add_argument("slug")
    ex_hist = ex_sub.add_parser("history")
    ex_hist.add_argument("slug")
    ex_create = ex_sub.add_parser("create")
    ex_create.add_argument("--slug", required=True)
    ex_create.add_argument("--file", help="JSON file to ingest (default stdin)")
    ex_create.add_argument("--force", action="store_true")

    pr = sub.add_parser("profile")
    pr_sub = pr.add_subparsers(dest="profile_cmd")
    pr_sub.add_parser("test")
    pr_sub.add_parser("show")
    pr_sub.add_parser("clear")

    sub.add_parser("state")

    sv = sub.add_parser("save")
    sv.add_argument("kind", choices=["daily", "therapy", "warroom", "exercise", "profile"])
    sv.add_argument("--slug", help="required for exercise kind")
    sv.add_argument("--run", help="required for warroom kind (run-NNN or 'new')")
    sv.add_argument("--file", help="JSON file to ingest (default stdin)")

    vl = sub.add_parser("vault")
    vl_sub = vl.add_subparsers(dest="vault_cmd")
    for name in ("sync", "open", "clean"):
        vp = vl_sub.add_parser(name)
        vp.add_argument("--path", help="override vault export path")

    args = p.parse_args()
    if not args.cmd:
        print(AGNI_BANNER)
        print(f"  {AD}usage: agni <command>{R}")
        print(f"  {AD}commands: init · daily · therapy · warroom · exercise · vault · read · streak · status · list · unlock · lock{R}")
        print(f"  {AD}start with: agni init{R}")
        return

    handlers = {
        "init": cmd_init, "unlock": cmd_unlock, "lock": cmd_lock,
        "daily": cmd_daily, "therapy": cmd_therapy, "warroom": cmd_warroom,
        "read": cmd_read, "status": cmd_status, "list": cmd_list,
        "streak": cmd_streak, "exercise": cmd_exercise, "vault": cmd_vault,
        "state": cmd_state, "save": cmd_save, "profile": cmd_profile,
    }
    handlers[args.cmd](args)
    try:
        _update_claude_context()
    except Exception:
        pass


if __name__ == "__main__":
    main()
