import streamlit as st
import pandas as pd
import re
import io
import pdfplumber
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

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
            st.error("Passwort falsch.")
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
# 3. PDF-EXTRAKTION  (horizontales Merging!)
# ─────────────────────────────────────────────
def extract_tables_from_pdf(uploaded_pdf):
    """
    Liest Tabellen aus einem PDF.

    Breite Sortiment-Tabellen werden von pdfplumber in mehrere
    nebeneinanderliegende Sub-Tabellen aufgeteilt (z.B. Produktinfo |
    Kiosk 01-16 | Kiosk 17-32). Haben alle Sub-Tabellen auf einer Seite
    DIESELBE Zeilenanzahl, werden sie horizontal zusammengefuehrt.
    """
    try:
        uploaded_pdf.seek(0)
        result_rows = None

        with pdfplumber.open(io.BytesIO(uploaded_pdf.read())) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    continue

                row_counts = [len(t) for t in tables]
                # Alle Sub-Tabellen gleich lang: horizontales Merging
                if len(tables) > 1 and len(set(row_counts)) == 1:
                    n_rows = row_counts[0]
                    merged = []
                    for r in range(n_rows):
                        row = []
                        for t in tables:
                            row.extend([
                                str(c).strip().replace("\n", " ") if c is not None else ""
                                for c in t[r]
                            ])
                        merged.append(row)
                    page_rows = merged
                else:
                    # Unterschiedliche Zeilenzahlen: vertikales Stapeln
                    page_rows = []
                    for t in tables:
                        for row in t:
                            page_rows.append([
                                str(c).strip().replace("\n", " ") if c is not None else ""
                                for c in row
                            ])

                if result_rows is None:
                    result_rows = page_rows
                else:
                    result_rows.extend(page_rows)

        if not result_rows:
            return None, "Keine Tabellen im PDF gefunden."

        max_cols = max(len(r) for r in result_rows)
        df = pd.DataFrame([r + [""] * (max_cols - len(r)) for r in result_rows])
        return df, None

    except Exception as e:
        return None, f"Fehler beim Lesen der PDF: {e}"


# ─────────────────────────────────────────────
# 4. ANOMALIE-ERKENNUNG
# ─────────────────────────────────────────────
def _get_header_info(df):
    """Gibt (h_row, k_cols, n_col, p_col) zurueck."""
    h_row, k_cols, n_col, p_col = -1, [], 1, 5
    for i, row in df.iterrows():
        row_vals = [normalize(str(x)).upper() for x in row]
        kiosk_hits = [
            c for c, v in enumerate(row_vals)
            if re.search(r"KIOSK.*\d", v.replace(" ", ""))
        ]
        if len(kiosk_hits) >= 2:
            h_row = i
            k_cols = kiosk_hits
            for c, v in enumerate(row_vals):
                if any(x in v for x in ["PRODUKT", "ARTIKEL", "BEZEICHNUNG"]):
                    n_col = c
                if any(x in v for x in ["PREIS", "VK PREIS", "BRUTTO"]):
                    p_col = c
            break
    return h_row, k_cols, n_col, p_col


