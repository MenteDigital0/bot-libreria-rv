import os, json, random, gspread, sqlite3, re
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURACIÓN IA ---
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model_ai = genai.GenerativeModel('gemini-1.5-flash')

# --- CONFIGURACIÓN GOOGLE & TWILIO ---
json_env = os.environ.get('GOOGLE_JSON_CONTENT')
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_env), ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
client_gs = gspread.authorize(creds)
spreadsheet = client_gs.open("Libreria RV Datos")

hoja_prod = spreadsheet.worksheet("Productos")
hoja_regalos = spreadsheet.worksheet("Regalos")
hoja_v_utiles = spreadsheet.worksheet("Ventas")
hoja_v_regalos = spreadsheet.worksheet("Ventas_Regalos")
hoja_v_listas = spreadsheet.worksheet("Ventas_Listas")

client_twilio = Client(os.environ.get('TWILIO_ACCOUNT_SID'), os.environ.get('TWILIO_AUTH_TOKEN'))
NUMERO_BOT = 'whatsapp:+14155238886'
NUMERO_ENCARGADA = 'whatsapp:+51921264742'

# --- BASE DE DATOS SQLITE ---
DB_NAME = "sesiones_bot.db"
def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute('CREATE TABLE IF NOT EXISTS sesiones (num TEXT PRIMARY KEY, step TEXT, orden TEXT, total REAL, prod_nom TEXT, cant INTEGER)')
    conn.close()

init_db()

def db_save(num, step, orden="", total=0.0, prod_nom="", cant=0):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT OR REPLACE INTO sesiones VALUES (?, ?, ?, ?, ?, ?)", (num, step, orden, total, prod_nom, cant))
    conn.commit()
    conn.close()

def db_get(num):
    conn = sqlite3.connect(DB_NAME)
    row = conn.execute("SELECT * FROM sesiones WHERE num = ?", (num,)).fetchone()
    conn.close()
    return {"step": row[1], "o": row[2], "t": row[3], "p_nom": row[4], "c": row[5]} if row else None

def db_delete(num):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("DELETE FROM sesiones WHERE num = ?", (num,)).close()
    conn.commit()
    conn.close()

def procesar_lista_con_ia(texto_usuario, lista_precios):
    datos_ia = [{"n": f.get('Producto'), "p": f.get('Precio', 0) if f.get('Precio') else f.get('', 0)} for f in lista_precios if f.get('Producto')]
    prompt = f"Librería R&V. Stock: {json.dumps(datos_ia)}. Pedido: '{texto_usuario}'. Responde solo JSON: {{\"total\": 0.0, \"items_encontrados\": [\"2x Producto (S/ 0.00)\"], \"no_encontrados\": []}}"
    try:
        response = model_ai.generate_content(prompt)
        match = re.search(r'\{.*\}', response.text, re.DOTALL)
        return json.loads(match.group()) if match else {"total":0,"items_encontrados":[],"no_encontrados":["Error"]}
    except:
        return {"total":0,"items_encontrados":[],"no_encontrados":["Error"]}

@app.route("/whatsapp", methods=['POST'])
def reply():
    num = request.form.get('From')
    num_limpio = num.replace('whatsapp:', '').replace('+', '')
    body = request.form.get('Body', '').strip()
    media_url = request.form.get('MediaUrl0')
    res = MessagingResponse()
    sesion = db_get(num)

    # 1. INICIO
    if not sesion or body.upper() in ["HOLA", "MENU", "REINICIAR"]:
        db_save(num, "menu_principal")
        msg = ("📚 *LIBRERÍA R&V*\n¡Hola André! ¿Qué deseas llevar?\n\n"
               "1️⃣ *Enviar Lista:* Cotización con IA.\n"
               "2️⃣ *Regalos:* Detalles especiales.\n"
               "3️⃣ *Compra Rápida:* Productos por unidad.")
        res.message(msg)
        return str(res)

    # 2. MENÚ PRINCIPAL
    elif sesion["step"] == "menu_principal":
        if body == "1":
            db_save(num, "esperando_lista")
            res.message("📝 *MODO LISTA*\nEscribe tus útiles ahora.")
        elif body == "2":
            datos = hoja_regalos.get_all_records()
            cats = sorted(list(set([r["Categoría"] for r in datos])))
            db_save(num, "regalo_cat")
            txt = "💝 *CATEGORÍAS REGALOS*\n" + "\n".join([f"*{i+1}.* {c}" for i, c in enumerate(cats)])
            res.message(txt + "\nElige un número.")
        elif body == "3":
            datos = hoja_prod.get_all_records()
            cats = sorted(list(set([p["Categoría"] for p in datos if p.get("Categoría")])))
            db_save(num, "prod_cat")
            txt = "🛍️ *CATEGORÍAS ÚTILES*\n" + "\n".join([f"*{i+1}.* {c}" for i, c in enumerate(cats)])
            res.message(txt + "\nElige un número.")
        else:
            res.message("❌ Elige 1, 2 o 3.")
        return str(res)

    # 3. FLUJO COMPRA RÁPIDA (OPCIÓN 3)
    elif sesion["step"] == "prod_cat":
        datos = hoja_prod.get_all_records()
        cats = sorted(list(set([p["Categoría"] for p in datos if p.get("Categoría")])))
        idx = int(body)-1 if body.isdigit() else -1
        if 0 <= idx < len(cats):
            cat_sel = cats[idx]
            items = [p for p in datos if p.get("Categoría") == cat_sel]
            txt = f"📦 *{cat_sel}*\n" + "\n".join([f"*{i+1}.* {p['Producto']} - S/ {p['Precio']}" for i, p in enumerate(items)])
            db_save(num, f"prod_item_{cat_sel}")
            res.message(txt + "\nElige el número del producto.")
        else: res.message("❌ Elige un número válido.")
        return str(res)

    elif "prod_item_" in sesion["step"]:
        cat = sesion["step"].replace("prod_item_", "")
        items = [p for p in hoja_prod.get_all_records() if p.get("Categoría") == cat]
        idx = int(body)-1 if body.isdigit() else -1
        if 0 <= idx < len(items):
            it = items[idx]
            db_save(num, "seleccion_pago", orden=str(random.randint(1000,9999)), total=float(it['Precio']), prod_nom=it['Producto'], cant=1)
            res.message(f"Elegiste: *{it['Producto']}*\nTotal: S/ {it['Precio']}\n\n1. Yape\n2. Efectivo")
        else: res.message("❌ Opción inválida.")
        return str(res)

    # (Aquí siguen las funciones de regalo_cat, esperando_lista, seleccion_pago y finalizar...)
    # [Para ahorrar espacio, mantengo la misma lógica de pago y cierre que ya tienes]
    
    return str(res) # Cierre de seguridad
