import argparse
import os
import sys
import time

import dlt
from dlt.sources.helpers import requests

from agency_analytics import client_config
from agency_analytics.google_auth import GoogleTokenManager, resolve_google_credentials

GOOGLE_API_BASE = "https://googleads.googleapis.com/v25"
MAX_RETRIES = 5


def _google_request(url, payload, headers, max_retries=MAX_RETRIES):
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=payload, headers=headers)
            data = response.json()
        except requests.RequestException as e:
            if attempt == max_retries:
                print(f"[GOOGLE] Max retries ({max_retries}) exceeded: {e}")
                raise
            wait = 2**attempt
            print(
                f"[GOOGLE] Request failed (attempt {attempt}/{max_retries}). Retrying in {wait}s..."
            )
            time.sleep(wait)
            continue

        if "error" not in data:
            return data

        error = data["error"]
        code = error.get("code", 0)
        msg = error.get("message", "")

        if code == 429:
            if attempt == max_retries:
                raise Exception(f"[GOOGLE] Max retries ({max_retries}) exceeded on rate limit")
            details = error.get("details", [{}])[0]
            wait_str = details.get("retryDelay", "30s")
            wait = int(wait_str.replace("s", "")) if isinstance(wait_str, str) else 30
            print(f"[GOOGLE] Rate limited (attempt {attempt}/{max_retries}). Waiting {wait}s...")
            time.sleep(wait)
            continue

        if code == 401:
            raise Exception("[GOOGLE] Token expired. Renew OAuth token and redeploy.")

        if code == 403:
            raise Exception(f"[GOOGLE] Access denied: {msg}")

        raise Exception(f"[GOOGLE] API error {code}: {msg}")

    raise Exception(f"[GOOGLE] Max retries ({max_retries}) exceeded without response")


def _google_search(
    customer_id: str,
    access_token: str = "",
    query: str = "",
    page_size: int = 10000,
    token_manager: GoogleTokenManager | None = None,
):
    url = f"{GOOGLE_API_BASE}/customers/{customer_id}/googleAds:search"
    payload = {"query": query, "pageSize": page_size}

    token = token_manager.get_token() if token_manager is not None else access_token
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    refreshed_on_401 = False
    while True:
        try:
            data = _google_request(url, payload, headers)
        except Exception as exc:
            if "Token expired" in str(exc) and token_manager is not None and not refreshed_on_401:
                refreshed_on_401 = True
                token_manager._access_token = None
                headers["Authorization"] = f"Bearer {token_manager.get_token()}"
                continue
            raise

        for row in data.get("results", []):
            yield row

        next_page = data.get("nextPageToken")
        if not next_page:
            break
        payload["pageToken"] = next_page


@dlt.resource(name="ads", write_disposition="replace")
def get_ads(
    customer_id: str,
    access_token: str = "",
    token_manager: GoogleTokenManager | None = None,
):
    query = """
        SELECT
            ad_group_ad.ad.id,
            ad_group_ad.ad.name,
            ad_group_ad.status,
            ad_group.id,
            campaign.id,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.average_cpc,
            metrics.conversions,
            metrics.cost_per_conversion,
            segments.date
        FROM ad_group_ad
        WHERE segments.date DURING LAST_30_DAYS
        ORDER BY segments.date DESC
    """

    for row in _google_search(
        customer_id, access_token=access_token, query=query, token_manager=token_manager
    ):
        ad = row.get("adGroupAd", {}).get("ad", {})
        ad_group = row.get("adGroup", {})
        campaign = row.get("campaign", {})
        metrics = row.get("metrics", {})
        segments = row.get("segments", {})

        cost = float(metrics.get("costMicros", 0)) / 1_000_000
        avg_cpc = float(metrics.get("averageCpc", 0)) / 1_000_000
        cost_per_conv = float(metrics.get("costPerConversion", 0)) / 1_000_000

        yield {
            "ad_id": ad.get("id"),
            "ad_name": ad.get("name"),
            "ad_status": row.get("adGroupAd", {}).get("status"),
            "ad_group_id": ad_group.get("id"),
            "campaign_id": campaign.get("id"),
            "spend": round(cost, 2),
            "impressions": int(metrics.get("impressions", 0)),
            "clicks": int(metrics.get("clicks", 0)),
            "ctr": float(metrics.get("ctr", 0.0)),
            "cpc": round(avg_cpc, 2),
            "conversions": float(metrics.get("conversions", 0.0)),
            "cost_per_conversion": round(cost_per_conv, 2),
            "date": segments.get("date"),
        }


