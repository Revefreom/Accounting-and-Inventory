# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, session, g
import sqlite3
import os
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
from decorators import login_required
# stok.py'den init_stock_table'ı ve blueprint'i içe aktar
from stok import stok_bp, init_stock_table 

app = Flask(__name__)
app.secret_key = secrets.token_hex(16) # Güvenli bir secret key oluştur

# Veritabanı yolları
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
USERS_DB_PATH = os.path.join(BASE_DIR, 'users.db')
# Her kullanıcı için dinamik stok veritabanlarını tutacak klasör
STOCK_DB_DIR = os.path.join(BASE_DIR, 'stock_dbs') 

# stock_dbs dizininin var olduğundan emin olun
if not os.path.exists(STOCK_DB_DIR):
    os.makedirs(STOCK_DB_DIR)

# SQLite bağlantısı
def get_db():
    """Genel kullanıcı veritabanı (users.db) bağlantısını döndürür."""
    if not hasattr(g, 'user_db'):
        g.user_db = sqlite3.connect(USERS_DB_PATH)
        g.user_db.row_factory = sqlite3.Row
    return g.user_db

# Uygulama kapatıldığında veritabanı bağlantısını kapat
@app.teardown_appcontext
def close_db(error):
    """Uygulama bağlamı sona erdiğinde kullanıcı veritabanı bağlantısını kapatır."""
    if hasattr(g, 'user_db'):
        g.user_db.close()

# Kullanıcılar veritabanını başlat (users.db)
def init_user_db():
    """
    Users veritabanını ve 'users' tablosunu oluşturur/günceller.
    'stock_db_path' sütununu ekler.
    """
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                stock_db_path TEXT UNIQUE -- Kullanıcıya özel stok veritabanı yolu
            )
        ''')
        # Mevcut 'users' tablosuna 'stock_db_path' sütununu eklemek için kontrol
        cursor.execute("PRAGMA table_info(users)")
        user_table_columns = [row[1] for row in cursor.fetchall()]
        if 'stock_db_path' not in user_table_columns:
            cursor.execute('ALTER TABLE users ADD COLUMN stock_db_path TEXT UNIQUE')
        
        db.commit()
        print("Users veritabanı oluşturuldu/güncellendi.")

# Blueprint'i kaydet
app.register_blueprint(stok_bp)

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        db = get_db() # users.db bağlantısı
        cursor = db.cursor()

        try:
            # Kullanıcıyı ekle (stock_db_path başlangıçta NULL olabilir)
            cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
            user_id = cursor.lastrowid # Yeni kullanıcının ID'sini al

            # Kullanıcıya özel stok veritabanı yolunu oluştur
            user_stock_db_name = f'stock_{user_id}.db'
            user_stock_db_path = os.path.join(STOCK_DB_DIR, user_stock_db_name)

            # users tablosundaki 'stock_db_path' sütununu güncelle
            cursor.execute("UPDATE users SET stock_db_path = ? WHERE id = ?", (user_stock_db_path, user_id))
            db.commit()

            # Kullanıcıya özel stok veritabanı dosyasını ve tablolarını başlat
            # Bu çağrı, stok modülündeki init_stock_table fonksiyonunu kullanır.
            init_stock_table(user_stock_db_path, user_id) 

            flash("Hesabınız başarıyla oluşturuldu!", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Bu kullanıcı adı zaten mevcut.", "danger")
        except Exception as e:
            flash(f"Kayıt olurken bir hata oluştu: {e}", "danger")

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db() # users.db bağlantısı
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['stock_db_path'] = user['stock_db_path'] # Kullanıcının stok db yolunu session'a kaydet
            flash("Giriş başarılı!", "success")
            return redirect(url_for('dashboard'))
        else:
            flash("Geçersiz kullanıcı adı veya şifre.", "danger")
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('stock_db_path', None) # Session'dan da kaldır
    flash("Başarıyla çıkış yaptınız.", "info")
    return redirect(url_for('home'))

if __name__ == '__main__':
    # init_user_db() uygulamanın başlatılmasında bir kez çağrılmalı.
    init_user_db() 
    app.run(debug=True)

