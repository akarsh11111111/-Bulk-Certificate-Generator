# Bulk Certificate Generator

A Python project for creating certificates in bulk from participant data. It includes modules for reading CSV and Excel files, editing certificate layouts, rendering PNG and PDF certificates, and packaging generated files into a ZIP archive.

## Project contents

- `src/data_handler.py` — reads and cleans participant CSV/Excel data.
- `src/template_handler.py` — loads templates, edits text-field layouts, and renders previews.
- `src/certificate_engine.py` — renders individual or batch certificates as PNG or PDF.
- `src/export_handler.py` — prepares individual downloads and ZIP exports.
- `data/sample_data.csv` — example participant data.
- `requirements.txt` — Python dependencies.

## Setup

Requires Python 3.10 or newer.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The repository currently contains the application modules but does not include an `app.py` entry point to launch the Streamlit interface.
