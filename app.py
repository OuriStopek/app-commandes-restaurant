from flask import Flask, render_template, jsonify, request
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os

app = Flask(__name__)

DATA_FILE = os.path.join(os.path.dirname(__file__), 'data.json')
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')


def load_data():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    # First run in production: create empty config
    default = {"restaurant_name": "Le Restaurant", "email_sender": "", "email_password": "", "smtp_host": "smtp.gmail.com", "smtp_port": 587, "suppliers": {}}
    save_config(default)
    return default


def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/suppliers')
def get_suppliers():
    data = load_data()
    config = load_config()
    result = []
    for key, supplier in data.items():
        supplier_config = config.get('suppliers', {}).get(key, {})
        result.append({
            'id': key,
            'name': supplier['name'],
            'email': supplier_config.get('email', ''),
            'product_count': len(supplier['products'])
        })
    return jsonify(result)


@app.route('/api/suppliers/<supplier_id>')
def get_supplier(supplier_id):
    data = load_data()
    config = load_config()
    if supplier_id not in data:
        return jsonify({'error': 'Fournisseur introuvable'}), 404
    supplier = data[supplier_id]
    supplier_config = config.get('suppliers', {}).get(supplier_id, {})
    return jsonify({
        'id': supplier_id,
        'name': supplier['name'],
        'email': supplier_config.get('email', ''),
        'products': supplier['products']
    })


@app.route('/api/send-order', methods=['POST'])
def send_order():
    order_data = request.json
    config = load_config()

    supplier_name = order_data['supplier_name']
    supplier_email = order_data['supplier_email']
    items = order_data['items']
    restaurant_name = config.get('restaurant_name', 'Le Restaurant')
    date_str = datetime.now().strftime('%d/%m/%Y')

    # Build email lines
    lines = []
    for item in items:
        unit = item.get('unit') or ''
        qty = item['quantity']
        line = f"  - {item['name']}"
        if item.get('ref'):
            line += f" (réf. {item['ref']})"
        line += f" : {qty}"
        if unit:
            line += f" {unit}"
        lines.append(line)

    items_text = '\n'.join(lines)

    body = f"""Bonjour {supplier_name},

Voici la commande de {restaurant_name} pour le {date_str} :

{items_text}

Cordialement,
{restaurant_name}"""

    # Env vars take priority over config.json (used in production)
    sender = os.environ.get('EMAIL_SENDER') or config.get('email_sender', '')
    password = os.environ.get('EMAIL_PASSWORD') or config.get('email_password', '')

    if not sender or not password:
        return jsonify({'success': False, 'error': 'Email expéditeur non configuré. Allez dans Paramètres.'}), 400

    if not supplier_email:
        return jsonify({'success': False, 'error': 'Email du fournisseur non configuré.'}), 400

    try:
        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = supplier_email
        msg['Subject'] = f"Commande {restaurant_name} – {date_str}"
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP(config.get('smtp_host', 'smtp.gmail.com'),
                          int(config.get('smtp_port', 587))) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.send_message(msg)

        return jsonify({'success': True, 'body': body})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config', methods=['GET'])
def get_config():
    config = load_config()
    safe = {k: v for k, v in config.items() if k != 'email_password'}
    safe['email_password_set'] = bool(config.get('email_password'))
    return jsonify(safe)


@app.route('/api/config', methods=['POST'])
def update_config():
    new_data = request.json
    config = load_config()

    # Update top-level keys
    for key in ('restaurant_name', 'email_sender', 'smtp_host', 'smtp_port'):
        if key in new_data:
            config[key] = new_data[key]

    # Only update password if provided
    if new_data.get('email_password'):
        config['email_password'] = new_data['email_password']

    # Update supplier emails
    if 'suppliers' in new_data:
        if 'suppliers' not in config:
            config['suppliers'] = {}
        for sid, sdata in new_data['suppliers'].items():
            if sid not in config['suppliers']:
                config['suppliers'][sid] = {}
            config['suppliers'][sid].update(sdata)

    save_config(config)
    return jsonify({'success': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(debug=False, port=port, host='0.0.0.0')