def detect_row_anomalies(df):
    """
    Erkennt zwei Klassen von Anomalien:

    'category_with_x':
        Kategorie-Zeile (kein Preis) hat trotzdem ein X in einer Kiosk-Spalte.

    'product_empty_kiosk':
        Produkt-Zeile (hat Preis) hat in mind. einer Kiosk-Spalte einen
        leeren Wert (weder X noch -).

    Rueckgabe: {row_index: {"type", "name", "x_cols", "empty_cols", "kiosk_labels"}}
    """
    h_row, k_cols, n_col, p_col = _get_header_info(df)
    if h_row == -1:
        return {}

    header = df.iloc[h_row]
    k_label = {c: normalize(str(header.iloc[c])) for c in k_cols}

    anomalies = {}
    for i, row in df.iloc[h_row + 1:].iterrows():
        row_vals_up = [normalize(str(x)).upper() for x in row]
        if sum(1 for v in row_vals_up
               if re.search(r"KIOSK.*\d", v.replace(" ", ""))) >= 2:
            continue

        name  = normalize(str(row.iloc[n_col]) if n_col < len(row) else "")
        price = normalize(str(row.iloc[p_col]) if p_col < len(row) else "")
        if not name:
            continue

        kiosk_vals = {
            c: str(row.iloc[c]).strip() if c < len(row) else ""
            for c in k_cols
        }

        # Kategorie-Zeile mit X
        if not price:
            x_cols = [c for c, v in kiosk_vals.items() if v.upper() == "X"]
            if x_cols:
                anomalies[i] = {
                    "type":         "category_with_x",
                    "name":         name,
                    "x_cols":       x_cols,
                    "empty_cols":   [],
                    "kiosk_labels": [k_label.get(c, str(c)) for c in x_cols],
                }

        # Produkt mit leeren Kiosk-Feldern
        if price:
            empty_cols = [c for c, v in kiosk_vals.items()
                          if v not in ("X", "x", "-")]
            if empty_cols:
                anomalies[i] = {
                    "type":         "product_empty_kiosk",
                    "name":         name,
                    "x_cols":       [],
                    "empty_cols":   empty_cols,
                    "kiosk_labels": [k_label.get(c, str(c)) for c in empty_cols],
                }

    return anomalies


def detect_pdf_issues(df):
    """Gibt Liste von (typ, nachricht)-Tupeln zurueck."""
    issues = []
    h_row, k_cols, n_col, _ = _get_header_info(df)

    if h_row == -1:
        issues.append(("error", "Keine Kopfzeile mit Kiosk-Spalten gefunden."))
        return issues

    # Wiederholte Kopfzeilen
    repeated = [
        i + 1 for i, row in df.iloc[h_row + 1:].iterrows()
        if sum(1 for v in [normalize(str(x)).upper() for x in row]
               if re.search(r"KIOSK.*\d", v.replace(" ", ""))) >= 2
    ]
    if repeated:
        issues.append((
            "warning",
            f"Wiederholte Kopfzeilen in {len(repeated)} Zeile(n) – "
            "werden automatisch ignoriert.",
        ))

    anomalies = detect_row_anomalies(df)
    cat_x  = [a for a in anomalies.values() if a["type"] == "category_with_x"]
    prod_e = [a for a in anomalies.values() if a["type"] == "product_empty_kiosk"]

    if cat_x:
        details = "; ".join(
            '"' + a["name"] + '" (bei ' + ", ".join(a["kiosk_labels"]) + ")"
            for a in cat_x[:3]
        )
        issues.append((
            "warning",
            "Kategorie-Zeile(n) mit irrtaeglichem X: " + details
            + (" ..." if len(cat_x) > 3 else ""),
        ))

    if prod_e:
        details = "; ".join(
            '"' + a["name"] + '" (' + str(len(a["empty_cols"])) + " leere Felder)"
            for a in prod_e[:3]
        )
        issues.append((
            "info",
            "Produkt(e) mit leeren Kiosk-Feldern (weder X noch -): " + details
            + (" ..." if len(prod_e) > 3 else ""),
        ))

    if not issues:
        issues.append(("success", "Keine Auffaelligkeiten – Daten sehen gut aus."))

    return issues


def style_raw_df(df, anomalies):
    """
    Gibt einen pandas Styler zurueck mit eingefaerbten Anomalie-Zellen:
      - Kategorie + X : gesamte Zeile gelb, X-Zellen orange
      - Produkt + Leer: leere Kiosk-Zellen rot
    """
    col_names = list(df.columns)
    style_map = pd.DataFrame("", index=df.index, columns=df.columns)

    for row_idx, info in anomalies.items():
        if row_idx not in df.index:
            continue
        if info["type"] == "category_with_x":
            style_map.loc[row_idx, :] = "background-color: #fff3cd;"
            for c in info["x_cols"]:
                if c < len(col_names):
                    style_map.loc[row_idx, col_names[c]] = (
                        "background-color: #fd7e14; color: white; font-weight: bold;"
                    )
        elif info["type"] == "product_empty_kiosk":
            for c in info["empty_cols"]:
                if c < len(col_names):
                    style_map.loc[row_idx, col_names[c]] = (
                        "background-color: #f8d7da; color: #721c24;"
                    )

    return df.style.apply(lambda _: style_map, axis=None)


