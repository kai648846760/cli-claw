import json
from typer.testing import CliRunner

from cli_claw.cli import main


runner = CliRunner()


def test_config_check_passes_with_required_env():
    result = runner.invoke(
        main.app,
        ["config-check", "--channel", "telegram"],
        env={"TELEGRAM_BOT_TOKEN": "token"},
    )
    assert result.exit_code == 0


def test_config_check_fails_when_missing_env():
    result = runner.invoke(main.app, ["config-check", "--channel", "discord"], env={})
    assert result.exit_code == 1


def test_start_status_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_STATE_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    created = {}

    class _Proc:
        pid = 1234

    def _fake_popen(command, stdout, stderr, env):
        created["command"] = command
        created["env"] = env
        return _Proc()

    monkeypatch.setattr(main.subprocess, "Popen", _fake_popen)

    result = runner.invoke(
        main.app,
        [
            "start",
            "--channel",
            "feishu",
            "--provider",
            "iflow",
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
            "--path",
            "/feishu/webhook",
        ],
    )
    assert result.exit_code == 0
    assert created["env"]["CLI_CLAW_LOG_PATH"].endswith("feishu.jsonl")

    pid_path = tmp_path / "state" / "feishu" / "pid"
    meta_path = tmp_path / "state" / "feishu" / "meta.json"
    assert pid_path.read_text() == "1234"

    meta = json.loads(meta_path.read_text())
    assert meta["channel"] == "feishu"
    assert meta["port"] == 9000
    assert meta["path"] == "/feishu/webhook"

    monkeypatch.setattr(main, "_pid_running", lambda pid: True)
    status_result = runner.invoke(main.app, ["status", "--channel", "feishu"])
    assert status_result.exit_code == 0
    assert "running" in status_result.output

    killed = {}

    def _fake_kill(pid, sig):
        killed["pid"] = pid
        killed["sig"] = sig

    monkeypatch.setattr(main.os, "kill", _fake_kill)
    stop_result = runner.invoke(main.app, ["stop", "--channel", "feishu"])
    assert stop_result.exit_code == 0
    assert killed["pid"] == 1234
    assert not pid_path.exists()


def test_logs_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_STATE_DIR", tmp_path)
    log_path = tmp_path / "logs" / "feishu.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("line1\nline2\n")

    result = runner.invoke(main.app, ["logs", "--channel", "feishu", "--lines", "1"])
    assert result.exit_code == 0
    assert "line2" in result.output


def test_self_test_invokes_pytest(monkeypatch):
    monkeypatch.setattr(main.subprocess, "call", lambda *args, **kwargs: 0)
    result = runner.invoke(main.app, ["self-test"])
    assert result.exit_code == 0


def test_replay_reads_jsonl(monkeypatch, tmp_path):
    calls: list[tuple[str, object]] = []

    class _Queue:
        async def join(self):
            return None

    class _Manager:
        def __init__(self):
            self._queue = _Queue()

    class _Runtime:
        async def start(self, channels):
            calls.append(("start", channels))

        async def handle_inbound(self, channel, inbound):
            calls.append(("inbound", inbound))

        async def stop(self):
            calls.append(("stop", None))

    def _fake_build_runtime(provider, channel, channel_factory, log_path=None):
        _ = provider
        _ = channel_factory
        _ = log_path
        return _Runtime(), _Manager()

    monkeypatch.setattr(main, "_build_runtime", _fake_build_runtime)

    source = tmp_path / "log.jsonl"
    source.write_text(
        json.dumps(
            {
                "chat_id": "c1",
                "sender_id": "u1",
                "message_id": "m1",
                "reply_to_id": "r1",
                "content": "hello",
                "attachments": [{"kind": "file", "name": "a.txt", "url": "https://x"}],
                "meta": {"k": "v"},
            }
        )
        + "\n"
    )

    result = runner.invoke(
        main.app,
        ["replay", "--channel", "feishu", "--provider", "iflow", "--source", str(source)],
    )
    assert result.exit_code == 0
    assert calls[0][0] == "start"
    assert calls[1][0] == "inbound"
    inbound = calls[1][1]
    assert inbound.chat_id == "c1"
    assert inbound.text == "hello"
    assert calls[-1][0] == "stop"