@dlt.resource(name="campaigns", write_disposition="replace")
def get_campaigns(
    customer_id: str,
    access_token: str = "",
    token_manager: GoogleTokenManager | None = None,
):
    query = """
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.advertising_channel_type,
            campaign_budget.amount_micros,
            campaign.start_date,
            campaign.end_date,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.average_cpc,
            metrics.conversions,
            metrics.cost_per_conversion
        FROM campaign
        WHERE campaign.status != 'REMOVED'
        ORDER BY campaign.id
    """

    for row in _google_search(
        customer_id, access_token=access_token, query=query, token_manager=token_manager
    ):
        campaign = row.get("campaign", {})
        metrics = row.get("metrics", {})
        budget = campaign.get("budget", {})
        budget_amount = budget.get("amountMicros")

        daily_budget = float(budget_amount) / 1_000_000 if budget_amount is not None else None
        cost = float(metrics.get("costMicros", 0)) / 1_000_000
        avg_cpc = float(metrics.get("averageCpc", 0)) / 1_000_000
        cost_per_conv = float(metrics.get("costPerConversion", 0)) / 1_000_000

        yield {
            "campaign_id": campaign.get("id"),
            "campaign_name": campaign.get("name"),
            "status": campaign.get("status"),
            "channel_type": campaign.get("advertisingChannelType"),
            "daily_budget": round(daily_budget, 2) if daily_budget is not None else None,
            "spend": round(cost, 2),
            "impressions": int(metrics.get("impressions", 0)),
            "clicks": int(metrics.get("clicks", 0)),
            "ctr": float(metrics.get("ctr", 0.0)),
            "cpc": round(avg_cpc, 2),
            "conversions": float(metrics.get("conversions", 0.0)),
            "cost_per_conversion": round(cost_per_conv, 2),
            "start_date": campaign.get("startDate"),
            "end_date": campaign.get("endDate"),
            "date": campaign.get("startDate"),
        }


@dlt.source
def google_ads_source(
    customer_id: str,
    access_token: str = "",
    token_manager: GoogleTokenManager | None = None,
):
    return [
        get_ads(customer_id, access_token=access_token, token_manager=token_manager),
        get_campaigns(customer_id, access_token=access_token, token_manager=token_manager),
    ]


def main():
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Google Ads dlt extractor")
    parser.add_argument("--client", required=True, help="Client ID from clients/ YAML")
    args = parser.parse_args()

    client, connector, _ = client_config.setup_connector_run("google", args)

    customer_id = connector["customer_id"]

    # Resolve credentials: OAuth TokenManager with refresh token preferred
    oauth_creds = resolve_google_credentials(client_name=args.client, service="ads")
    token_manager: GoogleTokenManager | None = None
    access_token = ""

    if oauth_creds:
        token_manager = GoogleTokenManager(
            client_id=oauth_creds["client_id"],
            client_secret=oauth_creds["client_secret"],
            refresh_token=oauth_creds["refresh_token"],
        )
    else:
        # Fallback to direct token_env in client YAML
        token_env = connector.get("token_env")
        if token_env and token_env in os.environ and os.environ[token_env].strip():
            access_token = os.environ[token_env].strip()
        else:
            print(
                f"[GOOGLE] Missing Google Ads credentials for client {args.client}. "
                f"Run 'make auth-google CLIENT={args.client}' or set {token_env} in .env."
            )
            sys.exit(1)

    print(f"[GOOGLE] Extracting data for client '{args.client}' (customer {customer_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"google_{args.client}",
        destination="postgres",
        dataset_name=client_config.raw_dataset(args.client, "google"),
    )
    info = pipeline.run(
        google_ads_source(customer_id, access_token=access_token, token_manager=token_manager)
    )
    print(f"[GOOGLE] Done: {info}")


if __name__ == "__main__":
    main()
