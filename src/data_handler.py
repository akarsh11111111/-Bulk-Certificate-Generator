"""
data_handler.py
───────────────
Handles all participant data ingestion: CSV / Excel upload, validation,
normalisation, and preview rendering.  Returns clean pandas DataFrames.

No required columns are enforced — all columns are treated equally and
can be mapped to certificate fields by the user in the Design step.
"""

import io
import pandas as pd
import streamlit as st
from pathlib import Path


# ─── Public API ───────────────────────────────────────────────────────────────

def parse_uploaded_file(uploaded_file) -> pd.DataFrame | None:
    """
    Accept a Streamlit UploadedFile object (CSV or Excel) and return
    a cleaned DataFrame, or None on error.

    No column names are required — all columns are accepted as-is.
    """
    if uploaded_file is None:
        return None

    suffix = Path(uploaded_file.name).suffix.lower()

    try:
        raw_bytes = uploaded_file.read()
        buf = io.BytesIO(raw_bytes)

        if suffix == ".csv":
            df = _read_csv(buf)
        elif suffix in (".xlsx", ".xls"):
            df = _read_excel(buf)
        else:
            st.error(f"Unsupported file type: **{suffix}**. Please upload a .csv or .xlsx file.")
            return None

    except Exception as exc:
        st.error(f"Could not read file: {exc}")
        return None

    return _clean(df)


def get_available_placeholders(df: pd.DataFrame) -> list[str]:
    """Return placeholder strings like '{Name}', '{Course}' for every column."""
    return [f"{{{col}}}" for col in df.columns]


def format_record(record: dict, template_text: str) -> str:
    """Replace {ColumnName} tokens in template_text with actual values."""
    result = template_text
    for key, value in record.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def render_data_preview(df: pd.DataFrame) -> None:
    """Render a preview table and summary stats."""
    total = len(df)
    cols  = list(df.columns)

    m1, m2 = st.columns(2)
    m1.metric("👥 Participants", total)
    m2.metric("📋 Columns", len(cols))

    st.dataframe(
        df.style.set_properties(**{
            "background-color": "#0f172a",
            "color": "#e2e8f0",
        }),
        use_container_width=True,
        height=min(400, 55 + total * 35),
    )

    with st.expander("🔍 Available columns (usable as placeholders)", expanded=False):
        st.markdown(
            " ".join(f"`{{{c}}}`" for c in cols)
        )


# ─── Private helpers ──────────────────────────────────────────────────────────

def _read_csv(buf: io.BytesIO) -> pd.DataFrame:
    """Try common encodings to read CSV robustly."""
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            buf.seek(0)
            return pd.read_csv(buf, encoding=enc, skipinitialspace=True)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode CSV with any supported encoding.")


def _read_excel(buf: io.BytesIO) -> pd.DataFrame:
    """Read the first sheet of an Excel workbook."""
    buf.seek(0)
    return pd.read_excel(buf, sheet_name=0)


def _clean(df: pd.DataFrame) -> pd.DataFrame | None:
    """
    Strip whitespace from headers and values, drop fully empty rows.
    No column validation — accept whatever the user uploaded.
    """
    # Normalise column names
    df.columns = [str(c).strip() for c in df.columns]

    # Drop fully empty rows
    df = df.dropna(how="all").reset_index(drop=True)

    if df.empty:
        st.error("The uploaded file contains no data rows.")
        return None

    # Strip whitespace from string columns
    str_cols = df.select_dtypes(include="object").columns
    df[str_cols] = df[str_cols].apply(lambda s: s.str.strip())

    # Coerce all values to string for safe text rendering
    df = df.astype(str).replace("nan", "")

    return df
