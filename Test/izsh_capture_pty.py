#!/usr/bin/env python3
"""Black-box PTY checks for the opt-in izsh command recorder."""

import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import tempfile
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZSH = os.path.join(ROOT, "Src", "zsh")
LAUNCHER = os.path.join(ROOT, "packaging", "izsh")
PROMPT = b"IZSH_PROMPT>"


def fail(message):
    raise AssertionError(message)


class Shell:
    def __init__(self, events_file, session_id, session_dir, zdotdir=None):
        self.events_file = events_file
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ["IZSH_EVENTS_FILE"] = events_file
            os.environ["IZSH_SESSION_ID"] = session_id
            os.environ["IZSH_SESSION_DIR"] = session_dir
            os.environ["PS1"] = "IZSH_PROMPT>"
            os.environ["PS2"] = "IZSH_CONT>"
            if zdotdir is None:
                os.execv(ZSH, [ZSH, "-if"])
            os.environ["ZDOTDIR"] = zdotdir
            os.execv(ZSH, [ZSH, "-i"])
        self.read_until(PROMPT)

    def read_until(self, needle, timeout=8):
        data = bytearray()
        deadline = time.time() + timeout
        while needle not in data:
            remaining = deadline - time.time()
            if remaining <= 0:
                fail("timed out waiting for %r; got %r" % (needle, bytes(data[-400:])))
            readable, _, _ = select.select([self.fd], [], [], remaining)
            if not readable:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def drain(self, timeout=0.3):
        data = bytearray()
        deadline = time.time() + timeout
        while time.time() < deadline:
            readable, _, _ = select.select([self.fd], [], [], deadline - time.time())
            if not readable:
                break
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def command(self, text):
        os.write(self.fd, (text + "\n").encode())
        output = self.read_until(PROMPT)
        return (output + self.drain(0.3)).replace(b"\r\n", b"\n")

    def interrupt(self, text):
        os.write(self.fd, (text + " & p=$!; print -r -- __IZSH_PID__$p; wait $p\n").encode())
        output = bytearray()
        deadline = time.time() + 3
        match = None
        while time.time() < deadline and not match:
            readable, _, _ = select.select([self.fd], [], [], deadline - time.time())
            if not readable:
                continue
            output.extend(os.read(self.fd, 65536))
            match = re.search(rb"__IZSH_PID__(\d+)", output)
        if not match:
            fail("could not observe interrupted process pid: %r" % output[-500:])
        time.sleep(0.2)
        os.write(self.fd, b"\003")
        os.write(self.fd, b"\n")
        try:
            os.kill(int(match.group(1)), signal.SIGINT)
        except OSError:
            pass
        os.write(self.fd, b"print -r -- __IZSH_INTERRUPTED__\n")
        output.extend(self.read_until(b"__IZSH_INTERRUPTED__", timeout=3))
        return (bytes(output) + self.drain(0.2)).replace(b"\r\n", b"\n")

    def close(self):
        try:
            os.write(self.fd, b"exit\n")
            deadline = time.time() + 2
            while time.time() < deadline:
                waited, _ = os.waitpid(self.pid, os.WNOHANG)
                if waited == self.pid:
                    break
                readable, _, _ = select.select([self.fd], [], [], 0.05)
                if readable:
                    try:
                        os.read(self.fd, 65536)
                    except OSError:
                        pass
                time.sleep(0.05)
            else:
                raise ChildProcessError
        except (OSError, ChildProcessError):
            try:
                os.kill(self.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                os.waitpid(self.pid, os.WNOHANG)
            except (OSError, ChildProcessError):
                pass
        finally:
            try:
                os.close(self.fd)
            except OSError:
                pass


def load_events(path):
    with open(path, "r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def event_for(events, fragment):
    starts = [event for event in events
              if event["type"] == "command_start" and fragment in event["command"]]
    if len(starts) != 1:
        fail("expected one start event containing %r, got %r" % (fragment, starts))
    start = starts[0]
    ends = [event for event in events
            if event["type"] == "command_end" and event["id"] == start["id"]]
    if len(ends) != 1:
        fail("missing end event for %r" % fragment)
    return start, ends[0]


def session_environment(session_dir, session_id):
    os.mkdir(os.path.join(session_dir, "commands"))
    return dict(
        os.environ,
        IZSH_EVENTS_FILE=os.path.join(session_dir, "events.ndjson"),
        IZSH_SESSION_ID=session_id,
        IZSH_SESSION_DIR=session_dir,
    )


def main():
    with tempfile.TemporaryDirectory(prefix="izsh-pty-") as directory:
        os.mkdir(os.path.join(directory, "commands"))
        events_file = os.path.join(directory, "events.ndjson")
        shell = Shell(events_file, "20261007T120000Z-main", directory)
        try:
            output = shell.command("/usr/bin/printf 'hello\\n'")
            if b"hello\n" not in output:
                fail("stdout did not reach the PTY")

            output += shell.command("/bin/sh -c 'printf error >&2; exit 7'")
            if b"error" not in output:
                fail("stderr did not reach the PTY")

            output += shell.command("/usr/bin/printf 'a\\nb\\n' | /usr/bin/tr a-z A-Z")
            if b"A\nB\n" not in output:
                fail("pipeline output did not reach the PTY")

            output += shell.command("/usr/bin/yes x | /usr/bin/head -n 10000")
            if b"x\n" not in output:
                fail("large pipeline output did not reach the PTY")

            output += shell.command("/usr/bin/printf left; /usr/bin/printf right")
            if b"leftright" not in output:
                fail("compound command output did not reach the PTY")

            output += shell.command("print -rn -- builtin-output")
            if b"builtin-output" not in output:
                fail("shell builtin output did not reach the PTY")

            interrupted = shell.interrupt("/bin/sleep 5")
            if b"__IZSH_INTERRUPTED__" not in interrupted:
                fail("Ctrl+C did not return control to the shell")
            if PROMPT not in interrupted:
                fail("Ctrl+C did not return control to the shell")

        finally:
            shell.close()

        fallback = subprocess.run(
            [ZSH, "-f", "-c", "/usr/bin/printf fallback"],
            env=dict(
                os.environ,
                IZSH_EVENTS_FILE=directory,
                IZSH_SESSION_ID="fallback",
                IZSH_SESSION_DIR=directory,
            ),
            capture_output=True,
            check=False,
        )
        if fallback.returncode != 0 or fallback.stdout != b"fallback":
            fail("recorder failure broke ordinary command execution")

        with tempfile.TemporaryDirectory(prefix="izsh-redirect-") as redirect_dir:
            redirect_env = session_environment(redirect_dir, "redirect-session")
            redirected = os.path.join(redirect_dir, "redirected.txt")
            result = subprocess.run(
                [ZSH, "-f", "-c", "/usr/bin/printf redirected > %s" % redirected],
                env=redirect_env,
                check=False,
            )
            if (result.returncode != 0 or
                    open(redirected, "r", encoding="utf-8").read() != "redirected"):
                fail("explicit stdout redirection changed behavior")

        events = load_events(events_file)
        start, end = event_for(events, "/usr/bin/printf 'hello")
        if start["session_id"] != "20261007T120000Z-main":
            fail("session id was not recorded")
        if start["id"] != "%s:%s" % (start["session_id"], start["command_id"]):
            fail("command id is not scoped to the session")
        if not isinstance(start["started_at_ms"], int):
            fail("command start timestamp was not recorded")
        if end["exit"] != 0:
            fail("hello command exit status was not recorded")
        if end["stdout_path"] != start["stdout_path"]:
            fail("command_end did not repeat the stdout location")
        stdout_path = start["stdout_path"]
        if open(stdout_path, "r", encoding="utf-8").read() != "hello\n":
            fail("stdout capture file is incorrect")
        if end["stdout_bytes"] != os.path.getsize(stdout_path):
            fail("stdout byte count is incorrect")

        start, end = event_for(events, "/bin/sh -c")
        if end["exit"] != 7:
            fail("stderr command exit status was not recorded")
        if open(start["stderr_path"], "r", encoding="utf-8").read() != "error":
            fail("stderr capture file is incorrect")

        start, _ = event_for(events, " | /usr/bin/tr")
        if open(start["stdout_path"], "r", encoding="utf-8").read() != "A\nB\n":
            fail("pipeline capture file is incorrect")

        start, _ = event_for(events, "/usr/bin/yes x")
        if len(open(start["stdout_path"], "rb").read()) != 20000:
            fail("large output capture file is incorrect")

        start, _ = event_for(events, "/usr/bin/printf left")
        if open(start["stdout_path"], "r", encoding="utf-8").read() != "leftright":
            fail("a compound input line was not captured as one command")
        compound_starts = [event for event in events
                           if event["type"] == "command_start" and
                           ("printf left" in event["command"] or
                            "printf right" in event["command"])]
        if len(compound_starts) != 1:
            fail("a compound input line produced multiple command events")

        start, _ = event_for(events, "builtin-output")
        if open(start["stdout_path"], "r", encoding="utf-8").read() != "builtin-output":
            fail("shell builtin output capture is incorrect")

        if not any(event["type"] == "shell_start" for event in events):
            fail("shell_start event was not recorded")
        if not any(event["type"] == "shell_end" for event in events):
            fail("shell_end event was not recorded")
        for event in events:
            if event.get("session_id") != "20261007T120000Z-main":
                fail("event escaped its session")

        if any("fallback" in event.get("command", "") for event in events):
            fail("unwritable recorder path should fall back without events")

        with tempfile.TemporaryDirectory(prefix="izsh-startup-") as startup_dir:
            rc_dir = os.path.join(startup_dir, "rc")
            session_dir = os.path.join(startup_dir, "session")
            os.mkdir(rc_dir)
            os.mkdir(session_dir)
            os.mkdir(os.path.join(session_dir, "commands"))
            with open(os.path.join(rc_dir, ".zshrc"), "w", encoding="utf-8") as stream:
                stream.write("/usr/bin/printf startup-noise\n")
                stream.write("preexec() { /usr/bin/printf preexec-noise; }\n")
                stream.write("precmd() { /usr/bin/printf precmd-noise; }\n")
                stream.write("PS1='IZSH_PROMPT>'\n")
            startup_events = os.path.join(session_dir, "events.ndjson")
            startup_shell = Shell(
                startup_events, "20261007T120001Z-startup", session_dir, rc_dir)
            try:
                before = load_events(startup_events)
                if any(event["type"] == "command_start" for event in before):
                    fail("startup files produced command events")
                startup_shell.command("/usr/bin/printf user-command")
            finally:
                startup_shell.close()
            startup_records = load_events(startup_events)
            startup_start, _ = event_for(startup_records, "user-command")
            captured = open(
                startup_start["stdout_path"], "r", encoding="utf-8").read()
            if captured != "user-command":
                fail("preexec or precmd output leaked into the user command")

        with tempfile.TemporaryDirectory(prefix="izsh-windows-") as windows_dir:
            session_a = os.path.join(windows_dir, "a")
            session_b = os.path.join(windows_dir, "b")
            os.mkdir(session_a)
            os.mkdir(session_b)
            os.mkdir(os.path.join(session_a, "commands"))
            os.mkdir(os.path.join(session_b, "commands"))
            events_a = os.path.join(session_a, "events.ndjson")
            events_b = os.path.join(session_b, "events.ndjson")
            shell_a = Shell(events_a, "20261007T120002Z-window-a", session_a)
            shell_b = Shell(events_b, "20261007T120002Z-window-b", session_b)
            try:
                os.write(shell_a.fd, b"/bin/sh -c 'sleep 0.1; printf window-a'\n")
                os.write(shell_b.fd, b"/bin/sh -c 'sleep 0.1; printf window-b'\n")
                if b"window-a" not in shell_a.read_until(PROMPT):
                    fail("first concurrent shell lost its output")
                if b"window-b" not in shell_b.read_until(PROMPT):
                    fail("second concurrent shell lost its output")
            finally:
                shell_a.close()
                shell_b.close()
            records_a = load_events(events_a)
            records_b = load_events(events_b)
            start_a, _ = event_for(records_a, "window-a")
            start_b, _ = event_for(records_b, "window-b")
            if start_a["session_id"] == start_b["session_id"]:
                fail("concurrent shells shared a session id")
            if not start_a["stdout_path"].startswith(session_a + os.sep):
                fail("first concurrent shell escaped its session directory")
            if not start_b["stdout_path"].startswith(session_b + os.sep):
                fail("second concurrent shell escaped its session directory")

        with tempfile.TemporaryDirectory(prefix="izsh-launcher-") as install_root:
            os.mkdir(os.path.join(install_root, "bin"))
            os.symlink(ZSH, os.path.join(install_root, "bin", "zsh"))
            launcher_env = dict(os.environ, IZSH_INSTALL_ROOT=install_root)
            for marker in ("launcher-one", "launcher-two"):
                result = subprocess.run(
                    ["/bin/sh", LAUNCHER, "-f", "-c", "/usr/bin/printf " + marker],
                    env=launcher_env,
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0 or result.stdout != marker.encode():
                    fail("launcher changed ordinary command behavior")
            sessions_root = os.path.join(install_root, "sessions")
            sessions = sorted(os.listdir(sessions_root))
            if len(sessions) != 2 or sessions[0] == sessions[1]:
                fail("launcher did not create one unique directory per session")
            for session_id in sessions:
                if not re.match(r"^\d{8}T\d{6}Z-[A-Za-z0-9]{6}$", session_id):
                    fail("session id is not timestamped and unique: %r" % session_id)
                session_path = os.path.join(sessions_root, session_id)
                records = load_events(os.path.join(session_path, "events.ndjson"))
                if any(event.get("session_id") != session_id for event in records):
                    fail("launcher session id did not reach the event stream")

        print("izsh PTY capture tests: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        print("izsh PTY capture tests: FAIL: %s" % error, file=sys.stderr)
        sys.exit(1)
