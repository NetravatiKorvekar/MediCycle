from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import requests
import uuid
import re

# Image validation
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("[WARN] Pillow not installed. Image validation will be limited.")

# OCR
try:
    import pytesseract
    # Configure Tesseract path for Windows
    TESSERACT_PATH = os.getenv('TESSERACT_CMD', r'C:\Program Files\Tesseract-OCR\tesseract.exe')
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("[WARN] pytesseract not installed. OCR will be disabled.")

# ---- Image upload configuration ----
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png'}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024   # 5 MB
MIN_IMAGE_WIDTH = 800
MIN_IMAGE_HEIGHT = 600
UPLOAD_FOLDER = os.path.join('static', 'uploads')

# Allow OAuth2 over HTTP for development
# (Google OAuth related code removed)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')

from flask_session import Session

app.config['SESSION_TYPE'] = 'filesystem'  # Stores sessions in files (simple for dev)
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'medi_cycle:'

Session(app)

# Session configuration
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Neon DB config
DATABASE_URL = os.getenv("NEON_DB_URL", "postgresql://neondb_owner:npg_vrnafFqP6i9B@ep-tiny-dust-a4m85eqk-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require")

# (Google OAuth config and functions removed)

# DB connection function
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

# Session protecting decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Please login first.', 'warning')
            return redirect(url_for('login_signup'))
        return f(*args, **kwargs)
    return decorated_function