# ─────────────────────────────────────────────
# 5. PARSING
# ─────────────────────────────────────────────
def parse_df_to_result(df, filename):
    try:
        df = pd.DataFrame(df).fillna("").astype(str)
        h_row, k_map = -1, {}
        n_col = p_col = w_col = None
        is_unit_col = False

        for i, row in df.iterrows():
            row_vals = [normalize(x).upper() for x in row]
            kiosk_hits = [
                c for c, v in enumerate(row_vals)
                if re.search(r"KIOSK.*\d", v.replace(" ", ""))
            ]
            if len(kiosk_hits) >= 2:
                h_row = i
                for c, v in enumerate(row_vals):
                    if re.search(r"KIOSK.*\d", v.replace(" ", "")):
                        num = re.search(r"\d+", v)
                        k_map[c] = "K" + (num.group().zfill(2) if num else str(c))
                    if any(x in v for x in ["PRODUKT", "ARTIKEL", "BEZEICHNUNG"]):
                        n_col = c
                    if any(x in v for x in ["PREIS", "VK PREIS", "BRUTTO"]):
                        p_col = c
                    if any(x in v for x in ["WARENGRUPPE", "GRUPPE"]):
                        w_col = c; is_unit_col = False
                    if "EINHEIT" in v:
                        w_col = c; is_unit_col = True
                break

        if h_row == -1:
            return None

        kiosk_cols = set(k_map.keys())

        # Fallback n_col
        if n_col is None or n_col in kiosk_cols:
            cands = [c for c in range(df.shape[1]) if c not in kiosk_cols]
            if cands:
                text_cnt = {
                    c: sum(1 for v in df.iloc[h_row + 1:][c]
                           if normalize(str(v)) not in ("X", "x", "", "0", "-"))
                    for c in cands
                }
                n_col = max(cands, key=lambda c: text_cnt.get(c, 0))

        # Fallback p_col
        if p_col is None or p_col in kiosk_cols:
            cands = [c for c in range(df.shape[1])
                     if c not in kiosk_cols and c != n_col]
            price_re = re.compile(r"\d+[.,]\d{2}")
            best_p, best_s = (cands[0] if cands else (n_col or 0) + 1), 0
            for c in cands:
                s = sum(1 for v in df.iloc[h_row + 1:][c] if price_re.search(str(v)))
                if s > best_s:
                    best_s, best_p = s, c
            p_col = best_p

        if w_col is None or w_col in kiosk_cols:
            fb = [c for c in range(df.shape[1])
                  if c not in kiosk_cols and c != n_col and c != p_col]
            w_col = fb[0] if fb else n_col

        food, drinks = [], []
        sec, current_cat = "FOOD", "ALLGEMEIN"

        for i, row in df.iloc[h_row + 1:].iterrows():
            name_val  = normalize(row.iloc[n_col] if n_col < len(row) else "")
            price_val = normalize(row.iloc[p_col] if p_col < len(row) else "")
            cat_val   = normalize(row.iloc[w_col] if w_col < len(row) else "")
            clean_name = name_val.upper()

            if clean_name in ["GETRAENKE", "GETRANKE", "GETRÄNKE", "DRINKS"]:
                sec = "DRINKS"; current_cat = "GETRÄNKE"; continue
            if clean_name in ["FOOD", "SPEISEN"]:
                sec = "FOOD"; current_cat = "FOOD"; continue

            if sum(1 for v in [normalize(str(x)).upper() for x in row]
                   if re.search(r"KIOSK.*\d", v.replace(" ", ""))) >= 2:
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
                    "cat":   current_cat,
                    "name":  name_val,
                    "price": price_val,
                    "ks":    marked_ks,
                }
                (food if sec == "FOOD" else drinks).append(item)

        return {
            "food":   food,
            "drinks": drinks,
            "ks":     sorted(list(k_map.values())),
            "name":   filename,
            "_cols":  {"n": n_col, "p": p_col, "w": w_col,
                       "kiosk": sorted(kiosk_cols)},
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
# 6. EXPORTS
# ─────────────────────────────────────────────
def create_excel_export(data):
    output = io.BytesIO()
    wb = Workbook()
    ws = wb.active
    ws.title = "Sortiment"
    ws.append(["Bereich", "Warengruppe", "Produkt", "Preis"])
    row_i = 2
    for label, items in [("FOOD", data["food"]), ("GETRÄNKE", data["drinks"])]:
        if not items:
            continue
        s_row = row_i
        groups = {}
        for k in data["ks"]:
            assort = tuple([(i["cat"], i["name"], i["price"])
                            for i in items if k in i["ks"]])
            if assort not in groups:
                groups[assort] = []
            groups[assort].append(k)
        for assort, ks in sorted(groups.items(), key=lambda x: x[1][0]):
            ws.merge_cells(f"B{row_i}:D{row_i}")
            ws.cell(row=row_i, column=2,
                    value="Kioske: " + format_k_list(ks)).font = Font(bold=True)
            row_i += 1
            for cat, n, p in assort:
                ws.cell(row=row_i, column=2, value=cat)
                ws.cell(row=row_i, column=3, value=n)
                ws.cell(row=row_i, column=4, value=p)
                row_i += 1
            row_i += 1
        ws.merge_cells(f"A{s_row}:A{row_i - 2}")
        ws.cell(row=s_row, column=1, value=label).font = Font(bold=True)
        row_i += 1
    wb.save(output)
    return output.getvalue()


def create_kiosk_diff_report(old, new):
    output = io.BytesIO()
    wb = Workbook()
    ws = wb.active
    ws.title = "Grafiker-Anweisung"
    ws.append(["Kiosk(e)", "Bereich", "Zugehoerigkeit Alt", "Zugehoerigkeit Neu",
               "Status", "Details der Aenderungen"])

    def get_kiosk_map(data):
        m = {}
        for skey in ["food", "drinks"]:
            assorts = {}
            for k in data["ks"]:
                asort = tuple(sorted([(i["name"], i["price"])
                                       for i in data[skey] if k in i["ks"]]))
                if asort not in assorts:
                    assorts[asort] = []
                assorts[asort].append(k)
            for asort, ks in assorts.items():
                name = format_k_list(ks)
                for k in ks:
                    m[(k, skey)] = (asort, name)
        return m

    old_m = get_kiosk_map(old)
    new_m = get_kiosk_map(new)
    report_groups = {}

    for skey, label in [("food", "FOOD"), ("drinks", "GETRÄNKE")]:
        old_groups = sorted(list(set([
            old_m.get((k, skey), (None, "Nicht vorhanden"))[1]
            for k in old["ks"]
        ])))
        for og_name in old_groups:
            if og_name == "Nicht vorhanden":
                continue
            ks_in_og = [k for k in old["ks"] if old_m.get((k, skey))[1] == og_name]
            sub_splits = {}
            for k in ks_in_og:
                n_asort, ng_name = new_m.get((k, skey), (tuple(), "Nicht vorhanden"))
                o_asort = old_m.get((k, skey))[0]
                if n_asort != o_asort:
                    if (n_asort, ng_name) not in sub_splits:
                        sub_splits[(n_asort, ng_name)] = []
                    sub_splits[(n_asort, ng_name)].append(k)
            for (n_asort, ng_name), ks_list in sub_splits.items():
                o_asort = old_m.get((ks_list[0], skey))[0]
                o_d, n_d = dict(o_asort), dict(n_asort)
                changes, status = [], ["Inhalt geaendert"]
                if og_name != ng_name:
                    status.append("Gruppe gewechselt / Split")
                for a in sorted(set(n_d) - set(o_d)):
                    changes.append("[+] Neu: " + a + " (" + n_d[a] + ")")
                for r in sorted(set(o_d) - set(n_d)):
                    changes.append("[-] Weg: " + r)
                for p in sorted(n for n in set(o_d) & set(n_d) if o_d[n] != n_d[n]):
                    changes.append("[!] Preis: " + p + " (" + o_d[p] + " -> " + n_d[p] + ")")
                group_key = (
                    format_k_list(ks_list), label, og_name, ng_name,
                    ", ".join(status), "\n".join(changes),
                )
                report_groups[group_key] = True

    row_i = 2
    for (k_ids, area, o_grp, n_grp, stat, det) in report_groups.keys():
        ws.cell(row=row_i, column=1, value=k_ids)
        ws.cell(row=row_i, column=2, value=area)
        ws.cell(row=row_i, column=3, value=o_grp)
        ws.cell(row=row_i, column=4, value=n_grp)
        ws.cell(row=row_i, column=5, value=stat)
        ws.cell(row=row_i, column=6, value=det).alignment = Alignment(wrap_text=True)
        row_i += 1

    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["C"].width = 30
    ws.column_dimensions["D"].width = 30
    ws.column_dimensions["F"].width = 70
    wb.save(output)
    return output.getvalue()


# ─────────────────────────────────────────────
# 7. ANALYSE-ANZEIGE
# ─────────────────────────────────────────────
def show_analysis_ui(res, source_filename, key_prefix=""):
    if "_cols" in res:
        c = res["_cols"]
        st.caption(
            "Erkannte Spalten — Produktname: " + str(c["n"]) +
            ", Preis: " + str(c["p"]) +
            ", Warengruppe: " + str(c["w"]) +
            ", Kiosk-Spalten: " + str(c["kiosk"])
        )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Wie viele Dateien benoetigt?", type="primary",
                     key=key_prefix + "_files_btn"):
            f_t, d_t = 0, 0
            for s, l in [("food", "**🍔 FOOD**"), ("drinks", "**🥤 GETRAENKE**")]:
                st.markdown(l)
                grps = {}
                for k in res["ks"]:
                    asort = tuple(sorted([(i["name"], i["price"])
                                          for i in res[s] if k in i["ks"]]))
                    if asort:
                        if asort not in grps:
                            grps[asort] = []
                        grps[asort].append(k)
                for ks in grps.values():
                    st.code(format_k_list(ks), language=None)
                    if s == "food": f_t += 1
                    else:           d_t += 1
            st.divider()
            st.write("FOOD: " + str(f_t) + " | GETRAENKE: " + str(d_t) +
                     " | **GESAMT: " + str(f_t + d_t) + "**")
    with col2:
        st.download_button(
            "📥 Excel Export",
            data=create_excel_export(res),
            file_name="Analyse_" + source_filename + ".xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=key_prefix + "_dl_btn",
        )

    for label, items in [("FOOD", res["food"]), ("GETRÄNKE", res["drinks"])]:
        st.subheader(label)
        grps = {}
        for k in res["ks"]:
            asort = tuple([(i["cat"], i["name"], i["price"])
                           for i in items if k in i["ks"]])
            if asort not in grps:
                grps[asort] = []
            grps[asort].append(k)
        for asort, ks in sorted(grps.items(), key=lambda x: x[1][0]):
            with st.expander("Kioske: " + format_k_list(ks)):
                curr = ""
                for cat, n, p in asort:
                    if cat != curr:
                        st.markdown("**" + cat + "**")
                        curr = cat
                    st.write("- " + n + ": " + p)


