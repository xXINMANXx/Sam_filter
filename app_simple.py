"""
Simple Flask app for Vercel deployment
Minimal version of the Government Contracting Search Tool
"""

from flask import Flask, render_template, jsonify, request
import os
import pandas as pd
from werkzeug.utils import secure_filename
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
# Set different limits based on environment
if os.environ.get('VERCEL'):
    app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB for Vercel
elif os.environ.get('RENDER'):
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB for Render
else:
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB for local

# Sample data for demo
def get_sample_data():
    """Generate sample contract data for demonstration"""
    sample_data = {
        'Notice ID': ['ABC123', 'DEF456', 'GHI789', 'JKL012', 'MNO345'],
        'Title': [
            'IT Support Services Contract',
            'Building Maintenance Agreement',
            'Software Development Project',
            'Security Guard Services',
            'Office Supply Agreement'
        ],
        'Description': [
            'Comprehensive IT support for government offices including help desk and network maintenance',
            'Ongoing maintenance of federal building facilities including HVAC and electrical systems',
            'Custom software development for data management and reporting systems',
            'Professional security services for federal facilities during business hours',
            'Supply of office materials including paper, pens, and computer accessories'
        ],
        'Current Response Date': ['01/15/2025', '02/20/2025', '03/10/2025', '01/30/2025', '02/15/2025'],
        'Agency': ['GSA', 'DOD', 'VA', 'DHS', 'EPA'],
        'Set-Aside': ['Small Business', 'Unrestricted', 'Small Business', 'SDVOSB', 'HUBZone']
    }
    return pd.DataFrame(sample_data)

