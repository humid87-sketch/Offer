"""Build the Lock Your Rate call sheet from the Mindbody exports.

Identifies monthly-contract members (Four/Eight/Twelve Classes are recurring autopay
items, not packs), splits them into live and summer-lapsed pools, and ranks each pool
by consistency and recency rather than lifetime spend.

Usage: python3 build_call_sheet.py <exports_dir> <out_csv>
"""
import sys, os
import pandas as pd
import numpy as np

CUT = pd.Timestamp("2026-08-10")          # last date present in the exports
LIVE_DAYS, LAPSED_DAYS = 45, 150
TIERS = {"Four Classes": 550, "Eight Classes": 1000,
         "Twelve Classes": 1400, "Ten Classes per Month": 1200}


def build(exports_dir):
    sales = pd.read_excel(os.path.join(exports_dir, "Sales_Report.xlsx"), sheet_name="Sales")
    members = pd.read_excel(os.path.join(exports_dir, "Members_Report.xlsx"),
                            sheet_name="Detail Report", dtype={"Phone": str})

    sales["Sale Date"] = pd.to_datetime(sales["Sale Date"])
    sales["Item Total"] = pd.to_numeric(sales["Item Total"], errors="coerce").fillna(0)
    sales["cid"] = pd.to_numeric(sales["Client ID"], errors="coerce")

    charges = sales[sales["Item name"].isin(TIERS)]
    df = charges.groupby("cid").agg(
        client=("Client", "last"),
        last=("Sale Date", "max"),
        months=("Sale Date", "size"),
        tier=("Item name", "last"),
    ).reset_index()

    df["days_since"] = (CUT - df["last"]).dt.days
    df["rate"] = df["tier"].map(TIERS)
    df["lifetime"] = df["cid"].map(sales.groupby("cid")["Item Total"].sum()).fillna(0)

    members["cid"] = pd.to_numeric(members["BarcodeID"], errors="coerce")
    df = df.merge(members[["cid", "Phone", "Email Address", "Status"]], on="cid", how="left")
    df["Phone"] = df["Phone"].astype(str).str.replace(r"\.0$", "", regex=True)

    # staff and comped accounts are not prospects
    df = df[~df["Email Address"].fillna("").str.contains("kaizenpilates.ae", case=False)]
    df = df[df["lifetime"] >= 500]

    # six months for the tiers where the ticket justifies the ask, three for the entry tier
    df["term"] = np.where(df["rate"] >= 1000, 6, 3)
    df["ticket"] = (df["rate"] * df["term"]).astype(int)
    df["bonus"] = df["rate"].astype(int)
    df["pool"] = np.select(
        [df["days_since"] <= LIVE_DAYS, df["days_since"] <= LAPSED_DAYS],
        ["1. LIVE - lock now", "2. SUMMER-LAPSED - restart + lock"],
        "3. Cold",
    )

    live = df[df["pool"].str[0].isin(["1", "2"])].copy()
    # consistency and recency lead; ticket size breaks ties
    live["score"] = (
        (live["ticket"] / live["ticket"].max()) * 0.40
        + (live["months"].clip(upper=15) / 15) * 0.35
        + (1 - live["days_since"].clip(upper=LAPSED_DAYS) / LAPSED_DAYS) * 0.25
    )
    live = live.sort_values(["pool", "score"], ascending=[True, False]).reset_index(drop=True)
    live["call_order"] = live.groupby("pool").cumcount() + 1

    out = live[["pool", "call_order", "client", "tier", "rate", "months", "days_since",
                "term", "ticket", "bonus", "lifetime", "Status", "Phone", "Email Address"]]
    out.columns = ["pool", "call_order", "client_name", "current_tier", "monthly_rate_aed",
                   "months_paid_to_date", "days_since_last_charge", "offer_term_months",
                   "ticket_aed", "bonus_month_value_aed", "lifetime_spend_aed",
                   "member_status", "phone", "email"]
    return out


if __name__ == "__main__":
    sheet = build(sys.argv[1])
    sheet.to_csv(sys.argv[2], index=False)
    print(sheet.groupby("pool").agg(people=("client_name", "size"),
                                    ticket_pool=("ticket_aed", "sum")).to_string())
