import streamlit as st
import pandas as pd
import re
import io
import pdfplumber
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

# ─────────────────────────────────────────────
# 1. PASSWORT-SCHUTZ
# ─────────────────────────────────────────────
def check_password():
    if "password_correct" not in st.session_state:
        st.markdown("<h1 style='text-align: center;'>🔐 Interner Login</h1>", unsafe_allow_html=True)
        pwd = st.text_input("Passwort eingeben:", type="password", key="init_pw")
        if pwd == "makeitso!":
            st.session_state["password_correct"] = True
            st.rerun()
        elif pwd != "":
            st.error("😕 Passwort falsch.")
        return False
    return True


# ─────────────────────────────────────────────
# 2. KERN-HILFSFUNKTIONEN
# ─────────────────────────────────────────────
def normalize(s):
    if not s or pd.isna(s):
        return ""
    return " ".join(str(s).replace("\n", " ").split()).strip()


def format_k_list(ks):
    if not ks:
        return "-"
    nums = sorted(list(set([
        int(re.search(r"\d+", str(k)).group())
        for k in ks if re.search(r"\d+", str(k))
    ])))
    return "K" + "-".join([str(n).zfill(2) for n in nums])


# ─────────────────────────────────────────────
# 3. PARSING
# ─────────────────────────────────────────────
def parse_df_to_result(df, filename):
    try:
        df = pd.DataFrame(df).fillna("").astype(str)
        h_row, k_map = -1, {}
        w_col, n_col, p_col = 1, 0, 2
        is_unit_col = False

        for i, row in df.iterrows():
            row_vals = [normalize(x).upper() for x in row]
            if sum(1 for v in row_vals if re.search(r"KIOSK.*\d", v)) >= 2:
                h_row = i
                for c, v in enumerate(row_vals):
                    if re.search(r"KIOSK.*\d", v):
                        num = re.search(r"\d+", v)
                        k_map[c] = "K" + (num.group().zfill(2) if num else str(c))
                    if any(x in v for x in ["PRODUKT", "ARTIKEL", "BEZEICHNUNG"]):
                        n_col = c
                    if any(x in v for x in ["PREIS", "€", "VK"]):
                        p_col = c
                    if any(x in v for x in ["WARENGRUPPE", "GRUPPE"]):
                        w_col = c
                        is_unit_col = False
                    if "EINHEIT" in v:
                        w_col = c
                        is_unit_col = True
                break

        if h_row == -1:
            return None

        food, drinks = [], []
        sec, current_cat = "FOOD", "ALLGEMEIN"

        for i, row in df.iloc[h_row + 1:].iterrows():
            name_val  = normalize(row.iloc[n_col] if n_col < len(row) else "")
            price_val = normalize(row.iloc[p_col] if p_col < len(row) else "")
            cat_val   = normalize(row.iloc[w_col] if w_col < len(row) else "")
            clean_name = name_val.upper()

            if clean_name in ["GETRÄNKE", "DRINKS"]:
                sec = "DRINKS"; current_cat = "GETRÄNKE"; continue
            if clean_name in ["FOOD", "SPEISEN"]:
                sec = "FOOD"; current_cat = "FOOD"; continue

            if sum(1 for v in [normalize(x).upper() for x in row]
                   if re.search(r"KIOSK.*\d", v)) >= 2:
                continue

            marked_ks = [
                k_map[c] for c in k_map
                if c < len(row) and str(row.iloc[c]).strip().upper() == "X"
            ]
            is_product = price_val != "" or len(marked_ks) > 0

            if not is_product and name_val:
                current_cat = name_val
                continue
            if is_product and not is_unit_col and cat_val:
                current_cat = cat_val
            if is_product and name_val:
                item = {"cat": current_cat, "name": name_val,
                        "price": price_val, "ks": marked_ks}
                if sec == "FOOD":
                    food.append(item)
                else:
                    drinks.append(item)

        return {
            "food": food,
            "drinks": drinks,
            "ks": sorted(list(k_map.values())),
            "name": filename,
        }
    except Exception:
        return None