# Role-based access control
def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                flash('Please login first.', 'warning')
                return redirect(url_for('login_signup'))
            if session.get('role') != role:
                flash('Access denied: Insufficient privileges.', 'danger')
                if session.get('role') == 'volunteer':
                    return redirect(url_for('index'))
                return redirect(url_for('my_activities'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Function to setup database tables
def setup_database():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    
    # Check if users table exists
    cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'users')")
    table_exists = cur.fetchone()[0]
    
    if not table_exists:
        # Create users table if it doesn't exist
        print("Creating users table...")
        cur.execute('''
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            fullname VARCHAR(100) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password VARCHAR(100) NOT NULL,
            role VARCHAR(20) NOT NULL,
            city VARCHAR(50),
            address VARCHAR(200),
            pincode VARCHAR(10),
            is_blocked BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            index_number SERIAL
        )
        ''')
    else:
        # Check if index_number column exists
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'index_number')")
        index_exists = cur.fetchone()[0]
        
        if not index_exists:
            print("Adding index_number column to users table...")
            try:
                cur.execute("ALTER TABLE users ADD COLUMN index_number SERIAL")
            except Exception as e:
                print(f"Error adding index_number column: {str(e)}")
                conn.rollback()
    
    # Check if medicines table exists
    cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'medicines')")
    medicines_table_exists = cur.fetchone()[0]
    
    if not medicines_table_exists:
        print("Creating medicines table...")
        cur.execute('''
        CREATE TABLE medicines (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            quantity DECIMAL(10,2) NOT NULL,
            quantity_unit VARCHAR(20) NOT NULL,
            expiry_date DATE NOT NULL,
            description TEXT,
            donation_type VARCHAR(20) NOT NULL,
            price DECIMAL(10,2),
            city VARCHAR(50) NOT NULL,
            area VARCHAR(100) NOT NULL,
            pincode VARCHAR(10) NOT NULL,
            user_id INTEGER REFERENCES users(id),
            image_path VARCHAR(255),
            status VARCHAR(20) DEFAULT 'Available',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            index_number SERIAL
        )
        ''')
    else:
        # Check if status column exists
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'medicines' AND column_name = 'status')")
        status_exists = cur.fetchone()[0]
        
        if not status_exists:
            print("Adding status column to medicines table...")
            try:
                cur.execute("ALTER TABLE medicines ADD COLUMN status VARCHAR(20) DEFAULT 'Available'")
            except Exception as e:
                print(f"Error adding status column: {str(e)}")
                conn.rollback()
        
        # Check if index_number column exists
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'medicines' AND column_name = 'index_number')")
        index_exists = cur.fetchone()[0]
        
        if not index_exists:
            print("Adding index_number column to medicines table...")
            try:
                cur.execute("ALTER TABLE medicines ADD COLUMN index_number SERIAL")
            except Exception as e:
                print(f"Error adding index_number column: {str(e)}")
                conn.rollback()
        
        # Check if category column exists
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'medicines' AND column_name = 'category')")
        category_exists = cur.fetchone()[0]
        
        if not category_exists:
            print("Adding category column to medicines table...")
            try:
                cur.execute("ALTER TABLE medicines ADD COLUMN category VARCHAR(50)")
                print("Category column added successfully!")
            except Exception as e:
                print(f"Error adding category column: {str(e)}")
                conn.rollback()
    
    # Check if findmedicine table exists
    cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'findmedicine')")
    findmedicine_exists = cur.fetchone()[0]
    
    if not findmedicine_exists:
        print("Creating findmedicine table...")
        cur.execute('''
        CREATE TABLE findmedicine (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) NOT NULL,
            medicine_id INTEGER REFERENCES medicines(id),
            medicine_name VARCHAR(100) NOT NULL,
            quantity INTEGER NOT NULL,
            purpose VARCHAR(50) NOT NULL,
            message TEXT,
            address TEXT NOT NULL,
            contact VARCHAR(15) NOT NULL,
            status VARCHAR(20) DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
    else:
        # Check if medicine_id column exists
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'findmedicine' AND column_name = 'medicine_id')")
        medicine_id_exists = cur.fetchone()[0]
        
        if not medicine_id_exists:
            print("Adding medicine_id column to findmedicine table...")
            try:
                cur.execute("ALTER TABLE findmedicine ADD COLUMN medicine_id INTEGER REFERENCES medicines(id)")
            except Exception as e:
                print(f"Error adding medicine_id column: {str(e)}")
                conn.rollback()
        
        # Check if index_number column exists
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'findmedicine' AND column_name = 'index_number')")
        index_exists = cur.fetchone()[0]
        
        if not index_exists:
            print("Adding index_number column to findmedicine table...")
            try:
                cur.execute("ALTER TABLE findmedicine ADD COLUMN index_number SERIAL")
            except Exception as e:
                print(f"Error adding index_number column: {str(e)}")
                conn.rollback()
    
    # ---- medicine_images table ----
    cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'medicine_images')")
    med_images_exists = cur.fetchone()[0]

    if not med_images_exists:
        print("Creating medicine_images table...")
        cur.execute('''
        CREATE TABLE medicine_images (
            id SERIAL PRIMARY KEY,
            medicine_id INTEGER REFERENCES medicines(id) ON DELETE CASCADE,
            front_image_url VARCHAR(500),
            back_image_url VARCHAR(500),
            expiry_batch_image_url VARCHAR(500),
            ocr_medicine_name TEXT,
            ocr_batch_number TEXT,
            ocr_expiry_date TEXT,
            ocr_verification_status VARCHAR(20) DEFAULT 'pending',
            ocr_name_match BOOLEAN,
            ocr_expiry_match BOOLEAN,
            ocr_raw_front TEXT,
            ocr_raw_back TEXT,
            ocr_raw_expiry TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

    # Close connection
    cur.close()
    conn.close()

# Routes
@app.route('/')
def index():
    if 'user' in session:
        if session.get('role') in ['ngo', 'medical_org']:
            return redirect(url_for('dashboard'))
        else:
            return render_template('index.html')
    return redirect(url_for('login_signup'))

@app.route('/login-signup', methods=['GET', 'POST'])
def login_signup():
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        email = request.form.get('email')
        password = request.form.get('password')
        conn = get_db_connection()
        cur = conn.cursor()

        if form_type == 'login':
            # Login logic
            cur.execute('SELECT * FROM users WHERE email = %s', (email,))
            user = cur.fetchone()
            if user and check_password_hash(user['password'], password):
                if user['is_blocked']:
                    flash('Your account has been blocked. Please contact support.', 'danger')
                    return redirect(url_for('login_signup'))
                
                # Store user data in session
                session['user'] = {
                    'id': user['id'],
                    'email': user['email'],
                    'fullname': user['fullname']
                }
                session['role'] = user['role']
                
                print("DEBUG: User logged in:", session['user'])
                
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({
                        'success': True,
                        'user': session['user'],
                        'role': user['role']
                    })
                
                if user['role'] in ['ngo', 'medical_org']:
                    return redirect(url_for('dashboard'))
                else:
                    return redirect(url_for('index'))
            else:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({
                        'success': False,
                        'message': 'Invalid email or password'
                    })
                flash('Invalid email or password', 'danger')

        elif form_type == 'signup':
            fullname = request.form.get('fullname')
            role = request.form.get('role')
            org_code = request.form.get('org_code')
            cur.execute('SELECT * FROM users WHERE email = %s', (email,))
            if cur.fetchone():
                flash('Email already exists', 'danger')
            else:
                # Default to 'volunteer' if role not selected
                if not role:
                    role = 'volunteer'
                
                if role == 'ngo' and not org_code:
                    flash('Organization code is required for NGO registration', 'danger')
                    return redirect(url_for('login_signup'))
                hashed_password = generate_password_hash(password)
                cur.execute(
                    'INSERT INTO users (fullname, email, password, role) VALUES (%s, %s, %s, %s) RETURNING id',
                    (fullname, email, hashed_password, role)
                )
                user_id = cur.fetchone()['id']
                conn.commit()
                
                # Store user data in session after signup
                session['user'] = {
                    'id': user_id,
                    'email': email,
                    'fullname': fullname
                }
                session['role'] = role
                
                print("DEBUG: User signed up:", session['user'])
                
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('index'))

        cur.close()
        conn.close()
    return render_template('Login_SignUp.html')

@app.route('/my-activities')
@login_required
def my_activities():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get user ID from session
        if 'user' not in session or 'id' not in session['user']:
            flash('Please login again', 'warning')
            return redirect(url_for('login_signup'))
            
        user_id = session['user']['id']
        
        # Get user's medicines
        cur.execute('''
            SELECT * FROM medicines 
            WHERE user_id = %s 
            ORDER BY created_at DESC
        ''', (user_id,))
        listed_medicines = cur.fetchall()
        
        # Get user's medicine requests
        cur.execute('''
            SELECT * FROM findmedicine 
            WHERE user_id = %s 
            ORDER BY created_at DESC
        ''', (user_id,))
        medicine_requests = cur.fetchall()
        
        # Get user's orders (accepted requests for their medicines) - Robust query
        cur.execute('''
            SELECT f.*, u.fullname as requester_name, u.email as requester_email
            FROM findmedicine f
            JOIN users u ON f.user_id = u.id
            WHERE f.status = 'Accepted' AND (
                f.medicine_id IN (SELECT id FROM medicines WHERE user_id = %s) OR
                f.medicine_name IN (SELECT name FROM medicines WHERE user_id = %s)
            )
            ORDER BY f.created_at DESC
        ''', (user_id, user_id))
        orders = cur.fetchall()
        
        print(f"DEBUG: Found {len(orders)} orders for user {user_id}")
        
        # Calculate stats - Updated to include expired medicines count
        stats = {
            'total_listed': len(listed_medicines),
            'total_requests': len(medicine_requests),
            'active_listings': len([m for m in listed_medicines if m['status'] == 'Available']),
            'pending_requests': len([r for r in medicine_requests if r['status'] == 'Pending']),
            'total_donated': len([m for m in listed_medicines if m['status'].lower() == 'accepted']),
            'total_sold': len([r for r in medicine_requests if r['status'].lower() in ['fulfilled', 'completed', 'accepted']]),
            'total_orders': len(orders),
            'expired_medicines': len([m for m in listed_medicines if m['expiry_date'] < datetime.now().date()])
        }
        
        cur.close()
        conn.close()
        
        return render_template('MyActivities.html', 
                             listed_medicines=listed_medicines, 
                             medicine_requests=medicine_requests,
                             orders=orders,
                             stats=stats)
    except Exception as e:
        print("DEBUG: Error in my_activities:", str(e))
        flash('Error loading activities. Please try again.', 'danger')
        return redirect(url_for('index'))

@app.route('/find-medicine', methods=['GET', 'POST'])
@login_required
def find_medicine():
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Handle POST request (form submission for medicine requests)
        if request.method == 'POST':
            try:
                # Check if user is logged in and has ID
                if 'user' not in session or 'id' not in session['user']:
                    print("DEBUG: User not logged in or missing ID in session")
                    return jsonify({'success': False, 'message': 'Please login first'})
                
                # Get form data
                medicine_id_str = request.form.get('medicine_id', None)
                medicine_name = request.form.get('medicine_name', 'General Medicine')
                quantity = request.form.get('quantity')
                purpose = request.form.get('purpose', 'Medical need')
                message = request.form.get('message', None)
                address = request.form.get('address')
                contact = request.form.get('contact')
                
                print("DEBUG: Form data received:", {
                    'medicine_id': medicine_id_str,
                    'medicine_name': medicine_name,
                    'quantity': quantity,
                    'purpose': purpose,
                    'message': message,
                    'address': address,
                    'contact': contact,
                    'user_id': session['user']['id']
                })
                
                # Basic validation - only quantity, address, contact required
                if not all([quantity, address, contact]):
                    return jsonify({'success': False, 'message': 'Please fill quantity, address, and contact fields'})
                
                # For specific medicine requests, require medicine_id
                medicine_id = None
                if medicine_id_str:
                    try:
                        medicine_id = int(medicine_id_str)
                        print("DEBUG: Set medicine_id to", medicine_id)
                    except ValueError:
                        return jsonify({'success': False, 'message': 'Invalid Medicine ID'})
                else:
                    print("DEBUG: No medicine_id provided, using None")
                
                # Ensure quantity is integer
                try:
                    quantity_int = int(quantity)
                except ValueError:
                    return jsonify({'success': False, 'message': 'Quantity must be a valid number'})
                
                # Insert into database
                cur.execute('''
                    INSERT INTO findmedicine (user_id, medicine_id, medicine_name, quantity, purpose, message, address, contact, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending')
                    RETURNING id
                ''', (session['user']['id'], medicine_id, medicine_name, quantity_int, purpose, message, address, contact))
                
                inserted_id = cur.fetchone()['id']
                conn.commit()
                print("DEBUG: Request inserted successfully with ID:", inserted_id, "and medicine_id:", medicine_id)
                
                return jsonify({
                    'success': True, 
                    'message': 'Medicine request posted successfully!'
                })
                
            except Exception as e:
                print("DEBUG: Error in POST:", str(e))
                conn.rollback()
                return jsonify({'success': False, 'message': 'Error submitting request. Please try again.'})
        
        # Handle GET request (display accepted, non-expired medicines)
        search_query = request.args.get('search', '')
        location = request.args.get('location', '')
        expiry_filter = request.args.get('expiry', '')  # New filter
        type_filter = request.args.get('type', '')      # New filter
        
        # Query with field names matching frontend expectations
        query = '''
            SELECT 
                m.id,
                m.name as medicine_name,
                COALESCE(m.category, 'General Medicine') as category,
                CONCAT(m.city, ', ', m.area) as location,
                m.expiry_date,
                CONCAT(m.quantity, ' ', m.quantity_unit) as available_qty,
                CASE 
                    WHEN m.donation_type = 'free' THEN 'Free'
                    WHEN m.price IS NOT NULL THEN CONCAT('₹', m.price)
                    ELSE 'Free'
                END as type_price,
                m.image_path,
                m.donation_type,
                m.price,
                m.quantity,
                m.quantity_unit,
                m.city,
                m.area,
                m.pincode,
                u.fullname as donated_by,
                u.email as donor_email,
                u.id as donor_id,
                mi.front_image_url,
                mi.back_image_url,
                mi.expiry_batch_image_url,
                mi.ocr_verification_status,
                mi.ocr_name_match,
                mi.ocr_expiry_match
            FROM medicines m
            JOIN users u ON m.user_id = u.id
            LEFT JOIN medicine_images mi ON mi.medicine_id = m.id
            WHERE m.status = 'Accepted' 
            AND m.expiry_date >= CURRENT_DATE
        '''
        params = []
        
        # Add search filter
        if search_query:
            query += " AND (m.name ILIKE %s OR m.description ILIKE %s)"
            params.extend([f'%{search_query}%', f'%{search_query}%'])
        
        # Add location filter
        if location:
            query += " AND (m.city ILIKE %s OR m.area ILIKE %s)"
            params.extend([f'%{location}%', f'%{location}%'])
        
        # Add expiry date filter
        if expiry_filter:
            if expiry_filter == '1month':
                query += " AND m.expiry_date <= CURRENT_DATE + INTERVAL '1 month'"
            elif expiry_filter == '3months':
                query += " AND m.expiry_date <= CURRENT_DATE + INTERVAL '3 months'"
            elif expiry_filter == '6months':
                query += " AND m.expiry_date <= CURRENT_DATE + INTERVAL '6 months'"
            elif expiry_filter == '1year':
                query += " AND m.expiry_date <= CURRENT_DATE + INTERVAL '1 year'"
            # If 'all' is selected, no additional filter needed
        
        # Add medicine type filter (free/paid)
        if type_filter:
            if type_filter == 'free':
                query += " AND m.donation_type = 'free'"
            elif type_filter == 'paid':
                query += " AND m.donation_type = 'paid'"
            # If 'all' is selected, no additional filter needed
        
        # Order by most recent first
        query += " ORDER BY m.created_at DESC"
        
        # Execute query
        cur.execute(query, params)
        medicines = cur.fetchall()
        
        print(f"DEBUG: Found {len(medicines)} accepted, non-expired medicines")
        print(f"DEBUG: Filters applied - expiry: {expiry_filter}, type: {type_filter}")
        if medicines:
            print(f"DEBUG: Sample medicine data: {dict(medicines[0])}")
        
        # Return response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True,
                'medicines': [dict(m) for m in medicines]
            })
        
        return render_template('FindMedicine.html', medicines=medicines)
        
    except Exception as e:
        print("DEBUG: Main Error:", str(e))
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)})
        
    finally:
        cur.close()
        conn.close()

