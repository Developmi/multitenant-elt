import argparse
import json
import os
import sys
import time

import dlt
from dlt.sources.helpers import requests

from agency_analytics import client_config
from agency_analytics.google_auth import GoogleTokenManager, resolve_google_credentials

GTM_API_BASE = "https://tagmanager.googleapis.com/tagmanager/v2"


def _headers(access_token: str = "", token_manager: GoogleTokenManager | None = None):
    token = token_manager.get_token() if token_manager is not None else access_token
    return {"Authorization": f"Bearer {token}"}


def _handle_response(response, context: str):
    if response.status_code == 429:
        wait = int(response.headers.get("Retry-After", 30))
        print(f"[GTM] Rate limited ({context}). Waiting {wait}s...")
        time.sleep(wait)
        return None

    try:
        data = response.json()
    except requests.RequestException as e:
        print(f"[GTM] JSON parse error in {context}: {e}")
        raise

    if "error" in data:
        err = data["error"]
        code = err.get("code", 0)
        msg = err.get("message", "")
        if code == 429:
            wait = 30
            print(f"[GTM] Rate limited (code 429). Waiting {wait}s...")
            time.sleep(wait)
            return None
        elif code in (401, 403):
            raise Exception(
                f"[GTM] Token expired or access denied ({context}). "
                f"Renew the access token in .env and redeploy."
            )
        elif code == 404:
            raise Exception(f"[GTM] Resource not found ({context}): {msg}")
        elif code == 400:
            raise Exception(f"[GTM] Bad request ({context}): {msg}")
        else:
            raise Exception(f"[GTM] API error {code} ({context}): {msg}")
    return data


def _get_with_refresh(
    url: str,
    context: str,
    access_token: str = "",
    token_manager: GoogleTokenManager | None = None,
):
    refreshed_on_401 = False
    while True:
        try:
            response = requests.get(url, headers=_headers(access_token, token_manager))
        except requests.RequestException as e:
            print(f"[GTM] Request error ({context}): {e}")
            raise

        try:
            data = _handle_response(response, context)
            if data is None:
                continue
            return data
        except Exception as exc:
            if "Token expired" in str(exc) and token_manager is not None and not refreshed_on_401:
                refreshed_on_401 = True
                token_manager._access_token = None
                continue
            raise


@dlt.resource(name="containers", write_disposition="replace")
def get_containers(
    account_path: str,
    access_token: str = "",
    token_manager: GoogleTokenManager | None = None,
):
    url = f"{GTM_API_BASE}/{account_path}/containers"
    data = _get_with_refresh(
        url, "containers", access_token=access_token, token_manager=token_manager
    )
    for container in data.get("container", []):
        yield {
            "container_id": container.get("containerId"),
            "account_id": container.get("accountId"),
            "name": container.get("name"),
            "public_id": container.get("publicId"),
            "usage_context": json.dumps(container.get("usageContext", [])),
        }


@dlt.resource(name="tags", write_disposition="replace")
def get_tags(
    account_path: str,
    access_token: str = "",
    token_manager: GoogleTokenManager | None = None,
):
    containers_url = f"{GTM_API_BASE}/{account_path}/containers"
    containers_data = _get_with_refresh(
        containers_url,
        "containers list for tags",
        access_token=access_token,
        token_manager=token_manager,
    )

    container_ids = [c["containerId"] for c in containers_data.get("container", [])]

    for cid in container_ids:
        workspaces_url = f"{GTM_API_BASE}/{account_path}/containers/{cid}/workspaces"
        ws_data = _get_with_refresh(
            workspaces_url,
            f"workspaces container {cid}",
            access_token=access_token,
            token_manager=token_manager,
        )
        workspaces = [w["workspaceId"] for w in ws_data.get("workspace", [])]
        workspace_paths = (
            [
                f"{GTM_API_BASE}/{account_path}/containers/{cid}/workspaces/{wid}/tags"
                for wid in workspaces
            ]
            if workspaces
            else [f"{GTM_API_BASE}/{account_path}/containers/{cid}/tags"]
        )

        for tags_url in workspace_paths:
            tags_data = _get_with_refresh(
                tags_url,
                f"tags container {cid}",
                access_token=access_token,
                token_manager=token_manager,
            )

            for tag in tags_data.get("tag", []):
                firing = tag.get("firingTriggerId", [])
                blocking = tag.get("blockingTriggerId", [])
                tag_manager_url = (
                    f"https://tagmanager.google.com/#/container/{account_path}"
                    f"/workspaces/{tag.get('workspaceId', '')}/tag/{tag.get('tagId', '')}"
                )
                yield {
                    "tag_id": tag.get("tagId"),
                    "container_id": cid,
                    "type": tag.get("type"),
                    "name": tag.get("name"),
                    "firing_triggers": json.dumps(firing),
                    "blocking_triggers": json.dumps(blocking),
                    "tag_manager_url": tag_manager_url,
                }


