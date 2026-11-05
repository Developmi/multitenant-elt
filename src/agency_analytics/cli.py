import argparse
import subprocess
import sys
from pathlib import Path

CLIENTS_DIR = Path(__file__).resolve().parent.parent.parent / "clients"


def cmd_pipeline(args):
    print("Corriendo pipeline local (sin Docker)...")
    client_files = list(CLIENTS_DIR.glob("*.yml"))
    if not client_files:
        print("No se encontraron archivos de clientes en", CLIENTS_DIR)
        return 1
    success = 0
    failed = 0
    for cf in client_files:
        client_id = cf.stem
        print(f"  Procesando cliente: {client_id}")
        result = subprocess.run(
            [sys.executable, "-m", "agency_analytics.cli", "dlt", "--client", client_id],
            capture_output=True,
            text=True,
        )
        print(f"    dlt: {'OK' if result.returncode == 0 else 'FAIL'}")
        if result.returncode != 0:
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
            print(f"    stderr: {result.stderr.strip()}")
        if result.returncode == 0:
            success += 1
        else:
            failed += 1
    print(f"Pipeline finalizado: {success} exitos, {failed} fallos")
    return 0 if failed == 0 else 1


def cmd_dbt(args):
    cmd = ["dbt", args.command]
    if args.client:
        cmd.extend(["--vars", f'{{"client_id": "{args.client}"}}'])
    print(f"Ejecutando: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def cmd_dlt(args):
    connector = args.connector
    client = args.client
    script = CLIENTS_DIR.parent / "src" / "connectors" / f"run_{connector}.py"
    if not script.exists():
        print(f"Error: script dlt no encontrado: {script}")
        return 1
    print(f"Ejecutando: {sys.executable} {script} --client {client}")
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

    p_pipeline = subparsers.add_parser("pipeline", help="Corre el pipeline completo localmente")
    p_pipeline.set_defaults(func=cmd_pipeline)

    p_dbt = subparsers.add_parser("dbt", help="Ejecuta comandos dbt")
    p_dbt.add_argument("--command", default="run", help="Comando dbt (run, test, build, etc.)")
    p_dbt.add_argument("--client", default=None, help="Cliente para filtrar modelos via tag")
    p_dbt.set_defaults(func=cmd_dbt)

    p_dlt = subparsers.add_parser("dlt", help="Corre un extractor dlt")
    p_dlt.add_argument(
        "--connector",
        required=True,
        help=(
            "Nombre del conector: meta, tiktok, google, facebook, instagram, "
            "tiktok_organic, youtube, pinterest, ga4, gtm"
        ),
    )
    p_dlt.add_argument("--client", required=True, help="ID del cliente")
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
