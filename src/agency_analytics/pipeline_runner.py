"""Centralized pipeline runner and orchestrator for multitenant-elt.

Replaces the shell-based subprocess spawning cascade with a single-process
Python orchestrator running directly inside the pipeline container.
Handles:
1. Client configuration loading and filtering.
2. Parameterized SQL recording of pipeline_runs and pipeline_run_steps.
3. Sequential execution of enabled dlt connectors.
4. Dynamic dbt model planning and execution (staging + investment + organic marts).
5. Comprehensive error tracking and Telegram notifications.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

from agency_analytics.pipeline_plan import (
    INVESTMENT_MARTS,
    build_plan,
    organic_marts,
    run_status,
)

logger = logging.getLogger("pipeline_runner")
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def get_db_connection() -> Any:
    """Create and return a database connection to PostgreSQL with fallback defaults."""
    import psycopg2

    host = (
        os.getenv("POSTGRES_HOST")
        or os.getenv("DESTINATION__POSTGRES__CREDENTIALS__HOST")
        or "agency_postgres"
    )
    port = int(
        os.getenv("POSTGRES_PORT") or os.getenv("DESTINATION__POSTGRES__CREDENTIALS__PORT") or 5432
    )
    dbname = (
        os.getenv("POSTGRES_DB")
        or os.getenv("DESTINATION__POSTGRES__CREDENTIALS__DATABASE")
        or "agency_dw"
    )
    user = (
        os.getenv("POSTGRES_USER")
        or os.getenv("DESTINATION__POSTGRES__CREDENTIALS__USERNAME")
        or "agency_admin"
    )
    password = (
        os.getenv("POSTGRES_PASSWORD")
        or os.getenv("DESTINATION__POSTGRES__CREDENTIALS__PASSWORD")
        or ""
    )

    return psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        connect_timeout=10,
    )


def record_run_start(conn: Any, client_id: str) -> int:
    """Insert a new record into public.pipeline_runs using parameterized SQL."""
    with conn.cursor() as cur:
        query = (
            "INSERT INTO public.pipeline_runs (client_id, status, started_at) "
            "VALUES (%s, 'running', NOW()) RETURNING id;"
        )
        cur.execute(query, (client_id,))
        run_id = cur.fetchone()[0]
    conn.commit()
    return int(run_id)


def record_run_finish(
    conn: Any,
    run_id: int,
    status: str,
    connectors_ok: int,
    connectors_failed: int,
    dbt_status: str,
    error_message: str | None = None,
) -> None:
    """Update public.pipeline_runs record using parameterized SQL."""
    with conn.cursor() as cur:
        query = (
            "UPDATE public.pipeline_runs "
            "SET status = %s, "
            "    finished_at = NOW(), "
            "    connectors_ok = %s, "
            "    connectors_failed = %s, "
            "    dbt_status = %s, "
            "    dbt_duration_ms = EXTRACT(EPOCH FROM (NOW() - started_at))::int * 1000, "
            "    error_message = %s "
            "WHERE id = %s;"
        )
        cur.execute(
            query,
            (
                status,
                connectors_ok,
                connectors_failed,
                dbt_status,
                error_message,
                run_id,
            ),
        )
    conn.commit()


def record_step_start(
    conn: Any,
    run_id: int,
    step_name: str,
    connector: str | None = None,
) -> int:
    """Insert a new record into public.pipeline_run_steps using parameterized SQL."""
    with conn.cursor() as cur:
        query = (
            "INSERT INTO public.pipeline_run_steps "
            "(run_id, step_name, connector, status, started_at) "
            "VALUES (%s, %s, %s, 'running', NOW()) RETURNING id;"
        )
        cur.execute(query, (run_id, step_name, connector))
        step_id = cur.fetchone()[0]
    conn.commit()
    return int(step_id)


def record_step_finish(
    conn: Any,
    step_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    """Update public.pipeline_run_steps record using parameterized SQL."""
    with conn.cursor() as cur:
        query = (
            "UPDATE public.pipeline_run_steps "
            "SET status = %s, "
            "    finished_at = NOW(), "
            "    duration_ms = EXTRACT(EPOCH FROM (NOW() - started_at))::int * 1000, "
            "    error_message = %s "
            "WHERE id = %s;"
        )
        cur.execute(query, (status, error_message, step_id))
    conn.commit()


def send_telegram(
    message: str,
    token: str | None = None,
    chat_id: str | None = None,
) -> None:
    """Send an HTML notification to Telegram if credentials are provided."""
    bot_token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    target_chat = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not target_chat:
        logger.debug("Telegram notification skipped: credentials not configured.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, data=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to send Telegram alert: %s", exc)


def run_dbt(
    client_id: str,
    enabled_connectors: list[str],
    dbt_project_dir: Path | str,
) -> tuple[str, str | None]:
    """Execute dbt build for models resolved from enabled connectors.

    Returns (status, error_message).
    """
    plan = build_plan(enabled_connectors)
    selected_models = list(plan.models)

    if plan.investment:
        selected_models.extend(INVESTMENT_MARTS)
        logger.info("dbt: included advertising investment marts (meta+tiktok+google)")
    else:
        logger.info("dbt: investment marts omitted (missing required connectors)")

    org_marts = organic_marts(enabled_connectors)
    if org_marts:
        selected_models.extend(org_marts)
        logger.info("dbt: included organic marts (%s)", " ".join(org_marts))

    dbt_select = " ".join(selected_models)
    logger.info("dbt: selected models: %s", dbt_select)

    dbt_vars = json.dumps({"client_id": client_id})
    cmd = [
        "dbt",
        "build",
        "--select",
        dbt_select,
        "--vars",
        dbt_vars,
        "--profiles-dir",
        ".",
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(dbt_project_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return "success", None
        else:
            err = result.stderr or result.stdout
            logger.error("dbt build failed for client %s:\n%s", client_id, err)
            return "failed", "dbt build failed"
    except Exception as exc:
        logger.error("Unexpected exception running dbt build: %s", exc)
        return "failed", f"dbt build exception: {exc}"


class PipelineRunner:
    """Central orchestrator executing pipelines for active clients."""

    def __init__(
        self,
        clients_dir: Path | str = "/app/clients",
        dbt_project_dir: Path | str = "/app/src/dbt_project",
        db_conn: Any | None = None,
    ) -> None:
        self.clients_dir = Path(clients_dir)
        self.dbt_project_dir = Path(dbt_project_dir)
        self._conn = db_conn

    def get_connection(self) -> Any:
        if self._conn is None:
            self._conn = get_db_connection()
        return self._conn

    def run_connector(self, connector_name: str, client_id: str) -> int:
        """Execute connector script via subprocess inside the container."""
        connectors_dir = Path(__file__).resolve().parent.parent / "connectors"
        connector_script = connectors_dir / f"run_{connector_name}.py"
        cmd = [sys.executable, str(connector_script), "--client", client_id]
        logger.info("Running connector: %s for client %s", connector_name, client_id)
        result = subprocess.run(cmd)
        return result.returncode

    def process_client(self, client_file: Path) -> tuple[str, bool]:
        """Process a single client configuration file."""
        try:
            with open(client_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.error("Failed loading configuration %s: %s", client_file, exc)
            return client_file.stem, False

        client_id = data.get("client_id", client_file.stem)
        active = data.get("active", False)

        if not active:
            logger.info("Client %s is inactive. Skipping.", client_id)
            return client_id, True

        logger.info("--- Processing client: %s ---", client_id)
        conn = self.get_connection()
        run_id = record_run_start(conn, client_id)

        connectors_cfg = data.get("connectors") or {}
        enabled_connectors: list[str] = []
        connectors_ok = 0
        connectors_failed = 0

        # Step 1: Execute connectors
        for conn_name, cfg in connectors_cfg.items():
            if not isinstance(cfg, dict):
                continue
            is_enabled = cfg.get("enabled", False)
            step_name = f"dlt_{conn_name}"

            if not is_enabled:
                skip_id = record_step_start(conn, run_id, step_name, conn_name)
                record_step_finish(conn, skip_id, "skipped", "connector disabled in client config")
                continue

            enabled_connectors.append(conn_name)
            step_id = record_step_start(conn, run_id, step_name, conn_name)
            ret_code = self.run_connector(conn_name, client_id)

            if ret_code == 0:
                connectors_ok += 1
                record_step_finish(conn, step_id, "success")
            else:
                connectors_failed += 1
                err_msg = f"dlt_{conn_name} exited with status {ret_code}"
                record_step_finish(conn, step_id, "failed", err_msg)
                send_telegram(
                    f"⚠️ <b>Fallo dlt</b>\n"
                    f"Cliente: <code>{client_id}</code>\n"
                    f"Conector: <code>{conn_name}</code>"
                )
                logger.error(
                    "dlt_%s failed for %s. Continuing with next connector.",
                    conn_name,
                    client_id,
                )

        # Step 2: Transformation dbt (ALWAYS runs)
        dbt_step_id = record_step_start(conn, run_id, "dbt_run")
        dbt_status, dbt_error = run_dbt(
            client_id=client_id,
            enabled_connectors=enabled_connectors,
            dbt_project_dir=self.dbt_project_dir,
        )

        if dbt_status == "success":
            record_step_finish(conn, dbt_step_id, "success")
        else:
            record_step_finish(conn, dbt_step_id, "failed", dbt_error or "dbt build falló")
            send_telegram(
                f"⚠️ <b>Fallo dbt</b>\n"
                f"Cliente: <code>{client_id}</code>\n"
                f"Runbook: <code>docs/runbooks/pipeline-failure.md</code>"
            )

        # Step 3: Verdict & finish run
        verdict = run_status(
            connectors_ok=connectors_ok,
            connectors_failed=connectors_failed,
            dbt_status=dbt_status,
        )
        is_success = verdict == "success"

        record_run_finish(
            conn=conn,
            run_id=run_id,
            status=verdict,
            connectors_ok=connectors_ok,
            connectors_failed=connectors_failed,
            dbt_status=dbt_status,
            error_message=None if is_success else "One or more steps failed",
        )

        logger.info(
            "Client %s completed: %d/%d connectors OK, dbt: %s (Status: %s)",
            client_id,
            connectors_ok,
            len(enabled_connectors),
            dbt_status,
            verdict,
        )
        return client_id, is_success

    def run_all(self, target_client: str | None = None) -> int:
        """Run the pipeline across active clients."""
        logger.info("=== STARTING MULTITENANT PIPELINE ===")

        if target_client:
            client_files = [self.clients_dir / f"{target_client}.yml"]
        else:
            client_files = sorted(self.clients_dir.glob("*.yml"))

        client_files = [f for f in client_files if f.name != "_template.yml" and f.is_file()]

        if not client_files:
            logger.warning("No client configuration files found in %s", self.clients_dir)
            return 0

        overall_ok = 0
        overall_failed = 0

        for client_file in client_files:
            client_id, is_ok = self.process_client(client_file)
            if is_ok:
                overall_ok += 1
            else:
                overall_failed += 1

        logger.info(
            "=== PIPELINE FINISHED: %d succeeded, %d failed ===",
            overall_ok,
            overall_failed,
        )
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        send_telegram(
            f"✅ <b>Pipeline Nocturno Completado</b>\n"
            f"Éxitos: <b>{overall_ok}</b>\n"
            f"Fallos: <b>{overall_failed}</b>\n"
            f"Hora: {now_str}"
        )

        return 0 if overall_failed == 0 else 1


def main() -> int:
    """CLI entrypoint for running the pipeline."""
    parser = argparse.ArgumentParser(description="Multitenant Pipeline Orchestrator")
    parser.add_argument("--client", default=None, help="Target specific client ID")
    parser.add_argument(
        "--clients-dir",
        default=os.getenv("CLIENTS_DIR", "/app/clients"),
        help="Path to directory containing client YAML files",
    )
    parser.add_argument(
        "--dbt-dir",
        default=os.getenv("DBT_PROJECT_DIR", "/app/src/dbt_project"),
        help="Path to dbt project directory",
    )
    args = parser.parse_args()

    runner = PipelineRunner(clients_dir=args.clients_dir, dbt_project_dir=args.dbt_dir)
    return runner.run_all(target_client=args.client)


if __name__ == "__main__":
    sys.exit(main())
