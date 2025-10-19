# decorators.py
from functools import wraps
from flask import session, redirect, url_for, flash

def login_required(f):
    """
    Giriş yapılmasını zorunlu kılan bir dekoratör.
    Bu dekoratör ile işaretlenmiş rotalara sadece giriş yapmış kullanıcılar erişebilir.
    Giriş yapmamışlarsa, giriş sayfasına yönlendirilirler ve bir hata mesajı alırlar.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('Bu sayfaya erişmek için giriş yapmalısınız.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
