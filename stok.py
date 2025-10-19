# stok.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g, jsonify, send_file
import sqlite3, os
import pandas as pd
import json # JSON işlemleri için
from decorators import login_required # Import the decorator
from datetime import datetime # last_updated için

stok_bp = Blueprint('stok', __name__)

def get_stock_db():
    """
    Kullanıcının stok modülü için veritabanı bağlantısını döndürür.
    Bağlantı, kullanıcının session'ındaki 'stock_db_path' ile kurulur.
    Her istek başına tek bir bağlantı olmasını sağlar.
    """
    user_stock_db_path = session.get('stock_db_path')
    if not user_stock_db_path:
        raise RuntimeError("Kullanıcı stok veritabanı yolu session'da bulunamadı veya kullanıcı giriş yapmamış. Lütfen tekrar giriş yapın.")

    db_attribute_name = f'stock_db_conn_{session.get("user_id")}' 

    if not hasattr(g, db_attribute_name):
        conn = sqlite3.connect(user_stock_db_path)
        conn.row_factory = sqlite3.Row # Kolon isimleriyle verilere erişim için
        setattr(g, db_attribute_name, conn) # g nesnesine bağlantıyı kaydet
    return getattr(g, db_attribute_name)

@stok_bp.before_request
def before_stock_request():
    """
    Her istek öncesinde stok veritabanı bağlantısını açar.
    Bağlantı get_stock_db() içinde yönetiliyor ve session kontrolü orada yapılır.
    """
    pass 

@stok_bp.teardown_request
def teardown_stock_request(exception):
    """
    Her istek sonunda stok veritabanı bağlantısını kapatır.
    """
    user_id = session.get('user_id')
    if user_id:
        db_attribute_name = f'stock_db_conn_{user_id}'
        db = getattr(g, db_attribute_name, None)
        if db is not None:
            db.close()


