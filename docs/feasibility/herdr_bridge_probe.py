"""Probe Herdr's bridge in a disposable, isolated server. No LLM calls."""
import base64
import json
import os
import pathlib
import queue
import shlex
import socket
import subprocess
import tempfile
import threading
import time


class Stream:
    def __init__(self, env, target, mode="observe", takeover=False):
        command = ["herdr", "--session", "feasibility", "terminal", "session", mode, target,
                   "--cols", "100", "--rows", "24"]
        if takeover:
            command.append("--takeover")
        self.proc = subprocess.Popen(command, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.records = queue.Queue()
        self.frames = []
        self.content = b""
        self.errors = []
        def read():
            for line in self.proc.stdout:
                self.records.put(json.loads(line))
            self.records.put({"type": "stream.eof"})
        def errors():
            for line in self.proc.stderr:
                self.errors.append(line.strip())
        threading.Thread(target=read, daemon=True).start()
        threading.Thread(target=errors, daemon=True).start()

    def send(self, command):
        self.proc.stdin.write(json.dumps(command) + "\n")
        self.proc.stdin.flush()

    def next(self, timeout=8):
        record = self.records.get(timeout=timeout)
        if record["type"] == "terminal.frame":
            self.frames.append({k: v for k, v in record.items() if k != "bytes"})
            self.content += base64.b64decode(record["bytes"])
        return record

    def until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.next(deadline - time.monotonic())
            if predicate(record):
                return record
        raise TimeoutError("stream condition was not observed")

    def text(self, text):
        wanted = text.encode()
        return self.until(lambda record: wanted in self.content)

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)