def extract_data(uploaded_file):
    try:
        df = pd.read_excel(uploaded_file, header=None)
        return parse_df_to_result(df, uploaded_file.name)
    except Exception:
        return None


# ─────────────────────────────────────────────
# 4. PDF-IMPORT
# ─────────────────────────────────────────────
def extract_tables_from_pdf(uploaded_pdf):
    try:
        uploaded_pdf.seek(0)
        all_rows = []
        with pdfplumber.open(io.BytesIO(uploaded_pdf.read())) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        cleaned = [
                            str(c).strip().replace("\n", " ") if c is not None else ""
                            for c in row
                        ]
                        all_rows.append(cleaned)

        if not all_rows:
            return None, (
                "Keine Tabellen im PDF gefunden. "
                "Bitte prüfen, ob das PDF Tabellen mit Kiosk-Zuordnungen enthält."
            )

        max_cols = max(len(r) for r in all_rows)
        rows_padded = [r + [""] * (max_cols - len(r)) for r in all_rows]
        df = pd.DataFrame(rows_padded)
        return df, None

    except Exception as e:
        return None, f"Fehler beim Lesen der PDF: {e}"


def detect_pdf_issues(df):
    issues = []
    h_row, k_cols, n_col = -1, [], 0
    for i, row in df.iterrows():
        row_vals = [normalize(str(x)).upper() for x in row]
        kiosk_cols = [c for c, v in enumerate(row_vals) if re.search(r"KIOSK.*\d", v)]
        if len(kiosk_cols) >= 2:
            h_row = i
            k_cols = kiosk_cols
            for c, v in enumerate(row_vals):
                if any(x in v for x in ["PRODUKT", "ARTIKEL", "BEZEICHNUNG"]):
                    n_col = c
                    break
            break

    if h_row == -1:
        issues.append((
            "error",
            "Keine Kopfzeile mit Kiosk-Spalten gefunden (z. B. 'Kiosk 1', 'Kiosk 2'). "
            "Bitte die Tabelle manuell prüfen und ggf. korrigieren."
        ))
        return issues

    repeated = [
        i + 1 for i, row in df.iloc[h_row + 1:].iterrows()
        if sum(1 for v in [normalize(str(x)).upper() for x in row]
               if re.search(r"KIOSK.*\d", v)) >= 2
    ]
    if repeated:
        issues.append((
            "warning",
            f"Wiederholte Kopfzeilen erkannt (Zeilen {repeated}) – "
            "wahrscheinlich Seitenumbrüche aus dem PDF. "
            "Diese werden beim Parsen automatisch ignoriert."
        ))

    long_cells = []
    for i, row in df.iloc[h_row + 1:].iterrows():
        for j, val in enumerate(row):
            if len(str(val)) > 150:
                long_cells.append(f"Zeile {i + 1}, Spalte {j + 1}")
                break
    if long_cells:
        issues.append((
            "warning",
            f"Sehr langer Text in {len(long_cells)} Zeile(n) erkannt "
            f"({', '.join(long_cells[:3])}{'...' if len(long_cells) > 3 else ''}) – "
            "möglicherweise zusammengeführte Zellen aus dem PDF."
        ))

    no_kiosk = []
    p_col_guess = min(2, df.shape[1] - 1)
    for i, row in df.iloc[h_row + 1:].iterrows():
        name_val = normalize(str(row.iloc[n_col]) if n_col < len(row) else "")
        if not name_val or name_val.upper() in ["FOOD", "SPEISEN", "GETRÄNKE", "DRINKS"]:
            continue
        if sum(1 for v in [normalize(str(x)).upper() for x in row]
               if re.search(r"KIOSK.*\d", v)) >= 2:
            continue
        marked = [c for c in k_cols if c < len(row) and str(row.iloc[c]).strip().upper() == "X"]
        price_val = normalize(str(row.iloc[p_col_guess]) if p_col_guess < len(row) else "")
        if not marked and price_val:
            no_kiosk.append(name_val)

    if no_kiosk:
        preview = ", ".join(f'„{p}"' for p in no_kiosk[:3])
