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

# In-memory storage for demo purposes
app_data = {
    'main_data': get_sample_data(),  # Start with sample data
    'my_solicitations': pd.DataFrame()
}

@app.route("/")
def index():
    """Main index page."""
    df = app_data['main_data']
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

        # Parse the file based on extension
        if ext == '.csv':
            df = pd.read_csv(BytesIO(content), dtype=str)
        elif ext in ['.xlsx', '.xls']:
            df = pd.read_excel(BytesIO(content), dtype=str)

        # Clean the data
        for col in df.columns:
            df[col] = df[col].astype(str).fillna("")

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
    max_size = "1MB" if os.environ.get('VERCEL') else "16MB"
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
    app.run(debug=True, host="127.0.0.1", port=5000)