import os, json, random, gspread, sqlite3
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime

app = Flask(__name__)

# --- BASE DE DATOS SQLITE --
DB_NAME = "sesiones_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS sesiones 
                      (num TEXT PRIMARY KEY, step TEXT, orden TEXT, total REAL, prod_nom TEXT, cant INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS control_encargada 
                      (id INTEGER PRIMARY KEY, ultima_orden TEXT)''')
    cursor.execute("INSERT OR IGNORE INTO control_encargada (id, ultima_orden) VALUES (1, '')")
    conn.commit()
    conn.close()

init_db()

def db_save(num, step, orden="", total=0.0, prod_nom="", cant=0):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO sesiones VALUES (?, ?, ?, ?, ?, ?)", (num, step, orden, total, prod_nom, cant))
    conn.commit()
    conn.close()

def db_get(num):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sesiones WHERE num = ?", (num,))
    row = cursor.fetchone()
    conn.close()
    return {"step": row[1], "o": row[2], "t": row[3], "p_nom": row[4], "c": row[5]} if row else None

def db_delete(num):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sesiones WHERE num = ?", (num,))
    conn.commit()
    conn.close()

def db_get_by_order(orden_ref):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT num, total FROM sesiones WHERE orden = ?", (orden_ref,))
    row = cursor.fetchone()
    conn.close()
    return row

def set_ultima_orden(orden):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE control_encargada SET ultima_orden = ? WHERE id = 1", (orden,))
    conn.commit()
    conn.close()

def get_ultima_orden():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT ultima_orden FROM control_encargada WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else ""

# --- CONFIGURACIÓN GOOGLE & TWILIO ---
json_env = os.environ.get('GOOGLE_JSON_CONTENT')
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_env), ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
client_gs = gspread.authorize(creds)

nombre_excel = "Libreria RV Datos"
spreadsheet = client_gs.open(nombre_excel)
hoja_prod = spreadsheet.worksheet("Productos")
hoja_vent = spreadsheet.worksheet("Ventas")

ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
NUMERO_BOT = 'whatsapp:+14155238886'
NUMERO_ENCARGADA = 'whatsapp:+51921264742'
client_twilio = Client(ACCOUNT_SID, AUTH_TOKEN)

@app.route("/whatsapp", methods=['POST'])
def reply():
    num = request.form.get('From')
    body = request.form.get('Body', '').strip().upper()
    media_url = request.form.get('MediaUrl0')
    res = MessagingResponse()
    
    sesion = db_get(num)

    # --- LÓGICA PARA LA ENCARGADA (Responder 1 o 2) ---
    if num == NUMERO_ENCARGADA and (body == "1" or body == "2"):
        orden_ref = get_ultima_orden()
        if not orden_ref:
            res.message("⚠️ No hay ninguna orden pendiente.")
            return str(res)

        busqueda = db_get_by_order(orden_ref)
        if busqueda:
            cliente_num, total = busqueda
            if body == "1":
                client_twilio.messages.create(from_=NUMERO_BOT, to=cliente_num, 
                    body=f"✅ *¡STOCK CONFIRMADO!*\nTotal: *S/ {total:.2f}*\n\nYapea al *987654321* y envía captura.")
                db_save(cliente_num, "finalizar", orden=orden_ref, total=total)
                res.message(f"✅ Confirmaste Orden #{orden_ref}.")
            else:
                client_twilio.messages.create(from_=NUMERO_BOT, to=cliente_num, 
                    body="😔 *SIN STOCK*\nNo hay disponibilidad. Escribe MENU.")
                db_delete(cliente_num)
                res.message(f"❌ Cancelaste Orden #{orden_ref}.")
            set_ultima_orden("")
            return str(res)

    # --- LÓGICA PARA EL CLIENTE ---
    if not sesion or body in ["HOLA", "MENU", "REINICIAR"]:
        datos = hoja_prod.get_all_records()
        categorias = list(set([f["Categoría"] for f in datos]))
        db_save(num, "cat")
        txt = "📚 *LIBRERÍA R&V*\n¿Qué buscas?\n"
        for i, c in enumerate(categorias, 1): txt += f"*{i}.* {c}\n"
        res.message(txt)

    elif sesion["step"] == "cat":
        datos = hoja_prod.get_all_records()
        categorias = list(set([f["Categoría"] for f in datos]))
        idx = int(body) - 1 if body.isdigit() else -1
        if 0 <= idx < len(categorias):
            cat_sel = categorias[idx]
            productos = [p for p in datos if p["Categoría"] == cat_sel]
            txt = f"📂 *{cat_sel}*\n"
            for i, p in enumerate(productos, 1):
                txt += f"*{i}.* {p['Producto']} - S/ {p['Precio']:.2f}\n"
            db_save(num, f"prod_{cat_sel}")
            res.message(txt + "\nElige el número del producto.")
        else: res.message("❌ Elige una opción válida.")

    elif "prod_" in sesion["step"]:
        cat_sel = sesion["step"].replace("prod_", "")
        datos = [p for p in hoja_prod.get_all_records() if p["Categoría"] == cat_sel]
        idx = int(body) - 1 if body.isdigit() else -1
        if 0 <= idx < len(datos):
            p = datos[idx]
            db_save(num, "cant", prod_nom=p['Producto'], total=float(p['Precio']))
            res.message(f"¿Cuántos de *{p['Producto']}*?")
        else: res.message("❌ Elige un número.")

    elif sesion["step"] == "cant":
        if body.isdigit():
            cant, total = int(body), int(body) * sesion["t"]
            orden = str(random.randint(1000, 9999))
            db_save(num, "esperando_stock", orden=orden, total=total, prod_nom=sesion["p_nom"], cant=cant)
            set_ultima_orden(orden)
            res.message("⏳ *Verificando Stock...*")
            client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA, 
                body=f"❓ *STOCK?* #{orden}\n{cant}x {sesion['p_nom']}\n\n1: SÍ\n2: NO")
        else: res.message("❌ Escribe un número.")

   elif sesion["step"] == "finalizar":
        nombre = body.title()
        # Usamos los datos que guardamos en SQLite en el paso anterior
        orden_id = sesion['o']
        cantidad = sesion['c']
        producto = sesion['p_nom']
        total_pago = sesion['t']

        # 1. Guardamos en el Excel
        hoja_vent.append_row([
            datetime.now().strftime("%d/%m/%Y %H:%M"), 
            nombre, 
            orden_id, 
            cantidad, 
            producto, 
            total_pago
        ])

        # 2. Enviamos confirmación a la ENCARGADA con todos los detalles
        mensaje_confirmacion = (
            f"🚨 *PAGO RECIBIDO*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"ORDEN: #{orden_id}\n"
            f"CLIENTE: {nombre}\n"
            f"DETALLE: {cantidad}x {producto}\n"
            f"TOTAL: S/ {total_pago:.2f}"
        )
        
        # Enviamos el mensaje con la captura (media_url)
        client_twilio.messages.create(
            from_=NUMERO_BOT, 
            to=NUMERO_ENCARGADA, 
            body=mensaje_confirmacion, 
            media_url=[media_url] if media_url else None
        )

        # 3. Respondemos al CLIENTE
        res.message(f"¡Gracias {nombre}! Tu pedido de {producto} ha sido registrado. 🚀")
        
        # 4. Borramos la sesión de SQLite porque el proceso terminó
        db_delete(num)

if __name__ == "__main__":
    app.run()
