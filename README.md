# Hermes Terminal Status

A Hermes Agent plugin that reports a session's live state to its terminal with OSC 7501, the Program
Status Protocol, so a terminal that lists agents by status shows Hermes as one instead of a plain
shell.

A terminal only learns that the program in a pane is an agent when that program tells it so. Coding
agents that do say it appear in those lists with a state: idle, working, needs you, done. Hermes did
not say it. This plugin makes it say it, for every surface a person actually types into.

---

## The one sentence

The plugin turns Hermes' lifecycle hooks into one short OSC 7501 sequence on the device that is
showing the session.

That sentence is the whole design. Find the device, map the event to a state, write the sequence, and
do nothing when the device cannot be found.

---

## The problem it answers

A terminal emulator that understands OSC 7501 - `ESC ] 7501 ; state=<state> BEL` - lists the program
in a pane as an agent with a state. herdr-core, the engine under the Herdr desktop and phone apps, is
the first consumer: it decides what to list from the reports it receives and from nothing else. So a
Hermes session with no reporter appears there as a plain shell, however visible its pane is.

The awkward part is that the process which sees the events is not the process which owns the terminal.

- **The events run in the gateway.** When Hermes runs its gateway, every turn, tool call, approval and
  question runs in the gateway process, which has no controlling terminal. Hook callbacks fire there.
- **The terminal belongs to someone else.** The classic CLI's viewer owns it; under the TUI, the
  launcher owns it and the interface draws on it.
- **`/dev/tty` is therefore the wrong answer.** It belongs to whichever process opened it, and the
  gateway has none.

What the machine does publish is which device is showing which session. The plugin reads that, and
writes to the device directly - a write that needs no controlling terminal at all.

---

## What it reports

- **A session starts, or is reset** - idle.
- **A turn begins** - working.
- **Hermes asks you a question, or waits for your approval** - blocked.
- **You answer or decide** - working.
- **A turn finishes** - done.
- **A run fails** - error.
- **You interrupt a run** - idle.

Each report carries an application name, `hermes` by default, so the terminal can label the agent.
Approvals decided by an auxiliary model do not count as blocked: nobody is waiting on a person.

---

## Where it reports

By default the plugin reports only inside panes of a terminal engine that consumes status reports,
recognised by the pane variable that engine exports to the programs it runs. herdr-core exports
`HC_TERMINAL_ID`. A session anywhere else is left alone, because writing escape sequences into a
terminal that never asked for them is not a favour.

- **Pane variables** - `HERMES_TERMINAL_STATUS_PANES`, a comma-separated list, default
  `HC_TERMINAL_ID`. Add the variable another engine exports to reach it.
- **Ignored pane variables** - `HERMES_TERMINAL_STATUS_IGNORE_PANES`, default `HERDR_PANE_ID`. An
  engine that detects agents by itself needs no reports, and two reporters disagree with each other.
- **Every terminal** - `HERMES_TERMINAL_STATUS_TERMINALS=any` reports in any terminal. Use it only
  where unknown escape sequences are ignored, which is most modern terminals and no multiplexer.

---

## Install

From the plugin catalog or straight from this repository:

```sh
hermes plugins install NikolaRHristov/hermes-terminal-status --enable
```

From a local checkout, place the directory in the profile's plugin directory and enable it:

```sh
ln -s /path/to/hermes-terminal-status "$HERMES_HOME/plugins/hermes-terminal-status"
hermes plugins enable hermes-terminal-status
```

Requirements:

- **Hermes Agent 0.23.1 or newer.**
- **macOS or Linux.** The plugin reads the process table with `ps`, which Windows does not provide.
- **A terminal that consumes OSC 7501,** or `HERMES_TERMINAL_STATUS_TERMINALS=any` for one that
  silently ignores it.

Enabling asks a running gateway to load the plugin immediately. Sessions that were already open keep
the plugins they started with, so start a new session to see reports.

Check that it works:

```sh
hc ls        # herdr-core: the pane's agent should read hermes with a status
```

Send a prompt and the status moves from `working` to `done`. Ask Hermes a question with its `clarify`
tool and the pane reads as needing you until you answer.

---

## Settings

Every setting is an environment variable, read from the Hermes process.

- **`HERMES_TERMINAL_STATUS_APP`** - the application name carried by each report. Default `hermes`;
  empty sends none.
- **`HERMES_TERMINAL_STATUS_PANES`** - pane variables that mark a status-consuming terminal. Default
  `HC_TERMINAL_ID`.
- **`HERMES_TERMINAL_STATUS_IGNORE_PANES`** - pane variables whose panes are never reported to.
  Default `HERDR_PANE_ID`.
- **`HERMES_TERMINAL_STATUS_TERMINALS`** - `panes` (default) or `any`.
- **`HERMES_TERMINAL_STATUS_TTL`** - seconds one process-table read is reused. Default `5`.
- **`HERMES_TERMINAL_STATUS_LOG`** - path of a JSON-lines trace of every report, for working out why
  a terminal shows nothing.

---

## How it works

Two halves, in one file, neither of them patching Hermes.

- **The facts.** Hermes already records where a session is. The TUI launcher hands
  `HERMES_TUI_ACTIVE_SESSION_FILE` to the process it starts, and that process inherits the pane
  variable of the terminal engine it runs in; both are visible in the live process table, so a TUI
  session maps to its device exactly. The classic CLI writes
  `$HERMES_HOME/terminal-sessions/tty-<device>` naming the session on that terminal, and the file
  name inverts to the device path.
- **The write.** Each hook takes the event's session, finds its device, and writes one OSC 7501
  sequence to it with `O_WRONLY | O_NOCTTY | O_NONBLOCK`. No controlling terminal is involved, so a
  gateway process can do it.
- **The gate.** A device is reported to only when it is a real character device inside a pane named
  above. A record whose name is not a device path is refused rather than turned into a
  plausible-looking one.
- **The cache.** One process-table read serves a turn's hooks; it costs about 55 ms and 350 KB, so it
  is reused for `HERMES_TERMINAL_STATUS_TTL` seconds.

---

## What it reads and writes

- **Reads** the process table (`ps -axEww -o pid=,tty=,command=`), `HERMES_TUI_ACTIVE_SESSION_FILE`
  for the live TUI session, and `$HERMES_HOME/terminal-sessions/tty-*` for the default home and every
  profile home.
- **Writes** one OSC 7501 sequence, about 30 bytes, per state change, to devices a pane of a
  status-consuming terminal owns.
- **No network access, no credentials, no telemetry.** It starts no long-lived subprocess: the single
  `ps` call per cache window is the only one.

---

## Limits

- **`done` stays until the next prompt,** because Hermes has no hook for the person coming back.
- **A session change can lag a moment.** Right after `/new`, `/resume` or compression starting a new
  session, the device can be stale for a second; reports resume after that.
- **Two Hermes contracts are load-bearing:** `HERMES_TUI_ACTIVE_SESSION_FILE` and the
  `terminal-sessions/tty-*` record name. If a Hermes release changes either, reports stop quietly
  rather than loudly.
- **`session.terminal_continue` must stay enabled** in Hermes' configuration, or the classic CLI
  writes no terminal record and only TUI sessions can be located.
- **A session whose device cannot be found is skipped.** Messaging platforms, cron jobs, kanban
  workers and sessions in terminals that consume no status reports are all in that group, by design.

---

## Uninstall

```sh
hermes plugins disable hermes-terminal-status
hermes plugins remove hermes-terminal-status
```

---

## Licence

MIT. See `LICENSE`.
