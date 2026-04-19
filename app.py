from flask import Flask, render_template, jsonify, request
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os
import re
import requests as http_requests

app = Flask(__name__)

DATA_FILE   = os.path.join(os.path.dirname(__file__), 'data.json')
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
DATABASE_URL = os.environ.get('DATABASE_URL')


# ── Database helpers (used on Railway) ────────────────────────────────────────

def get_db():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Create store table and seed initial data from files if empty."""
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS store (
            key   TEXT PRIMARY KEY,
            value JSONB NOT NULL
        )
    """)
    # Seed data.json
    cur.execute("SELECT 1 FROM store WHERE key = 'data'")
    if not cur.fetchone():
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            cur.execute("INSERT INTO store (key, value) VALUES ('data', %s)", (f.read(),))
    # Seed config
    cur.execute("SELECT 1 FROM store WHERE key = 'config'")
    if not cur.fetchone():
        default = {"restaurant_name": "Le Restaurant", "email_sender": "",
                   "email_password": "", "smtp_host": "smtp.gmail.com",
                   "smtp_port": 587, "suppliers": {}}
        cur.execute("INSERT INTO store (key, value) VALUES ('config', %s)",
                    (json.dumps(default),))
    conn.commit()
    cur.close()
    conn.close()


def db_load(key):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT value FROM store WHERE key = %s", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def db_save(key, value):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO store (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (key, json.dumps(value)))
    conn.commit()
    cur.close()
    conn.close()


# ── Data & config I/O (file locally, DB on Railway) ──────────────────────────

def load_data():
    if DATABASE_URL:
        return db_load('data')
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_data(data):
    if DATABASE_URL:
        db_save('data', data)
    else:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    if DATABASE_URL:
        cfg = db_load('config')
        return cfg if cfg else {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    default = {"restaurant_name": "Le Restaurant", "email_sender": "",
               "email_password": "", "smtp_host": "smtp.gmail.com",
               "smtp_port": 587, "suppliers": {}}
    save_config(default)
    return default


def save_config(config):
    if DATABASE_URL:
        db_save('config', config)
    else:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)


# ── Init DB on startup ────────────────────────────────────────────────────────
if DATABASE_URL:
    with app.app_context():
        init_db()


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


# ── Read suppliers ─────────────────────────────────────────────────────────────

@app.route('/api/suppliers')
def get_suppliers():
    data   = load_data()
    config = load_config()
    result = []
    for key, supplier in data.items():
        supplier_config = config.get('suppliers', {}).get(key, {})
        result.append({
            'id':            key,
            'name':          supplier['name'],
            'email':         supplier_config.get('email', ''),
            'product_count': len(supplier['products'])
        })
    return jsonify(result)


@app.route('/api/suppliers/<supplier_id>')
def get_supplier(supplier_id):
    data   = load_data()
    config = load_config()
    if supplier_id not in data:
        return jsonify({'error': 'Fournisseur introuvable'}), 404
    supplier        = data[supplier_id]
    supplier_config = config.get('suppliers', {}).get(supplier_id, {})
    return jsonify({
        'id':       supplier_id,
        'name':     supplier['name'],
        'email':    supplier_config.get('email', ''),
        'products': supplier['products']
    })


# ── Create supplier ────────────────────────────────────────────────────────────