def main():
    evidence = {"version": subprocess.check_output(["herdr", "--version"], text=True).strip(),
                "checks": {}, "frames": {}}
    schema = json.loads(subprocess.check_output(["herdr", "api", "schema", "--json"], text=True))
    methods = set()
    agent_fields = []
    def inspect_schema(node):
        nonlocal agent_fields
        if isinstance(node, dict):
            method = node.get("properties", {}).get("method", {})
            if method.get("const"):
                methods.add(method["const"])
            for key, value in node.items():
                if key == "AgentInfo" and isinstance(value, dict):
                    agent_fields = sorted(value.get("properties", {}))
                inspect_schema(value)
        elif isinstance(node, list):
            for value in node:
                inspect_schema(value)
    inspect_schema(schema)
    evidence["schema"] = {
        "agent_fields": agent_fields,
        "question_or_approval_methods": sorted(m for m in methods
            if any(word in m for word in ("question", "approval", "permission"))),
        "relevant_methods": sorted(m for m in methods if m.startswith(
            ("agent.", "events.", "session.", "pane.send", "pane.read")))
    }
    assert agent_fields and not evidence["schema"]["question_or_approval_methods"]
    streams = []
    with tempfile.TemporaryDirectory(prefix="docket-herdr-lab-") as tmp:
        directory = pathlib.Path(tmp)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("HERDR_", "DOCKET_", "CODEX_", "CLAUDE_", "OPENCODE"))}
        env.update(XDG_CONFIG_HOME=str(directory / "config"),
                   XDG_STATE_HOME=str(directory / "state"),
                   XDG_CACHE_HOME=str(directory / "cache"))
        for prompt_var in ("PS1", "PS2", "PROMPT", "RPROMPT", "ENV", "BASH_ENV"):
            env.pop(prompt_var, None)
        config = directory / "config/herdr/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('onboarding = false\n[terminal]\ndefault_shell = "/bin/sh"\n'
                          '[update]\nversion_check = false\nmanifest_check = false\n')
        api_path = directory / "config/herdr/sessions/feasibility/herdr.sock"
        server_log = (directory / "server.log").open("w")
        server = subprocess.Popen(["herdr", "--session", "feasibility", "server"],
                                  cwd=directory, env=env, stdout=server_log, stderr=server_log)

        def api(method, params=None):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(5)
                connection.connect(str(api_path))
                connection.sendall((json.dumps({"id": "study", "method": method,
                                               "params": params or {}}) + "\n").encode())
                response = json.loads(connection.makefile("rb").readline())
                if "error" in response:
                    raise RuntimeError(response)
                return response["result"]

        def visible_contains(text, pane):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                result = api("pane.read", {"pane_id": pane, "source": "visible"})
                if text in result["read"]["text"]:
                    return result
                time.sleep(0.1)
            raise AssertionError({"wanted": text, "visible": result})

        try:
            deadline = time.monotonic() + 8
            while True:
                try:
                    api("ping")
                    break
                except (OSError, RuntimeError):
                    if time.monotonic() >= deadline or server.poll() is not None:
                        server_log.flush()
                        raise RuntimeError((directory / "server.log").read_text())
                    time.sleep(0.1)
            created = api("workspace.create", {"cwd": str(directory), "label": "Study"})
            wid = created["workspace"]["workspace_id"]
            panes = [created["root_pane"]["pane_id"]]
            for role in ("orchestrator", "implementor", "verifier", "reviewer"):
                panes.append(api("tab.create", {"workspace_id": wid, "cwd": str(directory),
                                                "label": role})["root_pane"]["pane_id"])
            for pane in panes:
                observer = Stream(env, pane)
                streams.append(observer)
                first = observer.next()
                assert first["type"] == "terminal.frame" and first["full"], first
            evidence["checks"]["five_simultaneous_observers"] = True
            pid = api("pane.process_info", {"pane_id": panes[0]})["process_info"]["shell_pid"]
            controller = Stream(env, panes[0], "control")
            streams.append(controller)
            assert controller.next()["type"] == "terminal.frame"
            controller.send({"type": "terminal.input", "text":
                             "python3 -c 'answer=input(\"STUDY_QUESTION proceed? \" ); print(\"STUDY_ANSWER:\"+answer)'\r"})
            controller.text("STUDY_QUESTION")
            controller.send({"type": "terminal.input", "text": "yes\r"})
            controller.text("STUDY_ANSWER:yes")
            streams[0].text("STUDY_ANSWER:yes")
            evidence["checks"]["observer_receives_controller_updates"] = True
            evidence["checks"]["question_and_answer"] = True
            menu = directory / "menu.py"
            menu.write_text(
                "import os,sys,termios,tty\n"
                "fd=sys.stdin.fileno(); old=termios.tcgetattr(fd)\n"
                "try:\n"
                " tty.setraw(fd)\n"
                " os.write(1,b'\\x1b[?1049h\\x1b[2J\\x1b[HSTUDY_MENU: arrow down and Enter')\n"
                " data=b''\n"
                " while not data.endswith(b'\\r'): data+=os.read(fd,1)\n"
                "finally:\n"
                " termios.tcsetattr(fd,termios.TCSADRAIN,old)\n"
                " os.write(1,b'\\x1b[?1049l')\n"
                "print('STUDY_MENU_RESULT:'+data.hex(),flush=True)\n")
            controller.send({"type": "terminal.input", "text":
                             "python3 " + shlex.quote(str(menu)) + "\r"})
            visible_contains("STUDY_MENU: arrow down and Enter", panes[0])
            controller.send({"type": "terminal.input", "bytes":
                             base64.b64encode(b"\x1b[B\r").decode()})
            visible_contains("STUDY_MENU_RESULT:1b5b420d", panes[0])
            evidence["checks"]["alternate_screen_arrow_and_enter"] = True
            controller.send({"type": "terminal.input", "text":
                             "python3 -c 'import time; print(\"STUDY_WAITING\", flush=True); time.sleep(60)'\r"})
            visible_contains("\nSTUDY_WAITING\n", panes[0])
            controller.send({"type": "terminal.input", "bytes":
                             base64.b64encode(b"\x03").decode()})
            visible_contains("KeyboardInterrupt", panes[0])
            evidence["checks"]["ctrl_c_interrupt"] = True
            controller.send({"type": "terminal.input", "text":
                             "python3 -c 'answer=input(); print(\"STUDY_UNICODE:\"+answer)'\r"})
            controller.send({"type": "terminal.input", "text": "नमस्ते café\r"})
            visible_contains("STUDY_UNICODE:नमस्ते café", panes[0])
            evidence["checks"]["unicode_input"] = True
            controller.send({"type": "terminal.resize", "cols": 110, "rows": 28})
            resized = controller.until(lambda r: r.get("width") == 110 and r.get("height") == 28)
            assert resized["full"]
            controller.send({"type": "terminal.input", "text": "printf 'STUDY_SIZE:'; stty size\r"})
            evidence["checks"]["pty_size_read"] = visible_contains("STUDY_SIZE:28 110", panes[0])
            evidence["checks"]["resize_reaches_real_pty"] = True
            controller.send({"type": "terminal.input", "text": "python3 -c 'print(\"\\n\".join(\"STUDY_LINE_%03d\"%i for i in range(150)))'\r"})
            controller.text("STUDY_LINE_149")
            recent = api("pane.read", {"pane_id": panes[0], "source": "recent_unwrapped",
                                       "lines": 300})
            evidence["checks"]["history_read"] = "STUDY_LINE_000" in recent["read"]["text"]
            assert evidence["checks"]["history_read"]
            controller.send({"type": "terminal.scroll", "direction": "up", "lines": 15})
            time.sleep(0.15)
            info = api("pane.get", {"pane_id": panes[0]})["pane"]
            evidence["checks"]["scroll"] = info.get("scroll", {})
            assert evidence["checks"]["scroll"]["offset_from_bottom"] == 15
            controller.send({"type": "terminal.scroll", "direction": "down", "lines": 200})
            second = Stream(env, panes[0], "control")
            streams.append(second)
            blocked = second.next()
            evidence["checks"]["second_controller_refused"] = blocked
            assert blocked["type"] == "terminal.closed", blocked
            takeover = Stream(env, panes[0], "control", takeover=True)
            streams.append(takeover)
            assert takeover.next()["type"] == "terminal.frame"
            old_closed = controller.until(lambda r: r["type"] == "terminal.closed")
            evidence["checks"]["explicit_takeover"] = old_closed
            takeover.send({"type": "terminal.mouse", "column": 5, "row": 3, "button": "left"})
            time.sleep(0.15)
            evidence["checks"]["structured_mouse_command"] = list(takeover.errors)
            assert any("unknown variant" in error for error in takeover.errors)
            takeover.send({"type": "terminal.release"})
            evidence["checks"]["release"] = takeover.until(lambda r: r["type"] in ("terminal.closed", "stream.eof"))
            reconnected = Stream(env, panes[0])
            streams.append(reconnected)
            fresh = reconnected.next()
            evidence["checks"]["reconnect_full_frame"] = fresh["full"]
            assert fresh["type"] == "terminal.frame" and fresh["full"]
            evidence["checks"]["same_shell_pid_after_reconnect"] = (
                api("pane.process_info", {"pane_id": panes[0]})["process_info"]["shell_pid"] == pid)
            assert evidence["checks"]["same_shell_pid_after_reconnect"]
            other = Stream(env, panes[1], "control")
            streams.append(other)
            assert other.next()["type"] == "terminal.frame"
            other.send({"type": "terminal.input", "text": "printf 'STUDY_'; printf 'OTHER_ROLE\\n'\r"})
            visible_contains("STUDY_OTHER_ROLE", panes[1])
            evidence["checks"]["switch_to_another_existing_terminal"] = True
            evidence["frames"] = {"observer": streams[0].frames[:3], "controller": controller.frames[:5]}
        except BaseException:
            print("PROBE_DIAGNOSTICS", json.dumps(evidence, indent=2), flush=True)
            if 'panes' in locals():
                print("VISIBLE", json.dumps(api("pane.read", {"pane_id": panes[0], "source": "visible"})), flush=True)
            raise
        finally:
            for stream in streams:
                stream.close()
            if api_path.exists():
                try:
                    api("server.stop")
                except (OSError, RuntimeError):
                    pass
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.terminate()
                server.wait(timeout=5)
            server_log.close()
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
