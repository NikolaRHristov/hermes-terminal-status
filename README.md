<p align="center">
  <strong>Hermes Terminal Status</strong><br>
  <sub>OSC 7501 status reports for Hermes Agent sessions</sub>
</p>

---

# [Hermes Terminal Status] 📡

> [!NOTE]
>
> A Hermes Agent plugin that reports a session's live state to its terminal with
> OSC 7501, the Program Status Protocol. A terminal that lists agents by
> status - herdr-core, the engine under the Herdr desktop and phone apps, first
> among them - then shows Hermes as an agent with a live state instead of a
> plain shell. _One plugin, one module, nothing patched._

[![plugin](https://img.shields.io/static/v1?label=plugin&message=v1.0.0&color=purple)](https://github.com/NikolaRHristov/hermes-terminal-status/blob/Current/plugin.yaml)
[![hermes](https://img.shields.io/static/v1?label=hermes&message=0.23.1%2B&color=blue)](https://github.com/NousResearch/hermes-agent)
[![platform](https://img.shields.io/static/v1?label=platform&message=macOS%20%7C%20Linux&color=lightgreen)](<>)
[![license](https://img.shields.io/static/v1?label=license&message=MIT&color=lightgrey)](https://github.com/NikolaRHristov/hermes-terminal-status/blob/Current/LICENSE)

---

## The Problem 🔥

A terminal learns that the program in a pane is an agent the same way it learns
anything else about that program: the program tells it. OSC 7501 is the
channel - `ESC ] 7501 ; state=<state> BEL` - and a terminal that understands it
keeps one state per pane: idle, working, needs you, done.

herdr-core builds its Agents list out of those reports and out of nothing else.
Coding agents that send them appear there with a live state. Hermes did not send
them, so a Hermes session appeared as a plain shell: correct by the engine's own
rules, and wrong for the person watching the list.

Sending one is not a one-line change, because the process that sees the events
is not the process that owns the terminal.

- **The events run in the gateway.** When Hermes runs its gateway, every turn,
  tool call, approval and question runs in the gateway process, which has no
  controlling terminal. Hook callbacks fire there.
- **The terminal belongs to a viewer.** The classic CLI's viewer owns it; under
  the TUI, the launcher owns it and the interface draws on it.
- **`/dev/tty` is the wrong answer.** It resolves to whichever terminal the
  calling process opened, and the gateway opened none.

What the machine does publish is which device is showing which session. This
plugin reads that, and writes to the device directly - a write that needs no
controlling terminal at all.

---

## How It Works ⚙️

**`Pipeline`**

```text
 HERMES EVENT ─────► plugin hook ─────► device ─────► terminal lists the agent
                      (gateway)          lookup        (idle / working / done)
                         │
                         ├─ session starts     → idle
                         ├─ turn starts        → working
                         ├─ clarify / approval → blocked
                         ├─ turn ends          → done
                         └─ run fails          → error

    The device, from two facts the machine already has:
    • TUI    HERMES_TUI_ACTIVE_SESSION_FILE + the pane variable, both visible in
             the live process table → the mapping is exact, not a guess
    • CLI    $HERMES_HOME/terminal-sessions/tty-<device> → the device path
    • gate   a real character device inside a pane that consumes status reports
```

Four steps, in the order they run.

1. **Find the device.** One read of the process table (`ps -axEww`) per cache
   window. A TUI process publishes the session file it was handed and inherits
   the pane variable, so its line names both the session and the device. A
   classic CLI session is located by inverting its terminal record's name.
2. **Gate it.** A device is reported to only when `os.stat` says it is a
   character device and the process table says it belongs to a pane of a
   status-consuming terminal. A record whose name is not a device path is
   refused, never turned into a plausible-looking one.
3. **Map the event.** Nine lifecycle hooks, one state each. An approval decided
   by an auxiliary model is not a person waiting, so it does not count as
   blocked.
4. **Write.** One OSC 7501 sequence, about 30 bytes, opened with
   `O_WRONLY | O_NOCTTY | O_NONBLOCK`. No controlling terminal, no blocking, no
   subprocess left behind.

`register()` wires the hooks and returns. There is no tool, no middleware, no
background thread and no second process.

---

## What It Reports 💬

- **A session starts, or is reset** - `idle`.
- **A turn begins** - `working`.
- **Hermes asks you a question, or waits for your approval** - `blocked`.
- **You answer or decide** - `working`.
- **A turn finishes** - `done`.
- **A run fails** - `error`.
- **You interrupt a run** - `idle`.

Each report carries an application name, `hermes` by default, so the terminal
can label the agent it is showing.

**`The wire format`**

```text
\x1b]7501;state=working:app=hermes\x07
   │    │       │          │        └─ BEL: end of the report
   │    │       │          └─ the name the terminal shows beside the agent
   │    │       └─ idle | working | done | blocked | error
   │    └─ Program Status Protocol
   └─ ESC: an escape sequence, which the terminal consumes instead of drawing
```

A terminal that does not understand it prints nothing, which is why the default
is to report only into panes that asked for it.

---

## Install ⚡

### From this repository (recommended)

**`Terminal`**

```sh
hermes plugins install NikolaRHristov/hermes-terminal-status --enable
```

The plugin is one directory: a manifest and a module. Nothing is downloaded, and
`register()` never writes outside the device it reports to.

### From a checkout

**`Terminal`**

```sh
git clone https://github.com/NikolaRHristov/hermes-terminal-status.git
cd hermes-terminal-status
ln -s "$(pwd)" ~/.hermes/plugins/hermes-terminal-status
hermes plugins enable hermes-terminal-status
```

Use this route for development: the symlink keeps the repository the only source
of truth, and an edit is live after the gateway reloads plugins.

### Requirements

- **Hermes Agent 0.23.1 or newer.**
- **macOS or Linux.** The device lookup reads the process table with `ps`, which
  Windows does not provide.
- **A terminal that consumes OSC 7501,** or
  `HERMES_TERMINAL_STATUS_TERMINALS=any` for one that silently ignores escape
  sequences it does not know.

> [!IMPORTANT]
>
> Sessions that were already open keep the plugins they started with. Start a
> new session after enabling, or the pane keeps reporting nothing.

### Verify it's working ✅

**`Terminal`**

```sh
hc ls
# {"hidden_finished":2,"terminals":[{"address":"t4","agent":"hermes","status":"working",...}]}

hermes plugins show hermes-terminal-status
# Status: enabled
# Runs in: Hermes process - runs in the plugin host (plugins.isolation: host)
```

Send a prompt and the status moves from `working` to `done`. Ask Hermes a
question with its `clarify` tool and the pane reads as needing you until you
answer.

If nothing appears, trace the reports instead of guessing:

**`Terminal`**

```sh
export HERMES_TERMINAL_STATUS_LOG="$HOME/hermes-terminal-status.jsonl"
# one JSON line per report: {"session":...,"state":...,"device":...,"written":true}
```

---

## Where It Reports 📡

By default the plugin reports only inside panes of a terminal engine that
consumes status reports, recognised by the pane variable that engine exports to
the programs it runs. herdr-core exports `HC_TERMINAL_ID`. A session anywhere
else is left alone, because writing escape sequences into a terminal that never
asked for them is not a favour.

- **Pane variables** - `HERMES_TERMINAL_STATUS_PANES`, a comma-separated list,
  default `HC_TERMINAL_ID`. Add the variable another engine exports to reach it.
- **Ignored pane variables** - `HERMES_TERMINAL_STATUS_IGNORE_PANES`, default
  `HERDR_PANE_ID`. An engine that detects agents by itself needs no reports, and
  two reporters disagree with each other.
- **Every terminal** - `HERMES_TERMINAL_STATUS_TERMINALS=any` reports in any
  terminal. Use it only where unknown escape sequences are ignored, which is
  most modern terminals and no multiplexer.

---

## Configuration 🎛️

Every setting is an environment variable, read from the Hermes process.

- **`HERMES_TERMINAL_STATUS_APP`** - the application name carried by each
  report. Default `hermes`; empty sends none.
- **`HERMES_TERMINAL_STATUS_PANES`** - pane variables that mark a
  status-consuming terminal. Default `HC_TERMINAL_ID`.
- **`HERMES_TERMINAL_STATUS_IGNORE_PANES`** - pane variables whose panes are
  never reported to. Default `HERDR_PANE_ID`.
- **`HERMES_TERMINAL_STATUS_TERMINALS`** - `panes` (default) or `any`.
- **`HERMES_TERMINAL_STATUS_TTL`** - seconds one process-table read is reused.
  Default `5`.
- **`HERMES_TERMINAL_STATUS_LOG`** - path of a JSON-lines trace of every report,
  for working out why a terminal shows nothing.

**`Terminal`**

```sh
export HERMES_TERMINAL_STATUS_TERMINALS=any # report in every terminal
export HERMES_TERMINAL_STATUS_APP=hermes    # the name the terminal shows
export HERMES_TERMINAL_STATUS_TTL=2         # fresher device lookup
```

---

## Layout 🏗️

```text
hermes-terminal-status/
├── plugin.yaml    manifest: name, hooks, requirements, platforms
├── __init__.py    the whole plugin
├── README.md
└── LICENSE
```

**One plugin, one module.** No package to import, no dependency to install, no
second plugin to run beside it, and no patch to Hermes. Everything the plugin
does is in `__init__.py`; everything Hermes needs to load it is in
`plugin.yaml`.

---

## What It Reads And Writes 🔍

- **Reads** the process table (`ps -axEww -o pid=,tty=,command=`),
  `HERMES_TUI_ACTIVE_SESSION_FILE` for the live TUI session, and
  `$HERMES_HOME/terminal-sessions/tty-*` for the default home and every profile
  home.
- **Writes** one OSC 7501 sequence, about 30 bytes, per state change, to devices
  that a pane of a status-consuming terminal owns. With
  `HERMES_TERMINAL_STATUS_LOG` set, it appends a line per report to that file.
- **No network access, no credentials, no telemetry.** The single `ps` call per
  cache window is the only subprocess, and it ends when the call returns.

---

## Limits ⚠️

- **`done` stays until the next prompt,** because Hermes has no hook for the
  person coming back.
- **A session change can lag a moment.** Right after `/new`, `/resume` or
  compression starting a new session, the device can be stale for a second;
  reports resume after that.
- **Two Hermes contracts are load-bearing:** `HERMES_TUI_ACTIVE_SESSION_FILE`
  and the `terminal-sessions/tty-*` record name. If a Hermes release changes
  either, reports stop quietly rather than loudly.
- **`session.terminal_continue` must stay enabled** in Hermes' configuration, or
  the classic CLI writes no terminal record and only TUI sessions can be
  located.
- **A session whose device cannot be found is skipped.** Messaging platforms,
  cron jobs, kanban workers and sessions in terminals that consume no status
  reports are all in that group, by design.

---

## Relationship To Other Status Reporters 🔗

- **herdr-core** consumes the reports; this plugin produces them. Neither needs
  to know about the other beyond the escape sequence.
- **Herdr's own Hermes integration** (`herdr integration install hermes`)
  reports a resumable session id to the older Herdr TUI. It carries no state and
  does not fire in herdr-core panes, so it cannot produce an Agents row.
- **Any other status reporter** covers the same ground. Run one, not two: two
  reporters write competing states for the same session.

---

## Contributing 🫱🏻🫲🏻

The plugin is deliberately small and stays that way: one manifest, one module,
no build step, no test suite, no CI. A change should keep it a single file that
Hermes can load, and should degrade rather than raise - a session with no
reportable terminal is a normal case, not an error.

Validate before sending a change:

**`Terminal`**

```sh
hermes plugins validate .
# Validation passed.
```

---

## License 📜

MIT. See
[LICENSE](https://github.com/NikolaRHristov/hermes-terminal-status/blob/Current/LICENSE).