@app.route('/api/suppliers', methods=['POST'])
def create_supplier():
    body  = request.json
    name  = body.get('name', '').strip()
    email = body.get('email', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Nom requis'}), 400

    slug = re.sub(r'[^a-z0-9]', '_', name.lower()).strip('_')
    data = load_data()
    if slug in data:
        slug = slug + '_2'

    data[slug] = {'name': name, 'email': email, 'products': []}
    save_data(data)

    config = load_config()
    config.setdefault('suppliers', {})[slug] = {'email': email}
    save_config(config)

    return jsonify({'success': True, 'id': slug})


# ── Update supplier ────────────────────────────────────────────────────────────

@app.route('/api/suppliers/<supplier_id>', methods=['PUT'])
def update_supplier(supplier_id):
    body = request.json
    data = load_data()
    if supplier_id not in data:
        return jsonify({'success': False, 'error': 'Fournisseur introuvable'}), 404

    if 'name' in body:
        data[supplier_id]['name'] = body['name'].strip()
    save_data(data)

    if 'email' in body:
        config = load_config()
        config.setdefault('suppliers', {}).setdefault(supplier_id, {})['email'] = body['email'].strip()
        save_config(config)

    return jsonify({'success': True})


# ── Delete supplier ────────────────────────────────────────────────────────────

@app.route('/api/suppliers/<supplier_id>', methods=['DELETE'])
def delete_supplier(supplier_id):
    data = load_data()
    if supplier_id not in data:
        return jsonify({'success': False, 'error': 'Fournisseur introuvable'}), 404
    del data[supplier_id]
    save_data(data)
    return jsonify({'success': True})


# ── Products ──────────────────────────────────────────────────────────────────

@app.route('/api/suppliers/<supplier_id>/products', methods=['POST'])
def add_product(supplier_id):
    body = request.json
    data = load_data()
    if supplier_id not in data:
        return jsonify({'success': False, 'error': 'Fournisseur introuvable'}), 404

    product = {
        'name':   body.get('name', '').strip(),
        'ref':    body.get('ref', '').strip() or None,
        'unit':   body.get('unit', '').strip() or None,
        'price':  float(body['price']) if body.get('price') not in (None, '') else None,
        'family': body.get('family', '').strip() or None,
    }
    if not product['name']:
        return jsonify({'success': False, 'error': 'Nom du produit requis'}), 400

    data[supplier_id]['products'].append(product)
    save_data(data)
    return jsonify({'success': True, 'idx': len(data[supplier_id]['products']) - 1})


@app.route('/api/suppliers/<supplier_id>/products/<int:idx>', methods=['PUT'])
def update_product(supplier_id, idx):
    body     = request.json
    data     = load_data()
    if supplier_id not in data:
        return jsonify({'success': False, 'error': 'Fournisseur introuvable'}), 404
    products = data[supplier_id]['products']
    if idx < 0 or idx >= len(products):
        return jsonify({'success': False, 'error': 'Produit introuvable'}), 404

    p = products[idx]
    if 'name'   in body: p['name']   = body['name'].strip()
    if 'ref'    in body: p['ref']    = body['ref'].strip() or None
    if 'unit'   in body: p['unit']   = body['unit'].strip() or None
    if 'family' in body: p['family'] = body['family'].strip() or None
    if 'price'  in body: p['price']  = float(body['price']) if body['price'] not in (None, '') else None

    save_data(data)
    return jsonify({'success': True})


@app.route('/api/suppliers/<supplier_id>/products/<int:idx>', methods=['DELETE'])
def delete_product(supplier_id, idx):
    data = load_data()
    if supplier_id not in data:
        return jsonify({'success': False, 'error': 'Fournisseur introuvable'}), 404
    products = data[supplier_id]['products']
    if idx < 0 or idx >= len(products):
        return jsonify({'success': False, 'error': 'Produit introuvable'}), 404
    products.pop(idx)
    save_data(data)
    return jsonify({'success': True})


# ── Send order ─────────────────────────────────────────────────────────────────

@app.route('/api/send-order', methods=['POST'])
def send_order():
    order_data = request.json
    config     = load_config()

    supplier_name   = order_data['supplier_name']
    supplier_email  = order_data['supplier_email']
    items           = order_data['items']
    restaurant_name = order_data.get('restaurant_name') or config.get('restaurant_name', 'Le Restaurant')
    date_str        = datetime.now().strftime('%d/%m/%Y')

    lines = []
    for item in items:
        unit = item.get('unit') or ''
        line = f"  - {item['name']}"
        if item.get('ref'):
            line += f" (réf. {item['ref']})"
        line += f" : {item['quantity']}"
        if unit:
            line += f" {unit}"
        lines.append(line)

    body = (f"Bonjour {supplier_name},\n\n"
            f"Voici la commande de {restaurant_name} pour le {date_str} :\n\n"
            + '\n'.join(lines) +
            f"\n\nCordialement,\n{restaurant_name}")

    sender   = os.environ.get('EMAIL_SENDER')   or config.get('email_sender', '')
    password = os.environ.get('EMAIL_PASSWORD') or config.get('email_password', '')

    if not sender:
        return jsonify({'success': False, 'error': 'Email expéditeur non configuré. Allez dans Paramètres.'}), 400
    if not supplier_email:
        return jsonify({'success': False, 'error': 'Email du fournisseur non configuré.'}), 400

    sendgrid_key = os.environ.get('SENDGRID_API_KEY')

    try:
        if sendgrid_key:
            # ── SendGrid API (production sur Railway) ──
            r = http_requests.post(
                'https://api.sendgrid.com/v3/mail/send',
                headers={
                    'Authorization': f'Bearer {sendgrid_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'personalizations': [{'to': [{'email': supplier_email}]}],
                    'from': {'email': sender, 'name': restaurant_name},
                    'subject': f"Commande {restaurant_name} – {date_str}",
                    'content': [{'type': 'text/plain', 'value': body}]
                },
                timeout=10
            )
            if r.status_code not in (200, 202):
                return jsonify({'success': False, 'error': f'SendGrid: {r.text}'}), 500
        else:
            # ── SMTP Gmail (test en local) ──
            if not password:
                return jsonify({'success': False, 'error': 'Mot de passe email non configuré.'}), 400
            msg = MIMEMultipart()
            msg['From']    = sender
            msg['To']      = supplier_email
            msg['Subject'] = f"Commande {restaurant_name} – {date_str}"
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(sender, password)
                server.send_message(msg)

        return jsonify({'success': True, 'body': body})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Config ─────────────────────────────────────────────────────────────────────

@app.route('/api/config', methods=['GET'])
def get_config():
    config = load_config()
    safe   = {k: v for k, v in config.items() if k != 'email_password'}
    safe['email_password_set'] = bool(config.get('email_password'))
    return jsonify(safe)


@app.route('/api/config', methods=['POST'])
def update_config():
    new_data = request.json
    config   = load_config()

    for key in ('restaurant_name', 'email_sender', 'smtp_host', 'smtp_port'):
        if key in new_data:
            config[key] = new_data[key]
    if new_data.get('email_password'):
        config['email_password'] = new_data['email_password']
    if 'suppliers' in new_data:
        config.setdefault('suppliers', {})
        for sid, sdata in new_data['suppliers'].items():
            config['suppliers'].setdefault(sid, {}).update(sdata)

    save_config(config)
    return jsonify({'success': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=False, port=port, host='0.0.0.0')
