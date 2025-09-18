"""
Government Contracting Search Tool
A comprehensive Flask application for searching, filtering, and managing government contract opportunities.
Enhanced with project tracking, SAM.gov automation, and persistent browser sessions.
"""

import os
import secrets
from io import BytesIO
from datetime import datetime, timedelta
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file, session, flash, redirect, url_for
from werkzeug.utils import secure_filename
from werkzeug.security import safe_join
import requests
import shutil
import logging
import json
import time
import re as _re
from pathlib import Path as _Path
from urllib.parse import urljoin, urlparse
import openai

# Selenium imports with error handling
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.edge.options import Options as EdgeOptions
    _SELENIUM_AVAILABLE = True
except ImportError as e:
    print(f"[SELENIUM] Not available: {e}")
    _SELENIUM_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('government_contracting_tool.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flask app configuration
app = Flask(
    __name__,
    static_url_path="/static",
    static_folder="static",
    template_folder="templates"
)

# Production/Debug mode detection
PRODUCTION_MODE = os.environ.get('PRODUCTION_MODE', 'false').lower() == 'true'

# Generate secure secret key if not provided in environment
def get_secret_key():
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        # Generate a secure random key and warn user to set environment variable
        secret_key = secrets.token_hex(32)
        print("[SECURITY WARNING] No SECRET_KEY environment variable found.")
        print("[SECURITY WARNING] Generated temporary key. Set SECRET_KEY environment variable for production!")
    return secret_key

app.config.update(
    SECRET_KEY=get_secret_key(),
    MAX_CONTENT_LENGTH=250 * 1024 * 1024,  # 250MB
    UPLOAD_FOLDER='uploads',
    SESSION_COOKIE_SECURE=False,  # Set to True when using HTTPS
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Strict',
    PERMANENT_SESSION_LIFETIME=timedelta(days=7)
)

# OpenAI configuration
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
    print("[AI] OpenAI API key configured")
else:
    print("[AI] WARNING: OPENAI_API_KEY environment variable not set. AI summary features will be disabled.")

# Directory configuration
DATA_DIR = "data"
UPLOAD_DIR = app.config['UPLOAD_FOLDER']
CONTRACTS_BASE = os.path.join(os.path.expanduser("~"), "Government_Contracts")
ACTIVE_MARKER = os.path.join(DATA_DIR, ".active_path.txt")
ALLOWED_EXTS = {".csv", ".xlsx", ".xls"}
MY_FILE = os.path.join(DATA_DIR, "my_solicitations.xlsx")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
AI_SUMMARIES_FILE = os.path.join(DATA_DIR, "ai_summaries.json")

# Ensure directories exist (only in development)
if not os.environ.get('VERCEL'):
    for directory in [DATA_DIR, UPLOAD_DIR, CONTRACTS_BASE, BACKUP_DIR]:
        os.makedirs(directory, exist_ok=True)

# Global variables for persistent session management
_persistent_driver = None
_session_start_time = None
_session_timeout = 3600  # 1 hour timeout
_max_session_age = 14400  # 4 hours maximum session age for security


# ====================== FILE MANAGEMENT ======================
def ensure_data_dir():
    """Ensure data directory exists."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)


def is_allowed(filename: str) -> bool:
    """Check if file extension is allowed."""
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTS


def create_backup(filepath: str) -> str:
    """Create a backup of the file with timestamp."""
    if not os.path.exists(filepath):
        return None
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    basename = os.path.basename(filepath)
    name, ext = os.path.splitext(basename)
    backup_name = f"{name}_backup_{timestamp}{ext}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    
    try:
        shutil.copy2(filepath, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        return None


def list_data_files():
    """List all data files in the data directory."""
    ensure_data_dir()
    return [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if is_allowed(f)]


def latest_data_file():
    """Get the most recent data file."""
    files = list_data_files()
    return max(files, key=lambda p: os.path.getmtime(p)) if files else None


def read_active_marker():
    """Read the active file marker."""
    try:
        if os.path.exists(ACTIVE_MARKER):
            with open(ACTIVE_MARKER, "r", encoding="utf-8") as f:
                rel = f.read().strip()
            p = os.path.join(DATA_DIR, rel)
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return None


def write_active_marker(path_abs: str):
    """Write the active file marker."""
    rel = os.path.relpath(path_abs, DATA_DIR)
    with open(ACTIVE_MARKER, "w", encoding="utf-8") as f:
        f.write(rel)


def find_data_file() -> str | None:
    """Return the active file if set, else the most recent CSV/XLSX/XLS in /data."""
    ensure_data_dir()
    active = read_active_marker()
    if active and os.path.exists(active):
        return active
    latest = latest_data_file()
    if latest:
        return latest
    return None


def load_data() -> pd.DataFrame:
    """Load the current dataset (preserve original headers)."""
    # In Vercel, return empty dataframe for now
    if os.environ.get('VERCEL'):
        return pd.DataFrame()

    fpath = find_data_file()
    if not fpath:
        print("[DATA] No CSV or Excel file found in /data")
        return pd.DataFrame()

    # Store active file in session
    session['active_file'] = fpath

    try:
        ext = os.path.splitext(fpath)[1].lower()
        if ext == ".csv":
            try:
                df = pd.read_csv(fpath, dtype=str, encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(fpath, dtype=str, encoding="cp1252")
        elif ext in (".xlsx", ".xls"):
            # Requires openpyxl for .xlsx
            df = pd.read_excel(fpath, dtype=str)
        else:
            print(f"[DATA] Unsupported file type: {ext}")
            return pd.DataFrame()

        # Normalize cell values to strings; keep header names as-is
        for c in df.columns:
            try:
                df[c] = df[c].astype(str).fillna("")
            except Exception:
                pass

        print(f"[DATA] Loaded {len(df)} rows from {os.path.basename(fpath)}")
        return df

    except Exception as e:
        print(f"[DATA] Error reading {fpath}: {e}")
        return pd.DataFrame()


# ====================== COLUMN HELPERS ======================
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


TITLE_CANDS = ["title", "opportunity title", "notice title", "name"]
DESC_CANDS  = ["description", "details", "summary"]
DATE_CANDS  = ["date", "posted date", "publish date", "due date", "current response date"]


def detect_current_response_date_col(df: pd.DataFrame) -> str | None:
    """Detect the current response date column."""
    lowered = {str(c).strip().lower(): c for c in df.columns}
    if "current response date" in lowered:
        return lowered["current response date"]
    return _find_col(df, DATE_CANDS)


def add_highlight_summary_column(df: pd.DataFrame) -> pd.DataFrame:
    """Add a 'Highlight Summary' column after Description."""
    if df.empty:
        return df

    # Make a copy to avoid modifying the original
    df_copy = df.copy()

    # Find the description column
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

    # Populate existing AI summaries if we have a Notice ID column
    notice_col = _find_notice_col(df_copy)
    if notice_col:
        # Load existing AI summaries
        ai_summaries = load_ai_summaries()

        # Populate the Highlight Summary column with existing AI summaries
        for idx, row in df_copy.iterrows():
            notice_id = str(row.get(notice_col, "")).strip()
            if notice_id and notice_id in ai_summaries:
                existing_summary = ai_summaries[notice_id].get("summary", "")
                if existing_summary:
                    df_copy.at[idx, "Highlight Summary"] = existing_summary
                    print(f"[AI] Loaded existing summary for Notice ID: {notice_id}")

    return df_copy


def generate_ai_summary(description_text: str) -> str:
    """Generate a 5-bullet point AI summary of the description text using OpenAI."""
    if not OPENAI_API_KEY or not description_text or not description_text.strip():
        return ""

    try:
        # Clean and prepare the description text
        clean_description = description_text.strip()
        if len(clean_description) < 50:  # Too short to summarize meaningfully
            return ""

        # Create the OpenAI client
        client = openai.OpenAI(api_key=OPENAI_API_KEY)

        # Prepare the prompt for summarization
        prompt = f"""Please analyze the following government contract opportunity description and create exactly 5 key bullet points that summarize the most important aspects. Focus on:
1. What the contract is for (main purpose/objective)
2. Key requirements or specifications
3. Important deliverables or outcomes
4. Relevant technical details or constraints
5. Any unique or notable aspects

Format your response as exactly 5 bullet points, each starting with "• " and ending with a period.

Description to analyze:
{clean_description}"""

        # Make the API call
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert at analyzing government contract opportunities and creating concise, informative summaries for procurement professionals."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.3,
            top_p=0.9
        )

        # Extract and clean the response
        summary = response.choices[0].message.content.strip()

        # Ensure we have proper bullet points
        if summary and not summary.startswith("•"):
            # If response doesn't start with bullets, try to format it
            lines = [line.strip() for line in summary.split('\n') if line.strip()]
            if lines:
                summary = '\n'.join([f"• {line}" if not line.startswith('•') else line for line in lines[:5]])

        return summary

    except Exception as e:
        print(f"[AI] Error generating summary: {str(e)}")
        return ""


# ====================== AI SUMMARIES PERSISTENCE ======================
def load_ai_summaries() -> dict:
    """Load AI summaries from JSON file."""
    if not os.path.exists(AI_SUMMARIES_FILE):
        return {}

    try:
        with open(AI_SUMMARIES_FILE, 'r', encoding='utf-8') as f:
            summaries = json.load(f)
        print(f"[AI] Loaded {len(summaries)} AI summaries from {AI_SUMMARIES_FILE}")
        return summaries
    except Exception as e:
        print(f"[AI] Error loading AI summaries: {str(e)}")
        return {}


def save_ai_summaries(summaries: dict):
    """Save AI summaries to JSON file."""
    try:
        # Ensure data directory exists
        os.makedirs(DATA_DIR, exist_ok=True)

        with open(AI_SUMMARIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(summaries, f, indent=2, ensure_ascii=False)
        print(f"[AI] Saved {len(summaries)} AI summaries to {AI_SUMMARIES_FILE}")
    except Exception as e:
        print(f"[AI] Error saving AI summaries: {str(e)}")


def save_ai_summary_for_notice(notice_id: str, summary: str):
    """Save a single AI summary for a specific Notice ID."""
    if not notice_id or not summary:
        return

    summaries = load_ai_summaries()
    summaries[notice_id] = {
        "summary": summary,
        "timestamp": datetime.now().isoformat()
    }
    save_ai_summaries(summaries)


def get_ai_summary_for_notice(notice_id: str) -> str:
    """Get AI summary for a specific Notice ID."""
    if not notice_id:
        return ""

    summaries = load_ai_summaries()
    summary_data = summaries.get(notice_id, {})
    return summary_data.get("summary", "")


# ====================== MY SOLICITATIONS HELPERS ======================
def load_my_data(columns_fallback=None) -> pd.DataFrame:
    """Load My Solicitations; if missing, return empty (optionally with fallback columns)."""
    if os.path.exists(MY_FILE):
        try:
            df = pd.read_excel(MY_FILE, dtype=str)
            for c in df.columns:
                df[c] = df[c].astype(str).fillna("")
            return df
        except Exception as e:
            print(f"[MY] Error reading {MY_FILE}: {e}")
    if columns_fallback:
        return pd.DataFrame(columns=columns_fallback)
    return pd.DataFrame()


def save_my_data(df: pd.DataFrame):
    """Save My Solicitations data."""
    try:
        # Create backup before saving
        if os.path.exists(MY_FILE):
            create_backup(MY_FILE)
        
        out = df.copy()
        for c in out.columns:
            out[c] = out[c].astype(str).fillna("")
        out.to_excel(MY_FILE, index=False)
        print(f"[MY] Saved {len(out)} rows -> {os.path.basename(MY_FILE)}")
    except Exception as e:
        print(f"[MY] Error saving {MY_FILE}: {e}")


# ====================== NOTICE ID HELPERS ======================
def _normalize(s: str) -> str:
    """Normalize string for comparison."""
    return _re.sub(r"[^a-z0-9]+", "", str(s).strip().lower())


def _find_notice_col(dfx):
    """Find the notice ID column in a dataframe."""
    if dfx is None or dfx.empty:
        return None
    cand = [("notice id", 10), ("notice_id", 9), ("noticeid", 9),
            ("notice no", 8), ("notice number", 8),
            ("opportunityid", 7), ("oppid", 7), ("id", 5)]
    norms = {_normalize(c): c for c in dfx.columns}
    for key,_ in cand:
        nk = _normalize(key)
        if nk in norms:
            return norms[nk]
    for nk, orig in norms.items():
        if "notice" in nk and "id" in nk:
            return orig
    return None


def _match_row_by_notice(dfx, notice_id):
    """Match a row by notice ID."""
    if dfx is None or dfx.empty:
        return None
    col = _find_notice_col(dfx)
    if not col:
        return None
    tgt = _normalize(notice_id)
    try:
        m = dfx[dfx[col].astype(str).map(_normalize) == tgt]
        if not m.empty:
            return m.iloc[0].to_dict()
    except Exception:
        pass
    try:
        m = dfx[dfx[col].astype(str).str.contains(str(notice_id), case=False, na=False, regex=True)]
        if not m.empty:
            return m.iloc[0].to_dict()
    except Exception:
        pass
    return None


# ====================== SAM.GOV AUTOMATION HELPERS ======================
def _sanitize_folder_name(name: str) -> str:
    """Sanitize folder name for filesystem."""
    name = (name or "").strip()
    name = _re.sub(r"[\\/:*?\"<>|]+", " ", name)
    name = _re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "Untitled"
    return name[:120]


def _create_contract_folder(job_title: str) -> str:
    """Create a folder for contract documents."""
    folder = os.path.join(CONTRACTS_BASE, _sanitize_folder_name(job_title))
    os.makedirs(folder, exist_ok=True)
    return folder


def _has_temp_download(dirpath: str):
    """Check if there are temporary download files."""
    p = _Path(dirpath)
    return any(p.glob("*.crdownload")) or any(p.glob("*.tmp")) or any(p.glob("*.partial"))


def _newest_pdf(dirpath: str):
    """Get the newest PDF file in directory."""
    p = _Path(dirpath)
    pdfs = list(p.glob("*.pdf"))
    if not pdfs: 
        return None
    return str(max(pdfs, key=lambda q: q.stat().st_mtime))


def _list_non_temp_files(dirpath: str):
    """List non-temporary files in directory."""
    p = _Path(dirpath)
    return [str(x) for x in p.glob("*") if x.is_file() and not any(str(x).endswith(ext) for ext in (".crdownload",".tmp",".partial"))]


# ====================== PERSISTENT BROWSER SESSION ======================
def _get_persistent_edge_driver(download_dir: str):
    """Get or create a persistent Edge driver with dedicated automation profile"""
    global _persistent_driver, _session_start_time
    
    # Check if we need to create a new session
    current_time = time.time()
    needs_new_session = (
        _persistent_driver is None or
        _session_start_time is None or
        (current_time - _session_start_time) > _session_timeout or
        (current_time - _session_start_time) > _max_session_age  # Force refresh for security
    )
    
    # Try to check if existing driver is still alive
    if _persistent_driver and not needs_new_session:
        try:
            # Test if driver is still responsive
            _persistent_driver.current_url
            print("[SAM] Using existing Edge automation session")
            
            # Update download directory for this run
            try:
                _persistent_driver.execute_cdp_cmd('Page.setDownloadBehavior', {
                    'behavior': 'allow',
                    'downloadPath': download_dir
                })
            except Exception:
                # Fallback if CDP command fails
                pass
            
            return _persistent_driver
        except Exception:
            print("[SAM] Existing session is dead, creating new one")
            needs_new_session = True
            _persistent_driver = None
    
    if needs_new_session:
        print("[SAM] Creating new persistent Edge automation session...")
        
        # Close old driver if it exists
        if _persistent_driver:
            try:
                _persistent_driver.quit()
            except Exception:
                pass
            _persistent_driver = None
        
        # Create dedicated automation profile directory
        try:
            # Create a persistent automation profile directory (not temporary)
            automation_profile_dir = os.path.join(os.path.expanduser("~"), "EdgeAutomation")
            os.makedirs(automation_profile_dir, exist_ok=True)
            
            options = EdgeOptions()
            
            # Use dedicated automation profile (separate from your normal Edge)
            options.add_argument(f"--user-data-dir={automation_profile_dir}")
            options.add_argument("--profile-directory=SAMAutomation")
            
            # Window positioning to not interfere with your browsing
            options.add_argument("--window-size=1200,800")
            options.add_argument("--window-position=200,100")
            
            # Essential options for stability and security
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            # Removed --disable-web-security and --allow-running-insecure-content for security
            # Removed --no-sandbox - use proper sandboxing for security
            
            # Configure downloads
            prefs = {
                "download.default_directory": download_dir,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
                "profile.default_content_settings.popups": 0,
                "profile.default_content_setting_values.automatic_downloads": 1
            }
            options.add_experimental_option("prefs", prefs)
            
            # Hide automation indicators
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Add user agent to look more like normal browsing
            options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0")
            
            print(f"[SAM] Starting Edge with automation profile: {automation_profile_dir}")
            
            # Create the driver
            try:
                _persistent_driver = webdriver.Edge(options=options)
                print("[SAM] Edge automation session created successfully")
            except Exception as e1:
                print(f"[SAM] Selenium Manager failed: {e1}")
                # Try with explicit service
                try:
                    service = EdgeService()
                    _persistent_driver = webdriver.Edge(service=service, options=options)
                    print("[SAM] Edge automation session created with EdgeService")
                except Exception as e2:
                    print(f"[SAM] EdgeService also failed: {e2}")
                    raise e2
            
            _session_start_time = current_time
            
            # Navigate to SAM.gov immediately after creating the session
            print("[SAM] Navigating to SAM.gov...")
            _persistent_driver.get("https://sam.gov/")
            time.sleep(3)
            
            return _persistent_driver
            
        except Exception as e:
            print(f"[SAM] Failed to create Edge automation session: {e}")
            raise e
    
    return _persistent_driver


def _ensure_sam_login(driver, wait):
    """Ensure user is logged into SAM.gov, prompt if needed"""
    
    print("[SAM] Checking SAM.gov login status...")
    
    # Make sure we're on SAM.gov
    current_url = driver.current_url
    if "sam.gov" not in current_url.lower():
        print("[SAM] Navigating to SAM.gov...")
        driver.get("https://sam.gov/")
        time.sleep(5)  # Give more time for page to load
    
    # Check if we're logged in by looking for login indicators
    try:
        # Look for common login indicators
        login_indicators = [
            "//button[contains(text(), 'Sign In')]",
            "//a[contains(text(), 'Sign In')]",
            "//a[contains(text(), 'Log In')]",
            "//button[contains(text(), 'Log In')]",
            "//a[contains(@href, 'login')]",
            "//button[contains(@class, 'login')]"
        ]
        
        login_needed = False
        for indicator in login_indicators:
            try:
                elements = driver.find_elements(By.XPATH, indicator)
                if elements and elements[0].is_displayed():
                    login_needed = True
                    print(f"[SAM] Login required - found indicator: {indicator}")
                    break
            except Exception:
                continue
        
        if login_needed:
            print("[SAM] ============================================")
            print("[SAM] LOGIN REQUIRED")
            print("[SAM] Please log in to SAM.gov in the browser window")
            print("[SAM] The automation will wait for you to complete login")
            print("[SAM] ============================================")
            
            # Bring the browser window to front
            try:
                driver.maximize_window()
                driver.switch_to.window(driver.current_window_handle)
            except Exception:
                pass
            
            # Wait for login to complete
            login_complete = False
            max_wait_time = 600  # 10 minutes
            start_wait = time.time()
            check_interval = 3  # Check every 3 seconds
            
            while not login_complete and (time.time() - start_wait) < max_wait_time:
                time.sleep(check_interval)
                
                # Check if login indicators are gone
                still_needs_login = False
                for indicator in login_indicators:
                    try:
                        elements = driver.find_elements(By.XPATH, indicator)
                        if elements and elements[0].is_displayed():
                            still_needs_login = True
                            break
                    except Exception:
                        continue
                
                if not still_needs_login:
                    login_complete = True
                    print("[SAM] Login detected as complete!")
                    break
                
                # Also check for profile/account indicators that suggest login
                profile_indicators = [
                    "//span[contains(text(), 'Account')]",
                    "//span[contains(text(), 'Profile')]",
                    "//button[contains(@aria-label, 'Account')]",
                    "//*[contains(text(), 'Welcome')]",
                    "//button[contains(@aria-label, 'User')]",
                    "//*[contains(@class, 'user-menu')]"
                ]
                
                for indicator in profile_indicators:
                    try:
                        elements = driver.find_elements(By.XPATH, indicator)
                        if elements and elements[0].is_displayed():
                            login_complete = True
                            print("[SAM] Login detected via profile indicator!")
                            break
                    except Exception:
                        continue
                
                if login_complete:
                    break
                
                # Show progress every 30 seconds
                elapsed = time.time() - start_wait
                if int(elapsed) % 30 == 0:
                    remaining = max_wait_time - elapsed
                    print(f"[SAM] Still waiting for login... {remaining:.0f} seconds remaining")
            
            if not login_complete:
                raise RuntimeError("Login timeout - please ensure you're logged into SAM.gov and try again")
            
            print("[SAM] Login successful!")
        
        else:
            print("[SAM] Already logged in to SAM.gov")
    
    except Exception as e:
        print(f"[SAM] Login check failed: {e}")
        # Continue anyway - maybe we're logged in but indicators changed
        print("[SAM] Continuing with automation...")
    
    time.sleep(2)  # Give page time to settle after login


def _extract_links_and_attachments_info(driver, wait):
    """Extract links and attachment information from SAM.gov opportunity page"""
    result = {"links": [], "attachments": []}
    
    try:
        # Navigate to Attachments/Links tab
        print("[SAM] Looking for Attachments/Links tab...")
        
        tab_candidates = [
            "//a[normalize-space()='Attachments/Links']",
            "//button[normalize-space()='Attachments/Links']",
            "//a[contains(text(),'Attachments/Links')]",
            "//button[contains(text(),'Attachments/Links')]",
            "//a[contains(text(),'Attachments')]",
            "//button[contains(text(),'Attachments')]",
            "//*[@role='tab'][contains(text(),'Attachments')]",
            "//*[contains(@class,'tab')][contains(text(),'Attachments')]"
        ]
        
        tab = None
        for xpath in tab_candidates:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                if elements:
                    tab = elements[0]
                    print(f"[SAM] Found tab with xpath: {xpath}")
                    break
            except Exception:
                continue
        
        if tab is None:
            print("[SAM] Attachments/Links tab not found")
            return result
        
        # Click the tab
        driver.execute_script("arguments[0].click();", tab)
        time.sleep(3)  # Wait for content to load
        
        # Extract external links
        print("[SAM] Extracting external links...")
        try:
            link_elements = driver.find_elements(By.XPATH, "//a[@href]")
            for link_el in link_elements:
                try:
                    href = link_el.get_attribute('href')
                    text = link_el.text.strip()
                    
                    # Filter for external links (not sam.gov internal links)
                    if href and href.startswith('http') and 'sam.gov' not in href:
                        if href not in [item['url'] for item in result['links']]:
                            result['links'].append({
                                'url': href,
                                'text': text or href,
                                'type': 'external'
                            })
                except Exception as e:
                    print(f"[SAM] Error processing link: {e}")
                    
        except Exception as e:
            print(f"[SAM] Error extracting links: {e}")
        
        # Extract attachment information
        print("[SAM] Extracting attachment information...")
        try:
            # Look for file elements with various selectors
            file_selectors = [
                "//a[contains(@href, '.pdf')]",
                "//a[contains(@href, '.doc')]", 
                "//a[contains(@href, '.xlsx')]",
                "//a[contains(@href, '.zip')]",
                "//span[contains(text(), '.pdf')]",
                "//span[contains(text(), '.doc')]",
                "//span[contains(text(), '.xlsx')]",
                "//div[contains(@class, 'attachment')]",
                "//div[contains(@class, 'file')]"
            ]
            
            for selector in file_selectors:
                try:
                    elements = driver.find_elements(By.XPATH, selector)
                    for el in elements:
                        try:
                            text = el.text.strip()
                            href = el.get_attribute('href') or ''
                            
                            # Extract filename
                            filename = ''
                            file_extensions = ['.pdf', '.doc', '.docx', '.xlsx', '.xls', '.zip', '.txt']
                            
                            if text and any(ext in text.lower() for ext in file_extensions):
                                filename = text
                            elif href and any(ext in href.lower() for ext in file_extensions):
                                filename = href.split('/')[-1]
                            
                            if filename and filename not in [item['filename'] for item in result['attachments']]:
                                file_size = ""
                                # Try to find file size in nearby elements
                                try:
                                    parent = el.find_element(By.XPATH, "./..")
                                    size_text = parent.text
                                    if 'KB' in size_text or 'MB' in size_text or 'bytes' in size_text:
                                        size_match = _re.search(r'(\d+(?:\.\d+)?\s*(?:KB|MB|bytes))', size_text, _re.IGNORECASE)
                                        if size_match:
                                            file_size = size_match.group(1)
                                except Exception:
                                    pass
                                
                                result['attachments'].append({
                                    'filename': filename,
                                    'url': href,
                                    'text': text,
                                    'size': file_size,
                                    'type': 'file'
                                })
                        except Exception as e:
                            print(f"[SAM] Error processing attachment element: {e}")
                except Exception as e:
                    print(f"[SAM] Error with selector {selector}: {e}")
                    
        except Exception as e:
            print(f"[SAM] Error extracting attachments: {e}")
        
        print(f"[SAM] Extracted {len(result['links'])} links and {len(result['attachments'])} attachments")
        
    except Exception as e:
        print(f"[SAM] Error in link/attachment extraction: {e}")
    
    return result


def _download_attachments_on_page(driver, download_dir: str, wait):
    """Download attachments from the current page"""
    before = set(_list_non_temp_files(download_dir))
    
    try:
        # Look for Download All button
        download_all_selectors = [
            "//button[normalize-space()='Download All' or contains(.,'Download All')]",
            "//a[normalize-space()='Download All' or contains(.,'Download All')]",
            "//button[contains(text(), 'Download All')]"
        ]
        
        dl_all = None
        for selector in download_all_selectors:
            try:
                dl_all = driver.find_element(By.XPATH, selector)
                break
            except Exception:
                continue
        
        if dl_all:
            driver.execute_script("arguments[0].click();", dl_all)
        else:
            # Try via menu
            try:
                menu = driver.find_element(By.XPATH, "//button[contains(@aria-label,'More') or contains(.,'More')]")
                driver.execute_script("arguments[0].click();", menu)
                dl_all = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//*[normalize-space()='Download All']")))
                driver.execute_script("arguments[0].click();", dl_all)
            except Exception as e2:
                raise RuntimeError("Download All button not found.") from e2

        # Wait for downloads to finish
        start = time.time()
        while time.time() - start < 240:
            time.sleep(1)
            if _has_temp_download(download_dir):
                continue
            after = set(_list_non_temp_files(download_dir))
            new = list(after - before)
            if new:
                return new
        
        # Return empty list if no new files (don't raise error)
        return []
        
    except Exception as e:
        print(f"[SAM] Attachment download failed: {e}")
        return []


def _sam_download_with_persistent_session(notice_id: str, job_title: str, download_dir: str, timeout_secs=300):
    """SAM.gov automation using persistent Edge session with login maintenance"""
    
    if not _SELENIUM_AVAILABLE:
        raise RuntimeError("Selenium not installed. Run: pip install selenium")
    
    print(f"[SAM] Starting automation for Notice ID: {notice_id}")
    print(f"[SAM] Download directory: {download_dir}")
    
    # Get persistent driver (maintains login across runs)
    driver = _get_persistent_edge_driver(download_dir)
    wait = WebDriverWait(driver, 30)
    
    try:
        # Ensure we're logged into SAM.gov
        _ensure_sam_login(driver, wait)
        
        # Now proceed with the automation
        print(f"[SAM] Searching for notice ID: {notice_id}")
        
        # Find search input
        search = None
        search_selectors = [
            "input[placeholder*='search' i]",
            "input[type='search']",
            "input[aria-label*='search' i]",
            "#search-input",
            ".search-input",
            "input[name*='search']"
        ]
        
        for selector in search_selectors:
            try:
                search = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                print(f"[SAM] Found search input with selector: {selector}")
                break
            except Exception:
                continue
        
        if not search:
            # Fallback: look for any input that might be the search
            inputs = driver.find_elements(By.TAG_NAME, "input")
            for inp in inputs:
                placeholder = (inp.get_attribute('placeholder') or '').lower()
                aria_label = (inp.get_attribute('aria-label') or '').lower()
                if 'search' in placeholder or 'search' in aria_label:
                    search = inp
                    break
        
        if not search:
            raise RuntimeError("Could not find search input on SAM.gov")
        
        # Clear any existing search and perform new search
        try:
            search.clear()
            search.send_keys(str(notice_id))
            search.send_keys(Keys.ENTER)
        except Exception as e:
            print(f"[SAM] Search input failed, trying click approach: {e}")
            # Alternative: click the search input first
            driver.execute_script("arguments[0].click();", search)
            time.sleep(1)
            driver.execute_script("arguments[0].value = '';", search)
            search.send_keys(str(notice_id))
            search.send_keys(Keys.ENTER)
        
        # Wait for search results
        print("[SAM] Waiting for search results...")
        time.sleep(5)
        
        # Find the opportunity link
        opportunity_links = driver.find_elements(By.XPATH, "//a[contains(@href, '/opp/')]")
        
        if not opportunity_links:
            # Try alternative selectors
            opportunity_links = driver.find_elements(By.XPATH, "//a[contains(@href, 'opportunity')]")
        
        if not opportunity_links:
            raise RuntimeError("No opportunity links found in search results. You may need to refine the search or check if you're on the right page.")
        
        # Select the best matching link
        target_link = opportunity_links[0]  # Take the first one
        
        print(f"[SAM] Found opportunity: {target_link.text.strip()}")
        
        # Click the link using JavaScript to avoid interception
        driver.execute_script("arguments[0].click();", target_link)
        
        # Wait for opportunity page to load
        time.sleep(5)
        
        # Extract links and attachments information
        print("[SAM] Extracting links and attachments information...")
        links_and_attachments = _extract_links_and_attachments_info(driver, wait)
        
        # Download main PDF
        main_pdf = None
        try:
            print("[SAM] Looking for download options...")
            
            # Look for More/Actions menu with various selectors
            more_selectors = [
                "//button[contains(@aria-label, 'More')]",
                "//button[contains(text(), 'More')]", 
                "//button[contains(text(), '⋯')]",
                "//button[contains(@aria-label, 'Actions')]",
                "//button[contains(text(), 'Actions')]",
                "//button[contains(@class, 'more')]",
                "//*[contains(@role, 'button') and contains(., 'More')]"
            ]
            
            more_button = None
            for selector in more_selectors:
                try:
                    buttons = driver.find_elements(By.XPATH, selector)
                    if buttons:
                        more_button = buttons[0]
                        print(f"[SAM] Found More button with selector: {selector}")
                        break
                except Exception:
                    continue
            
            if more_button:
                print("[SAM] Clicking More menu...")
                driver.execute_script("arguments[0].click();", more_button)
                time.sleep(2)
                
                # Look for Download option
                download_selectors = [
                    "//button[contains(text(), 'Download')]",
                    "//a[contains(text(), 'Download')]",
                    "//li[contains(text(), 'Download')]",
                    "//*[contains(@role, 'menuitem') and contains(text(), 'Download')]"
                ]
                
                download_button = None
                for selector in download_selectors:
                    try:
                        buttons = driver.find_elements(By.XPATH, selector)
                        if buttons:
                            download_button = buttons[0]
                            print(f"[SAM] Found Download button with selector: {selector}")
                            break
                    except Exception:
                        continue
                
                if download_button:
                    print("[SAM] Clicking Download option...")
                    driver.execute_script("arguments[0].click();", download_button)
                    time.sleep(2)
                    
                    # Look for PDF option if available
                    pdf_selectors = [
                        "//input[@value='PDF']",
                        "//button[contains(text(), 'PDF')]",
                        "//label[contains(text(), 'PDF')]",
                        "//*[contains(text(), 'PDF') and (@type='radio' or @role='radio')]"
                    ]
                    
                    pdf_option = None
                    for selector in pdf_selectors:
                        try:
                            options = driver.find_elements(By.XPATH, selector)
                            if options:
                                pdf_option = options[0]
                                print(f"[SAM] Found PDF option with selector: {selector}")
                                break
                        except Exception:
                            continue
                    
                    if pdf_option:
                        print("[SAM] Selecting PDF option...")
                        driver.execute_script("arguments[0].click();", pdf_option)
                        time.sleep(1)
                    
                    # Click final download/submit button
                    final_selectors = [
                        "//button[@type='submit' and contains(text(), 'Download')]",
                        "//button[contains(@class, 'btn') and contains(text(), 'Download')]",
                        "//input[@type='submit' and contains(@value, 'Download')]",
                        "//button[contains(text(), 'Submit')]"
                    ]
                    
                    final_button = None
                    for selector in final_selectors:
                        try:
                            buttons = driver.find_elements(By.XPATH, selector)
                            if buttons:
                                final_button = buttons[0]
                                print(f"[SAM] Found final download button with selector: {selector}")
                                break
                        except Exception:
                            continue
                    
                    if final_button:
                        print("[SAM] Triggering final download...")
                        driver.execute_script("arguments[0].click();", final_button)
                        
                        # Wait for download
                        print("[SAM] Waiting for PDF download...")
                        start_time = time.time()
                        
                        while time.time() - start_time < timeout_secs:
                            time.sleep(2)
                            
                            if _has_temp_download(download_dir):
                                continue
                            
                            newest_pdf = _newest_pdf(download_dir)
                            if newest_pdf:
                                # Rename to standard format
                                try:
                                    new_name = os.path.join(download_dir, f"SAM_{notice_id}.pdf")
                                    if os.path.abspath(newest_pdf) != os.path.abspath(new_name):
                                        shutil.move(newest_pdf, new_name)
                                        newest_pdf = new_name
                                except Exception as e:
                                    print(f"[SAM] Could not rename PDF: {e}")
                                
                                main_pdf = newest_pdf
                                print(f"[SAM] Downloaded PDF: {main_pdf}")
                                break
                        
                        if not main_pdf:
                            print("[SAM] PDF download timed out or failed")
                    else:
                        print("[SAM] Could not find final download button")
                else:
                    print("[SAM] Could not find Download option in menu")
            else:
                print("[SAM] Could not find More/Actions menu")
                
        except Exception as e:
            print(f"[SAM] PDF download failed: {e}")
        
        # Download additional attachments
        downloaded_attachments = []
        try:
            print("[SAM] Attempting to download additional attachments...")
            downloaded_attachments = _download_attachments_on_page(driver, download_dir, wait)
        except Exception as e:
            print(f"[SAM] Additional attachments download failed: {e}")
        
        print(f"[SAM] Automation completed!")
        print(f"[SAM] Main PDF: {main_pdf}")
        print(f"[SAM] Additional files: {len(downloaded_attachments)}")
        print(f"[SAM] Links found: {len(links_and_attachments.get('links', []))}")
        print(f"[SAM] Attachments detected: {len(links_and_attachments.get('attachments', []))}")
        
        return {
            "pdf": main_pdf,
            "attachments": downloaded_attachments,
            "links_info": links_and_attachments.get("links", []),
            "attachments_info": links_and_attachments.get("attachments", [])
        }
        
    except Exception as e:
        print(f"[SAM] Automation error: {e}")
        # Don't quit the driver on error - keep session alive for next attempt
        raise e


def _cleanup_persistent_session():
    """Clean up the persistent browser session"""
    global _persistent_driver, _session_start_time
    
    if _persistent_driver:
        try:
            _persistent_driver.quit()
            print("[SAM] Persistent Edge session closed")
        except Exception:
            pass
        _persistent_driver = None
        _session_start_time = None


# ====================== FLASK ROUTES ======================
@app.route("/")
def index():
    """Main index page."""
    df = load_data()
    if not df.empty:
        df = add_highlight_summary_column(df)
    columns = list(df.columns) if not df.empty else []
    return render_template(
        "index.html",
        columns=columns,
        total_count=len(df),
        solicitations=df.to_dict(orient="records")
    )


@app.route("/filter", methods=["POST"])
def filter_data():
    """Filter the data based on keyword and date criteria."""
    df = load_data()
    if df.empty:
        return jsonify({"count": 0, "columns": [], "solicitations": []})

    # Add the Highlight Summary column
    df = add_highlight_summary_column(df)

    payload = request.get_json(silent=True) or {}
    keyword = (payload.get("keyword") or "").strip()
    date_filter = payload.get("date_filter") or []  # list of date strings

    title_col = _find_col(df, TITLE_CANDS)
    desc_col  = _find_col(df, DESC_CANDS)
    resp_date_col = detect_current_response_date_col(df)

    filtered = df

    # Keyword across Title/Description
    if keyword and (title_col or desc_col):
        mask = False
        if title_col:
            mask = filtered[title_col].astype(str).str.contains(keyword, case=False, na=False)
        if desc_col:
            mask = mask | filtered[desc_col].astype(str).str.contains(keyword, case=False, na=False)
        filtered = filtered[mask]

    # Date filter on "Current Response Date" (if present and dates selected)
    if resp_date_col and date_filter and len(date_filter) > 0:
        # Parse dates from the filtered data
        def parse_date_for_comparison(date_str):
            """Parse date and return it in MM/DD/YYYY format for comparison."""
            if not date_str or pd.isna(date_str):
                return None

            try:
                # Try parsing with pandas
                parsed = pd.to_datetime(str(date_str), errors='coerce')
                if pd.isna(parsed):
                    return None
                return parsed.strftime('%m/%d/%Y')
            except:
                return None

        # Convert all dates in the column to MM/DD/YYYY format for comparison
        filtered['_temp_date_formatted'] = filtered[resp_date_col].apply(parse_date_for_comparison)

        # Filter by selected dates
        date_mask = filtered['_temp_date_formatted'].isin(date_filter)
        filtered = filtered[date_mask]

        # Remove the temporary column
        filtered = filtered.drop('_temp_date_formatted', axis=1)

    return jsonify({
        "count": int(len(filtered)),
        "columns": list(df.columns),  # preserve original header order
        "solicitations": filtered.to_dict(orient="records")
    })


@app.route("/upload-data", methods=["POST"])
def upload_data():
    """Upload a CSV/XLSX into /data and set it as the active dataset."""
    ensure_data_dir()

    # Input validation
    if "file" not in request.files:
        return jsonify({"ok": False, "message": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"ok": False, "message": "No file selected"}), 400

    # Validate file size (limit to 50MB for security)
    if file.content_length and file.content_length > 50 * 1024 * 1024:
        return jsonify({"ok": False, "message": "File too large (max 50MB)"}), 400

    fname = secure_filename(file.filename)
    if not fname:  # secure_filename can return empty string for malicious names
        return jsonify({"ok": False, "message": "Invalid filename"}), 400

    if not is_allowed(fname):
        return jsonify({"ok": False, "message": "Unsupported file type"}), 400

    # Additional filename validation
    if len(fname) > 255:
        return jsonify({"ok": False, "message": "Filename too long"}), 400

    save_path = os.path.join(DATA_DIR, fname)
    if os.path.exists(save_path):
        stem, ext = os.path.splitext(fname)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{stem}_{ts}{ext}"
        save_path = os.path.join(DATA_DIR, fname)

    try:
        file.save(save_path)
        write_active_marker(os.path.abspath(save_path))
        session['active_file'] = save_path

        df = load_data()
        return jsonify({"ok": True, "saved_as": fname, "rows": int(len(df))})
    except Exception as e:
        logger.error(f"File upload error: {e}")
        # Clean up partial file if it exists
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except:
                pass
        return jsonify({"ok": False, "message": "File upload failed"}), 500


@app.route("/export", methods=["POST"])
def export_filtered():
    """Export the currently filtered rows to an Excel download."""
    df = load_data()
    if df.empty:
        return "No data to export", 400

    # Add the Highlight Summary column
    df = add_highlight_summary_column(df)

    payload = request.get_json(silent=True) or {}
    keyword = (payload.get("keyword") or "").strip()
    date_filter = payload.get("date_filter") or []

    title_col  = _find_col(df, TITLE_CANDS)
    desc_col   = _find_col(df, DESC_CANDS)
    resp_date_col = detect_current_response_date_col(df)

    filtered = df

    if keyword and (title_col or desc_col):
        mask = False
        if title_col:
            mask = filtered[title_col].astype(str).str.contains(keyword, case=False, na=False)
        if desc_col:
            mask = mask | filtered[desc_col].astype(str).str.contains(keyword, case=False, na=False)
        filtered = filtered[mask]

    # Date filter on "Current Response Date" (if present and dates selected)
    if resp_date_col and date_filter and len(date_filter) > 0:
        def parse_date_for_comparison(date_str):
            """Parse date and return it in MM/DD/YYYY format for comparison."""
            if not date_str or pd.isna(date_str):
                return None

            try:
                # Try parsing with pandas
                parsed = pd.to_datetime(str(date_str), errors='coerce')
                if pd.isna(parsed):
                    return None
                return parsed.strftime('%m/%d/%Y')
            except:
                return None

        # Convert all dates in the column to MM/DD/YYYY format for comparison
        filtered['_temp_date_formatted'] = filtered[resp_date_col].apply(parse_date_for_comparison)

        # Filter by selected dates
        date_mask = filtered['_temp_date_formatted'].isin(date_filter)
        filtered = filtered[date_mask]

        # Remove the temporary column
        filtered = filtered.drop('_temp_date_formatted', axis=1)

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        filtered.to_excel(writer, index=False, sheet_name="Filtered")
    bio.seek(0)
    fname = f"Government_Contracts_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        bio,
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/my-solicitations")
def my_solicitations():
    """Render the My Solicitations page."""
    base_cols = list(load_data().columns)  # fallback to current dataset's columns
    df = load_my_data(columns_fallback=base_cols)
    if not df.empty:
        df = add_highlight_summary_column(df)
    return render_template(
        "my_solicitations.html",
        columns=list(df.columns),
        total_count=len(df),
        solicitations=df.to_dict(orient="records")
    )


@app.route("/add-to-my-solicitations", methods=["POST"])
def add_to_my_solicitations():
    """Append a single selected row (dict) to data/my_solicitations.xlsx."""
    ensure_data_dir()
    payload = request.get_json(silent=True) or {}
    row = payload.get("row")
    if not isinstance(row, dict):
        return jsonify({"ok": False, "message": "No row received"}), 400

    # Load existing "My Solicitations"
    if os.path.exists(MY_FILE):
        try:
            cur = pd.read_excel(MY_FILE, dtype=str)
        except Exception as e:
            print("[MY_SOL] Failed reading existing:", e)
            cur = pd.DataFrame()
    else:
        cur = pd.DataFrame()

    # Determine column order: prefer main data columns, else existing, else row keys
    base = load_data()
    if not base.empty:
        cols = list(base.columns)
    elif not cur.empty:
        cols = list(cur.columns)
    else:
        cols = list(row.keys())

    # Normalize row to those columns
    rec = {c: str(row.get(c, "")) for c in cols}

    # Reindex current to cols and append
    cur = cur.reindex(columns=cols)
    cur = pd.concat([cur, pd.DataFrame([rec])], ignore_index=True)

    # Save Excel
    try:
        with pd.ExcelWriter(MY_FILE, engine="openpyxl") as w:
            cur.to_excel(w, index=False)
    except Exception as e:
        print("[MY_SOL] Write failed:", e)
        return jsonify({"ok": False, "message": "Could not save My Solicitations."}), 500

    return jsonify({"ok": True, "saved": 1, "total": int(len(cur))})


@app.route("/add-solicitation", methods=["POST"])
def add_solicitation():
    """Add/Upsert a row into My Solicitations (dedupe by Notice ID if present)."""
    payload = request.get_json(silent=True) or {}
    row = payload.get("row") or {}
    columns = payload.get("columns") or []

    my_df = load_my_data(columns_fallback=columns)
    if my_df.empty and columns:
        my_df = pd.DataFrame(columns=columns)

    # Align to my_df columns (ignore any extra keys)
    to_add = {col: str(row.get(col, "")) for col in my_df.columns}

    # De-dupe by Notice ID if we can detect it
    id_col = _find_col(my_df, [
        "notice id", "solicitation id", "notice number", "solicitation number", "rfq", "reference id", "id"
    ])
    if id_col:
        new_id = str(row.get(id_col, "")).strip()
        if new_id:
            dup_mask = my_df[id_col].astype(str).str.strip().str.lower() == new_id.lower()
            if dup_mask.any():
                # Replace the first matching row
                ix = dup_mask[dup_mask].index[0]
                for k, v in to_add.items():
                    my_df.at[ix, k] = v
            else:
                my_df = pd.concat([my_df, pd.DataFrame([to_add])], ignore_index=True)
        else:
            my_df = pd.concat([my_df, pd.DataFrame([to_add])], ignore_index=True)
    else:
        my_df = pd.concat([my_df, pd.DataFrame([to_add])], ignore_index=True)

    save_my_data(my_df)
    return jsonify({"ok": True, "rows": int(len(my_df))})


@app.route("/delete-solicitation", methods=["POST"])
def delete_solicitation():
    """Delete a row from My Solicitations by Notice ID; if that fails, try full-row match."""
    payload = request.get_json(silent=True) or {}
    notice_id_value = (payload.get("notice_id") or "").strip()
    id_col_hint = (payload.get("id_col_hint") or "").strip()
    row_payload = payload.get("row") or {}

    my_df = load_my_data()
    if my_df.empty:
        return jsonify({"ok": False, "message": "No items to delete"}), 400

    # 1) Try to use the exact column the user clicked (id_col_hint)
    id_col = None
    if id_col_hint:
        # case-insensitive exact match to a column name
        lower_map = {str(c).strip().lower(): c for c in my_df.columns}
        id_col = lower_map.get(id_col_hint.strip().lower())

    # 2) If not provided/found, fall back to heuristics
    if not id_col:
        id_col = _find_col(my_df, [
            "notice id","solicitation id","notice number","solicitation number","rfq","reference id","id"
        ])

    # Try deletion by Notice ID first
    rows_before = len(my_df)
    if id_col and notice_id_value:
        mask_keep = my_df[id_col].astype(str).str.strip().str.lower() != notice_id_value.lower()
        my_df_del = my_df[mask_keep].reset_index(drop=True)
        if len(my_df_del) < rows_before:
            save_my_data(my_df_del)
            return jsonify({"ok": True, "rows": int(len(my_df_del))})

    # Fallback: delete by full-row match (all provided columns)
    if isinstance(row_payload, dict) and row_payload:
        # Align keys to existing columns and build a match mask
        mask_match = pd.Series([True] * len(my_df), index=my_df.index)
        for col in my_df.columns:
            if col in row_payload:
                left = my_df[col].astype(str).str.strip().str.lower()
                right = str(row_payload[col]).strip().lower()
                mask_match &= (left == right)

        if mask_match.any():
            my_df_del = my_df[~mask_match].reset_index(drop=True)
            save_my_data(my_df_del)
            return jsonify({"ok": True, "rows": int(len(my_df_del))})

    # Nothing matched
    return jsonify({"ok": False, "message": "Could not find a matching row to delete"}), 404


@app.route("/my-filter", methods=["POST"])
def my_filter():
    """Filter only the My Solicitations dataset (keyword search across ALL columns)."""
    base_cols = list(load_data().columns)  # fallback if file empty
    df = load_my_data(columns_fallback=base_cols)
    if df.empty:
        return jsonify({"count": 0, "columns": list(df.columns), "solicitations": []})

    # Add the Highlight Summary column
    df = add_highlight_summary_column(df)

    payload = request.get_json(silent=True) or {}
    keyword = (payload.get("keyword") or "").strip()

    print(f"[MY-SEARCH] Available columns: {list(df.columns)}")
    print(f"[MY-SEARCH] Keyword: '{keyword}'")

    filtered = df

    # Search ALL columns in the entire spreadsheet including Highlight Summary content
    if keyword:
        print(f"[MY-SEARCH] Searching ALL columns for keyword: '{keyword}'")
        mask = pd.Series([False] * len(df), index=df.index)
        matches_by_column = {}

        # Load saved highlights for searching
        highlights_data = {}
        try:
            if os.path.exists(HIGHLIGHTS_FILE):
                with open(HIGHLIGHTS_FILE, 'r', encoding='utf-8') as f:
                    highlights_data = json.load(f)
        except Exception as e:
            print(f"[MY-SEARCH] Could not load highlights: {e}")

        # Load AI summaries for searching
        ai_summaries = load_ai_summaries()

        for col in df.columns:
            try:
                if col == "Highlight Summary":
                    # Special handling for Highlight Summary column - search AI summaries and saved highlights
                    notice_col = _find_notice_col(df)
                    if notice_col:
                        highlight_mask = pd.Series([False] * len(df), index=df.index)

                        for idx, row in df.iterrows():
                            notice_id = str(row.get(notice_col, "")).strip()
                            found_match = False

                            # Search in AI summary
                            if notice_id in ai_summaries:
                                summary_text = ai_summaries[notice_id].get("summary", "")
                                if keyword.lower() in summary_text.lower():
                                    found_match = True

                            # Search in saved highlights
                            if notice_id in highlights_data:
                                highlights_text = highlights_data.get(notice_id, "")
                                if keyword.lower() in highlights_text.lower():
                                    found_match = True

                            if found_match:
                                highlight_mask.iloc[idx] = True

                        matches_count = highlight_mask.sum()
                        if matches_count > 0:
                            matches_by_column[col] = matches_count
                            mask = mask | highlight_mask
                else:
                    # Regular column search
                    col_mask = df[col].astype(str).str.contains(keyword, case=False, na=False, regex=False)
                    matches_count = col_mask.sum()
                    if matches_count > 0:
                        matches_by_column[col] = matches_count
                        mask = mask | col_mask
            except Exception as e:
                print(f"[MY-SEARCH] Could not search column '{col}': {e}")
                continue

        filtered = df[mask]
        print(f"[MY-SEARCH] Total rows with matches: {len(filtered)} out of {len(df)}")
        print(f"[MY-SEARCH] Matches by column: {matches_by_column}")
    else:
        print(f"[MY-SEARCH] No keyword provided, returning all data")

    return jsonify({
        "count": int(len(filtered)),
        "columns": list(df.columns),
        "solicitations": filtered.to_dict(orient="records")
    })


@app.route("/my-export", methods=["POST"])
def my_export():
    """Export the current My Solicitations view (keyword filter) to Excel."""
    base_cols = list(load_data().columns)
    df = load_my_data(columns_fallback=base_cols)
    if df.empty:
        return "No data to export", 400

    # Add the Highlight Summary column
    df = add_highlight_summary_column(df)

    payload = request.get_json(silent=True) or {}
    keyword = (payload.get("keyword") or "").strip()

    title_col = _find_col(df, ["title", "opportunity title", "notice title", "name"])
    desc_col  = _find_col(df, ["description", "details", "summary"])

    filtered = df
    if keyword and (title_col or desc_col):
        mask = False
        if title_col:
            mask = filtered[title_col].astype(str).str.contains(keyword, case=False, na=False)
        if desc_col:
            mask = mask | filtered[desc_col].astype(str).str.contains(keyword, case=False, na=False)
        filtered = filtered[mask]

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        filtered.to_excel(writer, index=False, sheet_name="MyFiltered")
    bio.seek(0)
    fname = f"My_Solicitations_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        bio,
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ====================== PROJECT TRACKING ROUTE ======================
@app.route('/project_tracking')
def project_tracking():
    """Display the project timeline page."""
    return render_template('project_tracking.html')


# ====================== PROJECT DATE PERSISTENCE ======================
PROJECT_DATES_FILE = os.path.join(DATA_DIR, "project_dates.json")

@app.route('/save-project-dates', methods=['POST'])
def save_project_dates():
    """Save project date changes to server storage."""
    try:
        payload = request.get_json() or {}
        notice_id = payload.get('notice_id')
        field = payload.get('field')
        value = payload.get('value')

        if not notice_id or not field:
            return jsonify({"ok": False, "message": "Missing notice_id or field"}), 400

        # Load existing dates
        dates_data = {}
        if os.path.exists(PROJECT_DATES_FILE):
            try:
                with open(PROJECT_DATES_FILE, 'r') as f:
                    dates_data = json.load(f)
            except Exception as e:
                print(f"[DATES] Error reading dates file: {e}")

        # Update dates
        if notice_id not in dates_data:
            dates_data[notice_id] = {}
        dates_data[notice_id][field] = value
        dates_data[notice_id]['last_updated'] = datetime.now().isoformat()

        # Save dates
        with open(PROJECT_DATES_FILE, 'w') as f:
            json.dump(dates_data, f, indent=2)

        print(f"[DATES] Saved {field} = {value} for {notice_id}")

        return jsonify({"ok": True, "saved": f"{field} = {value}"})

    except Exception as e:
        print(f"[DATES] Save error: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route('/get-project-dates', methods=['GET'])
def get_project_dates():
    """Get saved project dates from server storage."""
    try:
        if not os.path.exists(PROJECT_DATES_FILE):
            return jsonify({"ok": True, "dates": {}})

        with open(PROJECT_DATES_FILE, 'r') as f:
            dates_data = json.load(f)

        return jsonify({"ok": True, "dates": dates_data})

    except Exception as e:
        print(f"[DATES] Load error: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500


# ====================== HIGHLIGHTS PERSISTENCE ======================
HIGHLIGHTS_FILE = os.path.join(DATA_DIR, "solicitation_highlights.json")

@app.route('/save-highlights', methods=['POST'])
def save_highlights():
    """Save solicitation highlights to server storage."""
    try:
        payload = request.get_json() or {}
        notice_id = payload.get('notice_id')
        highlights = payload.get('highlights', '')

        if not notice_id:
            return jsonify({"ok": False, "message": "Missing notice_id"}), 400

        # Load existing highlights
        highlights_data = {}
        if os.path.exists(HIGHLIGHTS_FILE):
            try:
                with open(HIGHLIGHTS_FILE, 'r', encoding='utf-8') as f:
                    highlights_data = json.load(f)
            except Exception as e:
                print(f"[HIGHLIGHTS] Error reading highlights file: {e}")

        # Update highlights
        highlights_data[notice_id] = highlights
        highlights_data['last_updated'] = datetime.now().isoformat()

        # Save highlights
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(HIGHLIGHTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(highlights_data, f, indent=2, ensure_ascii=False)

        print(f"[HIGHLIGHTS] Saved highlights for {notice_id}: {highlights[:50]}...")

        return jsonify({"ok": True, "saved": True})

    except Exception as e:
        print(f"[HIGHLIGHTS] Save error: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route('/load-highlights', methods=['POST'])
def load_highlights():
    """Load solicitation highlights from server storage."""
    try:
        payload = request.get_json() or {}
        notice_id = payload.get('notice_id')

        if not notice_id:
            return jsonify({"ok": False, "message": "Missing notice_id"}), 400

        if not os.path.exists(HIGHLIGHTS_FILE):
            return jsonify({"ok": True, "highlights": ""})

        with open(HIGHLIGHTS_FILE, 'r', encoding='utf-8') as f:
            highlights_data = json.load(f)

        highlights = highlights_data.get(notice_id, "")
        print(f"[HIGHLIGHTS] Loaded highlights for {notice_id}: {highlights[:50]}...")

        return jsonify({"ok": True, "highlights": highlights})

    except Exception as e:
        print(f"[HIGHLIGHTS] Load error: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500


# ====================== OPPORTUNITY VIEWING ROUTES ======================
@app.route("/opportunity")
def opportunity():
    """Blank opportunity page."""
    return render_template("opportunity.html")


@app.route("/opportunity/<notice_id>")
def opportunity_by_id(notice_id):
    """View a specific opportunity by Notice ID."""
    print(f"[SAM] ContractView nid={notice_id}")
    df = load_data()
    my_df = load_my_data(columns_fallback=list(df.columns) if not df.empty else None)
    row = _match_row_by_notice(df, notice_id) or _match_row_by_notice(my_df, notice_id)

    job_title = ""
    if row:
        for key in ["Title","Opportunity Title","Notice Title","Name","Project Title","Solicitation Title","Description","Summary"]:
            if key in row and str(row[key]).strip():
                job_title = str(row[key]).strip()
                break

    sam_url = f"https://sam.gov/opp/{notice_id}/view"
    return render_template("opportunity.html",
                           notice_id=notice_id,
                           job_title=job_title,
                           record=row or {},
                           sam_url=sam_url)


# ====================== SAM.GOV AUTOMATION ROUTES ======================
@app.route('/sam-start/<notice_id>', methods=['POST','GET'])
def sam_start(notice_id):
    """Enhanced SAM automation with persistent login sessions"""
    print(f"[SAM] Enhanced automation starting for notice_id: {notice_id}")
    
    # Get job details
    df = load_data()
    my_df = load_my_data(columns_fallback=list(df.columns) if not df.empty else None)
    row = _match_row_by_notice(df, notice_id) or _match_row_by_notice(my_df, notice_id)

    job_title = ""
    if row:
        for key in ["Title","Opportunity Title","Notice Title","Name","Project Title","Solicitation Title","Description","Summary"]:
            if key in row and str(row[key]).strip():
                job_title = str(row[key]).strip()
                break
    
    if not job_title:
        job_title = f"Notice {notice_id}"
    
    # Create folder for this opportunity
    folder = _create_contract_folder(job_title)
    print(f"[SAM] Created folder: {folder}")

    if not _SELENIUM_AVAILABLE:
        return jsonify({
            "ok": False, 
            "message": "Selenium not installed in this environment.",
            "folder": folder
        }), 500
    
    try:
        result = _sam_download_with_persistent_session(notice_id, job_title, folder)
        
        print(f"[SAM] Automation completed successfully")
        print(f"[SAM] PDF: {result.get('pdf')}")
        print(f"[SAM] Attachments: {len(result.get('attachments', []))}")
        print(f"[SAM] Links: {len(result.get('links_info', []))}")
        
        return jsonify({
            "ok": True, 
            "folder": folder, 
            "pdf": result.get("pdf"), 
            "attachments": result.get("attachments", []),
            "links_info": result.get("links_info", []),
            "attachments_info": result.get("attachments_info", [])
        })
        
    except Exception as e:
        print(f"[SAM] Automation failed: {e}")
        return jsonify({
            "ok": False, 
            "folder": folder, 
            "message": str(e)
        }), 500


@app.route('/sam-cleanup', methods=['POST'])
def sam_cleanup():
    """Manually cleanup the persistent browser session"""
    try:
        _cleanup_persistent_session()
        return jsonify({"ok": True, "message": "Browser session cleaned up"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route('/create-opportunity-folder', methods=['POST'])
def create_opportunity_folder():
    """Create a folder for an opportunity in the Contracts directory"""
    try:
        payload = request.get_json() or {}
        notice_id = payload.get('notice_id', '').strip()
        title = payload.get('title', '').strip()

        if not title:
            return jsonify({"ok": False, "message": "No title provided for folder creation"}), 400

        # Sanitize the title for use as a folder name
        sanitized_title = _sanitize_folder_name(title)

        # Create the full path to the Contracts directory
        contracts_dir = os.path.join(os.path.dirname(__file__), "Contracts")
        folder_path = os.path.join(contracts_dir, sanitized_title)

        # Create the folder
        try:
            os.makedirs(folder_path, exist_ok=True)
            logger.info(f"Created opportunity folder: {folder_path}")

            return jsonify({
                "ok": True,
                "folder_path": folder_path,
                "title": sanitized_title,
                "message": f"Folder created successfully: {sanitized_title}"
            })

        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            return jsonify({
                "ok": False,
                "message": f"Failed to create folder: {str(e)}"
            }), 500

    except Exception as e:
        logger.error(f"Error in create_opportunity_folder: {e}")
        return jsonify({"ok": False, "message": "Internal server error"}), 500


@app.route('/get-folder-files', methods=['POST'])
def get_folder_files():
    """Get list of files in an opportunity folder"""
    try:
        payload = request.get_json() or {}
        title = payload.get('title', '').strip()

        if not title:
            return jsonify({"ok": False, "files": []})

        # Sanitize the title for use as a folder name
        sanitized_title = _sanitize_folder_name(title)

        # Create the full path to the Contracts directory
        contracts_dir = os.path.join(os.path.dirname(__file__), "Contracts")
        folder_path = os.path.join(contracts_dir, sanitized_title)

        files = []
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            try:
                for filename in os.listdir(folder_path):
                    file_path = os.path.join(folder_path, filename)
                    if os.path.isfile(file_path):
                        # Get file size for display
                        file_size = os.path.getsize(file_path)
                        size_str = ""
                        if file_size < 1024:
                            size_str = f"{file_size} B"
                        elif file_size < 1024 * 1024:
                            size_str = f"{file_size / 1024:.1f} KB"
                        else:
                            size_str = f"{file_size / (1024 * 1024):.1f} MB"

                        # Create relative path from Contracts directory with forward slashes for web URLs
                        relative_path = f"{sanitized_title}/{filename}"

                        files.append({
                            "name": filename,
                            "size": size_str,
                            "path": relative_path
                        })

                # Sort files by name
                files.sort(key=lambda x: x['name'].lower())

            except Exception as e:
                logger.error(f"Error reading folder contents: {e}")

        return jsonify({
            "ok": True,
            "files": files,
            "folder_path": folder_path,
            "folder_exists": os.path.exists(folder_path)
        })

    except Exception as e:
        logger.error(f"Error in get_folder_files: {e}")
        return jsonify({"ok": False, "files": []})


@app.route('/open-file/<path:file_path>')
def open_file(file_path):
    """Serve files from the Contracts directory"""
    try:
        # Secure file serving with proper path validation
        contracts_dir = os.path.join(os.path.dirname(__file__), "Contracts")

        # Convert forward slashes to OS-appropriate path separators
        normalized_file_path = file_path.replace('/', os.sep)

        # Security check: ensure no path traversal attempts
        if '..' in normalized_file_path or os.path.isabs(normalized_file_path):
            return "Access denied - path traversal detected", 403

        # Construct the full file path
        full_file_path = os.path.join(contracts_dir, normalized_file_path)

        # Use realpath for additional security and path normalization
        abs_contracts_dir = os.path.realpath(contracts_dir)
        abs_file_path = os.path.realpath(full_file_path)

        # Double-check the path is within the contracts directory
        if not abs_file_path.startswith(abs_contracts_dir + os.sep):
            return "Access denied", 403

        if not os.path.exists(abs_file_path) or not os.path.isfile(abs_file_path):
            return "File not found", 404

        # Validate file extension against allowed types
        allowed_extensions = {'.pdf', '.doc', '.docx', '.xlsx', '.xls', '.txt', '.zip'}
        file_ext = os.path.splitext(abs_file_path)[1].lower()
        if file_ext not in allowed_extensions:
            return "File type not allowed", 403

        return send_file(abs_file_path)

    except Exception as e:
        logger.error(f"Error serving file: {e}")
        return "Internal server error", 500


# ====================== AI SUMMARY ROUTES ======================
@app.route("/generate-ai-summary", methods=["POST"])
def generate_ai_summary_endpoint():
    """Generate AI summary for a given description text."""
    try:
        # Input validation
        if not request.is_json:
            return jsonify({"ok": False, "message": "Content-Type must be application/json"}), 400

        payload = request.get_json() or {}
        description = payload.get("description", "").strip()
        notice_id = payload.get("notice_id", "").strip()

        # Validate description input
        if not description:
            return jsonify({"ok": False, "message": "No description provided"}), 400

        # Prevent excessively long descriptions (DoS protection)
        if len(description) > 50000:  # 50K characters limit
            return jsonify({"ok": False, "message": "Description too long"}), 400

        # Validate notice_id format if provided
        if notice_id and (len(notice_id) > 100 or not notice_id.replace('-', '').replace('_', '').isalnum()):
            return jsonify({"ok": False, "message": "Invalid notice ID format"}), 400

        if not OPENAI_API_KEY:
            return jsonify({"ok": False, "message": "OpenAI API key not configured"}), 500

        # Check if we already have a summary for this Notice ID
        if notice_id:
            existing_summary = get_ai_summary_for_notice(notice_id)
            if existing_summary:
                print(f"[AI] Using existing summary for Notice ID: {notice_id}")
                return jsonify({"ok": True, "summary": existing_summary})

        # Generate the AI summary
        summary = generate_ai_summary(description)

        if summary:
            # Save the summary if we have a notice_id
            if notice_id:
                save_ai_summary_for_notice(notice_id, summary)
                print(f"[AI] Saved new summary for Notice ID: {notice_id}")

            return jsonify({"ok": True, "summary": summary})
        else:
            return jsonify({"ok": False, "message": "Failed to generate summary"}), 500

    except Exception as e:
        logger.error(f"AI summary endpoint error: {str(e)}")
        return jsonify({"ok": False, "message": "Internal server error"}), 500


# ====================== DIAGNOSTIC ROUTES (Development Only) ======================
if not PRODUCTION_MODE:
    @app.route("/diag/opportunity/<notice_id>")
    def diag_opportunity(notice_id):
        """Diagnostic information for an opportunity."""
        df = load_data()
        my_df = load_my_data(columns_fallback=list(df.columns) if not df.empty else None)
        row_df = _match_row_by_notice(df, notice_id)
        row_my = _match_row_by_notice(my_df, notice_id)
        return {
            "notice_id": notice_id,
            "cols_df": list(df.columns) if df is not None and not df.empty else [],
            "cols_my": list(my_df.columns) if my_df is not None and not my_df.empty else [],
            "found_in": "df" if row_df else ("my" if row_my else None),
            "row": row_df or row_my or {}
        }

    @app.route("/diag/selenium")
    def diag_selenium():
        """Diagnostic check for Selenium functionality."""
        if not _SELENIUM_AVAILABLE:
            return {"ok": False, "message": "Selenium is not installed in this environment."}, 500
        try:
            tmp_dir = os.path.join(os.getcwd(), "downloads_test")
            os.makedirs(tmp_dir, exist_ok=True)
            drv = _get_persistent_edge_driver(tmp_dir)
            try:
                drv.get("https://example.com/")
                title = drv.title
            finally:
                # Don't quit the persistent driver in diagnostic mode
                pass
            return {"ok": True, "title": title}
        except Exception as e:
            return {"ok": False, "message": str(e)}, 500

    @app.route("/reload-info")
    def reload_info():
        """Get information about the currently loaded data."""
        df = load_data()
        f = find_data_file()
        return {
            "rows": int(len(df)),
            "active_file": os.path.basename(f) if f else None,
            "columns": list(df.columns),
            "sample": df.head(3).to_dict(orient="records")
        }

    @app.route("/diag/routes")
    def diag_routes():
        """List all available routes."""
        lines = [f"{r.rule:40s} -> {r.endpoint}" for r in app.url_map.iter_rules()]
        return "<pre>" + "\n".join(sorted(lines)) + "</pre>"
else:
    # In production mode, diagnostic endpoints return 404
    @app.route("/diag/<path:path>")
    def diag_disabled(path):
        """Diagnostic endpoints disabled in production."""
        return "Not found", 404

    @app.route("/reload-info")
    def reload_info_disabled():
        """Reload info disabled in production."""
        return "Not found", 404


# ====================== SECURITY HEADERS ======================
@app.after_request
def security_headers(response):
    """Add security headers to all responses"""
    # Prevent MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'

    # Prevent clickjacking
    response.headers['X-Frame-Options'] = 'DENY'

    # XSS Protection (legacy browsers)
    response.headers['X-XSS-Protection'] = '1; mode=block'

    # Content Security Policy
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "font-src 'self' https://cdnjs.cloudflare.com; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    # HSTS (only add if using HTTPS)
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    # Referrer Policy
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    # Feature Policy / Permissions Policy
    response.headers['Permissions-Policy'] = (
        "accelerometer=(), camera=(), geolocation=(), "
        "gyroscope=(), magnetometer=(), microphone=(), "
        "payment=(), usb=()"
    )

    return response


# ====================== ERROR HANDLERS ======================
@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors."""
    return render_template('error.html', error="Page not found"), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    logger.error(f"Internal server error: {error}")
    return render_template('error.html', error="Internal server error"), 500


# ====================== CLEANUP AND STARTUP ======================
# Cleanup on app shutdown
import atexit
atexit.register(_cleanup_persistent_session)


# Application startup
if __name__ == "__main__":
    print("=" * 60)
    print("GOVERNMENT CONTRACTING SEARCH TOOL")
    print("=" * 60)
    print("[APP] Starting Government Contracting Search Tool...")
    print(f"[APP] Data directory: {os.path.abspath(DATA_DIR)}")
    print(f"[APP] Contracts folder: {CONTRACTS_BASE}")
    print(f"[APP] Selenium available: {_SELENIUM_AVAILABLE}")
    print("[APP] Starting Flask development server...")
    print("=" * 60)
    
    app.run(debug=True, host="127.0.0.1", port=5000)