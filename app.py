import sqlite3
from contextlib import closing
import pandas as pd
import streamlit as st
from datetime import datetime

DB_PATH = "inventory.db"

# ------------------------ DB SETUP ------------------------
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS SpareParts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_number TEXT UNIQUE NOT NULL,
            description TEXT,
            machine_type TEXT,
            supplier TEXT,
            min_qty INTEGER DEFAULT 0,
            current_qty INTEGER DEFAULT 0,
            location TEXT
        );
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS Transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            user TEXT,
            action TEXT CHECK(action IN ('IN','OUT')) NOT NULL,
            quantity INTEGER NOT NULL,
            remarks TEXT,
            FOREIGN KEY (part_id) REFERENCES SpareParts(id) ON DELETE CASCADE
        );
        """)
        conn.commit()

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

# ------------------------ SIMPLE LOGIN ------------------------
def login():
    st.sidebar.header("Login")
    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")
    if st.sidebar.button("Login"):
        if username == "employee" and password == "smt123":
            st.session_state.logged_in = True
            st.session_state.username = username
            st.sidebar.success("Login successful ✅")
        else:
            st.sidebar.error("Invalid username or password")

def ensure_session_state():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "username" not in st.session_state:
        st.session_state.username = ""

# ------------------------ DATA ACCESS ------------------------
def fetch_parts(conn, search=""):
    q = """
    SELECT id, part_number, description, machine_type, supplier, min_qty, current_qty, location
    FROM SpareParts
    WHERE part_number LIKE ? OR description LIKE ? OR COALESCE(machine_type,'') LIKE ? OR COALESCE(supplier,'') LIKE ?
    ORDER BY part_number;
    """
    like = f"%{search}%"
    return pd.read_sql_query(q, conn, params=(like, like, like, like))

def fetch_transactions(conn, limit=200):
    q = """
    SELECT t.id, s.part_number, t.ts, t.user, t.action, t.quantity, t.remarks
    FROM Transactions t
    JOIN SpareParts s ON s.id = t.part_id
    ORDER BY datetime(t.ts) DESC
    LIMIT ?;
    """
    return pd.read_sql_query(q, conn, params=(limit,))

def insert_part(conn, row):
    with conn:
        conn.execute("""
            INSERT INTO SpareParts (part_number, description, machine_type, supplier, min_qty, current_qty, location)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """, (row["part_number"], row.get("description",""), row.get("machine_type",""), row.get("supplier",""),
              int(row.get("min_qty",0)), int(row.get("current_qty",0)), row.get("location","")))

def update_part(conn, pid, row):
    with conn:
        conn.execute("""
            UPDATE SpareParts SET
                part_number=?, description=?, machine_type=?, supplier=?, min_qty=?, current_qty=?, location=?
            WHERE id=?;
        """, (row["part_number"], row.get("description",""), row.get("machine_type",""), row.get("supplier",""),
              int(row.get("min_qty",0)), int(row.get("current_qty",0)), row.get("location",""), pid))

def delete_part(conn, pid):
    with conn:
        conn.execute("DELETE FROM SpareParts WHERE id=?;", (pid,))

def adjust_stock(conn, part_id, qty, action, user, remarks=""):
    qty = int(qty)
    if action not in ("IN", "OUT"):
        raise ValueError("Action must be IN or OUT")
    with conn:
        cur = conn.execute("SELECT current_qty FROM SpareParts WHERE id=?", (part_id,)).fetchone()
        if not cur:
            raise ValueError("Part not found")
        current = cur[0] or 0
        new_qty = current + qty if action == "IN" else current - qty
        if new_qty < 0:
            raise ValueError("Cannot go below zero stock")
        conn.execute("UPDATE SpareParts SET current_qty=? WHERE id=?", (new_qty, part_id))
        conn.execute("""
            INSERT INTO Transactions (part_id, ts, user, action, quantity, remarks)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (part_id, datetime.now().isoformat(timespec="seconds"), user, action, qty, remarks))

# ------------------------ UI PAGES ------------------------
def dashboard(conn):
    st.subheader("📊 Dashboard")
    df = fetch_parts(conn, search=st.text_input("Search parts"))
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Total Parts", len(df))
    with c2:
        st.metric("Total Qty", int(df["current_qty"].sum()))
    with c3:
        st.metric("Low Stock Items", int((df["current_qty"] < df["min_qty"]).sum()))

    st.write("### Low Stock Alerts")
    low_df = df[df["current_qty"] < df["min_qty"]]
    if low_df.empty:
        st.success("All good. No items below minimum quantity.")
    else:
        st.warning(f"{len(low_df)} items below minimum:")
        st.dataframe(low_df[["part_number","description","machine_type","current_qty","min_qty","location"]])

    st.write("### Recent Transactions")
    tx = fetch_transactions(conn, limit=100)
    st.dataframe(tx)

