# iZSH

**An observable Zsh for AI-assisted command-line workflows.**

iZSH is a small fork of [Zsh](https://www.zsh.org/) that records command
lifecycle events and captures command output for external tools. It is designed
to give an AI assistant the context it actually needs - the command, working
directory, exit status, duration, stdout, and stderr - without replacing the
terminal or embedding model calls in the shell.

> [!IMPORTANT]
> iZSH is an early prototype. The current prebuilt release supports macOS on
> Apple silicon only, and the event schema may change before 1.0.

## Why iZSH?

Most shell integrations can observe the command line and its exit code, but not
the complete output that explains what happened. Terminal emulation and prompt
scraping can recover more context, at the cost of owning the terminal UX and
adding another compatibility layer.

iZSH takes a narrower approach: add capture at the shell execution layer and
leave everything else outside the shell.

```text
terminal
   |
   v
iZSH (execute commands, preserve normal Zsh behavior)
   |                         |
   v                         v
terminal output        local session events and output files
                                 |
                                 v
                        external AI integration
```

This boundary is deliberate:

- iZSH executes commands, captures their results, and emits local events.
- An external integration decides what to read, retain, redact, or send to a
  model.
- No model SDK, network client, or AI decision-making belongs in the shell
  core.

## What Stays the Same

iZSH is Zsh, not a terminal emulator or a shell wrapper built around a PTY. It
loads the user's normal Zsh startup files, so existing aliases, functions,
completion, prompts, plugins, environment variables, and history settings can
continue to work as configured.

The `izsh` launcher adds an isolated event directory for each invocation, then
starts the installed shell. Ordinary terminal output still reaches the terminal
while a copy is recorded for the external consumer.

## Install the Preview

Download the macOS arm64 package from the
[iZSH v0.1.0 release](https://github.com/noexcs/izsh/releases/tag/izsh-v0.1.0):

```sh
curl -LO https://github.com/noexcs/izsh/releases/download/izsh-v0.1.0/izsh-v0.1.0-macos-arm64.tar.gz
curl -LO https://github.com/noexcs/izsh/releases/download/izsh-v0.1.0/izsh-v0.1.0-macos-arm64.tar.gz.sha256
shasum -a 256 -c izsh-v0.1.0-macos-arm64.tar.gz.sha256
tar -xzf izsh-v0.1.0-macos-arm64.tar.gz
cd izsh-v0.1.0-macos-arm64
./install.sh
```

The default locations are:

```text
runtime:  ~/.izsh
launcher: ~/bin/izsh
```

Make sure `~/bin` is in `PATH`, then start an interactive session:

```sh
izsh
```

The installer does not edit `.zshrc`, `.zprofile`, or history files. Custom
installation locations are also supported:

```sh
IZSH_INSTALL_ROOT=/path/to/runtime \
IZSH_LAUNCHER_DIR=/path/to/bin \
./install.sh
```

If macOS blocks the downloaded executable, remove its quarantine attribute:

```sh
xattr -dr com.apple.quarantine ~/.izsh ~/bin/izsh
```

## Capture Model

Every launcher invocation receives a timestamped, unique session ID. Concurrent
terminal tabs therefore write to separate directories:

```text
~/.izsh/sessions/
`-- 20261007T135718Z-a1b2c3/
    |-- events.ndjson
    `-- commands/
        |-- 1.stdout
        |-- 1.stderr
        |-- 2.stdout
        `-- 2.stderr
```

`events.ndjson` contains four event types:

- `shell_start`
- `command_start`
- `command_end`
- `shell_end`

A command is uniquely addressable as `<session_id>:<command_id>`. For example:

```json
{"type":"command_start","session_id":"20261007T135718Z-a1b2c3","command_id":1,"id":"20261007T135718Z-a1b2c3:1","source":"interactive","command":"git status","cwd":"/workspace","stdout_path":"/home/user/.izsh/sessions/20261007T135718Z-a1b2c3/commands/1.stdout","stderr_path":"/home/user/.izsh/sessions/20261007T135718Z-a1b2c3/commands/1.stderr"}
{"type":"command_end","session_id":"20261007T135718Z-a1b2c3","command_id":1,"id":"20261007T135718Z-a1b2c3:1","exit":0,"duration_ms":18,"stdout_bytes":42,"stderr_bytes":0,"stdout_path":"/home/user/.izsh/sessions/20261007T135718Z-a1b2c3/commands/1.stdout","stderr_path":"/home/user/.izsh/sessions/20261007T135718Z-a1b2c3/commands/1.stderr"}
```

Consumers should wait for `command_end`, match its `id`, and then read the
referenced output files. This avoids guessing which output belongs to a command
when multiple shells are active.

Capture is opt-in at the shell-core level and is enabled by the packaged
launcher through these environment variables:

- `IZSH_SESSION_ID`
- `IZSH_SESSION_DIR`
- `IZSH_EVENTS_FILE`

If capture cannot be initialized, command execution continues normally.

## Current Scope

The prototype covers ordinary interactive and non-interactive commands,
including builtins, pipelines, compound command lines, large output, concurrent
sessions, interruption with Ctrl-C, and preservation of explicit redirection
behavior. The black-box PTY suite lives in
[`Test/izsh_capture_pty.py`](Test/izsh_capture_pty.py).

Known limitations:

- The prebuilt package is currently macOS arm64 only.
- Session data is not cleaned up automatically.
- Captured output does not yet have a hard per-command size limit.
- Output files can contain secrets printed by commands.
- The event schema is not yet a stable public API.

iZSH creates session data with user-only permissions where the platform allows
it, but consumers are still responsible for retention, redaction, consent, and
any transfer to external services.

## Build from Source

iZSH uses the standard Zsh build system. See [`INSTALL`](INSTALL) and
[`MACHINES`](MACHINES) for the complete upstream instructions. A typical
development build is:

```sh
./Util/preconfig
./configure
make -j4
python3 Test/izsh_capture_pty.py
```

The final command runs the iZSH-specific capture and launcher regression suite.

## Project Direction

The shell side of this project should remain small and deterministic. Near-term
work belongs in two separate areas:

- **Shell producer:** reliable event emission, output capture, limits, and
  compatibility.
- **External consumer:** cleanup policy, secret handling, context selection,
  model calls, and user-facing AI workflows.

Keeping that separation allows iZSH to remain familiar and unobtrusive while
giving AI tools substantially better evidence than an exit code alone.

## Upstream and License

iZSH is based on the open-source [Zsh project](https://github.com/zsh-users/zsh).
The original upstream README is preserved in [`README.zsh`](README.zsh).

The project is distributed under the Zsh license; see [`LICENCE`](LICENCE).