# ─────────────────────────────────────────────
# 8. HAUPT-UI
# ─────────────────────────────────────────────
if check_password():
    st.markdown("""
        <style>
        .main-title  { font-size: 2.2rem; font-weight: 700; margin-bottom: 1rem; }
        .status-stable { color: #0984e3; font-weight: bold;
                         border-left: 5px solid #0984e3; padding-left: 10px; }
        .status-split  { color: #d63031; font-weight: bold;
                         border-left: 5px solid #d63031; padding-left: 10px; }
        </style>
        <div class="main-title">🏟️ Analyse Verkaufssortimente - V 2.0</div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["1. Einzel-Analyse", "Verkaufssortimente-Vergleich"])

    # TAB 1
    with tab1:
        fmt = st.radio(
            "Dateiformat waehlen:",
            ["Excel (.xlsx)", "PDF (.pdf)"],
            horizontal=True,
            key="tab1_format",
        )

        if fmt == "Excel (.xlsx)":
            up_file = st.file_uploader("Excel-Datei hochladen",
                                       type=["xlsx"], key="xlsx_up")
            if up_file:
                res = extract_data(up_file)
                if res:
                    show_analysis_ui(res, up_file.name, key_prefix="xlsx")
                else:
                    st.error("Datei konnte nicht verarbeitet werden.")

        else:
            up_pdf = st.file_uploader("PDF-Datei hochladen",
                                      type=["pdf"], key="pdf_up")

            if up_pdf:
                if st.session_state.get("pdf_filename") != up_pdf.name:
                    for key in ("pdf_filename", "pdf_raw_df",
                                "pdf_confirmed", "pdf_result"):
                        st.session_state[key] = None
                    st.session_state["pdf_filename"] = up_pdf.name

                if st.session_state.get("pdf_raw_df") is None:
                    with st.spinner("PDF wird eingelesen ..."):
                        raw_df, err = extract_tables_from_pdf(up_pdf)
                    if err:
                        st.error(err)
                        st.stop()
                    st.session_state["pdf_raw_df"] = raw_df

                raw_df = st.session_state["pdf_raw_df"]

                if not st.session_state.get("pdf_confirmed"):

                    issues    = detect_pdf_issues(raw_df)
                    anomalies = detect_row_anomalies(raw_df)
                    has_warn  = any(t in ("error", "warning") for t, _ in issues)

                    with st.expander(
                        "🔍 Auffaelligkeiten beim PDF-Import" +
                        (" - Bitte pruefen!" if has_warn else " - Alles ok"),
                        expanded=has_warn,
                    ):
                        for issue_type, msg in issues:
                            if issue_type == "error":    st.error(msg)
                            elif issue_type == "warning": st.warning(msg)
                            elif issue_type == "info":    st.info(msg)
                            else:                         st.success(msg)

                    if anomalies:
                        st.markdown(
                            "**Farbmarkierungen:** "
                            "🟡 Gelb = Kategorie-Zeile mit irrtaeglichem X &nbsp;|&nbsp; "
                            "🟠 Orange = die fehlerhafte Zelle selbst &nbsp;|&nbsp; "
                            "🔴 Rot = leere Kiosk-Zelle (weder X noch -)",
                            unsafe_allow_html=True,
                        )
                        styled = style_raw_df(raw_df, anomalies)
                        st.dataframe(styled, use_container_width=True, height=420)

                    st.markdown(
                        "**Rohdaten korrigieren** – Fehlerhaft eingelesene Zellen "
                        "hier direkt bearbeiten, dann bestaetigen."
                    )
                    edited_df = st.data_editor(
                        raw_df,
                        use_container_width=True,
                        num_rows="dynamic",
                        key="pdf_editor",
                    )

                    col_btn, col_hint = st.columns([1, 3])
                    with col_btn:
                        confirm = st.button(
                            "Bestaetigen & Analysieren",
                            type="primary",
                            key="pdf_confirm_btn",
                        )
                    with col_hint:
                        st.caption(
                            "Kiosk-Zuordnungen = 'X' in den Kiosk-Spalten. "
                            "Bereichs-Trenner: Zeile mit nur 'FOOD' oder 'GETRÄNKE'."
                        )

                    if confirm:
                        with st.spinner("Analyse laeuft ..."):
                            parsed = parse_df_to_result(edited_df, up_pdf.name)
                        if parsed:
                            st.session_state["pdf_result"]    = parsed
                            st.session_state["pdf_confirmed"] = True
                            st.rerun()
                        else:
                            st.error(
                                "Analyse fehlgeschlagen – bitte Kiosk-Kopfzeile pruefen."
                            )

                else:
                    res = st.session_state["pdf_result"]
                    st.success(
                        "PDF importiert – " +
                        str(len(res["food"])) + " Food- und " +
                        str(len(res["drinks"])) + " Getraenke-Produkte erkannt."
                    )
                    if st.button("Zurueck zur Datenpruefung", key="pdf_back_btn"):
                        st.session_state["pdf_confirmed"] = False
                        st.rerun()
                    st.divider()
                    show_analysis_ui(res, up_pdf.name, key_prefix="pdf")

    # TAB 2
    with tab2:
        st.header("Vergleich zwischen zwei Versionen")
        c_v1, c_v2 = st.columns(2)
        f_old = c_v1.file_uploader("Altes Sortiment", type=["xlsx"], key="o")
        f_new = c_v2.file_uploader("Neues Sortiment", type=["xlsx"], key="n")

        if f_old and f_new:
            old_res, new_res = extract_data(f_old), extract_data(f_new)
            if old_res and new_res:
                b1, b2 = st.columns(2)
                with b1:
                    do_anal = st.button("Unterschieds-Analyse starten", type="primary")
                with b2:
                    report_data = create_kiosk_diff_report(old_res, new_res)
                    st.download_button(
                        "Unterschiede als Excel",
                        data=report_data,
                        file_name="Zusammenfassung_Grafiker.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

                if do_anal:
                    for skey, title in [("food", "FOOD"), ("drinks", "GETRÄNKE")]:
                        st.markdown("## " + title)
                        o_grps = {}
                        for k in old_res["ks"]:
                            asort = tuple(sorted([
                                (i["name"], i["price"])
                                for i in old_res[skey] if k in i["ks"]
                            ]))
                            if asort not in o_grps:
                                o_grps[asort] = []
                            o_grps[asort].append(k)

                        for o_asort, o_ks in sorted(o_grps.items(),
                                                     key=lambda x: x[1][0]):
                            new_variants = {}
                            for k in o_ks:
                                n_asort = tuple(sorted([
                                    (i["name"], i["price"])
                                    for i in new_res[skey] if k in i["ks"]
                                ]))
                                if n_asort not in new_variants:
                                    new_variants[n_asort] = []
                                new_variants[n_asort].append(k)

                            st.subheader("Ehemalige Gruppe: " + format_k_list(o_ks))
                            if len(new_variants) == 1:
                                n_asort = list(new_variants.keys())[0]
                                lbl = "STABIL" if n_asort == o_asort else "GEAENDERT"
                                st.markdown(
                                    '<p class="status-stable">Status: ' + lbl + '</p>',
                                    unsafe_allow_html=True,
                                )
                                o_d, n_d = dict(o_asort), dict(n_asort)
                                for name in sorted(set(o_d) | set(n_d)):
                                    if name not in o_d:
                                        st.success("[+] " + name + ": " + n_d[name])
                                    elif name not in n_d:
                                        st.error("[-] " + name)
                                    elif o_d[name] != n_d[name]:
                                        st.warning("[!] " + name + ": " +
                                                   o_d[name] + " -> " + n_d[name])
                            else:
                                st.markdown(
                                    '<p class="status-split">Status: STRUKTURBRUCH / SPLIT</p>',
                                    unsafe_allow_html=True,
                                )
                                for idx, (n_asort, sub_ks) in enumerate(
                                    new_variants.items()
                                ):
                                    with st.expander(
                                        "Untergruppe " + str(idx + 1) +
                                        ": " + format_k_list(sub_ks)
                                    ):
                                        o_d, n_d = dict(o_asort), dict(n_asort)
                                        for name in sorted(set(o_d) | set(n_d)):
                                            if name not in o_d:
                                                st.success("[+] " + name + ": " + n_d[name])
                                            elif name not in n_d:
                                                st.error("[-] " + name)
                                            elif o_d[name] != n_d[name]:
                                                st.warning("[!] " + name + ": " +
                                                           o_d[name] + " -> " + n_d[name])
                            st.divider()