# ---- Helper: validate and save an uploaded image file ----
def validate_and_save_image(file_storage, label):
    """Validate image format, size, dimensions. Save to uploads/. Return (saved_path, error_message)."""
    if not file_storage or file_storage.filename == '':
        return None, f'{label} is required.'

    filename = file_storage.filename
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return None, f'{label}: only JPG, JPEG, PNG files are allowed.'

    # Read bytes once
    file_bytes = file_storage.read()
    if len(file_bytes) == 0:
        return None, f'{label}: uploaded file is empty.'
    if len(file_bytes) > MAX_IMAGE_SIZE_BYTES:
        return None, f'{label}: file exceeds 5 MB limit.'

    if PIL_AVAILABLE:
        import io
        try:
            img = Image.open(io.BytesIO(file_bytes))
            img.verify()   # raises on corrupt files
            img = Image.open(io.BytesIO(file_bytes))  # re-open after verify
            w, h = img.size
            if w < MIN_IMAGE_WIDTH or h < MIN_IMAGE_HEIGHT:
                return None, (f'{label}: image resolution {w}x{h} is too low. '
                              f'Minimum required is {MIN_IMAGE_WIDTH}x{MIN_IMAGE_HEIGHT}.')
        except Exception as ve:
            return None, f'{label}: file does not appear to be a valid image ({ve}).'

    # Generate unique filename and save
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    save_path = os.path.join(UPLOAD_FOLDER, unique_name)
    with open(save_path, 'wb') as f:
        f.write(file_bytes)

    relative_url = f'uploads/{unique_name}'
    return relative_url, None


