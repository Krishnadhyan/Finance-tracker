import os
import json
import datetime
import calendar
from dotenv import load_dotenv

load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
if SUPABASE_URL and not SUPABASE_URL.startswith("http"):
    SUPABASE_URL = f"https://{SUPABASE_URL}.supabase.co"

SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

# Phase 1: Deterministic Vendor Memory Cache (Exact & Substring Mappings)
VENDOR_CATEGORY_MAP = {
    # Food & Dining
    "ZOMATO": ("Food & Dining", "Food Delivery"),
    "ZOMATO LIMITED": ("Food & Dining", "Food Delivery"),
    "SWIGGY": ("Food & Dining", "Food Delivery"),
    "SWIGGY LTD": ("Food & Dining", "Food Delivery"),
    "DOMINOS PIZZA": ("Food & Dining", "Fast Food"),
    "DOMINOS": ("Food & Dining", "Fast Food"),
    "NANDHINI DELUXE MINERVA CIRCLE": ("Food & Dining", "Restaurants"),
    "M S HORNBILL FOODS": ("Food & Dining", "Restaurants"),
    # Bills & Utilities
    "BANGALORE BROADBAND NETWORK PVT LTD": ("Utilities", "Broadband / Internet"),
    "JIO PREPAID RECHARGES": ("Utilities", "Mobile Recharge"),
    "JIO PLATFORM LTD FTTX": ("Utilities", "Broadband / Internet"),
    "RDPR KARNATAKA BAPUJI SEVA KENDRA MUNICIPAL BILL": ("Utilities", "Municipal Bills"),
    "PAYTM UTILITY": ("Utilities", "Bill Payments"),
    # Entertainment & Digital
    "GOOGLE PLAY": ("Entertainment", "Apps & Subscriptions"),
    "JIOHOTSTAR": ("Entertainment", "Streaming Subscriptions"),
    # Travel & Transport
    "UBER INDIA SYSTEMS PRIVATE LIMITED": ("Transport", "Cabs / Rides"),
    "IRCTC": ("Transport", "Train Tickets"),
    "PAYTM TRAIN TICKETS": ("Transport", "Train Tickets"),
    # Groceries & Shopping
    "BLINKIT": ("Groceries", "Instant Grocery"),
    "AMAZON INDIA": ("Shopping", "E-Commerce"),
    "SREE LAKSHMI VENKATESHWARA MEDICALS": ("Health & Medical", "Pharmacy"),
    "L AND T FINANCE LTD": ("Financial", "Loan / EMI"),
    "MSRATHODBUILDMARTPRIVATELIMITED": ("Shopping", "Home Improvement"),
}

# Fallback Keyword Rules
CATEGORY_RULES = [
    ({"zomato", "swiggy", "hornbill", "restaurant", "food", "nandhini", "hotel", "domino"}, "Food & Dining", "Restaurants"),
    ({"uber", "irctc", "ola", "fastag", "redbus", "abhibus"}, "Travel & Transport", "Cabs / Travel"),
    ({"jio", "airtel", "vi", "bsnl", "electricity", "bescom", "water", "gas", "paytm utility", "broadband"}, "Bills & Utilities", "Recharge / Utility"),
    ({"blinkit", "zepto", "instamart", "bigbasket", "supermarket", "groceries"}, "Groceries & Supplies", "Groceries"),
    ({"amazon", "flipkart", "myntra", "meesho", "decathlon"}, "Shopping", "E-Commerce"),
    ({"medicals", "pharmacy", "apollo", "1mg", "dr"}, "Health & Medical", "Pharmacy / Doctor"),
]


