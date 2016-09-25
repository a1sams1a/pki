from lib.core import USR_PATH, INT_USR_CRL, issue, revoke, gen_crl
from lib.sparcsssov2 import Client
from flask import Flask, request, redirect, \
        render_template, make_response, send_file
from functools import wraps
from settings import *
from OpenSSL import crypto as c
from datetime import datetime, timedelta
import hmac
import os
import time


BASE_PATH = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.config.from_pyfile(os.path.join(BASE_PATH, 'settings.py'))

client = Client(SSO_CLIENT_ID, SSO_CLIENT_KEY)

def hash_compare(hash1, hash2):
    if len(hash1) != len(hash2):
        return False

    for index in range(0, len(hash1)):
        if hash1[index] != hash2[index]:
            return False
    return True


def generate_cookie(username, sid, expire):
    d = '%s:%s:%s' % (username, sid, expire)
    m = hmac.new(app.secret_key, d).hexdigest()
    return '%s:%s' % (d, m)


def parse_cookie(cookie):
    l = cookie.strip().split(':')
    if len(l) != 4:
        return None, None, 0

    m = hmac.new(app.secret_key, ':'.join(l[:3])).hexdigest()
    if not hash_compare(m, str(l[3])):
        return None, None, 0
    return l[0], l[1], int(l[2])


def get_session(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        cookie = request.cookies.get('sso', '')
        username, sid, expire = parse_cookie(cookie)
        if username and int(time.time()) < expire:
            kwargs['auth_info'] = {
                'username': username,
                'sid': sid,
                'expire': expire,
            }
        else:
            kwargs['auth_info'] = {'username': '', 'expire': 0}
        return f(*args, **kwargs)
    return decorated


def get_state(username):
    user_crt = os.path.join(USR_PATH, '%s.crt' % username)
    if not os.path.exists(user_crt):
        return 'none', 0

    with file(user_crt, 'r') as f:
        cert = c.load_certificate(c.FILETYPE_PEM, f.read())

    serial = format(cert.get_serial_number(), 'x')
    with open(INT_USR_CRL, 'r') as f:
        crl = "".join(f.readlines())
        crl_obj = c.load_crl(c.FILETYPE_PEM, crl)
        for rvk in crl_obj.get_revoked():
            if rvk.get_serial() == serial:
                return 'revoked', 0

    expire = datetime.strptime(cert.get_notAfter(),"%Y%m%d%H%M%SZ")
    if expire < datetime.now():
        return 'expired', expire
    elif expire < datetime.now() - timedelta(days=10):
        return 'warn', expire
    return 'ok', expire


@app.route('/login/')
def login_init():
    login_url, state = client.get_login_params()
    return redirect(login_url)


@app.route('/login/callback/')
def login_callback():
    code = request.args.get('code', '')
    try:
        user_data = client.get_user_info(code)
    except:
        return redirect('/')

    cookie = generate_cookie(user_data['sparcs_id'],
                             user_data['sid'],
                             int(time.time()) + 600)
    resp = make_response(redirect('/'))
    resp.set_cookie('sso', cookie, secure=(not app.debug))
    return resp


@app.route('/logout/')
@get_session
def logout(auth_info=None):
    if not auth_info['username']:
        return redirect('/')

    logout_url = client.get_logout_url(auth_info['sid'], 'https://' + request.host)
    resp = redirect(logout_url)
    resp.set_cookie('sso', '', expires=0, secure=(not app.debug))
    return resp


@app.route('/unregister/')
def unregister(auth_info=None):
    return '<script>alert("You CANNOT unregister."); window.history.back();</script>'


@app.route('/')
@get_session
def main(auth_info=None):
    username = auth_info['username']
    s_expire = auth_info['expire'] - int(time.time())
    state, c_expire = get_state(username)
    return render_template('main.html', username=username,
                                        s_expire=s_expire,
                                        c_expire=c_expire,
                                        state=state)


@app.route('/mgt/')
@get_session
def mgt(auth_info=None):
    username = auth_info['username']
    if not username:
        return redirect('/')

    state, c_expire = get_state(username)
    user_p12 = os.path.join(USR_PATH, '%s.p12' % username)

    try:
        if state in ['revoked', 'expired', 'none']:
            issue(username)
        elif state in ['warn', ]:
            revoke(username)
            gen_crl()
            issue(username)
    except Exception as e:
        return '<script>alert("Unknown error is occurred."); window.history.back();</script>'

    return send_file(user_p12, mimetype='application/x-pkcs12',
                     as_attachment=True,
                     attachment_filename='%s.p12' % username)


@app.route('/int-usr.crl')
def crl():
    return send_file(INT_USR_CRL)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=22223)