# ---- Helper: run OCR on a saved image ----
def run_ocr_on_image(relative_url):
    """Return raw OCR text from the image at relative_url (under static/), or empty string."""
    if not OCR_AVAILABLE or not relative_url:
        return ''
    try:
        import pytesseract
        from PIL import Image
        img_path = os.path.join('static', relative_url)
        img = Image.open(img_path)
        text = pytesseract.image_to_string(img, config='--psm 6')
        return text.strip()
    except Exception as e:
        print(f"[OCR] Error reading {relative_url}: {e}")
        return ''


# ---- Helper: extract structured fields from OCR text ----
def extract_ocr_fields(ocr_text):
    """Try to pull medicine name, batch number, expiry date from raw OCR text."""
    result = {'medicine_name': '', 'batch_number': '', 'expiry_date': ''}
    if not ocr_text:
        return result

    lines = [l.strip() for l in ocr_text.splitlines() if l.strip()]

    # Expiry date patterns: EXP / EXPIRY / MFG patterns
    exp_patterns = [
        r'(?:exp(?:iry)?|expiry date|use before|best before)[\s:./\-]*(\d{2}[/\-.\s]\d{2}[/\-.\s]\d{2,4})',
        r'(?:exp(?:iry)?|expiry date|use before|best before)[\s:./\-]*(\w{3}[\s./\-]\d{2,4})',
        r'(\d{2}[/\-.]\d{4})',
        r'(\d{2}[/\-.]\d{2}[/\-.]\d{2,4})',
    ]
    for pat in exp_patterns:
        m = re.search(pat, ocr_text, re.IGNORECASE)
        if m:
            result['expiry_date'] = m.group(1).strip()
            break

    # Batch number patterns
    batch_patterns = [
        r'(?:batch(?:\s*no\.?)?|lot(?:\s*no\.?)?|b\.?no\.?)[\s:./\-]*([A-Z0-9]+)',
        r'(?:lot)[\s:./\-]*([A-Z0-9]+)',
    ]
    for pat in batch_patterns:
        m = re.search(pat, ocr_text, re.IGNORECASE)
        if m:
            result['batch_number'] = m.group(1).strip()
            break

    # Medicine name: look for prominent uppercase text on front (heuristic — first long all-caps or mixed line)
    for line in lines:
        cleaned = re.sub(r'[^A-Za-z0-9\s]', '', line).strip()
        if len(cleaned) >= 4:
            result['medicine_name'] = cleaned
            break

    return result