def init_stock_table(db_path, user_id):
    """
    Kullanıcıya özel stok veritabanını oluşturur ve 'products', 'inventory',
    'stock_columns' ve 'user_column_visibility' tablolarını tanımlar.
    Veritabanı zaten varsa bu işlemi yapmaz.
    `db_path`: Stok veritabanının fiziksel yolu.
    `user_id`: Veritabanı başlatılan kullanıcının ID'si.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # 1. 'products' tablosu
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_product_id INTEGER, 
                name TEXT NOT NULL,
                price REAL NOT NULL
            )
        ''')

        # 2. 'inventory' tablosu (adet ve lokasyon gibi stok bilgileri)
        # UNIQUE(product_id, location) eklendi
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL, -- products tablosuna referans
                quantity INTEGER NOT NULL DEFAULT 0,
                location TEXT NOT NULL DEFAULT '', -- Konum artık boş olamaz, varsayılan boş string
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                UNIQUE(product_id, location) -- Aynı ürünün aynı konumda sadece bir kaydı olabilir
            )
        ''')

        # 3. 'stock_columns' tablosu (ürün özelliklerinin tanımları, bu veritabanı içinde)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_columns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                column_name TEXT UNIQUE NOT NULL, 
                column_type TEXT NOT NULL DEFAULT 'text', 
                options TEXT 
            )
        ''')
        
        # 4. 'user_column_visibility' tablosu (bu kullanıcının sütun görünürlük tercihleri, bu DB içinde)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_column_visibility (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, 
                column_name TEXT NOT NULL,
                is_visible INTEGER NOT NULL DEFAULT 1, 
                UNIQUE(column_name) 
            )
        ''')

        # 'products' tablosuna eksik olabilecek sütunları eklemek için kontrol
        cursor.execute("PRAGMA table_info(products)")
        product_table_columns = [row[1] for row in cursor.fetchall()]

        if 'user_product_id' not in product_table_columns:
            cursor.execute('ALTER TABLE products ADD COLUMN user_product_id INTEGER')
        if 'name' not in product_table_columns:
            cursor.execute('ALTER TABLE products ADD COLUMN name TEXT NOT NULL DEFAULT ""')
        if 'price' not in product_table_columns:
            cursor.execute('ALTER TABLE products ADD COLUMN price REAL NOT NULL DEFAULT 0.0')

        # 'stock_columns' tablosundaki mevcut sütunlara column_type ve options ekle
        cursor.execute("PRAGMA table_info(stock_columns)")
        stock_columns_table_info = [row[1] for row in cursor.fetchall()]

        if 'column_type' not in stock_columns_table_info:
            cursor.execute('ALTER TABLE stock_columns ADD COLUMN column_type TEXT NOT NULL DEFAULT "text"')
        if 'options' not in stock_columns_table_info:
            cursor.execute('ALTER TABLE stock_columns ADD COLUMN options TEXT')
            
        # 'inventory' tablosundaki sütunları kontrol et (yeni sütun eklenmesi durumunda)
        cursor.execute("PRAGMA table_info(inventory)")
        inventory_table_columns = [row[1] for row in cursor.fetchall()]
        if 'product_id' not in inventory_table_columns:
            cursor.execute('ALTER TABLE inventory ADD COLUMN product_id INTEGER')
        if 'quantity' not in inventory_table_columns:
            cursor.execute('ALTER TABLE inventory ADD COLUMN quantity INTEGER NOT NULL DEFAULT 0')
        # location sütununa NOT NULL ve DEFAULT '' eklendiği için kontrolü farklı olabilir
        # Eğer mevcutsa ve eski sürümde NULL olabiliyorsa, UPDATE ile boş stringe çevirmek gerekebilir.
        if 'location' not in inventory_table_columns:
            cursor.execute('ALTER TABLE inventory ADD COLUMN location TEXT NOT NULL DEFAULT ""')
        if 'last_updated' not in inventory_table_columns:
            cursor.execute('ALTER TABLE inventory ADD COLUMN last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP')

        conn.commit()
        print(f"Stok veritabanı oluşturuldu/güncellendi: {db_path} for user {user_id}")


@stok_bp.route('/stok')
@login_required
def stok_listesi():
    """
    Kullanıcının ürün listesini ve dinamik parametre ekleme formlarını gösterir.
    Bu artık 'Ürün Tanımları' sayfasıdır.
    """
    user_id = session.get('user_id')
    db = get_stock_db() 
    cursor = db.cursor()

    edit_data = None
    edit_id = request.args.get('edit')
    if edit_id:
        cursor.execute('SELECT * FROM products WHERE id = ?', (edit_id,))
        edit_data = cursor.fetchone()
        if not edit_data:
            flash("Düzenlenecek ürün bulunamadı veya yetkiniz yok.", "danger")
            return redirect(url_for('stok.stok_listesi'))

    cursor.execute('PRAGMA table_info(products)') 
    product_table_columns_info = cursor.fetchall()
    all_product_column_names = [col['name'] for col in product_table_columns_info]
    
    dynamic_columns_info_dict = {} 
    cursor.execute('SELECT column_name, column_type, options FROM stock_columns')
    for row in cursor.fetchall():
        options_list = []
        db_options_str = row['options'] 

        if db_options_str: 
            db_options_str = db_options_str.strip() 
            try:
                loaded_options = json.loads(db_options_str)
                if isinstance(loaded_options, list): 
                    options_list = loaded_options
                else:
                    options_list = []
            except json.JSONDecodeError as e:
                cleaned_str = db_options_str.replace('[', '').replace(']', '').strip() 
                if cleaned_str: 
                    temp_list = []
                    for opt in cleaned_str.split(','):
                        cleaned_opt = opt.strip().strip("'\"").replace('\\"', '"')
                        if cleaned_opt: 
                            temp_list.append(cleaned_opt)
                    options_list = temp_list
                else:
                    options_list = [] 

        if not isinstance(options_list, list):
            options_list = [] 
        
        dynamic_columns_info_dict[row['column_name']] = {
            'column_type': row['column_type'],
            'options': options_list 
        }

    fixed_columns_meta = {
        'id': {'column_type': 'number', 'options': []}, 
        'user_product_id': {'column_type': 'number', 'options': []},
        'name': {'column_type': 'text', 'options': []},
        'price': {'column_type': 'number', 'options': []},
    }
    
    columns_for_form = [] 
    for col_name in all_product_column_names:
        if col_name not in ('id',): 
            if col_name in fixed_columns_meta:
                columns_for_form.append({'name': col_name, **fixed_columns_meta[col_name]})
            elif col_name in dynamic_columns_info_dict:
                columns_for_form.append({'name': col_name, **dynamic_columns_info_dict[col_name]})

    all_display_columns_with_info = [] 
    for col_name in all_product_column_names:
        if col_name not in ('id',): 
            if col_name in fixed_columns_meta:
                all_display_columns_with_info.append({'name': col_name, **fixed_columns_meta[col_name]})
            elif col_name in dynamic_columns_info_dict:
                all_display_columns_with_info.append({'name': col_name, **dynamic_columns_info_dict[col_name]})
    
    column_visibility_preferences = {}
    cursor.execute('SELECT column_name, is_visible FROM user_column_visibility WHERE user_id = ?', (user_id,))
    for row in cursor.fetchall():
        column_visibility_preferences[row['column_name']] = bool(row['is_visible'])
    
    for col_info in all_display_columns_with_info:
        col_name = col_info['name']
        if col_name not in column_visibility_preferences:
            column_visibility_preferences[col_name] = True 

    cursor.execute('SELECT * FROM products') 
    product_data = cursor.fetchall()

    return render_template('stok.html', 
                           columns_for_form=columns_for_form, 
                           all_display_columns_with_info=all_display_columns_with_info, 
                           stock_data=product_data, 
                           edit_data=edit_data,
                           column_visibility_preferences=column_visibility_preferences,
                           dynamic_columns_info=dynamic_columns_info_dict) 

@stok_bp.route('/stok/add_column', methods=['POST'])
@login_required
def add_column():
    """
    'products' tablosuna yeni bir dinamik sütun ekler ve sütun tipini, seçeneklerini kaydeder.
    """
    db = get_stock_db() 
    cursor = db.cursor()
    user_id = session.get('user_id') 

    new_col_raw = request.form['new_column'].strip()
    column_type = request.form.get('column_type', 'text') 
    options_raw = request.form.get('options_hidden_input', '').strip() 

    if not new_col_raw:
        flash('Parametre adı boş bırakılamaz.', 'danger')
        return redirect(url_for('stok.stok_listesi'))

    new_col = ''.join(c for c in new_col_raw if c.isalnum() or c == ' ').replace(' ', '_').lower()
    if not new_col or new_col in ['id', 'name', 'price', 'user_product_id']: 
        flash('Geçersiz veya sisteme ait bir parametre adı girdiniz.', 'danger')
        return redirect(url_for('stok.stok_listesi'))

    options_json = None
    if column_type == 'select':
        options_list = [opt.strip() for opt in options_raw.split(',') if opt.strip()]
        if not options_list:
            flash('Seçenekli parametre için en az bir seçenek girilmelidir.', 'danger')
            return redirect(url_for('stok.stok_listesi'))
        options_json = json.dumps(options_list)

    try:
        cursor.execute("PRAGMA table_info(products)")
        existing_product_columns = [row['name'].lower() for row in cursor.fetchall()]

        if new_col in existing_product_columns:
            flash(f'"{new_col_raw}" adında bir parametre zaten mevcut. Lütfen başka bir ad seçin.', 'warning')
        else:
            cursor.execute(f'ALTER TABLE products ADD COLUMN {new_col} TEXT DEFAULT ""') 
            
            cursor.execute('''
                INSERT OR IGNORE INTO stock_columns (column_name, column_type, options) 
                VALUES (?, ?, ?)
            ''', (new_col, column_type, options_json))
            
            cursor.execute('''
                INSERT OR REPLACE INTO user_column_visibility (user_id, column_name, is_visible)
                VALUES (?, ?, ?)
            ''', (user_id, new_col, 1)) 
            db.commit()
            flash(f'"{new_col_raw}" parametresi ({column_type}) başarıyla eklendi.', 'success')
    except sqlite3.IntegrityError:
        flash(f'Veritabanı hatası: "{new_col_raw}" adında bir parametre zaten mevcut veya başka bir çakışma oldu.', 'danger')
    except sqlite3.OperationalError as e:
        flash(f'Parametre eklenirken bir veritabanı hatası oluştu: {e}.', 'danger')
    except Exception as e:
        flash(f'Bilinmeyen bir hata oluştu: {e}', "danger")

    return redirect(url_for('stok.stok_listesi'))


@stok_bp.route('/stok/add_product', methods=['POST'])
@login_required
def add_product():
    """
    'products' tablosuna yeni bir ürün ekler ve 'inventory' tablosuna varsayılan adet ile kayıt ekler.
    """
    user_id = session.get('user_id')
    db = get_stock_db() 
    cursor = db.cursor()

    cursor.execute('PRAGMA table_info(products)') 
    columns_info = cursor.fetchall()
    all_product_column_names = [col['name'] for col in columns_info]
    
    dynamic_columns_info_dict = {}
    cursor.execute('SELECT column_name, column_type, options FROM stock_columns')
    for row in cursor.fetchall():
        options_list = []
        if row['options']:
            try:
                loaded_options = json.loads(row['options'])
                if isinstance(loaded_options, list):
                    options_list = loaded_options
            except json.JSONDecodeError:
                cleaned_str = row['options'].strip()
                cleaned_str = cleaned_str.replace('[', '').replace(']', '').strip()
                options_list = [
                    opt.strip().strip("'\"").replace('\\"', '"')
                    for opt in cleaned_str.split(',') if opt.strip()
                ]
        dynamic_columns_info_dict[row['column_name']] = {
            'column_type': row['column_type'],
            'options': options_list
        }

    cursor.execute('SELECT MAX(user_product_id) FROM products') 
    result = cursor.fetchone()[0]
    max_user_id = int(result) if result is not None and str(result).strip() != '' else 0
    next_user_id = max_user_id + 1

    insert_columns = ['user_product_id'] 
    insert_values = [next_user_id]

    name = request.form.get('name', '').strip()
    price_str = request.form.get('price', '').strip()

    if not name:
        flash("Ürün adı boş bırakılamaz.", "danger")
        return redirect(url_for('stok.stok_listesi'))
    
    try:
        price = float(price_str)
    except ValueError:
        flash("Fiyat geçerli bir sayı olmalıdır.", "danger")
        return redirect(url_for('stok.stok_listesi'))

    insert_columns.append('name')
    insert_values.append(name)
    insert_columns.append('price')
    insert_values.append(price)

    for col_name in all_product_column_names:
        if col_name not in ['id', 'user_product_id', 'name', 'price']: 
            col_type = dynamic_columns_info_dict.get(col_name, {}).get('column_type', 'text')
            col_options = dynamic_columns_info_dict.get(col_name, {}).get('options', [])
            value = request.form.get(col_name, '').strip()

            if col_type == 'number':
                try:
                    value = float(value) if value else None 
                except ValueError:
                    flash(f'"{col_name.replace("_", " ").title()}" parametresi sayısal bir değer olmalıdır.', 'danger')
                    return redirect(url_for('stok.stok_listesi'))
            elif col_type == 'select':
                if value and value not in col_options:
                    flash(f'"{col_name.replace("_", " ").title()}" için geçersiz seçenek seçildi.', 'danger')
                    return redirect(url_for('stok.stok_listesi'))
                
            insert_columns.append(col_name)
            insert_values.append(value)
            
    placeholders = ', '.join(['?'] * len(insert_values))
    column_str = ', '.join(insert_columns)

    try:
        cursor.execute(f'INSERT INTO products ({column_str}) VALUES ({placeholders})', insert_values)
        product_id = cursor.lastrowid 

        # Ürün oluşturulduğunda varsayılan olarak bir envanter kaydı ekleyelim (konumsuz, 0 adet)
        cursor.execute('INSERT INTO inventory (product_id, quantity, location) VALUES (?, ?, ?)', (product_id, 0, ''))

        db.commit()
        flash("Ürün başarıyla eklendi.", "success")
    except Exception as e:
        flash(f'Ürün eklenirken bir hata oluştu: {e}', "danger")

    return redirect(url_for('stok.stok_listesi'))

@stok_bp.route('/stok/delete_product/<int:product_id>', methods=['POST'])
@login_required
def delete_product(product_id):
    """
    Belirtilen ID'ye sahip ürünü 'products' tablosundan siler.
    CASCADE kısıtlaması sayesinde 'inventory' tablosundaki ilgili kayıtlar da silinir.
    """
    db = get_stock_db() 
    cursor = db.cursor()

    try:
        cursor.execute('DELETE FROM products WHERE id = ?', (product_id,)) 
        db.commit()
        if cursor.rowcount > 0:
            flash("Ürün başarıyla silindi.", "success")
        else:
            flash("Silinecek ürün bulunamadı veya yetkiniz yok.", "warning")
    except Exception as e:
        flash(f'Ürün silinirken bir hata oluştu: {e}', "danger")

    return redirect(url_for('stok.stok_listesi'))


@stok_bp.route('/stok/update_product/<int:product_id>', methods=['POST'])
@login_required
def update_product(product_id):
    """
    Belirtilen ID'ye sahip ürünün bilgilerini 'products' tablosunda günceller.
    Form verilerini sütun tiplerine göre doğrular.
    """
    db = get_stock_db() 
    cursor = db.cursor()

    cursor.execute('PRAGMA table_info(products)') 
    columns_info = cursor.fetchall()
    all_product_column_names = [col['name'] for col in columns_info]
    
    dynamic_columns_info_dict = {}
    cursor.execute('SELECT column_name, column_type, options FROM stock_columns')
    for row in cursor.fetchall():
        options_list = []
        if row['options']:
            try:
                loaded_options = json.loads(row['options'])
                if isinstance(loaded_options, list):
                    options_list = loaded_options
            except json.JSONDecodeError:
                cleaned_str = row['options'].strip()
                cleaned_str = cleaned_str.replace('[', '').replace(']', '').strip()
                options_list = [
                    opt.strip().strip("'\"").replace('\\"', '"')
                    for opt in cleaned_str.split(',') if opt.strip()
                ]
        dynamic_columns_info_dict[row['column_name']] = {
            'column_type': row['column_type'],
            'options': options_list
        }

    update_set_parts = []
    update_values = []

    name = request.form.get('name', '').strip()
    price_str = request.form.get('price', '').strip()

    if not name:
        flash("Ürün adı boş bırakılamaz.", "danger")
        return redirect(url_for('stok.stok_listesi'))
    
    try:
        price = float(price_str)
    except ValueError:
        flash("Fiyat geçerli bir sayı olmalıdır.", "danger")
        return redirect(url_for('stok.stok_listesi'))

    update_set_parts.append(f"name = ?")
    update_values.append(name)
    update_set_parts.append(f"price = ?")
    update_values.append(price)

    for col_name in all_product_column_names:
        if col_name not in ['id', 'user_product_id', 'name', 'price']: 
            col_type = dynamic_columns_info_dict.get(col_name, {}).get('column_type', 'text')
            col_options = dynamic_columns_info_dict.get(col_name, {}).get('options', [])
            value = request.form.get(col_name, '').strip()

            if col_type == 'number':
                try:
                    value = float(value) if value else None
                except ValueError:
                    flash(f'"{col_name.replace("_", " ").title()}" parametresi sayısal bir değer olmalıdır.', 'danger')
                    return redirect(url_for('stok.stok_listesi'))
            elif col_type == 'select':
                if value and value not in col_options:
                    flash(f'"{col_name.replace("_", " ").title()}" için geçersiz seçenek seçildi.', 'danger')
                    return redirect(url_for('stok.stok_listesi'))
            
            update_set_parts.append(f"{col_name} = ?")
            update_values.append(value)
            
    set_expr = ', '.join(update_set_parts)

    try:
        cursor.execute(f'UPDATE products SET {set_expr} WHERE id = ?', (*update_values, product_id))
        db.commit()
        if cursor.rowcount > 0:
            flash("Ürün başarıyla güncellendi.", "success")
        else:
            flash("Güncellenecek ürün bulunamadı veya yetkiniz yok.", "warning")
    except Exception as e:
        flash(f'Ürün güncellenirken bir hata oluştu: {e}', "danger")

    return redirect(url_for('stok.stok_listesi'))

@stok_bp.route('/stok/rename_column', methods=['POST'])
@login_required
def rename_column():
    """
    'products' tablosundaki bir sütunun adını değiştirir.
    """
    user_id = session.get('user_id') 
    db = get_stock_db() 
    cursor = db.cursor()

    old_column_name = request.form.get('old_column_name')
    new_column_raw = request.form.get('new_column_name', '').strip() 

    if not old_column_name or not new_column_raw:
        flash("Hem eski hem de yeni parametre adı girilmelidir.", "danger")
        return redirect(url_for('stok.stok_listesi'))

    new_column_safe_name = ''.join(c for c in new_column_raw if c.isalnum() or c == ' ').replace(' ', '_').lower()

    if not new_column_safe_name or new_column_safe_name in ['id', 'name', 'price', 'user_product_id']:
        flash('Yeni parametre adı geçersiz veya sisteme ait bir isim olamaz.', 'danger')
        return redirect(url_for('stok.stok_listesi'))

    try:
        cursor.execute("PRAGMA table_info(products)")
        existing_columns = [row['name'].lower() for row in cursor.fetchall()]

        if old_column_name.lower() not in existing_columns:
            flash(f'"{old_column_name.replace("_", " ").title()}" adında bir parametre bulunamadı.', 'danger')
            return redirect(url_for('stok.stok_listesi'))
        
        if new_column_safe_name in existing_columns:
            flash(f'"{new_column_raw}" adında bir parametre zaten mevcut. Lütfen başka bir ad seçin.', 'warning')
            return redirect(url_for('stok.stok_listesi'))

        cursor.execute(f'ALTER TABLE products RENAME COLUMN {old_column_name} TO {new_column_safe_name}')
        
        cursor.execute('UPDATE stock_columns SET column_name = ? WHERE column_name = ?', (new_column_safe_name, old_column_name))
        
        cursor.execute('UPDATE user_column_visibility SET column_name = ? WHERE column_name = ? AND user_id = ?', (new_column_safe_name, old_column_name, user_id))
        db.commit()
        flash(f'Parametre adı "{old_column_name.replace("_", " ").title()}" başarıyla "{new_column_raw}" olarak değiştirildi.', 'success')
    except sqlite3.OperationalError as e:
        flash(f'Parametre adı değiştirilirken bir hata oluştu: {e}', 'danger')
    except Exception as e:
        flash(f'Bilinmeyen bir hata oluştu: {e}', 'danger')

    return redirect(url_for('stok.stok_listesi'))

@stok_bp.route('/stok/update_column_options', methods=['POST'])
@login_required
def update_column_options():
    """
    Belirli bir 'select' tipindeki sütunun seçeneklerini günceller.
    Frontend'den virgülle ayrılmış string olarak gelir.
    """
    db = get_stock_db() 
    cursor = db.cursor()

    column_name = request.form.get('column_name')
    options_raw = request.form.get('options_input', '').strip() 

    if not column_name:
        return jsonify({'status': 'error', 'message': 'Seçenekleri güncellemek için parametre adı belirtilmelidir.'}), 400

    options_list = [opt.strip() for opt in options_raw.split(',') if opt.strip()]
    
    options_json = json.dumps(options_list) 

    try:
        cursor.execute('SELECT column_type FROM stock_columns WHERE column_name = ?', (column_name,))
        col_info = cursor.fetchone()

        if not col_info:
            return jsonify({'status': 'error', 'message': f'"{column_name.replace("_", " ").title()}" adında bir parametre bulunamadı.'}), 404
        
        if col_info['column_type'] != 'select':
            return jsonify({'status': 'error', 'message': f'"{column_name.replace("_", " ").title()}" parametresi "Seçenekli Liste" tipinde değil. Seçenekleri yalnızca "Seçenekli Liste" tipli parametreler için düzenleyebilirsiniz.'}), 400

        cursor.execute('UPDATE stock_columns SET options = ? WHERE column_name = ?', (options_json, column_name))
        db.commit()
        return jsonify({'status': 'success', 'message': f'"{column_name.replace("_", " ").title()}" parametresinin seçenekleri başarıyla güncellendi.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Seçenekler güncellenirken bir hata oluştu: {e}'}), 500


@stok_bp.route('/stok/toggle_column_visibility', methods=['POST'])
@login_required
def toggle_column_visibility():
    """
    Kullanıcının belirli bir sütunun görünürlüğünü açıp kapatmasını sağlar.
    Tercihler veritabanında saklanır. Bu işlem artık flash mesajı göstermez.
    """
    user_id = session.get('user_id')
    db = get_stock_db() 
    cursor = db.cursor()

    column_name = request.form.get('column_name')
    is_visible = request.form.get('is_visible') == 'true' 

    if not column_name:
        return jsonify({'status': 'error', 'message': 'Sütun adı boş olamaz.'}), 400

    if column_name in ['id', 'name', 'price', 'user_product_id']: 
        return jsonify({'status': 'error', 'message': f'"{column_name}" sütununun görünürlüğü değiştirilemez.'}), 403

    try:
        cursor.execute('''
            INSERT OR REPLACE INTO user_column_visibility (user_id, column_name, is_visible)
            VALUES (?, ?, ?)
        ''', (user_id, column_name, 1 if is_visible else 0))
        db.commit()
        return jsonify({'status': 'success', 'message': f'Column {column_name} visibility set to {is_visible}'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error updating column visibility: {e}'}), 500


@stok_bp.route('/stok/export')
@login_required
def export_excel():
    """
    Ürün verilerini kullanıcının görünürlük tercihlerine göre Excel dosyası olarak dışa aktarır.
    Sadece tabloda görünen sütunlar (ve temel sütunlar) dışa aktarılır.
    Bu artık 'products' tablosu içindir. Envanter bilgisi dahil edilmemiştir.
    """
    user_id = session.get('user_id')
    db = get_stock_db() 
    cursor = db.cursor()

    column_visibility_preferences = {}
    cursor.execute('SELECT column_name, is_visible FROM user_column_visibility WHERE user_id = ?', (user_id,))
    for row in cursor.fetchall():
        column_visibility_preferences[row['column_name']] = bool(row['is_visible'])

    cursor.execute('PRAGMA table_info(products)') 
    all_db_column_names = [col['name'] for col in cursor.fetchall()]

    dynamic_columns_info_dict = {}
    cursor.execute('SELECT column_name, column_type, options FROM stock_columns')
    for row in cursor.fetchall():
        options_list = []
        db_options_str = row['options']
        if db_options_str:
            db_options_str = db_options_str.strip()
            try:
                loaded_options = json.loads(db_options_str)
                if isinstance(loaded_options, list):
                    options_list = loaded_options
            except json.JSONDecodeError:
                cleaned_str = db_options_str.replace('[', '').replace(']', '').strip()
                options_list = [
                    opt.strip().strip("'\"").replace('\\"', '"')
                    for opt in cleaned_str.split(',') if opt.strip()
                ]

        dynamic_columns_info_dict[row['column_name']] = {
            'column_type': row['column_type'],
            'options': options_list
        }
    
    columns_to_export = []
    fixed_columns = ['id', 'user_product_id', 'name', 'price'] 

    for col_name in fixed_columns:
        if col_name in all_db_column_names:
            columns_to_export.append(col_name)

    for col_name in all_db_column_names:
        if col_name in columns_to_export: 
            continue
        
        if column_visibility_preferences.get(col_name, True):
            columns_to_export.append(col_name)

    if not columns_to_export:
        flash('Excel\'e aktarılacak görünür sütun bulunamadı.', 'warning')
        return redirect(url_for('stok.stok_listesi'))

    columns_str_for_query = ', '.join(columns_to_export)
    
    try:
        cursor.execute(f'SELECT {columns_str_for_query} FROM products') 
        rows = cursor.fetchall() 

        data_for_df = []
        for row in rows:
            row_dict = {}
            for col_name in columns_to_export:
                value = row[col_name]
                
                if col_name in ['id', 'user_product_id'] and value is not None:
                     value = int(value) 
                elif dynamic_columns_info_dict.get(col_name, {}).get('column_type') == 'number' and value is None:
                    value = '' 

                row_dict[col_name] = value
            data_for_df.append(row_dict)

        df = pd.DataFrame(data_for_df)

        from io import BytesIO
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='ÜrünVerileri') 
        output.seek(0)
        return send_file(output, as_attachment=True,
                        download_name='urun_verileri.xlsx', 
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        flash(f'Excel dışa aktarılırken bir hata oluştu: {e}', 'danger')
        return redirect(url_for('stok.stok_listesi'))


# --- YENİ ENVANTER YÖNETİMİ BÖLÜMÜ ---

@stok_bp.route('/stok/envanter')
@login_required
def envanter_listesi():
    """
    Kullanıcının ürünlerinin envanter bilgilerini gösterir.
    Ürün tanımları (products) ve envanter (inventory) tablolarını birleştirir.
    Her ürün için birden fazla envanter kaydı gösterebilir.
    Ürün adı ve konum filtrelemesi eklendi.
    """
    user_id = session.get('user_id')
    db = get_stock_db()
    cursor = db.cursor()

    # Filtreleme parametrelerini al
    product_name_filter = request.args.get('product_name_filter', '').strip()
    location_filter = request.args.get('location_filter', '').strip()

    # Ürünleri çeken ana sorguyu oluştur
    # price sütununu da dahil et
    product_query = 'SELECT id, user_product_id, name, price FROM products' 
    product_query_params = []
    
    # Filtreleri SQL sorgusuna ekle
    product_filters = []
    if product_name_filter:
        product_filters.append('name LIKE ?')
        product_query_params.append(f'%{product_name_filter}%')
    
    if product_filters:
        product_query += ' WHERE ' + ' AND '.join(product_filters)
    
    product_query += ' ORDER BY user_product_id'

    cursor.execute(product_query, product_query_params)
    products = cursor.fetchall()

    # Her ürün için envanter kayıtlarını topla
    product_inventory_data = []
    for product in products:
        product_dict = dict(product) # Row objesini dict'e çevir
        
        # Envanter kayıtları için ayrı bir sorgu ve filtreleme
        inventory_query = '''
            SELECT 
                id AS inventory_id, 
                quantity, 
                location, 
                last_updated 
            FROM inventory 
            WHERE product_id = ? 
        '''
        inventory_query_params = [product['id']]
        
        if location_filter:
            inventory_query += ' AND location = ?' # Konum için tam eşleşme
            inventory_query_params.append(location_filter)
        
        inventory_query += ' ORDER BY location'

        cursor.execute(inventory_query, inventory_query_params)
        inventory_entries = cursor.fetchall()
        
        # Eğer envanter kaydı yoksa (veya filtreye uyan yoksa), varsayılan bir boş kayıt ekle (0 adet, boş konum)
        # Ancak, eğer bir konum filtresi varsa ve bu ürünün bu konumda kaydı yoksa, bu ürünü listelemememiz gerekir.
        # Bu durumda, eğer bu ürün için filtrelenmiş envanter kaydı gelmiyorsa, ana ürünü de atla.
        if not inventory_entries and location_filter:
            continue # Konum filtresi varsa ve bu ürün o konumda yoksa, ürünü tamamen atla.
        
        if not inventory_entries: # Eğer hiç kayıt yoksa ve konum filtresi yoksa, varsayılan boş kayıt ekle
             inventory_entries = [{
                'inventory_id': None, # Yeni kayıt olacağı için ID'si yok
                'quantity': 0,
                'location': '',
                'last_updated': 'N/A'
            }]
        
        product_dict['inventory_entries'] = [dict(entry) for entry in inventory_entries] # Nested dict listesi
        product_inventory_data.append(product_dict)
    
    # Tüm benzersiz lokasyonları çek (konum dropdown'ları için)
    cursor.execute('SELECT DISTINCT location FROM inventory WHERE location IS NOT NULL AND location != "" ORDER BY location')
    existing_locations = [row['location'] for row in cursor.fetchall()]

    return render_template('envanter.html', 
                           product_inventory_data=product_inventory_data, # Yeni değişken adı
                           existing_locations=existing_locations,
                           # Filtre değerlerini şablona geri gönder
                           product_name_filter=product_name_filter,
                           location_filter=location_filter)


@stok_bp.route('/stok/update_inventory', methods=['POST'])
@login_required
def update_inventory():
    """
    Belirli bir ürünün belirli bir konumdaki envanter miktarını günceller.
    Eğer ürün için bu konumda envanter kaydı yoksa yeni bir kayıt oluşturur.
    """
    db = get_stock_db()
    cursor = db.cursor()

    product_id = request.form.get('product_id')
    inventory_id = request.form.get('inventory_id') # Mevcut bir kaydı güncelliyorsak
    quantity_str = request.form.get('quantity')
    location = request.form.get('location', '').strip() 

    if not product_id or not quantity_str:
        return jsonify({'status': 'error', 'message': 'Ürün ID ve adet bilgileri zorunludur.'}), 400

    try:
        product_id = int(product_id)
        quantity = int(quantity_str)
        if quantity < 0:
            return jsonify({'status': 'error', 'message': 'Adet negatif olamaz.'}), 400
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Geçersiz ürün ID veya adet formatı.'}), 400

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        if inventory_id and inventory_id != 'None': # Mevcut bir envanter kaydını güncelliyoruz
            inventory_id = int(inventory_id)
            cursor.execute('''
                UPDATE inventory
                SET quantity = ?, location = ?, last_updated = ?
                WHERE id = ? AND product_id = ?
            ''', (quantity, location, current_time, inventory_id, product_id))
            if cursor.rowcount == 0:
                # Güncelleme başarısız olursa (ID/product_id eşleşmezse)
                return jsonify({'status': 'error', 'message': 'Envanter kaydı bulunamadı veya yetkiniz yok.'}), 404
        else: # Yeni bir envanter kaydı ekliyoruz (product_id ve location kombinasyonu için)
            # UNIQUE(product_id, location) constraint'i burada devreye girecek
            cursor.execute('''
                INSERT OR REPLACE INTO inventory (product_id, quantity, location, last_updated)
                VALUES (?, ?, ?, ?)
            ''', (product_id, quantity, location, current_time))
        
        db.commit()
        return jsonify({'status': 'success', 'message': 'Envanter başarıyla güncellendi!'})

    except sqlite3.IntegrityError as e:
        # Bu hata UNIQUE(product_id, location) ihlalinde oluşur
        db.rollback()
        return jsonify({'status': 'error', 'message': f'Bu ürün için aynı konumda zaten bir envanter kaydı mevcut. Lütfen mevcut kaydı güncelleyin veya farklı bir konum seçin.'}), 400
    except Exception as e:
        db.rollback() 
        return jsonify({'status': 'error', 'message': f'Envanter güncellenirken bir hata oluştu: {e}'}), 500

@stok_bp.route('/stok/delete_inventory_entry/<int:inventory_id>', methods=['POST'])
@login_required
def delete_inventory_entry(inventory_id):
    """
    Belirtilen envanter kaydını (quantity-location çifti) siler.
    """
    db = get_stock_db()
    cursor = db.cursor()
    try:
        cursor.execute('DELETE FROM inventory WHERE id = ?', (inventory_id,))
        db.commit()
        # Yanıtı JSON olarak döndür, redirect yapma
        return jsonify({'status': 'success', 'message': 'Envanter kaydı başarıyla silindi.'})
    except Exception as e:
        db.rollback()
        return jsonify({'status': 'error', 'message': f'Envanter kaydı silinirken bir hata oluştu: {e}'}), 500







