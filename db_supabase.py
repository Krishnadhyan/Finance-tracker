import os
import datetime
import calendar
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Auto-categorization rule mapping
CATEGORY_RULES = [
    ({"zomato", "swiggy", "hornbill", "restaurant", "food", "nandhini", "hotel"}, "Food & Dining", "Restaurants"),
    ({"uber", "irctc", "ola", "fastag", "redbus", "abhibus"}, "Travel & Transport", "Cabs / Travel"),
    ({"jio", "airtel", "vi", "bsnl", "electricity", "bescom", "water", "gas", "paytm utility", "bapuji seva"}, "Bills & Utilities", "Recharge / Utility"),
    ({"blinkit", "zepto", "instamart", "bigbasket", "supermarket", "groceries"}, "Groceries & Supplies", "Groceries"),
    ({"amazon", "flipkart", "myntra", "meesho", "decathlon"}, "Shopping", "E-Commerce"),
    ({"medicals", "pharmacy", "apollo", "1mg", "dr"}, "Health & Medical", "Pharmacy / Doctor"),
]


def auto_categorize(party, raw_text):
    """Categorize transaction based on vendor name or raw text snippet."""
    text_search = f"{party or ''} {raw_text or ''}".lower()
    for keywords, category, sub_category in CATEGORY_RULES:
        if any(kw in text_search for kw in keywords):
            return category, sub_category
    return "General / Uncategorized", "Other"


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


def upsert_transactions(transactions):
    """Upsert structured transactions into Supabase PostgreSQL database."""
    client = get_supabase_client()
    if not client:
        print("[Supabase Warning] Supabase credentials not configured in .env. Skipping cloud upload.")
        return 0

    records = []
    for t in transactions:
        cat, sub_cat = auto_categorize(t.get("party"), t.get("raw_text"))
        
        # Parse date to YYYY-MM-DD format if needed
        date_str = t.get("date", "")
        formatted_date = date_str
        if date_str and "-" in date_str:
            parts = date_str.split("-")
            if len(parts) == 3:
                if len(parts[0]) == 2 and len(parts[2]) == 2:
                    # DD-MM-YY -> 20YY-MM-DD
                    formatted_date = f"20{parts[2]}-{parts[1]}-{parts[0]}"

        records.append({
            "id": t.get("id"),
            "date": formatted_date,
            "timestamp_ms": t.get("timestamp_ms", 0),
            "day_of_week": t.get("day_of_week"),
            "time": t.get("time"),
            "amount": t.get("amount"),
            "type": t.get("type"),
            "direction": t.get("direction"),
            "party": t.get("party"),
            "vpa": t.get("vpa"),
            "bank": t.get("bank"),
            "category": t.get("category") or cat,
            "sub_category": t.get("sub_category") or sub_cat,
            "raw_text": t.get("raw_text"),
            "source": t.get("source")
        })

    try:
        response = client.table("transactions").upsert(records).execute()
        count = len(response.data) if response.data else len(records)
        print(f"[Supabase] Successfully upserted {count} transactions to cloud PostgreSQL.")
        return count
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
            "date": date_str,
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

    # Fetch daily balance logs from Supabase if available
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

    # Build daily balance series for elapsed days (fallback to current_balance if log missing)
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


def get_sql_schema():
    """Returns SQL statements to initialize Supabase PostgreSQL database tables."""
    return """
-- Execute the following SQL in Supabase -> SQL Editor:

-- 1. Transactions Table
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    date DATE NOT NULL,
    timestamp_ms BIGINT,
    day_of_week TEXT,
    time TEXT,
    amount NUMERIC(12,2) NOT NULL,
    type TEXT NOT NULL,
    direction TEXT,
    party TEXT,
    vpa TEXT,
    bank TEXT,
    category TEXT,
    sub_category TEXT,
    notes TEXT,
    raw_text TEXT,
    source TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 2. Daily Balances Table
CREATE TABLE IF NOT EXISTS daily_balances (
    date DATE PRIMARY KEY,
    closing_balance NUMERIC(12,2) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 3. AMB Settings Table
CREATE TABLE IF NOT EXISTS amb_settings (
    id INT PRIMARY KEY DEFAULT 1,
    target_amb NUMERIC(12,2) NOT NULL DEFAULT 10000.00,
    cycle_start_day INT NOT NULL DEFAULT 1,
    current_balance NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_party ON transactions(party);
"""


if __name__ == "__main__":
    print("=== Supabase Finance Tracker & AMB Engine ===")
    if is_supabase_configured():
        print(f"Supabase configured: Connected to {SUPABASE_URL}")
    else:
        print("[Note] Supabase credentials not set in .env yet.")
        print("\n--- SQL Schema To Run in Supabase SQL Editor ---")
        print(get_sql_schema())

    # Demo AMB Engine calculation
    print("\n--- Average Monthly Balance (AMB) Calculation Demo ---")
    metrics = calculate_amb_metrics(target_amb=10000.0, current_balance=6000.0)
    for k, v in metrics.items():
        print(f"{k}: {v}")