# ---- Helper: fuzzy compare two strings (for OCR verification) ----
def fuzzy_match(a, b, threshold=0.6):
    """Return True if strings are similar enough after normalization."""
    if not a or not b:
        return False
    def normalize(s):
        return re.sub(r'[^a-z0-9]', '', s.lower())
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    # Simple Jaccard over character trigrams
    def trigrams(s):
        return set(s[i:i+3] for i in range(len(s)-2)) if len(s) >= 3 else set(s)
    ta, tb = trigrams(na), trigrams(nb)
    if not ta and not tb:
        return True
    if not ta or not tb:
        return False
    jaccard = len(ta & tb) / len(ta | tb)
    return jaccard >= threshold


# ---- Helper: compare expiry dates (OCR vs seller) ----
def compare_expiry_dates(ocr_expiry, seller_expiry_str):
    """Return True if OCR expiry reasonably matches seller-entered expiry date string."""
    if not ocr_expiry or not seller_expiry_str:
        return False
    # Normalize both to year-month for comparison
    def extract_ym(s):
        s = re.sub(r'[^0-9a-zA-Z]', ' ', s).strip()
        # Patterns: MM/YYYY, MM-YYYY, YYYY-MM, DD/MM/YYYY, etc.
        m = re.search(r'(\d{4})[-/ ](\d{1,2})', s)
        if m:
            return (m.group(1), m.group(2).zfill(2))
        m = re.search(r'(\d{1,2})[-/ ](\d{4})', s)
        if m:
            return (m.group(2), m.group(1).zfill(2))
        m = re.search(r'(\d{2})[-/ ](\d{2})[-/ ](\d{2,4})', s)
        if m:
            parts = [m.group(1), m.group(2), m.group(3)]
            if len(parts[2]) == 2:
                parts[2] = '20' + parts[2]
            return (parts[2], parts[0])
        # Month name abbreviation
        months = {'jan':'01','feb':'02','mar':'03','apr':'04','may':'05','jun':'06',
                  'jul':'07','aug':'08','sep':'09','oct':'10','nov':'11','dec':'12'}
        for abbr, num in months.items():
            mo = re.search(rf'{abbr}', s, re.IGNORECASE)
            if mo:
                yr = re.search(r'(\d{4})', s)
                if yr:
                    return (yr.group(1), num)
        return None
    ym_ocr = extract_ym(ocr_expiry)
    ym_seller = extract_ym(seller_expiry_str)
    if ym_ocr and ym_seller:
        return ym_ocr == ym_seller
    return False


