#!/usr/bin/env python3
"""
Autonomous Cloud Pipeline Runner for GitHub Actions / Cron Schedulers.
Runs incremental Gmail bank alert sync, calculates AMB, and pushes ntfy mobile alerts.
"""

import os
import json
import base64
import datetime
from quickstart import main as sync_gmail
from amb_engine import forecast_amb, send_alert_notification, get_monthly_anchor
from db_supabase import get_supabase_client


def setup_auth_files():
    """Decode base64 GitHub Secrets into token.json & credentials.json if running in CI/CD."""
    token_b64 = os.getenv("GMAIL_TOKEN_BASE64")
    if token_b64 and not os.path.exists("token.json"):
        try:
            with open("token.json", "wb") as f:
                f.write(base64.b64decode(token_b64))
            print("[Cloud Setup] Reconstructed token.json from GMAIL_TOKEN_BASE64 Secret.")
        except Exception as e:
            print(f"[Cloud Setup Error] Could not write token.json: {e}")

    creds_b64 = os.getenv("GMAIL_CREDENTIALS_BASE64")
    if creds_b64 and not os.path.exists("credentials.json"):
        try:
            with open("credentials.json", "wb") as f:
                f.write(base64.b64decode(creds_b64))
            print("[Cloud Setup] Reconstructed credentials.json from GMAIL_CREDENTIALS_BASE64 Secret.")
        except Exception as e:
            print(f"[Cloud Setup Error] Could not write credentials.json: {e}")


def run_pipeline():
    print(f"==================================================")
    print(f"🚀 RUNNING CLOUD AMB PIPELINE: {datetime.datetime.now().isoformat()}")
    print(f"==================================================")

    # 1. Setup Gmail OAuth credentials in CI environment
    setup_auth_files()

    # 2. Incremental Sync with Gmail
    print("\n📩 [Step 1/3] Incremental Gmail Transaction Sync...")
    try:
        new_txns = sync_gmail()
        print(f"✅ Incremental sync completed! Processed {new_txns or 0} new bank alerts.")
    except Exception as e:
        print(f"⚠️ [Sync Warning] Gmail sync encountered an issue: {e}")

    # 3. Calculate Current AMB Forecast
    print("\n📈 [Step 2/3] Calculating Current Month AMB & Safety Index...")
    today_str = datetime.date.today().strftime("%Y-%m")
    forecast = forecast_amb(target_amb=10000.0, month_year_str=today_str)

    print(f"  - Month: {forecast['month_year']}")
    print(f"  - Current AMB: Rs.{forecast['current_amb']:,.2f}")
    print(f"  - Target AMB: Rs.{forecast['target_amb']:,.2f}")
    print(f"  - Required Daily Balance: Rs.{forecast['required_daily_balance']:,.2f}")
    print(f"  - Status: {forecast['status']}")

    # 4. Push Mobile Notification via ntfy.sh
    print("\n🔔 [Step 3/3] Evaluating Mobile Alert Trigger...")
    send_alert_notification(forecast)
    
    print("\n🎉 Pipeline Execution Completed Successfully!")


if __name__ == "__main__":
    run_pipeline()