def auto_categorize(party, raw_text):
    """
    Deterministic Tagging Layer:
    1. Check Vendor Memory Cache (Exact / Uppercase Match).
    2. Substring Vendor Matching.
    3. Keyword Category Rules.
    4. Personal / Person Transfer Detection.
    """
    party_clean = (party or "").strip()
    party_upper = party_clean.upper()

    if not party_clean:
        return "Uncategorized", "General"

    # 1. Exact Match in Vendor Memory Cache
    if party_clean in VENDOR_CATEGORY_MAP:
        return VENDOR_CATEGORY_MAP[party_clean]
    if party_upper in VENDOR_CATEGORY_MAP:
        return VENDOR_CATEGORY_MAP[party_upper]

    # 2. Substring Match in Vendor Memory Cache
    for vendor_key, cat_tuple in VENDOR_CATEGORY_MAP.items():
        if vendor_key in party_upper or party_upper in vendor_key:
            return cat_tuple

    # 3. Fallback Keyword Rule Engine
    text_search = f"{party_clean} {raw_text or ''}".lower()
    for keywords, category, sub_category in CATEGORY_RULES:
        if any(kw in text_search for kw in keywords):
            return category, sub_category

    # 4. Peer-to-Peer / Personal Transfer Detection
    if any(prefix in party_clean.lower() for prefix in ["mr ", "mrs ", "dr "]):
        return "Transfers & Personal", "Personal Transfer"
    if len(party_clean.split()) >= 2 and not any(char.isdigit() for char in party_clean):
        return "Transfers & Personal", "Personal Transfer"

    return "Uncategorized", "General"


def parse_to_iso_date(date_str):
    """Parse various date string formats into standard ISO YYYY-MM-DD for PostgreSQL DATE columns."""
    if not date_str:
        return None
    clean_d = date_str.replace("/", "-").strip()
    parts = clean_d.split("-")
    if len(parts) == 3:
        p1, p2, p3 = parts[0], parts[1], parts[2]
        if len(p1) == 4:
            return f"{p1}-{p2.zfill(2)}-{p3.zfill(2)}"
        elif len(p3) == 4:
            return f"{p3}-{p2.zfill(2)}-{p1.zfill(2)}"
        elif len(p1) == 2 and len(p3) == 2:
            return f"20{p3}-{p2.zfill(2)}-{p1.zfill(2)}"
    return date_str


def is_supabase_configured():
    """Check if valid Supabase credentials are present."""
    return bool(
        SUPABASE_URL 
        and SUPABASE_KEY 
        and "your-project-id" not in SUPABASE_URL 
        and "your-supabase" not in SUPABASE_KEY
    )


def get_supabase_client():
    """Initialize Supabase client if credentials are set."""
    if not is_supabase_configured():
        return None
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Error connecting to Supabase: {e}")
        return None


def get_last_processed_msg_id():
    """Fetch the last processed Gmail Message ID from sync_state table."""
    client = get_supabase_client()
    if not client:
        return None
    try:
        res = client.table("sync_state").select("value").eq("key", "last_processed_msg_id").execute()
        if res.data and len(res.data) > 0:
            msg_id = res.data[0].get("value")
            print(f"[Supabase Checkpoint] Last processed msg_id: {msg_id}")
            return msg_id
    except Exception as e:
        print(f"[Supabase Note] Could not fetch sync_state checkpoint: {e}")
    return None


def update_last_processed_msg_id(newest_msg_id):
    """Update the last processed Gmail Message ID in sync_state table."""
    client = get_supabase_client()
    if not client or not newest_msg_id:
        return False
    try:
        client.table("sync_state").upsert({
            "key": "last_processed_msg_id",
            "value": newest_msg_id,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }).execute()
        print(f"[Supabase Checkpoint] Updated last_processed_msg_id to: {newest_msg_id}")
        return True
    except Exception as e:
        print(f"[Supabase Error] Failed to update sync_state: {e}")
        return False


def upsert_transactions(transactions):
    """Upsert structured transactions into Supabase PostgreSQL database with deterministic tagging."""
    client = get_supabase_client()
    if not client:
        print("[Supabase Warning] Supabase credentials not configured in .env. Skipping cloud upload.")
        return 0

    records = []
    for t in transactions:
        cat, sub_cat = auto_categorize(t.get("party"), t.get("raw_text"))
        iso_date = parse_to_iso_date(t.get("date", ""))

        records.append({
            "id": t.get("id"),
            "date": iso_date,
            "timestamp_ms": t.get("timestamp_ms", 0),
            "day_of_week": t.get("day_of_week"),
            "time": t.get("time"),
            "amount": t.get("amount"),
            "type": t.get("type"),
            "direction": t.get("direction"),
            "party": t.get("party"),
            "vpa": t.get("vpa"),
            "bank": t.get("bank"),
            "category": cat,
            "sub_category": sub_cat,
            "raw_text": t.get("raw_text"),
            "source": t.get("source")
        })

    try:
        chunk_size = 100
        total_upserted = 0
        for i in range(0, len(records), chunk_size):
            chunk = records[i:i + chunk_size]
            res = client.table("transactions").upsert(chunk).execute()
            total_upserted += len(chunk)
        print(f"[Supabase] Successfully tagged and upserted {total_upserted} transactions to cloud PostgreSQL.")
        return total_upserted
    except Exception as e:
        print(f"[Supabase Error] Failed to upsert transactions: {e}")
        return 0