@app.route('/list-medicine', methods=['GET', 'POST'])
@login_required
def list_medicine():
    if request.method == 'POST':
        try:
            name = request.form.get('medicine-name')
            quantity = request.form.get('quantity')
            quantity_unit = request.form.get('quantity-unit')
            expiry_date = request.form.get('expiry-date')
            category = request.form.get('category')
            description = request.form.get('description')
            donation_type = request.form.get('donation-type')
            price = request.form.get('price')
            if price == '' or price is None:
                price = None
            else:
                try:
                    price = float(price)
                except ValueError:
                    error_msg = 'Invalid price value.'
                    print(f"[ERROR] {error_msg}")
                    return jsonify({'success': False, 'message': error_msg})
            city = request.form.get('city')
            area = request.form.get('area')
            pincode = request.form.get('pincode')

            print("name:", name)
            print("quantity:", quantity)
            print("quantity_unit:", quantity_unit)
            print("expiry_date:", expiry_date)
            print("category:", category)
            print("description:", description)
            print("donation_type:", donation_type)
            print("price:", price)
            print("city:", city)
            print("area:", area)
            print("pincode:", pincode)

            # ---- Validate and save the three required medicine photos ----
            front_file  = request.files.get('front_image')
            back_file   = request.files.get('back_image')
            expiry_file = request.files.get('expiry_batch_image')

            front_url, err = validate_and_save_image(front_file, 'Front Photo')
            if err:
                return jsonify({'success': False, 'message': err})

            back_url, err = validate_and_save_image(back_file, 'Back Photo')
            if err:
                # Clean up already-saved front
                if front_url:
                    try: os.remove(os.path.join('static', front_url))
                    except: pass
                return jsonify({'success': False, 'message': err})

            expiry_url, err = validate_and_save_image(expiry_file, 'Expiry/Batch Photo')
            if err:
                for u in [front_url, back_url]:
                    if u:
                        try: os.remove(os.path.join('static', u))
                        except: pass
                return jsonify({'success': False, 'message': err})

            # Keep existing image_path column pointing to front image (backward compat)
            image_path = front_url

            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute('''
                INSERT INTO medicines (name, quantity, quantity_unit, expiry_date, category, description,
                                       donation_type, price, city, area, pincode, user_id, image_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (name, quantity, quantity_unit, expiry_date, category, description,
                  donation_type, price, city, area, pincode, session['user']['id'], image_path))
            medicine_id = cur.fetchone()['id']

            # ---- Run OCR ----
            ocr_front_text  = run_ocr_on_image(front_url)
            ocr_back_text   = run_ocr_on_image(back_url)
            ocr_expiry_text = run_ocr_on_image(expiry_url)

            # Extract fields from OCR (prioritise front for name, expiry image for expiry)
            front_fields  = extract_ocr_fields(ocr_front_text)
            expiry_fields = extract_ocr_fields(ocr_expiry_text)

            ocr_medicine_name = front_fields.get('medicine_name', '')
            ocr_batch_number  = expiry_fields.get('batch_number', '') or front_fields.get('batch_number', '')
            ocr_expiry_date   = expiry_fields.get('expiry_date', '') or front_fields.get('expiry_date', '')

            # ---- Compare OCR with seller data ----
            name_match   = fuzzy_match(ocr_medicine_name, name)
            expiry_match = compare_expiry_dates(ocr_expiry_date, expiry_date)

            # Determine overall verification status
            has_ocr = OCR_AVAILABLE and (ocr_front_text or ocr_expiry_text)
            if not has_ocr:
                ocr_status = 'unclear'   # No OCR results (tesseract not installed / illegible)
            elif name_match and expiry_match:
                ocr_status = 'matched'
            elif not name_match and not expiry_match:
                ocr_status = 'mismatched'
            else:
                ocr_status = 'unclear'   # Partial match — treat as unclear

            # Store image record
            cur.execute('''
                INSERT INTO medicine_images
                    (medicine_id, front_image_url, back_image_url, expiry_batch_image_url,
                     ocr_medicine_name, ocr_batch_number, ocr_expiry_date, ocr_verification_status,
                     ocr_name_match, ocr_expiry_match, ocr_raw_front, ocr_raw_back, ocr_raw_expiry)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (medicine_id, front_url, back_url, expiry_url,
                  ocr_medicine_name, ocr_batch_number, ocr_expiry_date, ocr_status,
                  name_match, expiry_match, ocr_front_text, ocr_back_text, ocr_expiry_text))

            conn.commit()
            cur.close()
            conn.close()

            print(f"[LIST] Medicine {medicine_id} listed. OCR status: {ocr_status}")
            print(f"[OCR] name_match={name_match}, expiry_match={expiry_match}")

            response_data = {
                'success': True,
                'message': 'Medicine listed successfully!',
                'ocr_status': ocr_status,
                'ocr_name_match': name_match,
                'ocr_expiry_match': expiry_match,
                'ocr_medicine_name': ocr_medicine_name,
                'ocr_expiry_date': ocr_expiry_date,
                'ocr_batch_number': ocr_batch_number,
            }
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify(response_data)
            else:
                flash('Medicine listed successfully!', 'success')
                return redirect(url_for('my_activities'))
        except Exception as e:
            import traceback; traceback.print_exc()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': str(e)}), 500
            else:
                flash(f'Error: {str(e)}', 'danger')
                return redirect(url_for('list_medicine'))
    return render_template('ListMedicine.html')
  

