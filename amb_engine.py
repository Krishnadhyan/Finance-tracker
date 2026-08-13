import os
import json
import datetime
import calendar
import requests
from dotenv import load_dotenv
from db_supabase import get_supabase_client, parse_to_iso_date

load_dotenv(override=True)

TARGET_AMB_DEFAULT = float(os.getenv("TARGET_AMB", "10000.00"))
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "ai_finance_tracker_amb_alert")


def get_monthly_anchor(month_year_str):
    """Retrieve starting anchor balance for a given month ('YYYY-MM') from Supabase."""
    client = get_supabase_client()
    if client:
        try:
            res = client.table("monthly_anchors").select("anchor_balance").eq("month_year", month_year_str).execute()
            if res.data and len(res.data) > 0:
                return float(res.data[0]["anchor_balance"])
        except Exception as e:
            print(f"[AMB Engine Note] Could not fetch anchor balance from Supabase: {e}")

    # Fallback to .env setting
    return float(os.getenv("CURRENT_ACCOUNT_BALANCE", "10000.00"))


def set_monthly_anchor(month_year_str, anchor_balance):
    """Set or update starting anchor balance for a given month ('YYYY-MM') in Supabase."""
    client = get_supabase_client()
    if not client:
        print("[Supabase Warning] Supabase credentials not set in .env")
        return False
    try:
        record = {
            "month_year": month_year_str,
            "anchor_balance": float(anchor_balance),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        client.table("monthly_anchors").upsert(record).execute()
        print(f"[AMB Engine] Set anchor balance for {month_year_str} to Rs.{anchor_balance:,.2f}")
        return True
    except Exception as e:
        print(f"[AMB Engine Error] Failed to set monthly anchor: {e}")
        return False


def reconstruct_eod_ledger(month_year_str, anchor_balance, transactions_list):
    """
    Phase 2: Reconstruct Bank Ledger & End-of-Day (EOD) Balances.
    Groups transactions by day, computes daily net flows (Credits - Debits),
    simulates running EOD balances from 1st to today, and carries over empty days.
    """
    year, month = map(int, month_year_str.split("-"))
    today = datetime.date.today()

    _, total_days_in_month = calendar.monthrange(year, month)

    # Determine up to which day we simulate (today if current month, else full month)
    if today.year == year and today.month == month:
        days_passed = today.day
    elif datetime.date(year, month, 1) < today:
        days_passed = total_days_in_month
    else:
        days_passed = 0

    # 1. Group daily net flows (CREDIT - DEBIT)
    daily_net_flows = {d: 0.0 for d in range(1, days_passed + 1)}

    for t in transactions_list:
        raw_d = t.get("date")
        iso_d = parse_to_iso_date(raw_d)
        if not iso_d:
            continue

        try:
            t_date = datetime.datetime.strptime(iso_d, "%Y-%m-%d").date()
            if t_date.year == year and t_date.month == month and 1 <= t_date.day <= days_passed:
                amt = float(t.get("amount") or 0.0)
                t_type = (t.get("type") or "").lower()

                if t_type == "credit":
                    daily_net_flows[t_date.day] += amt
                elif t_type == "debit":
                    daily_net_flows[t_date.day] -= amt
        except Exception:
            continue

    # 2. Simulate EOD balances chronologically starting from anchor_balance
    eod_balances = {}
    running_balance = float(anchor_balance)

    for day in range(1, days_passed + 1):
        net_flow = daily_net_flows.get(day, 0.0)
        running_balance += net_flow
        date_str = f"{year}-{month:02d}-{day:02d}"
        eod_balances[date_str] = round(running_balance, 2)

    # 3. Log EOD balances to Supabase daily_balances table
    client = get_supabase_client()
    if client and eod_balances:
        try:
            records = [
                {"date": d_str, "closing_balance": bal, "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
                for d_str, bal in eod_balances.items()
            ]
            client.table("daily_balances").upsert(records).execute()
            print(f"[AMB Engine] Logged {len(records)} daily EOD balances to Supabase.")
        except Exception as e:
            print(f"[AMB Engine Note] Failed logging to daily_balances: {e}")

    return {
        "eod_balances": eod_balances,
        "days_passed": days_passed,
        "total_days_in_month": total_days_in_month,
        "final_running_balance": running_balance
    }


def forecast_amb(target_amb=TARGET_AMB_DEFAULT, month_year_str=None, anchor_balance=None, transactions_list=None):
    """
    Phase 3: AMB Forecasting Engine.
    Calculates Current AMB, Total Target Sum, Deficit, and Required Daily Balance for remaining days.
    """
    today = datetime.date.today()
    if not month_year_str:
        month_year_str = f"{today.year}-{today.month:02d}"

    if anchor_balance is None:
        anchor_balance = get_monthly_anchor(month_year_str)

    if transactions_list is None:
        client = get_supabase_client()
        if client:
            try:
                res = client.table("transactions").select("*").execute()
                transactions_list = res.data or []
            except Exception as e:
                print(f"[AMB Engine Note] Could not fetch transactions from Supabase: {e}")
                transactions_list = []
        else:
            json_path = "transactions.json"
            if os.path.exists(json_path):
                with open(json_path, "r") as f:
                    transactions_list = json.load(f)
            else:
                transactions_list = []

    # Phase 2: Reconstruct EOD Ledger
    ledger = reconstruct_eod_ledger(month_year_str, anchor_balance, transactions_list)
    eod_balances = ledger["eod_balances"]
    days_passed = ledger["days_passed"]
    total_days = ledger["total_days_in_month"]
    current_available_balance = ledger["final_running_balance"]

    days_remaining = max(0, total_days - days_passed)

    # 1. Calculate Current AMB (sum of EOD balances / days passed)
    sum_eod_passed = sum(eod_balances.values())
    current_amb = (sum_eod_passed / days_passed) if days_passed > 0 else anchor_balance

    # 2. Calculate Total Target Sum (Target AMB * total_days)
    total_target_sum = target_amb * total_days

    # 3. Calculate Deficit (Total Target Sum - sum of passed EOD balances)
    deficit = max(0.0, total_target_sum - sum_eod_passed)

    # 4. Determine Daily Requirement for remaining days
    if days_remaining > 0:
        required_daily_balance = deficit / days_remaining
    else:
        required_daily_balance = current_available_balance

    # Phase 4: Proactive Alert & Shortfall Calculation
    shortfall = max(0.0, required_daily_balance - current_available_balance)
    status = "WARNING" if shortfall > 0 or current_amb < target_amb else "SAFE"

    message = ""
    if shortfall > 0:
        message = (
            f"🚨 AMB SHORTFALL ALERT for {month_year_str}!\n"
            f"Current AMB: Rs.{current_amb:,.2f} | Target AMB: Rs.{target_amb:,.2f}\n"
            f"Current Available Balance: Rs.{current_available_balance:,.2f}\n"
            f"Required Daily Balance for next {days_remaining} days: Rs.{required_daily_balance:,.2f}\n"
            f"👉 Deposit at least Rs.{shortfall:,.2f} before midnight to meet minimum average requirements!"
        )
    else:
        message = (
            f"✅ AMB SAFE STATUS for {month_year_str}\n"
            f"Current AMB: Rs.{current_amb:,.2f} | Target AMB: Rs.{target_amb:,.2f}\n"
            f"Current Available Balance: Rs.{current_available_balance:,.2f}\n"
            f"Maintaining Rs.{required_daily_balance:,.2f}/day for remaining {days_remaining} days keeps your account safe."
        )

    forecast = {
        "month_year": month_year_str,
        "anchor_balance": anchor_balance,
        "days_passed": days_passed,
        "days_remaining": days_remaining,
        "total_days_in_month": total_days,
        "target_amb": target_amb,
        "sum_eod_passed": sum_eod_passed,
        "current_amb": round(current_amb, 2),
        "total_target_sum": round(total_target_sum, 2),
        "deficit": round(deficit, 2),
        "current_available_balance": round(current_available_balance, 2),
        "required_daily_balance": round(required_daily_balance, 2),
        "shortfall": round(shortfall, 2),
        "status": status,
        "message": message
    }

    return forecast


def send_alert_notification(forecast):
    """Phase 4: Push proactive alert via ntfy webhook or console if shortfall exists."""
    print(f"\n--- AMB FORECAST REPORT ({forecast['month_year']}) ---")
    print(forecast["message"])

    if forecast["status"] == "WARNING":
        try:
            url = f"https://ntfy.sh/{NTFY_TOPIC}"
            requests.post(url, data=forecast["message"].encode("utf-8"), headers={
                "Title": f"AMB Shortfall Alert: Rs.{forecast['shortfall']:,.2f} Needed",
                "Priority": "high",
                "Tags": "warning,bank,money"
            }, timeout=5)
            print(f"[Notification] Pushed alert to ntfy.sh/{NTFY_TOPIC}")
        except Exception as e:
            print(f"[Notification Note] Could not send ntfy push notification: {e}")


if __name__ == "__main__":
    today_str = datetime.date.today().strftime("%Y-%m")
    print("=== AMB Forecasting & Alert Engine ===")
    
    # Run Forecast with default or configured anchor balance
    report = forecast_amb(target_amb=10000.0, month_year_str=today_str)
    send_alert_notification(report)
