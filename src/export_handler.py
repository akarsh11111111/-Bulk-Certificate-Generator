"""
export_handler.py
─────────────────
Handles all export operations:
  • Build an in-memory ZIP archive from a list of GeneratedCert objects.
  • Provide Streamlit download buttons for individual certificates.
  • Provide a single-click ZIP bulk download button.
  • Optionally persist generated certificates to a local output folder.

No files are written to disk unless explicitly requested — everything
flows through in-memory bytes buffers to keep the app lightweight.
"""

import io
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st

from certificate_engine import GeneratedCert

# ─── Constants ────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path(__file__).parent / "output"


# ─── ZIP builder ──────────────────────────────────────────────────────────────

def build_zip(certs: list[GeneratedCert]) -> bytes:
    """
    Compress all *certs* into an in-memory ZIP archive and return the bytes.

    Each certificate is stored at:
        certificates/{safe_name}.{fmt}

    Args:
        certs: List of GeneratedCert dataclass instances.

    Returns:
        Raw ZIP bytes.
    """
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        seen_names: dict[str, int] = {}

        for cert in certs:
            base_filename = f"{cert.name}.{cert.fmt}"

            # Deduplicate filenames (two participants with same name)
            if base_filename in seen_names:
                seen_names[base_filename] += 1
                filename = f"{cert.name}_{seen_names[base_filename]}.{cert.fmt}"
            else:
                seen_names[base_filename] = 0
                filename = base_filename

            zf.writestr(f"certificates/{filename}", cert.data)

        # Add a manifest text file
        manifest_lines = [
            "Certificate Bulk Export",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total certificates: {len(certs)}",
            "",
            "Files:",
        ]
        for cert in certs:
            manifest_lines.append(f"  • {cert.name}.{cert.fmt}")

        zf.writestr("MANIFEST.txt", "\n".join(manifest_lines))

    return buf.getvalue()


# ─── Streamlit download UI ────────────────────────────────────────────────────

def render_download_section(certs: list[GeneratedCert]) -> None:
    """
    Render the full download UI:
      1. Bulk ZIP download button at the top.
      2. Expandable table of individual certificate download buttons.
      3. Optional save-to-disk checkbox.

    Args:
        certs: List of GeneratedCert objects from the generation step.
    """
    if not certs:
        st.warning("No certificates to download yet.")
        return

    fmt_label = certs[0].fmt.upper()
    total     = len(certs)

    st.markdown("### 📦 Bulk Download")

    # ── ZIP download ──────────────────────────────────────────────────────────
    zip_bytes = build_zip(certs)
    zip_name  = f"certificates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    col_left, col_right = st.columns([2, 1])
    with col_left:
        st.download_button(
            label=f"⬇️  Download All {total} Certificates as ZIP",
            data=zip_bytes,
            file_name=zip_name,
            mime="application/zip",
            type="primary",
            use_container_width=True,
        )
    with col_right:
        size_kb = len(zip_bytes) / 1024
        size_label = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"
        st.metric("ZIP size", size_label)

    st.divider()

    # ── Individual downloads ──────────────────────────────────────────────────
    st.markdown("### 🎓 Individual Downloads")

    with st.expander(f"Show all {total} certificates", expanded=total <= 10):
        # Show 10 per page to avoid DOM bloat for large batches
        PAGE_SIZE = 10
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

        if total_pages > 1:
            page = st.number_input(
                "Page", min_value=1, max_value=total_pages,
                value=1, step=1, key="cert_page",
            )
        else:
            page = 1

        start = (page - 1) * PAGE_SIZE
        end   = min(start + PAGE_SIZE, total)

        for i, cert in enumerate(certs[start:end], start=start + 1):
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{i}.** {cert.name}")
            mime = "application/pdf" if cert.fmt == "pdf" else "image/png"
            c2.download_button(
                label=f"⬇ {fmt_label}",
                data=cert.data,
                file_name=f"{cert.name}.{cert.fmt}",
                mime=mime,
                key=f"dl_{i}",
            )

    # ── Save to disk (optional) ───────────────────────────────────────────────
    st.divider()
    st.markdown("### 💾 Save to Local Folder")

    if st.checkbox("Save all certificates to the `output/` folder on this machine"):
        if st.button("💾 Save to disk", type="secondary"):
            _save_to_disk(certs)


def _save_to_disk(certs: list[GeneratedCert]) -> None:
    """
    Write each GeneratedCert to OUTPUT_DIR / {name}.{fmt}.

    Args:
        certs: List of generated certificate objects.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0

    for cert in certs:
        filename = OUTPUT_DIR / f"{cert.name}.{cert.fmt}"
        # Ensure unique filename
        counter = 1
        while filename.exists():
            filename = OUTPUT_DIR / f"{cert.name}_{counter}.{cert.fmt}"
            counter += 1

        filename.write_bytes(cert.data)
        saved += 1

    st.success(f"✅ Saved {saved} certificates to `{OUTPUT_DIR.resolve()}`")
