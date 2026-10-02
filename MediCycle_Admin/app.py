from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import random
import string
from flask import send_file
import io
import pandas as pd


app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')

# Session configuration
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

# Email configuration for OTP
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', 'your-email@gmail.com')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', 'your-app-password')
app.config['MAIL_ENABLED'] = False  # Console mode for testing

# Neon DB config
DATABASE_URL = os.getenv("NEON_DB_URL", "postgresql://neondb_owner:npg_vrnafFqP6i9B@ep-tiny-dust-a4m85eqk-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require")

# DB connection function
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

# OTP storage (in production, use Redis or database)
otp_storage = {}

def send_otp_email(email, otp):
    """Send OTP to user's email"""
    
    # Development mode: Print OTP to console if email is disabled
    if not app.config.get('MAIL_ENABLED', False):
        print("="*60)
        print(f"📧 EMAIL DISABLED - DEVELOPMENT MODE")
        print(f"OTP for {email}: {otp}")
        print(f"Valid for 10 minutes")
        print("="*60)
        return True
    
    try:
        # Check if email configuration is set
        if not app.config['MAIL_USERNAME'] or app.config['MAIL_USERNAME'] == 'your-email@gmail.com':
            print("ERROR: Email configuration not set properly!")
            print("Please configure MAIL_USERNAME and MAIL_PASSWORD in environment variables")
            print("\n🔧 Quick Fix: Set MAIL_ENABLED=False to use console OTP for development")
            return False
        
        msg = MIMEMultipart()
        msg['From'] = app.config['MAIL_USERNAME']
        msg['To'] = email
        msg['Subject'] = 'Password Reset OTP - Medicine Donation System'
        
        body = f"""
Hello,

Your OTP for password reset is: {otp}

This OTP is valid for 10 minutes only.

If you didn't request this, please ignore this email.

Best regards,
Medicine Donation Team
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        print(f"Attempting to send email to: {email}")
        print(f"Using SMTP server: {app.config['MAIL_SERVER']}:{app.config['MAIL_PORT']}")
        
        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        server.set_debuglevel(1)
        server.starttls()
        print(f"Logging in with: {app.config['MAIL_USERNAME']}")
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Email sent successfully to {email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ SMTP Authentication Error: {str(e)}")
        print("Please check your email and app password")
        print("\n📝 Steps to fix:")
        print("1. Enable 2FA on your Gmail account")
        print("2. Generate App Password: https://myaccount.google.com/apppasswords")
        print("3. Use the 16-character app password (not your regular password)")
        return False
    except smtplib.SMTPException as e:
        print(f"❌ SMTP Error: {str(e)}")
        return False
    except Exception as e:
        print(f"❌ Error sending email: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def generate_otp():
    """Generate a 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

# Session protecting decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Please login first.', 'warning')
            return redirect(url_for('login_signup'))
        return f(*args, **kwargs)
    return decorated_function

# CRITICAL: Add middleware to clear reset session on every request
@app.before_request
def clear_reset_flags_middleware():
    """Clear password reset flags from session on every request except reset flow"""
    
    # List of routes that are part of the reset flow
    reset_routes = ['forgot_password', 'verify_otp', 'reset_password', 'resend_otp']
    
    # If current request is NOT part of reset flow, clear reset flags
    if request.endpoint and request.endpoint not in reset_routes:
        if 'reset_email' in session or 'otp_verified' in session:
            print(f"DEBUG: Middleware clearing reset flags. Current route: {request.endpoint}")
            print(f"DEBUG: reset_email: {session.get('reset_email')}")
            print(f"DEBUG: otp_verified: {session.get('otp_verified')}")
            session.pop('reset_email', None)
            session.pop('otp_verified', None)
            print("DEBUG: Reset flags cleared by middleware")

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

# Function to clear password reset session data
def clear_reset_session():
    """Clear all password reset related session data"""
    session.pop('reset_email', None)
    session.pop('otp_verified', None)
    print("DEBUG: Password reset session data cleared")

