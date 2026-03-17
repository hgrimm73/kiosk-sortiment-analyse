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

    BUGFIX: Nach der Kiosk-Spalten-Erkennung wird sichergestellt,
    dass n_col und p_col nicht auf Kiosk-Spalten zeigen.
    """
    try:
        df = pd.DataFrame(df).fillna("").astype(str)
        h_row = -1
        k_map = {}          # Spalten-Index → Kiosk-Label
        # Sichere Startwerte; werden weiter unten ggf. überschrieben
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
        # Suche unter allen Nicht-Kiosk-Spalten nach brauchbaren Kandidaten.
        if n_col is None or n_col in kiosk_cols:
            # Längsten Text-Wert in Datenspalten als Produktname-Heuristik
            candidate_cols = [c for c in range(df.shape[1]) if c not in kiosk_cols]
            if candidate_cols:
                # Nimm die erste Nicht-Kiosk-Spalte, die am häufigsten Text enthält
                text_counts = {}
                for c in candidate_cols:
                    text_counts[c] = sum(
                        1 for v in df.iloc[h_row + 1:][c]
                        if normalize(str(v)) and normalize(str(v)).upper()
                        not in ["X", "", "0", "0.0"]
                    )
                n_col = max(candidate_cols, key=lambda c: text_counts.get(c, 0))

        if p_col is None or p_col in kiosk_cols:
            # Suche Spalte, die häufig Preismuster enthält (Zahl mit Komma/Punkt)
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
            # Erste Nicht-Kiosk-Spalte, die weder n_col noch p_col ist
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

            # Bereichs-Trenner
            if clean_name in ["GETRÄNKE", "DRINKS"]:
                sec = "DRINKS"; current_cat = "GETRÄNKE"; continue
            if clean_name in ["FOOD", "SPEISEN"]:
                sec = "FOOD"; current_cat = "FOOD"; continue

            # Wiederholte Kopfzeilen (PDF-Seitenumbrüche) überspringen
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
            # Debug-Info für den Review-Step
            "_cols": {"n": n_col, "p": p_col, "w": w_col, "kiosk": sorted(kiosk_cols)},
        }
    except Exception:
        return None


def extract_data(uploaded_file):
    """Excel-Datei direkt einlesen und parsen."""
    try:
        df = pd.read_excel(uploaded_file, header=None)
        return parse_df_to_result(df, uploaded_file.name)
    except Exception:
        return None


# ─────────────────────────────────────────────
# 4. PDF-IMPORT
# ─────────────────────────────────────────────
def extract_tables_from_pdf(uploaded_pdf):
    """Liest alle Tabellen aus einem PDF und gibt einen kombinierten DataFrame zurück."""
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
    """
    Analysiert den rohen DataFrame auf typische PDF-Import-Probleme.
    Gibt eine Liste von (typ, nachricht)-Tupeln zurück.
    """
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

    # Wiederholte Kopfzeilen
    repeated = [
        i + 1 for i, row in df.iloc[h_row + 1:].iterrows()
        if sum(1 for v in [normalize(str(x)).upper() for x in row]
               if re.search(r"KIOSK.*\d", v)) >= 2
    ]
    if repeated:
        issues.append((
            "warning",
            f"Wiederholte Kopfzeilen erkannt (Zeilen {repeated[:5]}"
            f"{'...' if len(repeated) > 5 else ''}) – "
            "wahrscheinlich Seitenumbrüche. Werden beim Parsen automatisch ignoriert."
        ))

    # Sehr lange Zellwerte
    long_cells = []
    for i, row in df.iloc[h_row + 1:].iterrows():
        for j, val in enumerate(row):
            if len(str(val)) > 150:
                long_cells.append(f"Zeile {i + 1}, Spalte {j + 1}")
                break
    if long_cells:
        issues.append((
            "warning",
            f"Sehr langer Text in {len(long_cells)} Zeile(n) – "
            f"möglicherweise zusammengeführte Zellen aus dem PDF: "
            f"{', '.join(long_cells[:3])}{'...' if len(long_cells) > 3 else ''}"
        ))

    # Produkte mit Preis aber ohne Kiosk-Zuordnung
    price_pattern = re.compile(r"\d+[.,]\d{2}")
    no_kiosk = []
    for i, row in df.iloc[h_row + 1:].iterrows():
        name_val = normalize(str(row.iloc[n_col]) if n_col < len(row) else "")
        if not name_val or name_val.upper() in ["FOOD", "SPEISEN", "GETRÄNKE", "DRINKS"]:
            continue
        if sum(1 for v in [normalize(str(x)).upper() for x in row]
               if re.search(r"KIOSK.*\d", v)) >= 2:
            continue
        has_price = any(price_pattern.search(str(row.iloc[c])) for c in range(len(row)))
        has_x = any(
            c < len(row) and str(row.iloc[c]).strip().upper() == "X"
            for c in k_cols
        )
        if has_price and not has_x:
            no_kiosk.append(name_val)

    if no_kiosk:
        preview = ", ".join(f'"{p}"' for p in no_kiosk[:3])
        extra = f" … und {len(no_kiosk) - 3} weitere" if len(no_kiosk) > 3 else ""
        issues.append((
            "info",
            f"{len(no_kiosk)} Produkt(e) mit Preis, aber ohne Kiosk-Zuordnung (kein X): "
            f"{preview}{extra}."
        ))

    if not issues:
        issues.append(("success", "Keine Auffälligkeiten erkannt – Daten sehen gut aus."))

    return issues


# ─────────────────────────────────────────────
# 5. EXPORTS
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
            assort = tuple([(i["cat"], i["name"], i["price"]) for i in items if k in i["ks"]])
            if assort not in groups:
                groups[assort] = []
            groups[assort].append(k)
        for assort, ks in sorted(groups.items(), key=lambda x: x[1][0]):
            ws.merge_cells(f"B{row_i}:D{row_i}")
            ws.cell(row=row_i, column=2, value="Kioske: " + format_k_list(ks)).font = Font(bold=True)
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
    headers = ["Kiosk(e)", "Bereich", "Zugehörigkeit Alt", "Zugehörigkeit Neu",
               "Status", "Details der Änderungen"]
    ws.append(headers)

    def get_kiosk_map(data):
        m = {}
        for skey in ["food", "drinks"]:
            assorts = {}
            for k in data["ks"]:
                asort = tuple(sorted([(i["name"], i["price"]) for i in data[skey] if k in i["ks"]]))
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
            old_m.get((k, skey), (None, "Nicht vorhanden"))[1] for k in old["ks"]
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
                changes, status = [], ["Inhalt geändert"]
                if og_name != ng_name:
                    status.append("Gruppe gewechselt / Split")
                added   = sorted(list(set(n_d.keys()) - set(o_d.keys())))
                removed = sorted(list(set(o_d.keys()) - set(n_d.keys())))
                prices  = sorted([n for n in set(o_d.keys()) & set(n_d.keys()) if o_d[n] != n_d[n]])
                for a in added:   changes.append(f"[+] Neu: {a} ({n_d[a]})")
                for r in removed: changes.append(f"[-] Weg: {r}")
                for p in prices:  changes.append(f"[!] Preis: {p} ({o_d[p]} -> {n_d[p]})")
                group_key = (
                    format_k_list(ks_list), label, og_name, ng_name,
                    ", ".join(status), "\n".join(changes)
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
# 6. ANALYSE-ANZEIGE (wiederverwendbar)
# ─────────────────────────────────────────────
def show_analysis_ui(res, source_filename, key_prefix=""):
    """Zeigt die vollständige Analyse-Ansicht für ein geparste Ergebnis."""
    # Spalten-Info anzeigen (hilfreich zur Kontrolle)
    if "_cols" in res:
        c = res["_cols"]
        st.caption(
            f"ℹ️ Erkannte Spalten — Produktname: {c['n']}, Preis: {c['p']}, "
            f"Warengruppe: {c['w']}, Kiosk-Spalten: {c['kiosk']}"
        )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Wie viele Dateien benötige ich?", type="primary",
                     key=f"{key_prefix}_files_btn"):
            f_t, d_t = 0, 0
            for s, l in [("food", "**🍔 FOOD**"), ("drinks", "**🥤 GETRÄNKE**")]:
                st.markdown(l)
                grps = {}
                for k in res["ks"]:
                    asort = tuple(sorted([
                        (i["name"], i["price"]) for i in res[s] if k in i["ks"]
                    ]))
                    if asort:
                        if asort not in grps:
                            grps[asort] = []
                        grps[asort].append(k)
                for ks in grps.values():
                    st.code(format_k_list(ks), language=None)
                    if s == "food": f_t += 1
                    else:           d_t += 1
            st.divider()
            st.write(f"FOOD: {f_t} | GETRÄNKE: {d_t} | **GESAMT: {f_t + d_t}**")
    with col2:
        st.download_button(
            "📥 Excel Export",
            data=create_excel_export(res),
            file_name=f"Analyse_{source_filename}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key_prefix}_dl_btn",
        )

    for label, items in [("FOOD", res["food"]), ("GETRÄNKE", res["drinks"])]:
        st.subheader(label)
        grps = {}
        for k in res["ks"]:
            asort = tuple([(i["cat"], i["name"], i["price"]) for i in items if k in i["ks"]])
            if asort not in grps:
                grps[asort] = []
            grps[asort].append(k)
        for asort, ks in sorted(grps.items(), key=lambda x: x[1][0]):
            with st.expander(f"Kioske: {format_k_list(ks)}"):
                curr = ""
                for cat, n, p in asort:
                    if cat != curr:
                        st.markdown(f"**{cat}**")
                        curr = cat
                    st.write(f"- {n}: {p}")


# ─────────────────────────────────────────────
# 7. HAUPT-UI
# ─────────────────────────────────────────────
if check_password():
    st.markdown("""
        <style>
        .main-title  { font-size: 2.2rem; font-weight: 700; margin-bottom: 1rem; }
        .status-stable { color: #0984e3; font-weight: bold;
                         border-left: 5px solid #0984e3; padding-left: 10px; }
        .status-split  { color: #d63031; font-weight: bold;
                         border-left: 5px solid #d63031; padding-left: 10px; }
        .review-hint   { background: #f0f4ff; border-left: 4px solid #4c6ef5;
                         padding: 10px 14px; border-radius: 4px;
                         margin-bottom: 1rem; font-size: 0.9rem; }
        </style>
        <div class="main-title">🏟️ Analyse Verkaufssortimente – V 2.0</div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["1. Einzel-Analyse", "Verkaufssortimente-Vergleich"])

    # ════════════════════════════════════════
    # TAB 1 – EINZEL-ANALYSE
    # ════════════════════════════════════════
    with tab1:
        fmt = st.radio(
            "Dateiformat wählen:",
            ["📊 Excel (.xlsx)", "📄 PDF (.pdf)"],
            horizontal=True,
            key="tab1_format",
        )

        # ── EXCEL ────────────────────────────────────
        if fmt == "📊 Excel (.xlsx)":
            up_file = st.file_uploader("Excel-Datei hochladen", type=["xlsx"], key="xlsx_up")
            if up_file:
                res = extract_data(up_file)
                if res:
                    show_analysis_ui(res, up_file.name, key_prefix="xlsx")
                else:
                    st.error(
                        "❌ Datei konnte nicht verarbeitet werden. "
                        "Bitte prüfen, ob eine Kiosk-Kopfzeile vorhanden ist."
                    )

        # ── PDF ──────────────────────────────────────
        else:
            up_pdf = st.file_uploader("PDF-Datei hochladen", type=["pdf"], key="pdf_up")

            if up_pdf:
                # Neues PDF → State zurücksetzen
                if st.session_state.get("pdf_filename") != up_pdf.name:
                    st.session_state["pdf_filename"]  = up_pdf.name
                    st.session_state["pdf_raw_df"]    = None
                    st.session_state["pdf_confirmed"] = False
                    st.session_state["pdf_result"]    = None

                # Einmalige Extraktion
                if st.session_state["pdf_raw_df"] is None:
                    with st.spinner("PDF wird eingelesen …"):
                        raw_df, err = extract_tables_from_pdf(up_pdf)
                    if err:
                        st.error(f"❌ {err}")
                        st.stop()
                    st.session_state["pdf_raw_df"] = raw_df

                raw_df = st.session_state["pdf_raw_df"]

                # ── REVIEW-SCHRITT ───────────────────
                if not st.session_state.get("pdf_confirmed"):

                    issues = detect_pdf_issues(raw_df)
                    has_problems = any(t in ("error", "warning") for t, _ in issues)

                    with st.expander(
                        "🔍 Auffälligkeiten beim PDF-Import"
                        + (" – ⚠️ Bitte prüfen!" if has_problems else " – ✅ Alles ok"),
                        expanded=has_problems,
                    ):
                        for issue_type, msg in issues:
                            if issue_type == "error":    st.error(msg)
                            elif issue_type == "warning": st.warning(msg)
                            elif issue_type == "info":    st.info(msg)
                            else:                         st.success(msg)

                    st.markdown("""
                        <div class="review-hint">
                        📋 <b>Rohdaten prüfen &amp; korrigieren</b><br>
                        Die nachfolgende Tabelle zeigt die direkt aus dem PDF extrahierten Daten.
                        Fehlerhaft eingelesene Zellen können hier bearbeitet werden –
                        Zeilen lassen sich auch hinzufügen oder löschen.<br>
                        Anschließend <b>„Bestätigen &amp; Analysieren"</b> klicken.
                        </div>
                    """, unsafe_allow_html=True)

                    edited_df = st.data_editor(
                        raw_df,
                        use_container_width=True,
                        num_rows="dynamic",
                        key="pdf_editor",
                    )

                    col_btn, col_hint = st.columns([1, 3])
                    with col_btn:
                        confirm = st.button(
                            "✅ Bestätigen & Analysieren",
                            type="primary",
                            key="pdf_confirm_btn",
                    with col_hint:
                        st.caption(
                            'Kiosk-Zuordnungen werden als "X" in den Kiosk-Spalten erkannt. '
                            'Bereichs-Trenner: Zeilen, die nur "FOOD" oder "GETRÄNKE" enthalten.'
                        )

                    if confirm:
                        with st.spinner("Daten werden analysiert …"):
                            parsed = parse_df_to_result(edited_df, up_pdf.name)
                        if parsed:
                            st.session_state["pdf_result"]    = parsed
                            st.session_state["pdf_confirmed"] = True
                            st.rerun()
                        else:
                            st.error(
                                "❌ Analyse fehlgeschlagen. Bitte prüfen, ob die Kopfzeile "
                                "mit den Kiosk-Spalten korrekt eingelesen wurde."
                            )

                # ── ANALYSE (nach Bestätigung) ───────
                else:
                    res = st.session_state["pdf_result"]
                    st.success(
                        f"✅ PDF erfolgreich importiert – "
                        f"{len(res['food'])} Food- und "
                        f"{len(res['drinks'])} Getränke-Produkte erkannt."
                    )
                    if st.button("↩ Zurück zur Datenprüfung", key="pdf_back_btn"):
                        st.session_state["pdf_confirmed"] = False
                        st.rerun()
                    st.divider()
                    show_analysis_ui(res, up_pdf.name, key_prefix="pdf")

    # ════════════════════════════════════════
    # TAB 2 – VERGLEICH
    # ════════════════════════════════════════
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
                        "📄 Unterschiede zusammenfassen (Excel)",
                        data=report_data,
                        file_name="Zusammenfassung_Grafiker.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

                if do_anal:
                    for skey, title in [("food", "FOOD"), ("drinks", "GETRÄNKE")]:
                        st.markdown(f"## {title}")
                        o_grps = {}
                        for k in old_res["ks"]:
                            asort = tuple(sorted([
                                (i["name"], i["price"]) for i in old_res[skey] if k in i["ks"]
                            ]))
                            if asort not in o_grps:
                                o_grps[asort] = []
                            o_grps[asort].append(k)

                        for o_asort, o_ks in sorted(o_grps.items(), key=lambda x: x[1][0]):
                            new_variants = {}
                            for k in o_ks:
                                n_asort = tuple(sorted([
                                    (i["name"], i["price"]) for i in new_res[skey] if k in i["ks"]
                                ]))
                                if n_asort not in new_variants:
                                    new_variants[n_asort] = []
                                new_variants[n_asort].append(k)

                            st.subheader(f"Ehemalige Gruppe: {format_k_list(o_ks)}")
                            if len(new_variants) == 1:
                                n_asort = list(new_variants.keys())[0]
                                if n_asort == o_asort:
                                    st.markdown(
                                        '<p class="status-stable">Status: STABIL</p>',
                                        unsafe_allow_html=True,
                                    )
                                else:
                                    st.markdown(
                                        '<p class="status-stable">Status: GEÄNDERT</p>',
                                        unsafe_allow_html=True,
                                    )
                                o_d, n_d = dict(o_asort), dict(n_asort)
                                for name in sorted(set(o_d.keys()) | set(n_d.keys())):
                                    if name not in o_d:           st.success(f"[+] {name}: {n_d[name]}")
                                    elif name not in n_d:         st.error(f"[-] {name}")
                                    elif o_d[name] != n_d[name]:  st.warning(f"[!] {name}: {o_d[name]} → {n_d[name]}")
                            else:
                                st.markdown(
                                    '<p class="status-split">Status: STRUKTURBRUCH / SPLIT</p>',
                                    unsafe_allow_html=True,
                                )
                                for i, (n_asort, sub_ks) in enumerate(new_variants.items()):
                                    with st.expander(f"Untergruppe {i + 1}: {format_k_list(sub_ks)}"):
                                        o_d, n_d = dict(o_asort), dict(n_asort)
                                        for name in sorted(set(o_d.keys()) | set(n_d.keys())):
                                            if name not in o_d:           st.success(f"[+] {name}: {n_d[name]}")
                                            elif name not in n_d:         st.error(f"[-] {name}")
                                            elif o_d[name] != n_d[name]:  st.warning(f"[!] {name}: {o_d[name]} → {n_d[name]}")
                            st.divider()
