import os, json, random, gspread, sqlite3
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime

app = Flask(__name__)

# --- BASE DE DATOS SQLITE ---
DB_NAME = "sesiones_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Guardamos: telefono, step, orden, total, producto, cantidad
    cursor.execute('''CREATE TABLE IF NOT EXISTS sesiones 
                      (num TEXT PRIMARY KEY, step TEXT, orden TEXT, total REAL, prod_nom TEXT, cant INTEGER)''')
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
    if row:
        return {"step": row[1], "o": row[2], "t": row[3], "p_nom": row[4], "c": row[5]}
    return None

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
    return row # Retorna (num, total)

# --- CONFIGURACIÓN GOOGLE & TWILIO ---
json_env = os.environ.get('GOOGLE_JSON_CONTENT')
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_env), ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
client_gs = gspread.authorize(creds)

nombre_excel = "Libreria RV Datos"
try:
    spreadsheet = client_gs.open(nombre_excel)
    hoja_prod = spreadsheet.worksheet("Productos")
    hoja_vent = spreadsheet.worksheet("Ventas")
    print("✅ Conexión exitosa al Excel")
except Exception as e:
    print(f"❌ Error de conexión: {e}")

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

    # --- LÓGICA PARA LA ENCARGADA ---
    if num == NUMERO_ENCARGADA and ("SI" in body or "NO" in body):
        partes = body.split()
        if len(partes) >= 2:
            accion, orden_ref = partes[0], partes[1]
            busqueda = db_get_by_order(orden_ref)
            
            if busqueda:
                cliente_num, total = busqueda
                if accion == "SI":
                    client_twilio.messages.create(from_=NUMERO_BOT, to=cliente_num, 
                        body=f"✅ *¡STOCK CONFIRMADO!*\nTotal: *S/ {total:.2f}*\n\nPor favor, yapea al *987654321* y envía tu nombre con la captura.")
                    db_save(cliente_num, "finalizar", orden=orden_ref, total=total)
                else:
                    client_twilio.messages.create(from_=NUMERO_BOT, to=cliente_num, 
                        body="😔 *LO SENTIMOS*\nNo hay stock actualmente. Escribe 'MENU' para ver más.")
                    db_delete(cliente_num)
                return str(res)
            else:
                res.message(f"⚠️ Orden #{orden_ref} no encontrada o ya procesada.")
                return str(res)

    # --- LÓGICA PARA EL CLIENTE ---
    if not sesion or body in ["HOLA", "MENU", "REINICIAR"]:
        datos = hoja_prod.get_all_records()
        categorias = list(set([f["Categoría"] for f in datos]))
        # Guardamos en DB que está eligiendo categoría
        db_save(num, "cat")
        txt = "📚 *LIBRERÍA R&V*\n¿Qué categoría buscas?\n"
        for i, c in enumerate(categorias, 1): txt += f"*{i}.* {c}\n"
        res.message(txt)

    elif sesion["step"] == "cat":
        # Nota: Aquí para simplicidad volvemos a leer el excel para filtrar
        datos = hoja_prod.get_all_records()
        categorias = list(set([f["Categoría"] for f in datos]))
        idx = int(body) - 1 if body.isdigit() else -1
        if 0 <= idx < len(categorias):
            cat_sel = categorias[idx]
            productos = [p for p in datos if p["Categoría"] == cat_sel]
            txt = f"📂 *{cat_sel}*\n"
            for i, p in enumerate(productos, 1):
                txt += f"*{i}.* {p['Producto']} - S/ {p['Precio']:.2f}\n"
            db_save(num, f"prod_{cat_sel}") # Guardamos la categoría en el step
            res.message(txt + "\nElige el número del producto.")
        else: res.message("❌ Elige una categoría válida.")

    elif "prod_" in sesion["step"]:
        cat_sel = sesion["step"].replace("prod_", "")
        datos = [p for p in hoja_prod.get_all_records() if p["Categoría"] == cat_sel]
        idx = int(body) - 1 if body.isdigit() else -1
        if 0 <= idx < len(datos):
            p = datos[idx]
            db_save(num, "cant", prod_nom=p['Producto'], total=float(p['Precio']))
            res.message(f"¿Cuántos de *{p['Producto']}*?")
        else: res.message("❌ Elige un número válido.")

    elif sesion["step"] == "cant":
        if body.isdigit():
            cant = int(body)
            total = cant * sesion["t"]
            orden = str(random.randint(1000, 9999))
            db_save(num, "esperando_stock", orden=orden, total=total, prod_nom=sesion["p_nom"], cant=cant)
            res.message("⏳ *Verificando Stock...*\nTe avisaremos en un momento.")
            client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA, 
                body=f"❓ *STOCK? ORDEN #{orden}*\n{cant}x {sesion['p_nom']}\nResponde: *SI {orden}* o *NO {orden}*")
        else: res.message("❌ Escribe un número.")

    elif sesion["step"] == "finalizar":
        nombre = body.title()
        hoja_vent.append_row([datetime.now().strftime("%d/%m/%Y %H:%M"), nombre, sesion['o'], sesion['c'], sesion['p_nom'], sesion['t']])
        client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA, 
            body=f"🚨 *PAGO RECIBIDO #{sesion['o']}*\nCliente: {nombre}", media_url=[media_url] if media_url else None)
        res.message(f"¡Gracias {nombre}! Pedido registrado. 🚀")
        db_delete(num)

    return str(res)

if __name__ == "__main__":
    app.run()