def parts_page(conn):
    st.subheader("📦 Spare Parts")
    tab1, tab2 = st.tabs(["List & Edit", "Add New"])
    with tab1:
        search = st.text_input("Search")
        df = fetch_parts(conn, search=search)
        st.dataframe(df)
        if not df.empty:
            st.markdown("#### Edit / Delete")
            selected = st.selectbox("Select part_number to edit", df["part_number"])
            row = df[df["part_number"] == selected].iloc[0]
            with st.form("edit_part"):
                part_number = st.text_input("Part Number", value=row.part_number)
                description = st.text_input("Description", value=row.description or "")
                machine_type = st.text_input("Machine Type", value=row.machine_type or "")
                supplier = st.text_input("Supplier", value=row.supplier or "")
                min_qty = st.number_input("Minimum Qty", min_value=0, step=1, value=int(row.min_qty or 0))
                current_qty = st.number_input("Current Qty", min_value=0, step=1, value=int(row.current_qty or 0))
                location = st.text_input("Location", value=row.location or "")
                submitted = st.form_submit_button("Save Changes")
                if submitted:
                    update_part(conn, int(row.id), {
                        "part_number": part_number.strip(),
                        "description": description.strip(),
                        "machine_type": machine_type.strip(),
                        "supplier": supplier.strip(),
                        "min_qty": int(min_qty),
                        "current_qty": int(current_qty),
                        "location": location.strip(),
                    })
                    st.success("Saved.")
                    st.experimental_rerun()
            if st.button("Delete Selected Part"):
                delete_part(conn, int(row.id))
                st.success("Deleted.")
                st.experimental_rerun()

    with tab2:
        st.markdown("#### Add New Part")
        with st.form("add_part"):
            part_number = st.text_input("Part Number *")
            description = st.text_input("Description")
            machine_type = st.text_input("Machine Type")
            supplier = st.text_input("Supplier")
            min_qty = st.number_input("Minimum Qty", min_value=0, step=1, value=0)
            current_qty = st.number_input("Initial Qty", min_value=0, step=1, value=0)
            location = st.text_input("Location (Rack/Shelf/Box)")
            submitted = st.form_submit_button("Add Part")
            if submitted and part_number.strip():
                insert_part(conn, {
                    "part_number": part_number.strip(),
                    "description": description.strip(),
                    "machine_type": machine_type.strip(),
                    "supplier": supplier.strip(),
                    "min_qty": int(min_qty),
                    "current_qty": int(current_qty),
                    "location": location.strip(),
                })
                st.success("Part added.")
                st.experimental_rerun()

def io_page(conn):
    st.subheader("🔁 Issue / Receive")
    df = fetch_parts(conn)
    if df.empty:
        st.info("No parts yet. Add some parts first.")
        return
    part = st.selectbox("Select Part", df.apply(lambda r: f"{r.part_number} — {r.description}", axis=1))
    selected_row = df[df.apply(lambda r: f"{r.part_number} — {r.description}", axis=1) == part].iloc[0]
    st.caption(f"Current Qty: {selected_row.current_qty} | Min Qty: {selected_row.min_qty} | Location: {selected_row.location}")
    action = st.radio("Action", ["OUT","IN"], horizontal=True, index=0)
    qty = st.number_input("Quantity", min_value=1, step=1, value=1)
    remarks = st.text_input("Remarks", placeholder="WO#123, Line 3, etc.")
    if st.button("Submit"):
        adjust_stock(conn, int(selected_row.id), int(qty), action, st.session_state.username, remarks)
        st.success(f"Recorded {action} x{qty}.")
        st.experimental_rerun()

def reports_page(conn):
    st.subheader("🧾 Reports")
    df = fetch_parts(conn)
    st.write("### Low Stock")
    low_df = df[df["current_qty"] < df["min_qty"]]
    st.dataframe(low_df[["part_number","description","machine_type","current_qty","min_qty","location"]])
    st.write("### Transactions")
    tx = fetch_transactions(conn, limit=200)
    st.dataframe(tx)

    st.download_button("Export Parts CSV", df.to_csv(index=False).encode("utf-8"), file_name="spare_parts.csv")
    st.download_button("Export Transactions CSV", tx.to_csv(index=False).encode("utf-8"), file_name="transactions.csv")

# ------------------------ MAIN ------------------------
def main():
    st.set_page_config(page_title="Spare Inventory", page_icon="🧰", layout="wide")
    ensure_session_state()
    init_db()

    if not st.session_state.logged_in:
        login()
        st.stop()

    st.title("🔧 Spare Inventory Manager")
    conn = get_conn()
    page = st.sidebar.radio("Navigate", ["Dashboard", "Spare Parts", "Issue/Receive", "Reports"], index=0)
    if page == "Dashboard":
        dashboard(conn)
    elif page == "Spare Parts":
        parts_page(conn)
    elif page == "Issue/Receive":
        io_page(conn)
    elif page == "Reports":
        reports_page(conn)

if __name__ == "__main__":
    main()