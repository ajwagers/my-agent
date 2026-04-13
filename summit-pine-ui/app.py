"""
Summit Pine Business Dashboard — Streamlit app at port 8504.

Tabs: Dashboard | Inventory | Batches | Orders | Costs | FAQ
Connects directly to postgres-brain via psycopg2 (sp_app or brain role).
Receipt scanning via agent-core OCR pipeline.
"""

import base64
from datetime import date, datetime, timedelta
import json
import os

import pandas as pd
import plotly.express as px
import requests as _requests
import streamlit as st

import db

_AGENT_URL = os.getenv("AGENT_URL", "http://agent-core:8000")
_AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")

st.set_page_config(
    page_title="Summit Pine",
    page_icon="🌲",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("🌲 Summit Pine Business Dashboard")

# Test DB connection
try:
    db.scalar("SELECT 1")
except Exception as e:
    st.error(f"Database connection failed: {e}")
    st.stop()

tab_dash, tab_inv, tab_batch, tab_orders, tab_costs, tab_hours, tab_sales, tab_recipes, tab_promos, tab_faq, tab_todos = st.tabs([
    "📊 Dashboard", "📦 Inventory", "🧼 Batches", "🛒 Orders",
    "💰 Costs", "⏱️ Hours", "📈 Sales", "📋 Recipes", "🎟️ Promos", "❓ FAQ", "✅ Tasks"
])


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

with tab_dash:
    today = date.today()
    month_start = today.replace(day=1)

    col1, col2, col3, col4 = st.columns(4)

    # Revenue this month
    rev = db.scalar(
        "SELECT COALESCE(SUM(total),0) FROM orders "
        "WHERE created_at >= %s AND status NOT IN ('refunded','cancelled')",
        (month_start,),
    )
    col1.metric("Revenue (this month)", f"${float(rev or 0):,.2f}")

    # Orders this month
    ord_count = db.scalar(
        "SELECT COUNT(*) FROM orders WHERE created_at >= %s", (month_start,)
    )
    col2.metric("Orders (this month)", int(ord_count or 0))

    # Pending orders
    pending = db.scalar(
        "SELECT COUNT(*) FROM orders WHERE status IN ('pending','processing')"
    )
    col3.metric("Pending / Processing", int(pending or 0))

    # Low stock count
    low = db.scalar(
        "SELECT COUNT(*) FROM inventory_items "
        "WHERE reorder_threshold IS NOT NULL AND quantity_on_hand <= reorder_threshold"
    )
    col4.metric("Low Stock Items", int(low or 0), delta_color="inverse")

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Low Stock Alerts")
        low_rows = db.query(
            """SELECT sku, name, quantity_on_hand, reorder_threshold, unit,
                      is_critical, supplier
               FROM inventory_items
               WHERE reorder_threshold IS NOT NULL
                 AND quantity_on_hand <= reorder_threshold
               ORDER BY is_critical DESC, quantity_on_hand ASC
               LIMIT 10"""
        )
        if low_rows:
            df = pd.DataFrame(low_rows)
            df["critical"] = df["is_critical"].map({True: "🚨", False: ""})
            st.dataframe(
                df[["critical", "sku", "name", "quantity_on_hand", "reorder_threshold", "unit", "supplier"]],
                use_container_width=True, hide_index=True,
            )
        else:
            st.success("All stock levels are healthy.")

    with col_right:
        st.subheader("Recent Orders")
        recent = db.query(
            """SELECT order_number, customer_name, channel, status,
                      total, created_at::date AS date
               FROM orders ORDER BY created_at DESC LIMIT 10"""
        )
        if recent:
            st.dataframe(pd.DataFrame(recent), use_container_width=True, hide_index=True)
        else:
            st.info("No orders yet.")

    st.divider()
    st.subheader("Batches Ready / Curing Soon")
    batches = db.query(
        """SELECT batch_number, product_type, batch_date, quantity_produced,
                  cure_complete_date, status
           FROM production_batches
           WHERE status IN ('curing','cured')
           ORDER BY cure_complete_date ASC LIMIT 10"""
    )
    if batches:
        df = pd.DataFrame(batches)
        df["cure_complete_date"] = pd.to_datetime(df["cure_complete_date"]).dt.date
        df["days_left"] = (df["cure_complete_date"] - today).apply(
            lambda x: x.days if pd.notna(x) else None
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No active batches.")


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY
# ─────────────────────────────────────────────────────────────────────────────

with tab_inv:
    st.subheader("Inventory Items")

    cat_filter = st.selectbox(
        "Category", ["All", "raw_material", "finished_good", "packaging", "equipment"],
        key="inv_cat",
    )

    sql = "SELECT sku, name, category, unit, quantity_on_hand, reorder_threshold, unit_cost, supplier, is_critical FROM inventory_items"
    params = ()
    if cat_filter != "All":
        sql += " WHERE category = %s"
        params = (cat_filter,)
    sql += " ORDER BY category, name"

    rows = db.query(sql, params or None)
    if rows:
        df = pd.DataFrame(rows)
        df["low"] = df.apply(
            lambda r: "🚨" if r["reorder_threshold"] and r["quantity_on_hand"] <= r["reorder_threshold"] else "",
            axis=1,
        )
        df["critical"] = df["is_critical"].map({True: "⚠️", False: ""})
        st.dataframe(
            df[["low", "critical", "sku", "name", "category", "quantity_on_hand", "unit",
                "reorder_threshold", "unit_cost", "supplier"]],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No inventory items found.")

    st.divider()
    st.subheader("Update Quantity")
    with st.form("update_qty"):
        sku_in = st.text_input("SKU")
        qty_in = st.number_input("New quantity on hand", min_value=0.0, step=0.1)
        if st.form_submit_button("Update"):
            if sku_in.strip():
                try:
                    db.execute(
                        "UPDATE inventory_items SET quantity_on_hand=%s, updated_at=NOW() WHERE sku=%s",
                        (qty_in, sku_in.strip()),
                    )
                    st.success(f"Updated {sku_in} → {qty_in}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    st.divider()
    st.subheader("📋 Quick Ingest")
    st.caption("Paste a free-form list of items, quantities, or purchases. The agent will parse and ingest it.")
    with st.form("quick_ingest"):
        ingest_text = st.text_area(
            "Paste notes here",
            height=160,
            placeholder=(
                "Inventory update:\n  Coconut oil: 5kg\n  Shea butter: 2kg\n\n"
                "OR expenses:\n  Bought lye from Brambleberry $18.50\n  Ordered packaging from ULINE $42.00"
            ),
        )
        ingest_type = st.radio(
            "Type", ["Auto-detect", "Inventory update", "Expense log"], horizontal=True
        )
        if st.form_submit_button("Ingest with Agent"):
            if ingest_text.strip():
                if ingest_type == "Inventory update":
                    prompt = (
                        "Please update inventory quantities from this list using sp_inventory → bulk_update:\n"
                        + ingest_text
                    )
                elif ingest_type == "Expense log":
                    prompt = (
                        "Please log these expenses using sp_costs → log_expense for each item:\n"
                        + ingest_text
                    )
                else:
                    prompt = (
                        "Please review this list and take the appropriate action "
                        "(update inventory quantities or log expenses):\n" + ingest_text
                    )
                with st.spinner("Ingesting..."):
                    try:
                        resp = _requests.post(
                            f"{_AGENT_URL}/chat",
                            json={"message": prompt, "user_id": "summit-pine-ui", "channel": "cli"},
                            headers={"X-Api-Key": _AGENT_API_KEY},
                            timeout=120,
                        )
                        resp.raise_for_status()
                        st.success("Done.")
                        st.markdown(resp.json().get("response", ""))
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ingest failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# BATCHES
# ─────────────────────────────────────────────────────────────────────────────

with tab_batch:
    st.subheader("Production Batches")

    status_filter = st.selectbox(
        "Status", ["All", "curing", "cured", "in_stock", "depleted"], key="batch_status"
    )

    sql = """SELECT batch_number, product_type, batch_date, quantity_produced,
                    cure_complete_date, status, ph_test_result, qc_notes
             FROM production_batches"""
    params = ()
    if status_filter != "All":
        sql += " WHERE status = %s"
        params = (status_filter,)
    sql += " ORDER BY batch_date DESC LIMIT 50"

    rows = db.query(sql, params or None)
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No batches found.")

    st.divider()
    col_new, col_upd = st.columns(2)

    with col_new:
        st.subheader("Record New Batch")
        with st.form("new_batch"):
            bn = st.text_input("Batch number", placeholder="SP-2026-001")
            pt = st.selectbox("Product type", ["shampoo_bar", "conditioner_bar"])
            bd = st.date_input("Batch date", value=date.today())
            qty = st.number_input("Quantity produced", min_value=1, value=12, step=1)
            qc = st.text_area("QC notes", height=80)
            if st.form_submit_button("Record Batch"):
                if bn.strip():
                    cure_days = 42 if pt == "shampoo_bar" else 1
                    cure_date = bd + timedelta(days=cure_days)
                    try:
                        db.execute(
                            """INSERT INTO production_batches
                               (batch_number, product_type, batch_date, quantity_produced,
                                cure_complete_date, qc_notes)
                               VALUES (%s,%s,%s,%s,%s,%s)""",
                            (bn.strip(), pt, bd, qty, cure_date, qc.strip() or None),
                        )
                        st.success(f"Batch {bn} recorded. Cure by {cure_date}.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    with col_upd:
        st.subheader("Update Batch Status")
        with st.form("upd_batch"):
            bn_u = st.text_input("Batch number")
            st_u = st.selectbox("New status", ["curing", "cured", "in_stock", "depleted"])
            ph = st.number_input("pH test result (optional)", min_value=0.0, max_value=14.0, value=0.0, step=0.1)
            qc_u = st.text_area("QC notes", height=80)
            if st.form_submit_button("Update Status"):
                if bn_u.strip():
                    sets = ["status=%s", "updated_at=NOW()"]
                    vals: list = [st_u]
                    if ph > 0:
                        sets.append("ph_test_result=%s")
                        vals.append(ph)
                    if qc_u.strip():
                        sets.append("qc_notes=%s")
                        vals.append(qc_u.strip())
                    vals.append(bn_u.strip())
                    try:
                        db.execute(
                            f"UPDATE production_batches SET {', '.join(sets)} WHERE batch_number=%s",
                            vals,
                        )
                        st.success(f"Batch {bn_u} updated to {st_u}.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# ORDERS
# ─────────────────────────────────────────────────────────────────────────────

with tab_orders:
    st.subheader("Orders")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        order_status = st.selectbox(
            "Status filter",
            ["All", "pending", "processing", "shipped", "delivered",
             "refund_requested", "refunded", "cancelled"],
            key="order_status",
        )
    with col_f2:
        order_channel = st.selectbox(
            "Channel", ["All", "shopify", "local_market", "subscription"],
            key="order_channel",
        )

    sql = """SELECT order_number, customer_name, customer_email, channel, status,
                    subtotal, shipping, tax, total, tracking_number,
                    guarantee_expires_at, created_at::date AS date
             FROM orders"""
    conditions, params = [], []
    if order_status != "All":
        conditions.append("status = %s")
        params.append(order_status)
    if order_channel != "All":
        conditions.append("channel = %s")
        params.append(order_channel)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at DESC LIMIT 100"

    rows = db.query(sql, params or None)
    if rows:
        df = pd.DataFrame(rows)
        selected = st.dataframe(
            df, use_container_width=True, hide_index=True, on_select="rerun",
            selection_mode="single-row",
        )

        sel_rows = selected.get("selection", {}).get("rows", [])
        if sel_rows:
            row = rows[sel_rows[0]]
            st.markdown(f"### Order {row['order_number']}")
            st.json(row)
    else:
        st.info("No orders match the filter.")

    st.divider()
    st.subheader("Update Order Status")
    with st.form("upd_order"):
        on = st.text_input("Order number")
        ns = st.selectbox("New status",
                          ["pending", "processing", "shipped", "delivered",
                           "refund_requested", "refunded", "cancelled"])
        tn = st.text_input("Tracking number (optional)")
        if st.form_submit_button("Update"):
            if on.strip():
                sets = ["status=%s", "updated_at=NOW()"]
                vals: list = [ns]
                if tn.strip():
                    sets.append("tracking_number=%s")
                    vals.append(tn.strip())
                vals.append(on.strip())
                try:
                    db.execute(
                        f"UPDATE orders SET {', '.join(sets)} WHERE order_number=%s",
                        vals,
                    )
                    st.success(f"Order {on} → {ns}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# COSTS
# ─────────────────────────────────────────────────────────────────────────────

with tab_costs:
    st.subheader("Cost Tracking & P&L")

    today = date.today()
    col_yr, col_mo = st.columns(2)
    with col_yr:
        sel_year = st.number_input("Year", min_value=2024, max_value=2030, value=today.year, step=1)
    with col_mo:
        sel_month = st.number_input("Month", min_value=1, max_value=12, value=today.month, step=1)

    # P&L summary
    rev = db.scalar(
        """SELECT COALESCE(SUM(total),0) FROM orders
           WHERE EXTRACT(year FROM created_at)=%s
             AND EXTRACT(month FROM created_at)=%s
             AND status NOT IN ('refunded','cancelled')""",
        (sel_year, sel_month),
    )
    exp = db.scalar(
        """SELECT COALESCE(SUM(amount),0) FROM sp_expenses
           WHERE EXTRACT(year FROM expense_date)=%s
             AND EXTRACT(month FROM expense_date)=%s""",
        (sel_year, sel_month),
    )
    rev_f = float(rev or 0)
    exp_f = float(exp or 0)
    profit = rev_f - exp_f
    margin = round(profit / rev_f * 100, 1) if rev_f else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Revenue", f"${rev_f:,.2f}")
    m2.metric("Expenses", f"${exp_f:,.2f}")
    m3.metric("Gross Profit", f"${profit:,.2f}")
    m4.metric("Margin", f"{margin}%")

    st.divider()

    col_exp, col_chart = st.columns([3, 2])

    with col_exp:
        st.subheader("Expenses This Period")
        exp_rows = db.query(
            """SELECT expense_date, category, description, supplier, amount, receipt_ref
               FROM sp_expenses
               WHERE EXTRACT(year FROM expense_date)=%s
                 AND EXTRACT(month FROM expense_date)=%s
               ORDER BY expense_date DESC""",
            (sel_year, sel_month),
        )
        if exp_rows:
            st.dataframe(pd.DataFrame(exp_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No expenses recorded for this period.")

    with col_chart:
        if exp_rows:
            st.subheader("By Category")
            cat_totals = (
                pd.DataFrame(exp_rows)
                .groupby("category")["amount"]
                .sum()
                .reset_index()
            )
            fig = px.pie(cat_totals, names="category", values="amount",
                         color_discrete_sequence=px.colors.qualitative.Set2)
            fig.update_layout(showlegend=True, height=280, margin=dict(t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Log an Expense")
    with st.form("log_exp"):
        e_desc = st.text_input("Description", placeholder="Bulk Apothecary coconut oil order")
        c1, c2 = st.columns(2)
        with c1:
            e_amt = st.number_input("Amount ($)", min_value=0.01, step=0.01)
            e_cat = st.selectbox("Category",
                                 ["ingredients", "packaging", "equipment", "shipping", "marketing", "other"])
            e_date = st.date_input("Date", value=date.today())
        with c2:
            e_supplier = st.text_input("Supplier")
            e_sku = st.text_input("SKU (optional)")
            e_ref = st.text_input("Receipt / Invoice ref")
        e_notes = st.text_input("Notes")
        if st.form_submit_button("Log Expense"):
            if e_desc.strip():
                try:
                    db.execute(
                        """INSERT INTO sp_expenses
                           (expense_date, category, description, supplier, amount, sku, receipt_ref, notes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (e_date, e_cat, e_desc.strip(),
                         e_supplier.strip() or None,
                         e_amt,
                         e_sku.strip() or None,
                         e_ref.strip() or None,
                         e_notes.strip() or None),
                    )
                    st.success("Expense logged.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    st.divider()
    st.subheader("📷 Scan Receipt")
    st.caption("Upload a receipt photo or PDF — the agent will extract and log the expenses automatically.")
    uploaded = st.file_uploader(
        "Receipt image or PDF",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tiff", "pdf"],
        key="receipt_upload",
    )
    receipt_note = st.text_input(
        "Optional note (e.g. 'Bulk Apothecary order for shampoo batch')",
        key="receipt_note",
    )
    if st.button("Scan & Log Receipt", disabled=uploaded is None):
        if uploaded:
            caption = receipt_note.strip() or "Please extract and log the expenses from this receipt."
            with st.spinner("Scanning receipt..."):
                try:
                    if uploaded.type == "application/pdf":
                        import pypdf as _pypdf
                        import io as _io
                        reader = _pypdf.PdfReader(_io.BytesIO(uploaded.read()))
                        pdf_text = "\n".join(p.extract_text() or "" for p in reader.pages).strip()
                        if not pdf_text:
                            st.error("PDF appears to be image-only. Try uploading a scanned image instead.")
                        else:
                            fname = uploaded.name or "receipt.pdf"
                            full_message = f"[File: {fname}]\n{pdf_text[:8000]}\n\n{caption}"
                            resp = _requests.post(
                                f"{_AGENT_URL}/chat",
                                json={"message": full_message, "user_id": "summit-pine-ui", "channel": "cli"},
                                headers={"X-Api-Key": _AGENT_API_KEY},
                                timeout=120,
                            )
                            resp.raise_for_status()
                            st.success("Receipt processed.")
                            st.markdown(resp.json().get("response", ""))
                            st.rerun()
                    else:
                        img_b64 = base64.b64encode(uploaded.read()).decode()
                        resp = _requests.post(
                            f"{_AGENT_URL}/chat",
                            json={
                                "message": caption,
                                "image_base64": img_b64,
                                "user_id": "summit-pine-ui",
                                "channel": "cli",
                            },
                            headers={"X-Api-Key": _AGENT_API_KEY},
                            timeout=120,
                        )
                        resp.raise_for_status()
                        st.success("Receipt processed.")
                        st.markdown(resp.json().get("response", ""))
                        st.rerun()
                except Exception as e:
                    st.error(f"Failed to process receipt: {e}")

    st.divider()
    st.subheader("Batch COGS Calculator")
    with st.form("batch_cogs"):
        cogs_batch = st.text_input("Batch number")
        if st.form_submit_button("Calculate COGS"):
            if cogs_batch.strip():
                batch = db.query(
                    "SELECT ingredients_used, quantity_produced, product_type FROM production_batches WHERE batch_number=%s",
                    (cogs_batch.strip(),),
                )
                if not batch:
                    st.error("Batch not found.")
                else:
                    b = batch[0]
                    ingredients = b["ingredients_used"] or []
                    if isinstance(ingredients, str):
                        ingredients = json.loads(ingredients)
                    total_cost = 0.0
                    lines = []
                    for ing in ingredients:
                        sku = ing.get("sku")
                        amount_g = float(ing.get("amount_g", 0))
                        item = db.query(
                            "SELECT name, unit_cost FROM inventory_items WHERE sku=%s", (sku,)
                        )
                        if item and item[0]["unit_cost"]:
                            lc = amount_g * float(item[0]["unit_cost"])
                            total_cost += lc
                            lines.append({"sku": sku, "name": item[0]["name"],
                                          "amount_g": amount_g, "line_cost": round(lc, 4)})
                        else:
                            lines.append({"sku": sku, "amount_g": amount_g, "line_cost": "n/a"})
                    qty = b["quantity_produced"]
                    st.markdown(f"**Total ingredient cost:** ${total_cost:.2f}  |  "
                                f"**Per unit:** ${total_cost/qty:.4f}" if qty else f"**Total:** ${total_cost:.2f}")
                    st.dataframe(pd.DataFrame(lines), hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# HOURS
# ─────────────────────────────────────────────────────────────────────────────

with tab_hours:
    st.subheader("Labour Hours")

    today = date.today()
    col_yr, col_mo = st.columns(2)
    with col_yr:
        h_sel_year = st.number_input("Year", min_value=2024, max_value=2030, value=today.year, step=1, key="h_year")
    with col_mo:
        h_sel_month = st.number_input("Month", min_value=1, max_value=12, value=today.month, step=1, key="h_month")

    summary = db.query(
        """SELECT person,
                  SUM(hours) AS total_hours,
                  SUM(hours * COALESCE(hourly_rate, 0)) AS labour_cost,
                  COUNT(*) AS sessions
           FROM sp_time_logs
           WHERE EXTRACT(year FROM log_date)=%s AND EXTRACT(month FROM log_date)=%s
           GROUP BY person""",
        (h_sel_year, h_sel_month),
    )
    if summary:
        cols = st.columns(len(summary))
        for i, row in enumerate(summary):
            cols[i].metric(
                f"{row['person'].title()} — hours",
                f"{float(row['total_hours']):.1f}h",
                f"${float(row['labour_cost']):.2f} labour",
            )

    hour_rows = db.query(
        """SELECT log_date, person, hours, start_time, end_time,
                  task_description, hourly_rate, notes
           FROM sp_time_logs
           WHERE EXTRACT(year FROM log_date)=%s AND EXTRACT(month FROM log_date)=%s
           ORDER BY log_date DESC""",
        (h_sel_year, h_sel_month),
    )
    if hour_rows:
        df_h = pd.DataFrame(hour_rows)
        st.dataframe(df_h, use_container_width=True, hide_index=True)
    else:
        st.info("No hours logged for this period.")

    st.divider()
    st.subheader("Log Hours")
    with st.form("log_hours_form"):
        col1, col2 = st.columns(2)
        with col1:
            lh_date = st.date_input("Date", value=date.today(), key="lh_date")
            lh_hours = st.number_input("Hours", min_value=0.1, step=0.25, value=1.0, key="lh_hours")
            lh_rate = st.number_input("Hourly rate ($ — leave 0 for uncosted)", min_value=0.0, step=0.5, value=0.0, key="lh_rate")
        with col2:
            lh_person = st.selectbox("Person", ["owner", "helper", "contractor"], key="lh_person")
            lh_task = st.text_input("Task description", placeholder="production run, packaging, admin", key="lh_task")
            lh_notes = st.text_input("Notes", key="lh_notes")
        if st.form_submit_button("Log Hours"):
            try:
                db.execute(
                    """INSERT INTO sp_time_logs (log_date, person, hours, task_description, hourly_rate, notes)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (lh_date, lh_person, lh_hours,
                     lh_task.strip() or None,
                     lh_rate if lh_rate > 0 else None,
                     lh_notes.strip() or None),
                )
                st.success(f"Logged {lh_hours}h for {lh_person}.")
                st.rerun()
            except Exception as e:
                st.error(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SALES ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

with tab_sales:
    st.subheader("Sales Analytics")

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sales_start = st.date_input("From", value=date.today().replace(day=1), key="sales_start")
    with col_s2:
        sales_end = st.date_input("To", value=date.today(), key="sales_end")

    # KPI row
    rev_total = db.scalar(
        """SELECT COALESCE(SUM(total),0) FROM orders
           WHERE created_at::date >= %s AND created_at::date <= %s
             AND status NOT IN ('refunded','cancelled')""",
        (sales_start, sales_end),
    )
    ord_total = db.scalar(
        """SELECT COUNT(*) FROM orders
           WHERE created_at::date >= %s AND created_at::date <= %s
             AND status NOT IN ('refunded','cancelled')""",
        (sales_start, sales_end),
    )
    aov = db.scalar(
        """SELECT ROUND(AVG(total)::numeric, 2) FROM orders
           WHERE created_at::date >= %s AND created_at::date <= %s
             AND status NOT IN ('refunded','cancelled')""",
        (sales_start, sales_end),
    )
    refunds = db.scalar(
        """SELECT COALESCE(SUM(total),0) FROM orders
           WHERE created_at::date >= %s AND created_at::date <= %s
             AND status = 'refunded'""",
        (sales_start, sales_end),
    )
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Revenue", f"${float(rev_total or 0):,.2f}")
    k2.metric("Orders", int(ord_total or 0))
    k3.metric("Avg Order Value", f"${float(aov or 0):,.2f}")
    k4.metric("Refunds", f"${float(refunds or 0):,.2f}", delta_color="inverse")

    st.divider()

    # Weekly revenue chart
    weekly = db.query(
        """SELECT DATE_TRUNC('week', created_at)::date AS week,
                  channel,
                  COUNT(*) AS orders,
                  COALESCE(SUM(total),0) AS revenue
           FROM orders
           WHERE created_at::date >= %s AND created_at::date <= %s
             AND status NOT IN ('refunded','cancelled')
           GROUP BY 1,2 ORDER BY 1""",
        (sales_start, sales_end),
    )
    if weekly:
        df_w = pd.DataFrame(weekly)
        df_w["revenue"] = df_w["revenue"].astype(float)
        fig_w = px.bar(
            df_w, x="week", y="revenue", color="channel", barmode="stack",
            title="Weekly Revenue by Channel",
            labels={"week": "Week", "revenue": "Revenue ($)"},
        )
        fig_w.update_layout(height=300, margin=dict(t=40, b=20))
        st.plotly_chart(fig_w, use_container_width=True)

    st.divider()
    col_left, col_right = st.columns(2)

    # Top products from JSONB items array
    with col_left:
        st.subheader("Top Products")
        top_products = db.query(
            """SELECT item->>'sku' AS sku,
                      item->>'name' AS name,
                      SUM((item->>'qty')::int) AS units_sold,
                      ROUND(SUM((item->>'qty')::float * (item->>'unit_price')::float)::numeric, 2) AS revenue
               FROM orders,
                    jsonb_array_elements(items) AS item
               WHERE created_at::date >= %s AND created_at::date <= %s
                 AND status NOT IN ('refunded','cancelled')
                 AND items != '[]'::jsonb
               GROUP BY 1,2 ORDER BY revenue DESC""",
            (sales_start, sales_end),
        )
        if top_products:
            df_p = pd.DataFrame(top_products)
            st.dataframe(df_p, use_container_width=True, hide_index=True)
        else:
            st.info("No product line items found. Orders may not have itemised data.")

    with col_right:
        st.subheader("By Channel")
        channel_split = db.query(
            """SELECT channel, COUNT(*) AS orders,
                      ROUND(COALESCE(SUM(total),0)::numeric, 2) AS revenue
               FROM orders
               WHERE created_at::date >= %s AND created_at::date <= %s
                 AND status NOT IN ('refunded','cancelled')
               GROUP BY channel""",
            (sales_start, sales_end),
        )
        if channel_split:
            df_c = pd.DataFrame(channel_split)
            df_c["revenue"] = df_c["revenue"].astype(float)
            fig_c = px.pie(df_c, names="channel", values="revenue", title="Revenue by Channel",
                           color_discrete_sequence=px.colors.qualitative.Set2)
            fig_c.update_layout(height=260, margin=dict(t=40, b=10))
            st.plotly_chart(fig_c, use_container_width=True)
        else:
            st.info("No orders in this period.")


# ─────────────────────────────────────────────────────────────────────────────
# RECIPES
# ─────────────────────────────────────────────────────────────────────────────

with tab_recipes:
    st.subheader("Production Recipes")

    tag_filter = st.text_input("Filter by tag", placeholder="shampoo, conditioner, lavender...", key="recipe_tag")

    recipe_sql = "SELECT id, name, servings, prep_time_minutes, tags FROM recipes"
    recipe_params = []
    if tag_filter.strip():
        recipe_sql += " WHERE %s = ANY(tags)"
        recipe_params.append(tag_filter.strip().lower())
    recipe_sql += " ORDER BY name"

    recipe_rows = db.query(recipe_sql, recipe_params or None)
    if recipe_rows:
        for row in recipe_rows:
            tags_list = row.get("tags") or []
            label = f"{row['name']}  ({', '.join(tags_list) if tags_list else 'no tags'})"
            with st.expander(label):
                full = db.query("SELECT * FROM recipes WHERE id=%s", (row["id"],))
                if full:
                    r = full[0]
                    rc1, rc2 = st.columns(2)
                    rc1.write(f"**Yield:** {r['servings']} bars" if r["servings"] else "**Yield:** —")
                    rc2.write(f"**Prep time:** {r['prep_time_minutes']} min" if r["prep_time_minutes"] else "**Prep time:** —")
                    ings = r.get("ingredients") or []
                    if isinstance(ings, str):
                        import json as _json
                        try:
                            ings = _json.loads(ings)
                        except Exception:
                            ings = []
                    if ings:
                        st.markdown("**Ingredients:**")
                        for ing in ings:
                            n = ing.get("name", "")
                            amt = ing.get("amount", "")
                            unit = ing.get("unit", "")
                            st.write(f"- {n}: {amt} {unit}".strip())
                    if r.get("instructions"):
                        st.markdown("**Instructions:**")
                        st.markdown(r["instructions"])
    else:
        st.info("No recipes found. Add one below.")

    st.divider()
    st.subheader("Add Recipe")
    with st.form("add_recipe_form"):
        r_name = st.text_input("Recipe name", placeholder="Lavender Shampoo Bar")
        col_r1, col_r2 = st.columns(2)
        r_servings = col_r1.number_input("Yield (bars)", min_value=1, value=12, step=1)
        r_prep = col_r2.number_input("Prep time (min)", min_value=0, step=5)
        r_tags_raw = st.text_input("Tags (comma-separated)", placeholder="shampoo, lavender")
        r_ingredients_raw = st.text_area(
            "Ingredients — one per line: name, amount, unit",
            placeholder="coconut oil, 200, g\nshea butter, 100, g\nlye, 65, g",
            height=100,
        )
        r_instructions = st.text_area("Instructions", height=150)
        if st.form_submit_button("Save Recipe"):
            if r_name.strip():
                ings = []
                for line in r_ingredients_raw.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2 and parts[0]:
                        ings.append({
                            "name": parts[0],
                            "amount": parts[1],
                            "unit": parts[2] if len(parts) > 2 else "",
                        })
                tags_list = [t.strip().lower() for t in r_tags_raw.split(",") if t.strip()]
                import json as _json
                try:
                    db.execute(
                        """INSERT INTO recipes (name, ingredients, instructions, servings, prep_time_minutes, tags)
                           VALUES (%s, %s::jsonb, %s, %s, %s, %s)""",
                        (r_name.strip(), _json.dumps(ings),
                         r_instructions.strip() or None,
                         r_servings, r_prep,
                         tags_list or None),
                    )
                    st.success(f"Recipe '{r_name}' saved.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# PROMOTIONS
# ─────────────────────────────────────────────────────────────────────────────

with tab_promos:
    st.subheader("Promotions & Discount Codes")

    show_all_promos = st.checkbox("Show inactive / expired promotions", value=False)
    if show_all_promos:
        promo_rows = db.query("SELECT * FROM sp_promotions ORDER BY start_date DESC")
    else:
        promo_rows = db.query(
            """SELECT * FROM sp_promotions
               WHERE is_active=TRUE
                 AND start_date <= CURRENT_DATE
                 AND (end_date IS NULL OR end_date >= CURRENT_DATE)
               ORDER BY start_date DESC"""
        )

    if promo_rows:
        df_pr = pd.DataFrame(promo_rows)
        display_cols = ["name", "code", "discount_type", "discount_value",
                        "applies_to", "start_date", "end_date", "uses_count", "max_uses", "is_active"]
        st.dataframe(
            df_pr[[c for c in display_cols if c in df_pr.columns]],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No active promotions.")

    st.divider()
    st.subheader("Create Promotion")
    with st.form("create_promo_form"):
        pc1, pc2 = st.columns(2)
        p_name = pc1.text_input("Promotion name", placeholder="Spring Sale 2026")
        p_code = pc2.text_input("Discount code (optional)", placeholder="SPRING20")
        p_type = pc1.selectbox("Discount type", ["percent", "fixed_amount", "free_shipping", "buy_x_get_y"])
        p_val = pc2.number_input("Discount value (% or $)", min_value=0.0, step=0.5)
        p_start = pc1.date_input("Start date", value=date.today(), key="promo_start")
        p_end = pc2.date_input("End date (optional)", value=None, key="promo_end")
        p_applies = pc1.selectbox("Applies to", ["all", "sku_list", "category"])
        p_min = pc2.number_input("Min order amount ($ — 0 = none)", min_value=0.0, step=1.0)
        p_max_uses = st.number_input("Max uses (0 = unlimited)", min_value=0, step=1)
        p_notes = st.text_input("Notes")
        if st.form_submit_button("Create"):
            if p_name.strip() and p_val > 0:
                try:
                    db.execute(
                        """INSERT INTO sp_promotions
                           (name, code, discount_type, discount_value, applies_to,
                            min_order_amount, max_uses, start_date, end_date, notes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (p_name.strip(),
                         p_code.strip() or None,
                         p_type, p_val, p_applies,
                         p_min if p_min > 0 else None,
                         p_max_uses if p_max_uses > 0 else None,
                         p_start,
                         p_end if p_end and p_end > p_start else None,
                         p_notes.strip() or None),
                    )
                    st.success(f"Promotion '{p_name}' created.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
            else:
                st.warning("Name and a positive discount value are required.")

    st.divider()
    st.subheader("Deactivate Promotion")
    with st.form("deactivate_promo_form"):
        p_deact_id = st.text_input("Promotion ID (UUID from table above)")
        if st.form_submit_button("Deactivate"):
            if p_deact_id.strip():
                try:
                    db.execute(
                        "UPDATE sp_promotions SET is_active=FALSE, updated_at=NOW() WHERE id=%s",
                        (p_deact_id.strip(),),
                    )
                    st.success("Promotion deactivated.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# FAQ
# ─────────────────────────────────────────────────────────────────────────────

with tab_faq:
    st.subheader("Customer Support FAQ")

    faq_cat = st.selectbox(
        "Category",
        ["All", "usage", "ingredients", "guarantee", "ordering", "shipping", "production", "science"],
        key="faq_cat",
    )
    search_q = st.text_input("Search", placeholder="Type to filter questions...")

    sql = "SELECT id, question, answer, category, guardrail, usage_count FROM faq_entries"
    params = []
    conditions = []
    if faq_cat != "All":
        conditions.append("category = %s")
        params.append(faq_cat)
    if search_q.strip():
        conditions.append("(question ILIKE %s OR answer ILIKE %s)")
        params += [f"%{search_q}%", f"%{search_q}%"]
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY category, usage_count DESC"

    faq_rows = db.query(sql, params or None)
    if faq_rows:
        for row in faq_rows:
            with st.expander(f"[{row['category']}] {row['question']}"):
                st.markdown(row["answer"])
                col_g, col_u = st.columns([3, 1])
                if row.get("guardrail"):
                    col_g.caption(f"Guardrail: {row['guardrail']}")
                col_u.caption(f"Used {row['usage_count']} times")
    else:
        st.info("No FAQ entries found.")

    st.divider()
    st.subheader("Add FAQ Entry")
    with st.form("add_faq"):
        fq = st.text_input("Question")
        fa = st.text_area("Answer", height=120)
        fc = st.selectbox("Category",
                          ["usage", "ingredients", "guarantee", "ordering",
                           "shipping", "production", "science"])
        fg = st.text_input("Guardrail (optional)", placeholder="no_medical_advice")
        if st.form_submit_button("Add Entry"):
            if fq.strip() and fa.strip():
                try:
                    db.execute(
                        """INSERT INTO faq_entries (question, answer, category, guardrail)
                           VALUES (%s,%s,%s,%s)""",
                        (fq.strip(), fa.strip(), fc, fg.strip() or None),
                    )
                    st.success("FAQ entry added.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TASKS & SHOPPING LIST
# ─────────────────────────────────────────────────────────────────────────────

CATEGORY_ICON = {"task": "📋", "purchase": "🛒", "errand": "📍"}
PRIORITY_COLOR = {"high": "🔴", "medium": "🟡", "low": "🟢"}

with tab_todos:
    st.subheader("Tasks & Shopping List")

    # ── Add new item ──────────────────────────────────────────────────────────
    with st.expander("➕ Add item", expanded=False):
        with st.form("add_todo"):
            col_text, col_cat, col_pri = st.columns([4, 2, 2])
            new_text = col_text.text_input("Item")
            new_cat  = col_cat.selectbox("Category", ["task", "purchase", "errand"])
            new_pri  = col_pri.selectbox("Priority", ["medium", "high", "low"])
            if st.form_submit_button("Add"):
                if new_text.strip():
                    try:
                        db.execute(
                            "INSERT INTO todos (text, category, priority, source) VALUES (%s,%s,%s,'dashboard')",
                            (new_text.strip(), new_cat, new_pri),
                        )
                        st.success(f"Added: {new_text.strip()}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    # ── Pending items ─────────────────────────────────────────────────────────
    try:
        pending_rows = db.query(
            """SELECT id, text, category, priority, created_at
               FROM todos WHERE status='pending'
               ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at"""
        )
    except Exception as e:
        st.error(f"Could not load todos: {e}")
        pending_rows = []

    if not pending_rows:
        st.info("No pending items — you're all caught up!")
    else:
        st.caption(f"{len(pending_rows)} pending item(s)")
        for row in pending_rows:
            todo_id = row["id"]
            text    = row["text"]
            cat     = row["category"]
            pri     = row["priority"]
            created = row["created_at"]
            icon = CATEGORY_ICON.get(cat, "📋")
            dot  = PRIORITY_COLOR.get(pri, "⚪")
            col_check, col_label, col_del = st.columns([1, 10, 1])
            if col_check.button("✅", key=f"done_{todo_id}", help="Mark done"):
                try:
                    db.execute(
                        "UPDATE todos SET status='done', completed_at=NOW() WHERE id=%s",
                        (todo_id,),
                    )
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
            col_label.markdown(
                f"{dot} {icon} **{text}** "
                f"<small style='color:grey'>#{todo_id} · {cat} · added {created.strftime('%b %d')}</small>",
                unsafe_allow_html=True,
            )
            if col_del.button("🗑️", key=f"del_{todo_id}", help="Delete"):
                try:
                    db.execute("DELETE FROM todos WHERE id=%s", (todo_id,))
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    # ── Completed items (collapsible) ─────────────────────────────────────────
    with st.expander("View completed items"):
        try:
            done_rows = db.query(
                """SELECT id, text, category, completed_at
                   FROM todos WHERE status='done'
                   ORDER BY completed_at DESC LIMIT 50"""
            )
        except Exception:
            done_rows = []
        if not done_rows:
            st.caption("No completed items yet.")
        else:
            for row in done_rows:
                icon      = CATEGORY_ICON.get(row["category"], "📋")
                completed = row["completed_at"]
                when      = completed.strftime("%b %d") if completed else "?"
                st.markdown(
                    f"~~{icon} {row['text']}~~ <small style='color:grey'>done {when}</small>",
                    unsafe_allow_html=True,
                )