# Helper functions from original app
def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Find a column by exact-lower match first, then by contains."""
    norm_to_orig = {str(col).strip().lower(): col for col in df.columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in norm_to_orig:
            return norm_to_orig[key]
    for col in df.columns:
        if any(cand in str(col).strip().lower() for cand in candidates):
            return col
    return None

def add_highlight_summary_column(df: pd.DataFrame) -> pd.DataFrame:
    """Add a 'Highlight Summary' column after Description."""
    if df.empty:
        return df

    # Make a copy to avoid modifying the original
    df_copy = df.copy()

    # Find the description column
    DESC_CANDS = ["description", "details", "summary"]
    desc_col = _find_col(df_copy, DESC_CANDS)

    # Get current column order
    columns = list(df_copy.columns)

    # If description column exists, insert after it
    if desc_col and desc_col in columns:
        insert_idx = columns.index(desc_col) + 1
    else:
        # If no description column, insert at the end
        insert_idx = len(columns)

    # Insert the new column with empty values initially
    df_copy.insert(insert_idx, "Highlight Summary", "")

    return df_copy

# In-memory storage for demo purposes
app_data = {
    'main_data': get_sample_data(),  # Start with sample data
    'my_solicitations': pd.DataFrame()
}

@app.route("/")
def index():
    """Main index page."""
    df = app_data['main_data']
    if not df.empty:
        df = add_highlight_summary_column(df)
    return render_template("index.html",
                         columns=list(df.columns) if not df.empty else [],
                         total_count=len(df),
                         solicitations=df.to_dict(orient="records") if not df.empty else [])

@app.route("/my-solicitations")
def my_solicitations():
    """My Solicitations page."""
    return render_template("my_solicitations.html",
                         columns=[],
                         total_count=0,
                         solicitations=[])

@app.route('/project_tracking')
def project_tracking():
    """Project tracking page."""
    return render_template('project_tracking.html')

@app.route("/upload-data", methods=["POST"])
def upload_data():
    """Upload a CSV/XLSX file and store in memory."""
    try:
        print(f"[UPLOAD] Environment: {'Vercel' if os.environ.get('VERCEL') else 'Local'}")
        print(f"[UPLOAD] Request content length: {request.content_length}")
        print(f"[UPLOAD] Request files: {list(request.files.keys())}")

        if "file" not in request.files:
            return jsonify({"ok": False, "message": "No file part"}), 400
    except Exception as e:
        print(f"[UPLOAD] Initial error: {e}")
        return jsonify({"ok": False, "message": f"Request processing error: {str(e)}"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"ok": False, "message": "No file selected"}), 400

    fname = secure_filename(file.filename)
    if not fname:
        return jsonify({"ok": False, "message": "Invalid filename"}), 400

    # Check file extension
    allowed_extensions = {'.csv', '.xlsx', '.xls'}
    ext = os.path.splitext(fname)[1].lower()
    if ext not in allowed_extensions:
        return jsonify({"ok": False, "message": "Unsupported file type"}), 400

    try:
        # Read file content
        content = file.read()

        # Parse the file based on extension (match original app's logic)
        if ext == '.csv':
            try:
                df = pd.read_csv(BytesIO(content), dtype=str, encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(BytesIO(content), dtype=str, encoding="cp1252")
        elif ext in ['.xlsx', '.xls']:
            # Requires openpyxl for .xlsx
            df = pd.read_excel(BytesIO(content), dtype=str)

        # Normalize cell values to strings; keep header names as-is (match original)
        for c in df.columns:
            try:
                df[c] = df[c].astype(str).fillna("")
            except Exception:
                pass

        # Add Highlight Summary column (match original functionality)
        df = add_highlight_summary_column(df)

        # Store in memory
        app_data['main_data'] = df

        return jsonify({
            "ok": True,
            "saved_as": fname,
            "rows": len(df)
        })

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Upload error: {error_details}")
        return jsonify({
            "ok": False,
            "message": f"Error processing file: {str(e)}",
            "details": error_details[:500]  # Limit details for response
        }), 500

@app.route("/filter", methods=["POST"])
def filter_data():
    """Filter the main data."""
    df = app_data['main_data']
    if df.empty:
        return jsonify({"count": 0, "columns": [], "solicitations": []})

    payload = request.get_json(silent=True) or {}
    keyword = (payload.get("keyword") or "").strip()

    filtered = df

    # Simple keyword search across all columns
    if keyword:
        mask = df.astype(str).apply(lambda x: x.str.contains(keyword, case=False, na=False)).any(axis=1)
        filtered = df[mask]

    return jsonify({
        "count": len(filtered),
        "columns": list(df.columns),
        "solicitations": filtered.to_dict(orient="records")
    })

@app.route("/my-filter", methods=["POST"])
def my_filter():
    """Filter my solicitations data."""
    df = app_data['my_solicitations']
    if df.empty:
        return jsonify({"count": 0, "columns": [], "solicitations": []})

    payload = request.get_json(silent=True) or {}
    keyword = (payload.get("keyword") or "").strip()

    filtered = df

    # Simple keyword search across all columns
    if keyword:
        mask = df.astype(str).apply(lambda x: x.str.contains(keyword, case=False, na=False)).any(axis=1)
        filtered = df[mask]

    return jsonify({
        "count": len(filtered),
        "columns": list(df.columns),
        "solicitations": filtered.to_dict(orient="records")
    })

@app.errorhandler(413)
def too_large(e):
    if os.environ.get('VERCEL'):
        max_size = "1MB"
    elif os.environ.get('RENDER'):
        max_size = "50MB"
    else:
        max_size = "16MB"
    return jsonify({
        "ok": False,
        "message": f"File too large. Maximum size is {max_size}."
    }), 413

@app.errorhandler(404)
def not_found_error(error):
    return "Page not found", 404

@app.errorhandler(500)
def internal_error(error):
    return "Internal server error", 500

if __name__ == "__main__":
    import os
    port = int(os.environ.get('PORT', 5000))
    debug = not os.environ.get('RENDER')  # Disable debug on Render
    host = '0.0.0.0' if os.environ.get('RENDER') else '127.0.0.1'
    app.run(debug=debug, host=host, port=port)