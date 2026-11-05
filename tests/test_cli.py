from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from agency_analytics import cli


class TestCliSubcommands:
    """Direct tests for command implementations in cli.py."""

    @patch("agency_analytics.cli.CLIENTS_DIR")
    def test_cmd_pipeline_empty_clients_dir(self, mock_clients_dir, capsys):
        mock_clients_dir.glob.return_value = []
        args = MagicMock(client=None, connector=None)
        ret = cli.cmd_pipeline(args)
        assert ret == 1
        out = capsys.readouterr().out
        assert "No client configuration files found" in out

    @patch("agency_analytics.cli.CLIENTS_DIR")
    @patch("agency_analytics.cli.subprocess.run")
    def test_cmd_pipeline_success(self, mock_run, mock_clients_dir, tmp_path):
        client_file = tmp_path / "acme.yml"
        client_file.write_text("active: true\nconnectors:\n  google:\n    enabled: true\n")
        mock_clients_dir.glob.return_value = [client_file]
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        args = MagicMock(client=None, connector=None)
        ret = cli.cmd_pipeline(args)
        assert ret == 0
        assert mock_run.call_count == 2
        # Verify the dlt call passes --connector and --client
        dlt_call = mock_run.call_args_list[0][0][0]
        assert "dlt" in dlt_call
        assert "--connector" in dlt_call
        assert "google" in dlt_call
        assert "--client" in dlt_call
        assert "acme" in dlt_call

    @patch("agency_analytics.cli.CLIENTS_DIR")
    @patch("agency_analytics.cli.subprocess.run")
    def test_cmd_pipeline_failure(self, mock_run, mock_clients_dir, tmp_path):
        client_file = tmp_path / "acme.yml"
        client_file.write_text("active: true\nconnectors:\n  google:\n    enabled: true\n")
        mock_clients_dir.glob.return_value = [client_file]
        mock_run.return_value = MagicMock(returncode=1, stderr="Error detail")
        args = MagicMock(client=None, connector=None)
        ret = cli.cmd_pipeline(args)
        assert ret == 1

    @patch("agency_analytics.cli.subprocess.run")
    def test_cmd_dbt_without_client(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        args = MagicMock(command="run", client=None)
        ret = cli.cmd_dbt(args)
        assert ret == 0
        mock_run.assert_called_once_with(["dbt", "run"])

    @patch("agency_analytics.cli.subprocess.run")
    def test_cmd_dbt_with_client(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        args = MagicMock(command="test", client="acme")
        ret = cli.cmd_dbt(args)
        assert ret == 0
        mock_run.assert_called_once_with(["dbt", "test", "--vars", '{"client_id": "acme"}'])

    def test_cmd_dlt_script_missing(self, capsys):
        args = MagicMock(connector="non_existent_connector_xyz", client="acme")
        ret = cli.cmd_dlt(args)
        assert ret == 1
        out = capsys.readouterr().out
        assert "Error: dlt script not found" in out

    @patch("agency_analytics.cli.subprocess.run")
    def test_cmd_dlt_script_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        args = MagicMock(connector="google", client="acme")
        ret = cli.cmd_dlt(args)
        assert ret == 0
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == sys.executable
        assert "run_google.py" in str(call_args[1])
        assert call_args[2:] == ["--client", "acme"]

    @patch("agency_analytics.meta_auth.run_meta_auth_wizard")
    def test_cmd_auth_meta_delegation(self, mock_wizard):
        mock_wizard.return_value = 0
        args = MagicMock(
            client="acme",
            app_id="123",
            app_secret="sec",
            token="tok",
            env_path=".env",
            non_interactive=True,
        )
        ret = cli.cmd_auth_meta(args)
        assert ret == 0
        mock_wizard.assert_called_once_with(
            client_id="acme",
            app_id="123",
            app_secret="sec",
            token="tok",
            env_path=".env",
            interactive=False,
        )

    @patch("agency_analytics.google_auth.run_google_auth_wizard")
    def test_cmd_auth_google_delegation(self, mock_wizard):
        mock_wizard.return_value = 0
        args = MagicMock(
            client="acme",
            services="ga4,gtm",
            client_id="cid",
            client_secret="csec",
            env_path=".env",
            non_interactive=False,
        )
        ret = cli.cmd_auth_google(args)
        assert ret == 0
        mock_wizard.assert_called_once_with(
            client_name="acme",
            services=["ga4", "gtm"],
            client_id="cid",
            client_secret="csec",
            env_path=".env",
            interactive=True,
        )


class TestCliParserDispatch:
    """End-to-end tests for CLI entrypoint argument parsing and dispatch."""

    def test_cli_no_args_raises(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["multitenant-elt"])
        with pytest.raises(SystemExit) as exc:
            cli.cli()
        assert exc.value.code != 0

    @patch("agency_analytics.cli.cmd_pipeline")
    def test_cli_pipeline_dispatch(self, mock_cmd, monkeypatch):
        mock_cmd.return_value = 0
        monkeypatch.setattr(sys, "argv", ["multitenant-elt", "pipeline"])
        ret = cli.cli()
        assert ret == 0
        assert mock_cmd.called

    @patch("agency_analytics.cli.cmd_dbt")
    def test_cli_dbt_dispatch(self, mock_cmd, monkeypatch):
        mock_cmd.return_value = 0
        monkeypatch.setattr(
            sys, "argv", ["multitenant-elt", "dbt", "--command", "test", "--client", "acme"]
        )
        ret = cli.cli()
        assert ret == 0
        args = mock_cmd.call_args[0][0]
        assert args.command == "test"
        assert args.client == "acme"

    @patch("agency_analytics.cli.cmd_dlt")
    def test_cli_dlt_dispatch(self, mock_cmd, monkeypatch):
        mock_cmd.return_value = 0
        monkeypatch.setattr(
            sys, "argv", ["multitenant-elt", "dlt", "--connector", "google", "--client", "acme"]
        )
        ret = cli.cli()
        assert ret == 0
        args = mock_cmd.call_args[0][0]
        assert args.connector == "google"
        assert args.client == "acme"

    @patch("agency_analytics.cli.cmd_auth_meta")
    def test_cli_auth_meta_dispatch(self, mock_cmd, monkeypatch):
        mock_cmd.return_value = 0
        monkeypatch.setattr(sys, "argv", ["multitenant-elt", "auth", "meta", "--client", "acme"])
        ret = cli.cli()
        assert ret == 0
        args = mock_cmd.call_args[0][0]
        assert args.client == "acme"

    @patch("agency_analytics.cli.cmd_auth_google")
    def test_cli_auth_google_dispatch(self, mock_cmd, monkeypatch):
        mock_cmd.return_value = 0
        monkeypatch.setattr(
            sys, "argv", ["multitenant-elt", "auth", "google", "--services", "ga4,gtm"]
        )
        ret = cli.cli()
        assert ret == 0
        args = mock_cmd.call_args[0][0]
        assert args.services == "ga4,gtm"