@dlt.resource(name="triggers", write_disposition="replace")
def get_triggers(
    account_path: str,
    access_token: str = "",
    token_manager: GoogleTokenManager | None = None,
):
    containers_url = f"{GTM_API_BASE}/{account_path}/containers"
    containers_data = _get_with_refresh(
        containers_url,
        "containers list for triggers",
        access_token=access_token,
        token_manager=token_manager,
    )

    container_ids = [c["containerId"] for c in containers_data.get("container", [])]

    for cid in container_ids:
        workspaces_url = f"{GTM_API_BASE}/{account_path}/containers/{cid}/workspaces"
        ws_data = _get_with_refresh(
            workspaces_url,
            f"workspaces container {cid}",
            access_token=access_token,
            token_manager=token_manager,
        )
        workspaces = [w["workspaceId"] for w in ws_data.get("workspace", [])]
        workspace_paths = (
            [
                f"{GTM_API_BASE}/{account_path}/containers/{cid}/workspaces/{wid}/triggers"
                for wid in workspaces
            ]
            if workspaces
            else [f"{GTM_API_BASE}/{account_path}/containers/{cid}/triggers"]
        )

        for triggers_url in workspace_paths:
            trig_data = _get_with_refresh(
                triggers_url,
                f"triggers container {cid}",
                access_token=access_token,
                token_manager=token_manager,
            )

            for trigger in trig_data.get("trigger", []):
                yield {
                    "trigger_id": trigger.get("triggerId"),
                    "container_id": cid,
                    "type": trigger.get("type"),
                    "name": trigger.get("name"),
                    "filter_json": json.dumps(trigger.get("filter", [])),
                }


@dlt.source
def gtm_source(
    account_path: str,
    access_token: str = "",
    token_manager: GoogleTokenManager | None = None,
):
    return [
        get_containers(account_path, access_token=access_token, token_manager=token_manager),
        get_tags(account_path, access_token=access_token, token_manager=token_manager),
        get_triggers(account_path, access_token=access_token, token_manager=token_manager),
    ]


if __name__ == "__main__":
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Google Tag Manager API v2 dlt extractor")
    parser.add_argument("--client", required=True, help="Client ID from clients/ YAML")
    args = parser.parse_args()

    client_file = client_config.client_file_path(args.client)

    if not os.path.exists(client_file):
        print(f"[GTM] Client file not found: {client_file}")
        sys.exit(1)

    client = client_config.load_client(args.client)

    if not client.get("active", True):
        print(f"[GTM] Client {args.client} is not active. Skipping.")
        sys.exit(0)

    errors = client_config.validate_client(client)
    if errors:
        for error in errors:
            print(f"[GTM] {error}")
        sys.exit(1)

    connector = client["connectors"].get("gtm", {})
    if not connector.get("enabled"):
        print(f"[GTM] GTM connector not enabled for client {args.client}. Skipping.")
        sys.exit(0)

    account_path = connector["account_path"]
    if account_path and not account_path.startswith("accounts/"):
        account_path = f"accounts/{account_path}"

    # Resolve credentials: OAuth TokenManager preferred over static token
    oauth_creds = resolve_google_credentials(client_name=args.client, service="gtm")
    token_manager: GoogleTokenManager | None = None
    access_token = ""

    if oauth_creds:
        token_manager = GoogleTokenManager(
            client_id=oauth_creds["client_id"],
            client_secret=oauth_creds["client_secret"],
            refresh_token=oauth_creds["refresh_token"],
        )
    else:
        token_env = connector.get("token_env")
        if token_env and token_env in os.environ and os.environ[token_env].strip():
            access_token = os.environ[token_env].strip()
        else:
            print(
                f"[GTM] Missing GTM credentials for client {args.client}. "
                f"Run 'make auth-google CLIENT={args.client}' or set {token_env} in .env."
            )
            sys.exit(1)

    print(f"[GTM] Extracting data for client '{args.client}' (path {account_path})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"gtm_{args.client}",
        destination="postgres",
        dataset_name=client_config.raw_dataset(args.client, "gtm"),
    )
    info = pipeline.run(
        gtm_source(account_path, access_token=access_token, token_manager=token_manager)
    )
    print(f"[GTM] Done: {info}")