# Function to setup admin credentials
def setup_admin():
    """Create admin user if not exists"""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    
    admin_email = 'nknketra07@gmail.com'
    admin_password = 'Admin@123'
    
    try:
        # Check if admin exists
        cur.execute("SELECT * FROM users WHERE email = %s", (admin_email,))
        admin = cur.fetchone()
        
        if not admin:
            print("Creating admin user...")
            hashed_password = generate_password_hash(admin_password)
            cur.execute('''
                INSERT INTO users (fullname, email, password, role, is_blocked)
                VALUES (%s, %s, %s, %s, %s)
            ''', ('Admin User', admin_email, hashed_password, 'ngo', False))
            print("Admin user created successfully!")
        else:
            print("Admin user already exists")
            # Update password if needed
            hashed_password = generate_password_hash(admin_password)
            cur.execute("UPDATE users SET password = %s WHERE email = %s", (hashed_password, admin_email))
            print("Admin password updated!")
    except Exception as e:
        print(f"Error setting up admin: {str(e)}")
    finally:
        cur.close()
        conn.close()

# Function to setup database tables
def setup_database():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    
    # Check if users table exists
    cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'users')")
    table_exists = cur.fetchone()[0]
    
    if not table_exists:
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
            index_number SERIAL,
            category VARCHAR(50)
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
            medicine_name VARCHAR(100) NOT NULL,
            quantity INTEGER NOT NULL,
            purpose VARCHAR(50) NOT NULL,
            message TEXT,
            address TEXT NOT NULL,
            contact VARCHAR(15) NOT NULL,
            status VARCHAR(20) DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            index_number SERIAL
        )
        ''')
    else:
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
    
    # Close connection
    cur.close()
    conn.close()

# Routes
@app.route('/')
def index():
    # CRITICAL: Clear password reset session when accessing home
    if 'reset_email' in session or 'otp_verified' in session:
        print("DEBUG: Found reset session on index page, clearing...")
        clear_reset_session()
    
    if 'user' in session:
        if session.get('role') in ['ngo', 'medical_org']:
            return redirect(url_for('dashboard'))
        else:
            return render_template('index.html')
    return redirect(url_for('login_signup'))

@app.route('/login_signup', methods=['GET', 'POST'])
def login_signup():
    # CRITICAL: Force clear password reset session data on every access to login page
    if request.method == 'GET':
        # Check if there are any reset flags in session
        if 'reset_email' in session or 'otp_verified' in session:
            print(f"DEBUG: Found reset flags in session on login page access. Clearing...")
            print(f"DEBUG: reset_email = {session.get('reset_email')}")
            print(f"DEBUG: otp_verified = {session.get('otp_verified')}")
        
        # Force clear ALL session data if not logged in
        if 'user' not in session:
            print("DEBUG: User not logged in, clearing entire session")
            session.clear()
        else:
            # User is logged in, just clear reset flags
            clear_reset_session()
    
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
                    cur.close()
                    conn.close()
                    return redirect(url_for('login_signup'))
                
                # CRITICAL: Clear entire session first, then set new user session
                session.clear()
                print("DEBUG: Session cleared before login")
                
                # Store user data in session
                session.permanent = True
                session['user'] = {
                    'id': user['id'],
                    'email': user['email'],
                    'fullname': user['fullname']
                }
                session['role'] = user['role']
                
                print("DEBUG: User logged in:", session['user'])
                print("DEBUG: Session keys after login:", list(session.keys()))
                
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    cur.close()
                    conn.close()
                    return jsonify({
                        'success': True,
                        'user': session['user'],
                        'role': user['role']
                    })
                
                cur.close()
                conn.close()
                
                if user['role'] in ['ngo', 'medical_org']:
                    return redirect(url_for('dashboard'))
                else:
                    return redirect(url_for('index'))
            else:
                cur.close()
                conn.close()
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
                cur.close()
                conn.close()
            else:
                if role == 'ngo' and not org_code:
                    flash('Organization code is required for NGO registration', 'danger')
                    cur.close()
                    conn.close()
                    return redirect(url_for('login_signup'))
                hashed_password = generate_password_hash(password)
                cur.execute(
                    'INSERT INTO users (fullname, email, password, role) VALUES (%s, %s, %s, %s) RETURNING id',
                    (fullname, email, hashed_password, role)
                )
                user_id = cur.fetchone()['id']
                conn.commit()
                
                # CRITICAL: Clear entire session first
                session.clear()
                
                # Store user data in session after signup
                session.permanent = True
                session['user'] = {
                    'id': user_id,
                    'email': email,
                    'fullname': fullname
                }
                session['role'] = role
                
                print("DEBUG: User signed up:", session['user'])
                
                cur.close()
                conn.close()
                
                flash('Registration successful! Welcome aboard.', 'success')
                return redirect(url_for('index'))

        cur.close()
        conn.close()
    
    return render_template('Login_SignUp.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    # If user is already logged in, redirect them away
    if 'user' in session:
        flash('You are already logged in. Logout first to reset password.', 'info')
        return redirect(url_for('index'))
    
    # Clear any existing reset session on GET request
    if request.method == 'GET':
        clear_reset_session()
    
    if request.method == 'POST':
        email = request.form.get('email')
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Check if user exists
        cur.execute('SELECT * FROM users WHERE email = %s', (email,))
        user = cur.fetchone()
        
        cur.close()
        conn.close()
        
        if user:
            # Generate OTP
            otp = generate_otp()
            
            # Store OTP with expiration time (10 minutes)
            otp_storage[email] = {
                'otp': otp,
                'expires_at': datetime.now() + timedelta(minutes=10)
            }
            
            # Send OTP via email
            if send_otp_email(email, otp):
                # Clear any old session data first
                clear_reset_session()
                # Set new session data
                session['reset_email'] = email
                print(f"DEBUG: Password reset initiated for {email}")
                flash('OTP sent to your email. Please check your inbox.', 'success')
                return redirect(url_for('verify_otp'))
            else:
                flash('Error sending OTP. Please try again.', 'danger')
        else:
            # For security, don't reveal if email exists or not
            flash('If this email exists, an OTP has been sent.', 'info')
    
    return render_template('forgot_password.html')

@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    # If user is already logged in, redirect them away
    if 'user' in session:
        flash('You are already logged in.', 'info')
        return redirect(url_for('index'))
    
    if 'reset_email' not in session:
        print("DEBUG: No reset_email in session - redirecting to forgot password")
        flash('Please start the password reset process again.', 'warning')
        clear_reset_session()
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        entered_otp = request.form.get('otp')
        email = session['reset_email']
        
        # Check if OTP exists and is valid
        if email in otp_storage:
            stored_data = otp_storage[email]
            
            if datetime.now() > stored_data['expires_at']:
                del otp_storage[email]
                clear_reset_session()
                flash('OTP expired. Please request a new one.', 'danger')
                return redirect(url_for('forgot_password'))
            
            if entered_otp == stored_data['otp']:
                session['otp_verified'] = True
                print(f"DEBUG: OTP verified for {email}")
                return redirect(url_for('reset_password'))
            else:
                flash('Invalid OTP. Please try again.', 'danger')
        else:
            clear_reset_session()
            flash('OTP expired or invalid. Please request a new one.', 'danger')
            return redirect(url_for('forgot_password'))
    
    return render_template('verify_otp.html')

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    # If user is already logged in, redirect them away
    if 'user' in session:
        flash('You are already logged in.', 'info')
        return redirect(url_for('index'))
    
    # Check if they have proper authorization to access this page
    if 'reset_email' not in session or 'otp_verified' not in session:
        print("DEBUG: Unauthorized access to reset password page - redirecting to forgot password")
        flash('Please complete OTP verification first.', 'warning')
        clear_reset_session()
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('reset_password'))
        
        if len(new_password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return redirect(url_for('reset_password'))
        
        email = session.get('reset_email')
        
        if not email:
            print("DEBUG: No email in session during password reset")
            clear_reset_session()
            flash('Session expired. Please start again.', 'warning')
            return redirect(url_for('forgot_password'))
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        try:
            # Update password
            hashed_password = generate_password_hash(new_password)
            cur.execute('UPDATE users SET password = %s WHERE email = %s', (hashed_password, email))
            conn.commit()
            
            print(f"DEBUG: Password reset successful for {email}")
            
            # Clean up OTP storage
            if email in otp_storage:
                del otp_storage[email]
                print(f"DEBUG: Cleared OTP for {email}")
            
        except Exception as e:
            conn.rollback()
            print(f"ERROR: Failed to reset password: {str(e)}")
            flash('Error resetting password. Please try again.', 'danger')
            return redirect(url_for('reset_password'))
        finally:
            cur.close()
            conn.close()
        
        # CRITICAL: Clear all session data including password reset flags
        email_for_msg = email  # Store before clearing
        session.clear()  # Clear entire session
        print(f"DEBUG: All session data cleared for {email_for_msg}")
        
        flash('Password reset successfully! Please login with your new password.', 'success')
        return redirect(url_for('login_signup'))
    
    return render_template('reset_password.html')

@app.route('/resend-otp', methods=['POST'])
def resend_otp():
    if 'reset_email' not in session:
        return jsonify({'success': False, 'message': 'Session expired'})
    
    email = session['reset_email']
    
    # Generate new OTP
    otp = generate_otp()
    
    # Update OTP storage
    otp_storage[email] = {
        'otp': otp,
        'expires_at': datetime.now() + timedelta(minutes=10)
    }
    
    # Send OTP
    if send_otp_email(email, otp):
        return jsonify({'success': True, 'message': 'New OTP sent successfully'})
    else:
        return jsonify({'success': False, 'message': 'Error sending OTP'})

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
        
        # Calculate stats
        stats = {
            'total_listed': len(listed_medicines),
            'total_requests': len(medicine_requests),
            'active_listings': len([m for m in listed_medicines if m['status'] == 'Available']),
            'pending_requests': len([r for r in medicine_requests if r['status'] == 'Pending']),
            'total_donated': len([m for m in listed_medicines if m['status'].lower() == 'accepted']),
            'total_sold': len([r for r in medicine_requests if r['status'].lower() in ['fulfilled', 'completed', 'accepted']]),
            'expired_medicines': len([m for m in listed_medicines if m['expiry_date'] < datetime.now().date()])
        }
        
        cur.close()
        conn.close()
        
        return render_template('MyActivities.html', 
                             listed_medicines=listed_medicines, 
                             medicine_requests=medicine_requests, 
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
                medicine_name = request.form.get('medicine_name')
                quantity = request.form.get('quantity')
                purpose = request.form.get('reason')
                message = request.form.get('message')
                address = request.form.get('address')
                contact = request.form.get('contact')
                
                print("DEBUG: Form data received:", {
                    'medicine_name': medicine_name,
                    'quantity': quantity,
                    'purpose': purpose,
                    'message': message,
                    'address': address,
                    'contact': contact,
                    'user_id': session['user']['id']
                })
                
                # Basic validation
                if not all([medicine_name, quantity, purpose, address, contact]):
                    return jsonify({'success': False, 'message': 'Please fill all required fields'})
                
                # Insert into database
                cur.execute('''
                    INSERT INTO findmedicine (user_id, medicine_name, quantity, purpose, message, address, contact, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'Pending')
                    RETURNING id
                ''', (session['user']['id'], medicine_name, quantity, purpose, message, address, contact))
                
                inserted_id = cur.fetchone()['id']
                conn.commit()
                print("DEBUG: Request inserted successfully with ID:", inserted_id)
                
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
                u.id as donor_id
            FROM medicines m
            JOIN users u ON m.user_id = u.id
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
        
        # Order by most recent first
        query += " ORDER BY m.created_at DESC"
        
        # Execute query
        cur.execute(query, params)
        medicines = cur.fetchall()
        
        print(f"DEBUG: Found {len(medicines)} accepted, non-expired medicines")
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
            
            # Handle image upload
            image_path = None
            if 'image' in request.files:
                image = request.files['image']
                if image.filename != '':
                    # Save image to static/uploads directory
                    image_path = f"uploads/{image.filename}"
                    image.save(os.path.join('static', image_path))
            
            conn = get_db_connection()
            cur = conn.cursor()
            
            cur.execute('''
                INSERT INTO medicines (name, quantity, quantity_unit, expiry_date,category, description, 
                                     donation_type, price, city, area, pincode, user_id, image_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,%s)
            ''', (name, quantity, quantity_unit, expiry_date,category, description, 
                  donation_type, price, city, area, pincode, session['user']['id'], image_path))
            
            conn.commit()
            cur.close()
            conn.close()
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': True, 'message': 'Medicine listed successfully!'})
            else:
                flash('Medicine listed successfully!', 'success')
                return redirect(url_for('my_activities'))
        except Exception as e:
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
    print(f"DEBUG: User logging out: {session.get('user', {}).get('email', 'Unknown')}")
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login_signup'))

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
            INSERT INTO findmedicine (user_id, medicine_name, quantity, purpose, message, address, contact)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', (session['user']['id'], medicine['name'], medicine['quantity'],
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
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': success, 'message': msg})
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('my_activities'))

@app.route('/dashboard')
@login_required
@role_required('ngo')
def dashboard():
    conn = get_db_connection()
    cur = conn.cursor()
    
    last_24_hours = datetime.utcnow() - timedelta(hours=24)
    
    # Medicines (Listed)
    cur.execute('''
        SELECT m.id, u.fullname as user_name, m.name as medicine_name, m.quantity, m.quantity_unit, 
               m.expiry_date, m.donation_type, m.price, m.city, m.status, m.created_at
        FROM medicines m
        JOIN users u ON m.user_id = u.id
        WHERE m.created_at >= %s
        ORDER BY m.created_at DESC
    ''', (last_24_hours,))
    medicines = cur.fetchall()
    for med in medicines:
        med['activity_type'] = 'Listed'
    
    # Requests (Requested)
    cur.execute('''
        SELECT f.id, u.fullname as user_name, f.medicine_name, f.quantity, NULL as quantity_unit, 
               NULL as expiry_date, NULL as donation_type, NULL as price, NULL as city, f.status, f.created_at
        FROM findmedicine f
        JOIN users u ON f.user_id = u.id
        WHERE f.created_at >= %s
        ORDER BY f.created_at DESC
    ''', (last_24_hours,))
    requests = cur.fetchall()
    for req in requests:
        req['activity_type'] = 'Requested'
    
    # Combine and sort by created_at desc
    recent_activities = medicines + requests
    recent_activities.sort(key=lambda x: x['created_at'], reverse=True)
    
    # Get statistics
    cur.execute('SELECT COUNT(*) as total FROM users')
    total_users = cur.fetchone()['total']
    
    # Count only unblocked volunteers
    cur.execute('SELECT COUNT(*) as regular FROM users WHERE role = %s AND is_blocked = FALSE', ('volunteer',))
    regular_users = cur.fetchone()['regular']
    
    # CHANGED: Count total medicines instead of organizers
    cur.execute('SELECT COUNT(*) as total_medicines FROM medicines')
    total_medicines = cur.fetchone()['total_medicines']
    
    # Count successfully donated medicines (Accepted status, excluding expired)
    cur.execute('''
        SELECT COUNT(*) as donated 
        FROM medicines 
        WHERE status = 'Accepted' 
        AND expiry_date >= CURRENT_DATE
    ''')
    medicines_donated = cur.fetchone()['donated']
    
    cur.close()
    conn.close()
    
    return render_template('dashboard.html', 
      recent_activities=recent_activities,
      total_users=total_users,
      regular_users=regular_users,
      total_medicines=total_medicines,  # Changed variable name
      medicines_donated=medicines_donated,
      fullname=session['user']['fullname'] if 'user' in session and 'fullname' in session['user'] else None)

@app.route('/block-user/<int:user_id>', methods=['POST'])
@login_required
@role_required('ngo')
def block_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('UPDATE users SET is_blocked = TRUE WHERE id = %s', (user_id,))
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'User blocked successfully'
        })
    except Exception as e:
        conn.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
    finally:
        cur.close()
        conn.close()

@app.route('/unblock-user/<int:user_id>', methods=['POST'])
@login_required
@role_required('ngo')
def unblock_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('UPDATE users SET is_blocked = FALSE WHERE id = %s', (user_id,))
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'User unblocked successfully'
        })
    except Exception as e:
        conn.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
    finally:
        cur.close()
        conn.close()

@app.route('/get-all-users', methods=['GET'])
@login_required
@role_required('ngo')
def get_all_users():
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('''
            SELECT id, fullname, email, role, is_blocked, created_at 
            FROM users 
            ORDER BY created_at DESC
        ''')
        users = cur.fetchall()
        
        # Convert is_blocked to string
        users_list = []
        for user in users:
            user_dict = dict(user)
            user_dict['is_blocked'] = 'Yes' if user_dict['is_blocked'] else 'No'
            users_list.append(user_dict)
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'users': users_list
        })
    except Exception as e:
        cur.close()
        conn.close()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/get-all-medicines')
@login_required
@role_required('ngo')
def get_all_medicines():
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('''
            SELECT m.*, u.fullname as user_name
            FROM medicines m
            JOIN users u ON m.user_id = u.id
            ORDER BY m.created_at DESC
        ''')
        medicines = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'medicines': [dict(medicine) for medicine in medicines]
        })
    except Exception as e:
        cur.close()
        conn.close()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/get-all-requests')
@login_required
@role_required('ngo')
def get_all_requests():
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('''
            SELECT f.*, u.fullname as user_name
            FROM findmedicine f
            JOIN users u ON f.user_id = u.id
            ORDER BY f.created_at DESC
        ''')
        requests = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'requests': [dict(request) for request in requests]
        })
    except Exception as e:
        cur.close()
        conn.close()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/accept-medicine/<int:id>', methods=['POST'])
@login_required
@role_required('ngo')
def accept_medicine(id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('UPDATE medicines SET status = %s WHERE id = %s', ('Accepted', id))
        conn.commit()
        return jsonify({'success': True, 'message': 'Medicine accepted successfully'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)})
    finally:
        cur.close()
        conn.close()

@app.route('/reject-medicine/<int:id>', methods=['POST'])
@login_required
@role_required('ngo')
def reject_medicine(id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('UPDATE medicines SET status = %s WHERE id = %s', ('Rejected', id))
        conn.commit()
        return jsonify({'success': True, 'message': 'Medicine rejected successfully'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)})
    finally:
        cur.close()
        conn.close()

@app.route('/accept-request/<int:id>', methods=['POST'])
@login_required
@role_required('ngo')
def accept_request(id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('UPDATE findmedicine SET status = %s WHERE id = %s', ('Accepted', id))
        conn.commit()
        return jsonify({'success': True, 'message': 'Request accepted successfully'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)})
    finally:
        cur.close()
        conn.close()

@app.route('/reject-request/<int:id>', methods=['POST'])
@login_required
@role_required('ngo')
def reject_request(id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('UPDATE findmedicine SET status = %s WHERE id = %s', ('Rejected', id))
        conn.commit()
        return jsonify({'success': True, 'message': 'Request rejected successfully'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)})
    finally:
        cur.close()
        conn.close()

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
    
    # Clear any reset session data
    clear_reset_session()
    
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

@app.route('/get-requested-medicine/<int:request_id>')
@login_required
def get_requested_medicine(request_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM findmedicine WHERE id = %s AND user_id = %s', (request_id, session['user']['id']))
    req = cur.fetchone()
    cur.close()
    conn.close()
    if req:
        return jsonify({'success': True, 'request': req})
    else:
        return jsonify({'success': False, 'message': 'Request not found or unauthorized'})

@app.route('/update-medicine-request/<int:request_id>', methods=['POST'])
@login_required
def update_medicine_request(request_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM findmedicine WHERE id = %s AND user_id = %s', (request_id, session['user']['id']))
    req = cur.fetchone()
    if not req:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Request not found or unauthorized'})
    try:
        medicine_name = request.form.get('medicine_name')
        quantity = request.form.get('quantity')
        purpose = request.form.get('purpose')
        message = request.form.get('message')
        address = request.form.get('address')
        contact = request.form.get('contact')
        cur.execute('''
            UPDATE findmedicine SET medicine_name=%s, quantity=%s, purpose=%s, message=%s, address=%s, contact=%s
            WHERE id=%s
        ''', (medicine_name, quantity, purpose, message, address, contact, request_id))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)})
    finally:
        cur.close()
        conn.close()

@app.route('/firebase-login', methods=['POST'])
def firebase_login():
    data = request.get_json()
    email = data.get('email')
    fullname = data.get('fullname')
    uid = data.get('uid')
    if not email or not fullname or not uid:
        return jsonify({'success': False, 'message': 'Missing user info'}), 400
    
    # Clear any reset session data
    clear_reset_session()
    
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
        session.permanent = True
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

# Debug route to check session state
@app.route('/debug-session')
def debug_session():
    """Debug route to check current session state"""
    session_data = {
        'has_user': 'user' in session,
        'has_role': 'role' in session,
        'has_reset_email': 'reset_email' in session,
        'has_otp_verified': 'otp_verified' in session,
        'all_keys': list(session.keys())
    }
    return jsonify(session_data)

# Route to manually clear session (for debugging)
@app.route('/clear-session-debug')
def clear_session_debug():
    """Manually clear all session data"""
    session.clear()
    return redirect(url_for('login_signup'))

# Emergency route to bypass all checks
@app.route('/emergency-login')
def emergency_login():
    """Emergency route to clear everything and go to login"""
    session.clear()
    print("DEBUG: Emergency clear activated")
    return redirect(url_for('login_signup'), code=302)

# Diagnostic route
@app.route('/check-templates')
def check_templates():
    """Check which template would be rendered"""
    info = {
        'session_keys': list(session.keys()),
        'has_user': 'user' in session,
        'has_reset_email': 'reset_email' in session,
        'has_otp_verified': 'otp_verified' in session,
        'user_role': session.get('role', 'None'),
        'endpoint': request.endpoint
    }
    return jsonify(info)

@app.route('/export-users-excel')
@login_required
@role_required('ngo')
def export_users_excel():
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('''
            SELECT id, fullname, email, role, is_blocked, created_at 
            FROM users 
            ORDER BY created_at DESC
        ''')
        users = cur.fetchall()
        
        # Convert to list of dicts
        users_list = []
        for user in users:
            user_dict = dict(user)
            user_dict['is_blocked'] = 'Yes' if user_dict['is_blocked'] else 'No'
            user_dict['created_at'] = str(user_dict['created_at'])
            users_list.append(user_dict)
        
        # Create DataFrame
        df = pd.DataFrame(users_list)
        df.columns = ['ID', 'Full Name', 'Email', 'Role', 'Blocked', 'Created At']
        
        # Create Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Users')
        output.seek(0)
        
        filename = f'users_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"Error exporting users: {str(e)}")
        flash('Error exporting users to Excel', 'danger')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()
        conn.close()


@app.route('/export-medicines-excel')
@login_required
@role_required('ngo')
def export_medicines_excel():
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('''
            SELECT m.id, m.name, m.quantity, m.quantity_unit, m.expiry_date, 
                   m.donation_type, m.price, m.city, m.area, m.pincode, 
                   m.status, m.created_at, u.fullname as user_name
            FROM medicines m
            JOIN users u ON m.user_id = u.id
            ORDER BY m.created_at DESC
        ''')
        medicines = cur.fetchall()
        
        # Convert to list of dicts
        medicines_list = []
        for medicine in medicines:
            med_dict = dict(medicine)
            med_dict['created_at'] = str(med_dict['created_at'])
            med_dict['expiry_date'] = str(med_dict['expiry_date'])
            medicines_list.append(med_dict)
        
        # Create DataFrame
        df = pd.DataFrame(medicines_list)
        df.columns = ['ID', 'Name', 'Quantity', 'Unit', 'Expiry Date', 
                      'Donation Type', 'Price', 'City', 'Area', 'Pincode', 
                      'Status', 'Created At', 'Listed By']
        
        # Create Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Medicines')
        output.seek(0)
        
        filename = f'medicines_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"Error exporting medicines: {str(e)}")
        flash('Error exporting medicines to Excel', 'danger')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()
        conn.close()


@app.route('/export-requests-excel')
@login_required
@role_required('ngo')
def export_requests_excel():
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute('''
            SELECT f.id, f.medicine_name, f.quantity, f.purpose, f.message, 
                   f.address, f.contact, f.status, f.created_at, u.fullname as user_name
            FROM findmedicine f
            JOIN users u ON f.user_id = u.id
            ORDER BY f.created_at DESC
        ''')
        requests = cur.fetchall()
        
        # Convert to list of dicts
        requests_list = []
        for request in requests:
            req_dict = dict(request)
            req_dict['created_at'] = str(req_dict['created_at'])
            requests_list.append(req_dict)
        
        # Create DataFrame
        df = pd.DataFrame(requests_list)
        df.columns = ['ID', 'Medicine Name', 'Quantity', 'Purpose', 'Message', 
                      'Address', 'Contact', 'Status', 'Created At', 'Requested By']
        
        # Create Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Requests')
        output.seek(0)
        
        filename = f'requests_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"Error exporting requests: {str(e)}")
        flash('Error exporting requests to Excel', 'danger')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    setup_database()
    setup_admin()  # Setup admin credentials
    app.run(debug=True, port=5001)