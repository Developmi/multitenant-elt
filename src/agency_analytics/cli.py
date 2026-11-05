import argparse
import subprocess
import sys
from pathlib import Path

import yaml

CLIENTS_DIR = Path(__file__).resolve().parent.parent.parent / "clients"


def cmd_pipeline(args):
    """Run the local pipeline across active clients."""
    print("Running local pipeline (standalone)...")
    target_client = getattr(args, "client", None)
    if target_client:
        client_files = [CLIENTS_DIR / f"{target_client}.yml"]
    else:
        client_files = sorted(CLIENTS_DIR.glob("*.yml"))

    client_files = [f for f in client_files if f.name != "_template.yml" and f.exists()]

    if not client_files:
        print(f"No client configuration files found in {CLIENTS_DIR}")
        return 1

    success = 0
    failed = 0
    for cf in client_files:
        client_id = cf.stem
        try:
            with open(cf, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            print(f"  Failed reading config for {client_id}: {exc}")
            failed += 1
            continue

        if not data.get("active", True):
            print(f"  Skipping inactive client: {client_id}")
            continue

        print(f"  Processing client: {client_id}")
        target_connector = getattr(args, "connector", None)
        if target_connector:
            enabled_connectors = [target_connector]
        else:
            enabled_connectors = [
                name
                for name, cfg in (data.get("connectors") or {}).items()
                if isinstance(cfg, dict) and cfg.get("enabled", False)
            ]

        client_failed = False
        for connector in enabled_connectors:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agency_analytics.cli",
                    "dlt",
                    "--connector",
                    connector,
                    "--client",
                    client_id,
                ],
                capture_output=True,
                text=True,
            )
            print(f"    dlt ({connector}): {'OK' if result.returncode == 0 else 'FAIL'}")
            if result.returncode != 0:
                client_failed = True
                if result.stderr:
                    print(f"    stderr: {result.stderr.strip()}")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agency_analytics.cli",
                "dbt",
                "--command",
                "run",
                "--client",
                client_id,
            ],
            capture_output=True,
            text=True,
        )
        print(f"    dbt: {'OK' if result.returncode == 0 else 'FAIL'}")
        if result.returncode != 0:
            client_failed = True
            if result.stderr:
                print(f"    stderr: {result.stderr.strip()}")

        if client_failed:
            failed += 1
        else:
            success += 1

    print(f"Pipeline finished: {success} succeeded, {failed} failed")
    return 0 if failed == 0 else 1


def cmd_dbt(args):
    """Execute dbt commands with optional tenant scoping."""
    cmd = ["dbt", args.command]
    if args.client:
        cmd.extend(["--vars", f'{{"client_id": "{args.client}"}}'])
    print(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def cmd_dlt(args):
    """Run an individual dlt connector for a specific client."""
    connector = args.connector
    client = args.client
    script = CLIENTS_DIR.parent / "src" / "connectors" / f"run_{connector}.py"
    if not script.exists():
        print(f"Error: dlt script not found: {script}")
        return 1
    print(f"Executing: {sys.executable} {script} --client {client}")
    result = subprocess.run([sys.executable, str(script), "--client", client])
    return result.returncode


def cmd_auth_meta(args):
    from agency_analytics.meta_auth import run_meta_auth_wizard

    return run_meta_auth_wizard(
        client_id=args.client,
        app_id=args.app_id,
        app_secret=args.app_secret,
        token=args.token,
        env_path=args.env_path,
        interactive=not args.non_interactive,
    )


def cmd_auth_google(args):
    from agency_analytics.google_auth import run_google_auth_wizard

    services = [s.strip() for s in args.services.split(",")] if args.services else ["all"]
    return run_google_auth_wizard(
        client_name=args.client,
        services=services,
        client_id=args.client_id,
        client_secret=args.client_secret,
        env_path=args.env_path,
        interactive=not args.non_interactive,
    )


def cli():
    parser = argparse.ArgumentParser(
        prog="multitenant-elt",
        description="Multitenant ELT CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_pipeline = subparsers.add_parser("pipeline", help="Run the full pipeline locally")
    p_pipeline.add_argument("--client", default=None, help="Target client ID (or all if omitted)")
    p_pipeline.add_argument(
        "--connector", default=None, help="Target connector name (or all enabled if omitted)"
    )
    p_pipeline.set_defaults(func=cmd_pipeline)

    p_dbt = subparsers.add_parser("dbt", help="Execute dbt commands")
    p_dbt.add_argument("--command", default="run", help="dbt command (run, test, build, etc.)")
    p_dbt.add_argument("--client", default=None, help="Client ID to filter models via tag")
    p_dbt.set_defaults(func=cmd_dbt)

    p_dlt = subparsers.add_parser("dlt", help="Run a dlt connector extractor")
    p_dlt.add_argument(
        "--connector",
        required=True,
        help=(
            "Connector name: meta, tiktok, google, facebook, instagram, "
            "tiktok_organic, youtube, pinterest, ga4, gtm"
        ),
    )
    p_dlt.add_argument("--client", required=True, help="Client ID")
    p_dlt.set_defaults(func=cmd_dlt)

    p_auth = subparsers.add_parser("auth", help="Authentication and token management tools")
    auth_subparsers = p_auth.add_subparsers(dest="auth_provider", required=True)

    p_meta = auth_subparsers.add_parser(
        "meta",
        help="Interactive Meta Graph API token renewal and Page Access Token resolution wizard",
    )
    p_meta.add_argument(
        "--client", default=None, help="Target client ID to update in .env (e.g. client_example)"
    )
    p_meta.add_argument("--app-id", default=None, help="Meta Application ID")
    p_meta.add_argument("--app-secret", default=None, help="Meta Application Secret")
    p_meta.add_argument("--token", default=None, help="Initial short-lived User Access Token")
    p_meta.add_argument("--env-path", default=".env", help="Path to .env file (default: .env)")
    p_meta.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run without interactive prompts (fails if required flags are omitted)",
    )
    p_meta.set_defaults(func=cmd_auth_meta)

    p_google = auth_subparsers.add_parser(
        "google",
        help="Interactive unified Google OAuth refresh token wizard (Ads, GA4, GTM, YouTube)",
    )
    p_google.add_argument(
        "--client",
        default=None,
        help="Target client ID for per-client keys, or omit for global agency credentials",
    )
    p_google.add_argument(
        "--services",
        default="all",
        help="Comma-separated services: all, ga4, gtm, google_ads, youtube (default: all)",
    )
    p_google.add_argument("--client-id", default=None, help="Google OAuth Client ID")
    p_google.add_argument("--client-secret", default=None, help="Google OAuth Client Secret")
    p_google.add_argument("--env-path", default=".env", help="Path to .env file (default: .env)")
    p_google.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run without interactive prompts (fails if required flags are omitted)",
    )
    p_google.set_defaults(func=cmd_auth_google)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(cli())