def log_daily_balance(date_str, closing_balance):
    """Log or update daily account closing balance in Supabase."""
    client = get_supabase_client()
    if not client:
        return False
    try:
        record = {
            "date": parse_to_iso_date(date_str),
            "closing_balance": float(closing_balance),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        client.table("daily_balances").upsert(record).execute()
        return True
    except Exception as e:
        print(f"[Supabase Error] Failed to log daily balance: {e}")
        return False


def calculate_amb_metrics(target_amb=10000.0, current_balance=0.0, cycle_start_day=1):
    """
    Average Monthly Balance (AMB) Calculation Engine.
    Computes current AMB status, projected status, and daily balance required
    for remaining days of the month to maintain target AMB.
    """
    today = datetime.date.today()
    year = today.year
    month = today.month
    day = today.day

    _, total_days = calendar.monthrange(year, month)
    days_elapsed = day
    days_remaining = total_days - days_elapsed

    client = get_supabase_client()
    balances = {}
    if client:
        try:
            start_date = f"{year}-{month:02d}-01"
            end_date = f"{year}-{month:02d}-{total_days:02d}"
            res = client.table("daily_balances").select("*").gte("date", start_date).lte("date", end_date).execute()
            if res.data:
                for row in res.data:
                    balances[row["date"]] = float(row["closing_balance"])
        except Exception as e:
            print(f"[AMB Engine Note] Could not fetch daily_balances table: {e}")

    accumulated_balance = 0.0
    for d in range(1, days_elapsed + 1):
        d_str = f"{year}-{month:02d}-{d:02d}"
        bal = balances.get(d_str, current_balance)
        accumulated_balance += bal

    current_amb = accumulated_balance / days_elapsed if days_elapsed > 0 else current_balance
    total_required_cumulative = target_amb * total_days
    remaining_cumulative_needed = total_required_cumulative - accumulated_balance

    required_daily_balance = 0.0
    if days_remaining > 0:
        required_daily_balance = max(0.0, remaining_cumulative_needed / days_remaining)
    else:
        required_daily_balance = current_balance

    status = "SAFE"
    if required_daily_balance > current_balance:
        status = "WARNING"
    elif current_amb < target_amb:
        status = "WARNING"

    message = ""
    if status == "WARNING":
        message = (
            f"Alert: Your current AMB is Rs.{current_amb:,.2f} (Target: Rs.{target_amb:,.2f}). "
            f"You need to maintain an average daily balance of Rs.{required_daily_balance:,.2f} "
            f"for the remaining {days_remaining} days of this month to avoid penalty charges."
        )
    else:
        message = (
            f"Status Safe: Current AMB is Rs.{current_amb:,.2f}. "
            f"Maintaining Rs.{required_daily_balance:,.2f}/day for remaining {days_remaining} days "
            f"meets your target of Rs.{target_amb:,.2f}."
        )

    return {
        "year_month": f"{year}-{month:02d}",
        "total_days_in_month": total_days,
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "target_amb": target_amb,
        "current_amb": current_amb,
        "current_balance": current_balance,
        "accumulated_balance": accumulated_balance,
        "total_required_cumulative": total_required_cumulative,
        "remaining_cumulative_needed": max(0.0, remaining_cumulative_needed),
        "required_daily_balance": required_daily_balance,
        "status": status,
        "message": message
    }


def sync_existing_json_to_supabase():
    """Import existing transactions.json file into Supabase cloud database with deterministic tags."""
    json_path = "transactions.json"
    if not os.path.exists(json_path):
        print(f"[Note] {json_path} not found.")
        return 0
    with open(json_path, "r") as f:
        data = json.load(f)
    print(f"Loaded {len(data)} transactions from {json_path}. Tagging & uploading to Supabase...")
    return upsert_transactions(data)


if __name__ == "__main__":
    print("=== Supabase Finance Tracker & Tagging Engine ===")
    if is_supabase_configured():
        print(f"Supabase configured: Connected to {SUPABASE_URL}")
        sync_existing_json_to_supabase()
    else:
        print("[Note] Supabase credentials not set in .env yet.")