@app.route('/delete-medicine/<int:medicine_id>', methods=['POST'])
@login_required
def delete_medicine(medicine_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Check if medicine belongs to user
    cur.execute('SELECT * FROM medicines WHERE id = %s AND user_id = %s', 
                (medicine_id, session['user']['id']))
    medicine = cur.fetchone()
    
    if medicine:
        # Delete image if exists
        if medicine['image_path']:
            try:
                os.remove(os.path.join('static', medicine['image_path']))
            except:
                pass
        
        # Delete medicine
        cur.execute('DELETE FROM medicines WHERE id = %s', (medicine_id,))
        conn.commit()
        msg = 'Medicine deleted successfully!'
        success = True
    else:
        msg = 'Medicine not found or unauthorized'
        success = False
    
    cur.close()
    conn.close()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': success, 'message': msg})
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('my_activities'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/get-medicine/<int:medicine_id>')
@login_required
def get_medicine(medicine_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT m.*, u.fullname, u.email
        FROM medicines m
        JOIN users u ON m.user_id = u.id
        WHERE m.id = %s
    ''', (medicine_id,))
    medicine = cur.fetchone()
    cur.close()
    conn.close()
    if medicine:
        return jsonify({'success': True, 'medicine': dict(medicine)})
    return jsonify({'success': False, 'message': 'Medicine not found'})

@app.route('/update-medicine/<int:medicine_id>', methods=['POST'])
@login_required
def update_medicine(medicine_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM medicines WHERE id = %s AND user_id = %s', (medicine_id, session['user']['id']))
        medicine = cur.fetchone()
        if not medicine:
            msg = 'Medicine not found or unauthorized'
            success = False
        else:
            name = request.form.get('name')
            quantity = request.form.get('quantity')
            quantity_unit = request.form.get('quantity_unit')
            expiry_date = request.form.get('expiry_date')
            donation_type = request.form.get('donation_type')
            price = request.form.get('price')
            if price == '' or price is None:
                price = None
            else:
                try:
                    price = float(price)
                except ValueError:
                    error_msg = 'Invalid price value.'
                    print(f"[ERROR] {error_msg}")
                    return jsonify({'success': False, 'message': error_msg})
            city = request.form.get('city')
            area = request.form.get('area')
            pincode = request.form.get('pincode')
            if not pincode or not pincode.isdigit() or len(pincode) != 6:
                error_msg = 'Please enter a valid 6-digit pincode.'
                print(f"[ERROR] {error_msg}")
                return jsonify({'success': False, 'message': error_msg})
            description = request.form.get('description')
            image_path = medicine['image_path']
            if 'image' in request.files:
                image = request.files['image']
                if image.filename != '':
                    if image_path:
                        try:
                            os.remove(os.path.join('static', image_path))
                        except:
                            pass
                    image_path = f"uploads/{image.filename}"
                    image.save(os.path.join('static', image_path))
            cur.execute('''
                UPDATE medicines 
                SET name = %s, quantity = %s, quantity_unit = %s, expiry_date = %s,
                    description = %s, donation_type = %s, price = %s, city = %s,
                    area = %s, pincode = %s, image_path = %s
                WHERE id = %s
            ''', (name, quantity, quantity_unit, expiry_date, description,
                  donation_type, price, city, area, pincode, image_path, medicine_id))
            conn.commit()
            msg = 'Medicine updated successfully!'
            success = True
    except Exception as e:
        print(f"[ERROR] {str(e)}")
        conn.rollback()
        msg = f'Error updating medicine: {str(e)}'
        success = False
    finally:
        cur.close()
        conn.close()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': success, 'message': msg})
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('my_activities'))

@app.route('/submit-medicine-request', methods=['POST'])
@login_required
def submit_medicine_request():
    medicine_id = request.form.get('medicine_id')
    message = request.form.get('message')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get medicine details
    cur.execute('SELECT * FROM medicines WHERE id = %s', (medicine_id,))
    medicine = cur.fetchone()
    
    if medicine:
        # Create request
        cur.execute('''
            INSERT INTO findmedicine (user_id, medicine_id, medicine_name, quantity, purpose, message, address, contact)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ''', (session['user']['id'], medicine_id, medicine['name'], medicine['quantity'],
              'Request for listed medicine', message, session['user'].get('address', ''),
              session['user'].get('contact', '')))
        
        conn.commit()
        flash('Request submitted successfully!', 'success')
    else:
        flash('Medicine not found', 'danger')
    
    cur.close()
    conn.close()
    
    return redirect(url_for('my_activities'))

@app.route('/delete-medicine-request/<int:request_id>', methods=['POST'])
@login_required
def delete_medicine_request(request_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM findmedicine WHERE id = %s AND user_id = %s',
                (request_id, session['user']['id']))
    request_row = cur.fetchone()
    if request_row:
        cur.execute('DELETE FROM findmedicine WHERE id = %s', (request_id,))
        conn.commit()
        msg = 'Request deleted successfully!'
        success = True
    else:
        msg = 'Request not found or unauthorized'
        success = False
    cur.close()
    conn.close()
    # AJAX (fetch) असेल तर JSON द्या
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': success, 'message': msg})
    # नॉर्मल form submit असेल तर redirect
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('my_activities'))


@app.route('/test-login/<role>')
def test_login(role):
    if role not in ['volunteer', 'ngo', 'medical_org']:
        return 'Invalid role'
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Create test user if not exists
    cur.execute('SELECT * FROM users WHERE email = %s', ('test@example.com',))
    user = cur.fetchone()
    
    if not user:
        cur.execute('''
            INSERT INTO users (fullname, email, password, role)
            VALUES (%s, %s, %s, %s)
        ''', ('Test User', 'test@example.com', generate_password_hash('test123'), role))
        conn.commit()
        
        cur.execute('SELECT * FROM users WHERE email = %s', ('test@example.com',))
        user = cur.fetchone()
    
    session['user'] = {
        'id': user['id'],
        'email': user['email'],
        'fullname': user['fullname'],
        'role': user['role']
    }
    session['role'] = user['role']
    
    cur.close()
    conn.close()
    
    return redirect(url_for('index'))

@app.route('/firebase-login', methods=['POST'])
def firebase_login():
    data = request.get_json()
    email = data.get('email')
    fullname = data.get('fullname')
    uid = data.get('uid')
    if not email or not fullname or not uid:
        return jsonify({'success': False, 'message': 'Missing user info'}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM users WHERE email = %s', (email,))
        user = cur.fetchone()
        if not user:
            # New user, create
            cur.execute('INSERT INTO users (fullname, email, password, role) VALUES (%s, %s, %s, %s) RETURNING id, role',
                        (fullname, email, generate_password_hash(uid), 'volunteer'))
            user_row = cur.fetchone()
            user_id = user_row['id']
            role = user_row['role']
            conn.commit()
        else:
            user_id = user['id']
            role = user['role']
        # Session set
        session['user'] = {
            'id': user_id,
            'email': email,
            'fullname': fullname
        }
        session['role'] = role
        return jsonify({'success': True, 'redirect': url_for('index')})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    setup_database()
    app.run(debug=True)