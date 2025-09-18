"""
Simple Flask app for Vercel deployment
Minimal version of the Government Contracting Search Tool
"""

from flask import Flask, render_template, jsonify
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')

@app.route("/")
def index():
    """Main index page."""
    return render_template("index.html",
                         columns=[],
                         total_count=0,
                         solicitations=[])

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

@app.route("/filter", methods=["POST"])
def filter_data():
    """Filter endpoint - returns empty for now."""
    return jsonify({"count": 0, "columns": [], "solicitations": []})

@app.route("/my-filter", methods=["POST"])
def my_filter():
    """My solicitations filter - returns empty for now."""
    return jsonify({"count": 0, "columns": [], "solicitations": []})

@app.errorhandler(404)
def not_found_error(error):
    return "Page not found", 404

@app.errorhandler(500)
def internal_error(error):
    return "Internal server error", 500

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)