import streamlit as st
import pandas as pd
import re
import io
import pdfplumber
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

# Muss die allererste Streamlit-Anweisung sein
st.set_page_config(page_title="Kiosk Sortiment Analyse", page_icon="🏟️", layout="wide")


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
# 2. HILFSFUNKTIONEN
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
    """
    Wandelt einen DataFrame (aus Excel oder korrigiertem PDF)
    in das interne Ergebnis-Format um.
    """
    try:
        df = pd.DataFrame(df).fillna("").astype(str)
        h_row = -1
        k_map = {}          # Spalten-Index → Kiosk-Label
        n_col = None        # Produktname-Spalte
        p_col = None        # Preis-Spalte
        w_col = None        # Warengruppe-Spalte
        is_unit_col = False

        # ── Kopfzeile finden ──────────────────────────────────────
        for i, row in df.iterrows():
            row_vals = [normalize(x).upper() for x in row]
            kiosk_count = sum(1 for v in row_vals if re.search(r"KIOSK.*\d", v))
            if kiosk_count >= 2:
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

        kiosk_cols = set(k_map.keys())

        # ── Fallback-Erkennung, falls Header-Labels fehlen ────────
        if n_col is None or n_col in kiosk_cols:
            candidate_cols = [c for c in range(df.shape[1]) if c not in kiosk_cols]
            if candidate_cols:
                text_counts = {}
                for c in candidate_cols:
                    text_counts[c] = sum(
                        1 for v in df.iloc[h_row + 1:][c]
                        if normalize(str(v)) and normalize(str(v)).upper()
                        not in ["X", "", "0", "0.0"]
                    )
                n_col = max(candidate_cols, key=lambda c: text_counts.get(c, 0))

        if p_col is None or p_col in kiosk_cols:
            candidate_cols = [c for c in range(df.shape[1]) if c not in kiosk_cols and c != n_col]
            price_pattern = re.compile(r"\d+[.,]\d{2}")
            best_p, best_score = (candidate_cols[0] if candidate_cols else n_col + 1), 0
            for c in candidate_cols:
                score = sum(
                    1 for v in df.iloc[h_row + 1:][c]
                    if price_pattern.search(str(v))
                )
                if score > best_score:
                    best_score = score
                    best_p = c
            p_col = best_p

        if w_col is None or w_col in kiosk_cols:
            fallback = [c for c in range(df.shape[1])
                        if c not in kiosk_cols and c != n_col and c != p_col]
            w_col = fallback[0] if fallback else n_col

        # ── Datenzeilen verarbeiten ───────────────────────────────
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

            if sum(1 for v in [normalize(str(x)).upper() for x in row]
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
                item = {
                    "cat": current_cat,
                    "name": name_val,
                    "price": price_val,
                    "ks": marked_ks,
                }
                if sec == "FOOD":
                    food.append(item)
                else:
                    drinks.append(item)

        return {
            "food": food,
            "drinks": drinks,
            "ks": sorted(list(k_map.values())),
            "name": filename,
            "_cols": {"n": n_col, "p": p_col, "w": w_col, "kiosk": sorted(kiosk_cols)},
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
def
