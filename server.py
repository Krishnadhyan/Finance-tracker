#!/usr/bin/env python3
"""
Lightweight REST API & Static File Web Server for AI Finance Tracker Dashboard.
Integrates directly with amb_engine.py, db_supabase.py, and transactions.json.
"""

import os
import json
import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Import local engines
from amb_engine import forecast_amb, set_monthly_anchor, send_alert_notification
from db_supabase import (
    auto_categorize,
    get_supabase_client,
    is_supabase_configured,
    sync_existing_json_to_supabase,
    get_last_processed_msg_id
)

PORT = 8000
WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_transactions_from_file_or_db():
    json_path = os.path.join(WORKSPACE_DIR, "transactions.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Server Error] Failed reading transactions.json: {e}")

    client = get_supabase_client()
    if client:
        try:
            res = client.table("transactions").select("*").execute()
            return res.data or []
        except Exception as e:
            print(f"[Server Error] Failed fetching transactions from Supabase: {e}")
    return []


class FinanceTrackerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WORKSPACE_DIR, **kwargs)

    def _set_headers(self, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        query = parse_qs(parsed_path.query)

        if path == "/api/overview":
            self.handle_get_overview()
        elif path == "/api/transactions":
            self.handle_get_transactions(query)
        elif path == "/api/amb/forecast":
            self.handle_get_forecast(query)
        elif path == "/api/supabase/status":
            self.handle_get_supabase_status()
        else:
            # Serve static files (index.html, styles.css, app.js, etc.)
            super().do_GET()

    def do_POST(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        content_length = int(self.headers.get("Content-Length", 0))
        post_data = {}
        if content_length > 0:
            raw_body = self.rfile.read(content_length)
            try:
                post_data = json.loads(raw_body.decode("utf-8"))
            except Exception:
                post_data = {}

        if path == "/api/categorize":
            self.handle_post_categorize(post_data)
        elif path == "/api/amb/forecast":
            self.handle_post_forecast(post_data)
        elif path == "/api/amb/anchor":
            self.handle_post_anchor(post_data)
        elif path == "/api/notify":
            self.handle_post_notify(post_data)
        elif path == "/api/sync":
            self.handle_post_sync()
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Endpoint not found"}).encode("utf-8"))

    def handle_get_overview(self):
        transactions = load_transactions_from_file_or_db()
        today_str = datetime.date.today().strftime("%Y-%m")
        forecast = forecast_amb(month_year_str=today_str, transactions_list=transactions)

        # Compute summary stats
        total_debit = 0.0
        total_credit = 0.0
        category_breakdown = {}

        for t in transactions:
            amt = float(t.get("amount") or 0.0)
            t_type = (t.get("type") or "").lower()
            cat, _ = auto_categorize(t.get("party"), t.get("raw_text"))

            if t_type == "debit":
                total_debit += amt
                category_breakdown[cat] = category_breakdown.get(cat, 0.0) + amt
            elif t_type == "credit":
                total_credit += amt

        supabase_ok = is_supabase_configured()
        checkpoint = get_last_processed_msg_id() if supabase_ok else "Local JSON"

        res_data = {
            "timestamp": datetime.datetime.now().isoformat(),
            "transaction_count": len(transactions),
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
            "net_flow": round(total_credit - total_debit, 2),
            "category_breakdown": {k: round(v, 2) for k, v in category_breakdown.items()},
            "amb_forecast": forecast,
            "supabase_configured": supabase_ok,
            "checkpoint_msg_id": checkpoint
        }

        self._set_headers(200)
        self.wfile.write(json.dumps(res_data).encode("utf-8"))

    def handle_get_transactions(self, query):
        transactions = load_transactions_from_file_or_db()
        
        # Apply filters if provided
        search = query.get("search", [""])[0].lower()
        category = query.get("category", [""])[0]
        txn_type = query.get("type", [""])[0].lower()

        filtered = []
        for t in transactions:
            cat, sub_cat = auto_categorize(t.get("party"), t.get("raw_text"))
            t_copy = dict(t)
            t_copy["category"] = cat
            t_copy["sub_category"] = sub_cat

            if search:
                searchable = f"{t_copy.get('party', '')} {t_copy.get('raw_text', '')} {t_copy.get('vpa', '')} {cat}".lower()
                if search not in searchable:
                    continue

            if category and category.lower() != "all" and cat.lower() != category.lower():
                continue

            if txn_type and txn_type != "all" and (t_copy.get("type") or "").lower() != txn_type:
                continue

            filtered.append(t_copy)

        self._set_headers(200)
        self.wfile.write(json.dumps({"total": len(filtered), "transactions": filtered}).encode("utf-8"))

    def handle_get_forecast(self, query):
        target_amb = float(query.get("target_amb", [10000.0])[0])
        month_year = query.get("month_year", [datetime.date.today().strftime("%Y-%m")])[0]
        transactions = load_transactions_from_file_or_db()
        forecast = forecast_amb(target_amb=target_amb, month_year_str=month_year, transactions_list=transactions)

        self._set_headers(200)
        self.wfile.write(json.dumps(forecast).encode("utf-8"))

    def handle_post_forecast(self, post_data):
        target_amb = float(post_data.get("target_amb", 10000.0))
        month_year = post_data.get("month_year", datetime.date.today().strftime("%Y-%m"))
        anchor_balance = post_data.get("anchor_balance")
        if anchor_balance is not None:
            anchor_balance = float(anchor_balance)

        transactions = load_transactions_from_file_or_db()

        # Check for simulated deposit or expense in post_data
        simulated_deposit = float(post_data.get("simulated_deposit", 0.0))
        simulated_expense = float(post_data.get("simulated_expense", 0.0))

        if simulated_deposit > 0 or simulated_expense > 0:
            today_str = datetime.date.today().strftime("%d-%m-%y")
            sim_txns = list(transactions)
            if simulated_deposit > 0:
                sim_txns.append({
                    "date": today_str,
                    "amount": simulated_deposit,
                    "type": "credit",
                    "party": "SIMULATED DEPOSIT"
                })
            if simulated_expense > 0:
                sim_txns.append({
                    "date": today_str,
                    "amount": simulated_expense,
                    "type": "debit",
                    "party": "SIMULATED EXPENSE"
                })
            transactions = sim_txns

        forecast = forecast_amb(
            target_amb=target_amb,
            month_year_str=month_year,
            anchor_balance=anchor_balance,
            transactions_list=transactions
        )

        self._set_headers(200)
        self.wfile.write(json.dumps(forecast).encode("utf-8"))

    def handle_post_categorize(self, post_data):
        party = post_data.get("party", "")
        raw_text = post_data.get("raw_text", "")
        cat, sub_cat = auto_categorize(party, raw_text)

        self._set_headers(200)
        self.wfile.write(json.dumps({
            "party": party,
            "raw_text": raw_text,
            "category": cat,
            "sub_category": sub_cat
        }).encode("utf-8"))

    def handle_post_anchor(self, post_data):
        month_year = post_data.get("month_year", datetime.date.today().strftime("%Y-%m"))
        anchor_balance = float(post_data.get("anchor_balance", 10000.0))
        success = set_monthly_anchor(month_year, anchor_balance)

        self._set_headers(200)
        self.wfile.write(json.dumps({
            "success": success,
            "month_year": month_year,
            "anchor_balance": anchor_balance
        }).encode("utf-8"))

    def handle_post_notify(self, post_data):
        target_amb = float(post_data.get("target_amb", 10000.0))
        forecast = forecast_amb(target_amb=target_amb)
        send_alert_notification(forecast)

        self._set_headers(200)
        self.wfile.write(json.dumps({
            "sent": True,
            "status": forecast["status"],
            "message": forecast["message"]
        }).encode("utf-8"))

    def handle_post_sync(self):
        count = sync_existing_json_to_supabase()
        self._set_headers(200)
        self.wfile.write(json.dumps({
            "success": True,
            "upserted_count": count
        }).encode("utf-8"))

    def handle_get_supabase_status(self):
        self._set_headers(200)
        self.wfile.write(json.dumps({
            "configured": is_supabase_configured(),
            "url": os.getenv("SUPABASE_URL", "Not configured")
        }).encode("utf-8"))


def run_server():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, FinanceTrackerHandler)
    print(f"🚀 AI Finance Tracker UI Server running at http://localhost:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
