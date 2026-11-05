"""Unit tests for the centralized Python pipeline orchestrator (agency_analytics.pipeline_runner).

Follows strict TDD: Tests written RED first to specify the behavior of:
- Database observability recording (pipeline_runs and pipeline_run_steps with parameterized SQL)
- Telegram alert handling
- Client configuration loading & active checks
- Connector execution and step logging
- dbt model selection (staging, investment marts, organic marts) and dbt build execution
- Overall run status determination via pipeline_plan
"""

import json
from unittest.mock import MagicMock, patch

from agency_analytics.pipeline_runner import (
    PipelineRunner,
    get_db_connection,
    record_run_finish,
    record_run_start,
    record_step_finish,
    record_step_start,
    run_dbt,
    send_telegram,
)

# ─── Database Observability Tests ──────────────────────────────────────────


def test_get_db_connection_uses_env_variables(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.example.com")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "custom_dw")
    monkeypatch.setenv("POSTGRES_USER", "custom_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret123")

    mock_connect = MagicMock()
    with patch("psycopg2.connect", mock_connect):
        conn = get_db_connection()
        mock_connect.assert_called_once_with(
            host="db.example.com",
            port=5433,
            dbname="custom_dw",
            user="custom_user",
            password="secret123",
            connect_timeout=10,
        )
        assert conn == mock_connect.return_value


def test_get_db_connection_falls_back_to_defaults(monkeypatch):
    for var in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DESTINATION__POSTGRES__CREDENTIALS__HOST",
        "DESTINATION__POSTGRES__CREDENTIALS__PORT",
        "DESTINATION__POSTGRES__CREDENTIALS__DATABASE",
        "DESTINATION__POSTGRES__CREDENTIALS__USERNAME",
        "DESTINATION__POSTGRES__CREDENTIALS__PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)

    mock_connect = MagicMock()
    with patch("psycopg2.connect", mock_connect):
        get_db_connection()
        mock_connect.assert_called_once_with(
            host="agency_postgres",
            port=5432,
            dbname="agency_dw",
            user="agency_admin",
            password="",
            connect_timeout=10,
        )


def test_record_run_start_parameterized_query():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = (42,)

    run_id = record_run_start(mock_conn, "test_client")

    assert run_id == 42
    mock_cursor.execute.assert_called_once()
    query, params = mock_cursor.execute.call_args[0]
    assert "INSERT INTO public.pipeline_runs" in query
    assert "VALUES (%s, 'running', NOW())" in query
    assert params == ("test_client",)
    mock_conn.commit.assert_called_once()


def test_record_run_finish_parameterized_query():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    record_run_finish(
        mock_conn,
        run_id=42,
        status="success",
        connectors_ok=4,
        connectors_failed=0,
        dbt_status="success",
        error_message=None,
    )

    mock_cursor.execute.assert_called_once()
    query, params = mock_cursor.execute.call_args[0]
    assert "UPDATE public.pipeline_runs" in query
    assert "status = %s" in query
    assert params == ("success", 4, 0, "success", None, 42)
    mock_conn.commit.assert_called_once()


def test_record_step_start_parameterized_query():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = (101,)

    step_id = record_step_start(mock_conn, run_id=42, step_name="dlt_meta", connector="meta")

    assert step_id == 101
    mock_cursor.execute.assert_called_once()
    query, params = mock_cursor.execute.call_args[0]
    assert "INSERT INTO public.pipeline_run_steps" in query
    assert params == (42, "dlt_meta", "meta")
    mock_conn.commit.assert_called_once()


def test_record_step_finish_parameterized_query():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    record_step_finish(mock_conn, step_id=101, status="success", error_message=None)

    mock_cursor.execute.assert_called_once()
    query, params = mock_cursor.execute.call_args[0]
    assert "UPDATE public.pipeline_run_steps" in query
    assert params == ("success", None, 101)
    mock_conn.commit.assert_called_once()


# ─── Telegram Tests ────────────────────────────────────────────────────────


def test_send_telegram_skips_when_credentials_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with patch("requests.post") as mock_post:
        send_telegram("Test message")
        mock_post.assert_not_called()


def test_send_telegram_sends_post_when_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9999")

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        send_telegram("<b>Hello</b>")
        mock_post.assert_called_once_with(
            "https://api.telegram.org/bot123:ABC/sendMessage",
            data={"chat_id": "9999", "text": "<b>Hello</b>", "parse_mode": "HTML"},
            timeout=10,
        )


def test_send_telegram_catches_and_logs_exception(monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9999")

    with patch("requests.post", side_effect=Exception("Network error")):
        # Should not raise exception
        send_telegram("<b>Hello</b>")


# ─── dbt Execution Tests ───────────────────────────────────────────────────


def test_run_dbt_selects_models_and_executes_build():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0

        status, error_msg = run_dbt(
            client_id="acme",
            enabled_connectors=["meta", "tiktok", "google"],
            dbt_project_dir="/app/src/dbt_project",
        )

        assert status == "success"
        assert error_msg is None

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "dbt"
        assert cmd[1] == "build"
        assert "--select" in cmd
        # Investment models must be included when meta+tiktok+google are enabled
        select_arg = cmd[cmd.index("--select") + 1]
        assert "stg_meta__ads" in select_arg
        assert "int_unified_spend" in select_arg
        assert "ad_spend_summary" in select_arg
        assert "--vars" in cmd
        assert json.loads(cmd[cmd.index("--vars") + 1]) == {"client_id": "acme"}


def test_run_dbt_handles_build_failure():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "Model failed to build"

        status, error_msg = run_dbt(
            client_id="acme",
            enabled_connectors=["meta"],
            dbt_project_dir="/app/src/dbt_project",
        )

        assert status == "failed"
        assert error_msg == "dbt build failed"


# ─── PipelineRunner End-to-End Client Orchestration Tests ───────────────────


def test_pipeline_runner_skips_inactive_client(tmp_path):
    client_yml = tmp_path / "inactive_client.yml"
    client_yml.write_text(
        """
client_id: inactive_client
active: false
connectors:
  meta:
    enabled: true
"""
    )

    mock_conn = MagicMock()
    runner = PipelineRunner(clients_dir=tmp_path, db_conn=mock_conn)

    client_id, ok = runner.process_client(client_yml)

    assert client_id == "inactive_client"
    assert ok is True
    # Should not start any db runs for inactive client
    mock_conn.cursor.assert_not_called()


def test_pipeline_runner_processes_active_client_successfully(tmp_path):
    client_yml = tmp_path / "active_client.yml"
    client_yml.write_text(
        """
client_id: active_client
active: true
connectors:
  meta:
    enabled: true
  tiktok:
    enabled: false
"""
    )

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = (1,)  # ID returned

    runner = PipelineRunner(clients_dir=tmp_path, db_conn=mock_conn)

    with (
        patch.object(runner, "run_connector", return_value=0) as mock_connector,
        patch(
            "agency_analytics.pipeline_runner.run_dbt", return_value=("success", None)
        ) as mock_dbt,
    ):
        client_id, ok = runner.process_client(client_yml)

        assert client_id == "active_client"
        assert ok is True

        # meta was run, tiktok was skipped
        mock_connector.assert_called_once_with("meta", "active_client")
        mock_dbt.assert_called_once_with(
            client_id="active_client",
            enabled_connectors=["meta"],
            dbt_project_dir=runner.dbt_project_dir,
        )


def test_pipeline_runner_processes_active_client_with_connector_failure(tmp_path):
    client_yml = tmp_path / "failing_client.yml"
    client_yml.write_text(
        """
client_id: failing_client
active: true
connectors:
  meta:
    enabled: true
"""
    )

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = (1,)

    runner = PipelineRunner(clients_dir=tmp_path, db_conn=mock_conn)

    with (
        patch.object(runner, "run_connector", return_value=1) as mock_connector,
        patch(
            "agency_analytics.pipeline_runner.run_dbt", return_value=("success", None)
        ) as mock_dbt,
        patch("agency_analytics.pipeline_runner.send_telegram") as mock_telegram,
    ):
        client_id, ok = runner.process_client(client_yml)

        assert client_id == "failing_client"
        assert ok is False  # connector failed -> run failed

        mock_connector.assert_called_once_with("meta", "failing_client")
        mock_dbt.assert_called_once()  # dbt still runs (spec A2)
        mock_telegram.assert_called()  # alert sent on connector failure


def test_pipeline_runner_run_all(tmp_path):
    c1 = tmp_path / "c1.yml"
    c1.write_text("client_id: c1\nactive: true\nconnectors:\n  meta:\n    enabled: true\n")
    c2 = tmp_path / "c2.yml"
    c2.write_text("client_id: c2\nactive: true\nconnectors:\n  google:\n    enabled: true\n")

    mock_conn = MagicMock()
    runner = PipelineRunner(clients_dir=tmp_path, db_conn=mock_conn)

    with (
        patch.object(runner, "process_client", side_effect=[("c1", True), ("c2", True)]),
        patch("agency_analytics.pipeline_runner.send_telegram") as mock_telegram,
    ):
        exit_code = runner.run_all()
        assert exit_code == 0
        mock_telegram.assert_called_once()
        assert "2" in mock_telegram.call_args[0][0]  # 2 successes


def test_run_dbt_unexpected_exception():
    with patch("subprocess.run", side_effect=RuntimeError("Subprocess failed to launch")):
        status, error_msg = run_dbt(
            client_id="acme",
            enabled_connectors=["meta"],
            dbt_project_dir="/app/src/dbt_project",
        )
        assert status == "failed"
        assert "Subprocess failed to launch" in (error_msg or "")


def test_pipeline_runner_get_connection_lazy_init():
    runner = PipelineRunner(clients_dir="/tmp", db_conn=None)
    with patch("agency_analytics.pipeline_runner.get_db_connection") as mock_get_conn:
        conn = runner.get_connection()
        mock_get_conn.assert_called_once()
        assert conn == mock_get_conn.return_value


def test_pipeline_runner_run_connector_subprocess():
    runner = PipelineRunner(clients_dir="/tmp", db_conn=MagicMock())
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        ret = runner.run_connector("meta", "client_example")
        assert ret == 0
        mock_run.assert_called_once()


def test_pipeline_runner_yaml_load_error(tmp_path):
    bad_yml = tmp_path / "bad.yml"
    bad_yml.write_text("invalid: yaml: : [")
    runner = PipelineRunner(clients_dir=tmp_path, db_conn=MagicMock())
    client_id, ok = runner.process_client(bad_yml)
    assert client_id == "bad"
    assert ok is False


def test_pipeline_runner_ignores_non_dict_connector_cfg(tmp_path):
    client_yml = tmp_path / "client.yml"
    client_yml.write_text(
        """
client_id: client_test
active: true
connectors:
  invalid_connector: "not a dict"
"""
    )
    mock_conn = MagicMock()
    runner = PipelineRunner(clients_dir=tmp_path, db_conn=mock_conn)
    with (
        patch("agency_analytics.pipeline_runner.run_dbt", return_value=("success", None)),
    ):
        client_id, ok = runner.process_client(client_yml)
        assert client_id == "client_test"
        assert ok is True


def test_pipeline_runner_dbt_failure_branch(tmp_path):
    client_yml = tmp_path / "client.yml"
    client_yml.write_text("client_id: client_dbt_fail\nactive: true\nconnectors: {}\n")
    mock_conn = MagicMock()
    runner = PipelineRunner(clients_dir=tmp_path, db_conn=mock_conn)
    with (
        patch(
            "agency_analytics.pipeline_runner.run_dbt", return_value=("failed", "dbt build error")
        ),
        patch("agency_analytics.pipeline_runner.send_telegram") as mock_telegram,
    ):
        client_id, ok = runner.process_client(client_yml)
        assert client_id == "client_dbt_fail"
        assert ok is False
        mock_telegram.assert_called()


def test_pipeline_runner_target_client_and_empty_dir(tmp_path):
    # Empty directory
    runner = PipelineRunner(clients_dir=tmp_path, db_conn=MagicMock())
    assert runner.run_all() == 0

    # Target specific client
    c1 = tmp_path / "target.yml"
    c1.write_text("client_id: target\nactive: false\n")
    assert runner.run_all(target_client="target") == 0


def test_pipeline_runner_run_all_with_failures(tmp_path):
    c1 = tmp_path / "c1.yml"
    c1.write_text("client_id: c1\nactive: true\n")
    runner = PipelineRunner(clients_dir=tmp_path, db_conn=MagicMock())
    with (
        patch.object(runner, "process_client", return_value=("c1", False)),
        patch("agency_analytics.pipeline_runner.send_telegram"),
    ):
        exit_code = runner.run_all()
        assert exit_code == 1


def test_pipeline_runner_main(monkeypatch):
    from agency_analytics.pipeline_runner import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "pipeline_runner",
            "--client",
            "test_client",
            "--clients-dir",
            "/tmp",
            "--dbt-dir",
            "/tmp",
        ],
    )
    with patch(
        "agency_analytics.pipeline_runner.PipelineRunner.run_all", return_value=0
    ) as mock_run:
        exit_code = main()
        assert exit_code == 0
        mock_run.assert_called_once_with(target_client="test_client")
