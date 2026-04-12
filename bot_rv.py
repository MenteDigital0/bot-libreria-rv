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
    return {"step": row[1], "o": row[2], "t": row[3], "p_nom": row[4], "c": row[5]} if row else None

def db_delete(num):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sesiones WHERE num = ?", (num,))
    conn.commit()
    conn.close()

# --- CONFIGURACIÓN GOOGLE & TWILIO ---
json_env = os.environ.get('GOOGLE_JSON_CONTENT')
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_env), ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
client_gs = gspread.authorize(creds)

spreadsheet = client_gs.open("Libreria RV Datos")
hoja_prod = spreadsheet.worksheet("Productos")
hoja_regalos = spreadsheet.worksheet("Regalos")
hoja_ventas_utiles = spreadsheet.worksheet("Ventas")
hoja_ventas_regalos = spreadsheet.worksheet("Ventas_Regalos")

ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
NUMERO_BOT = 'whatsapp:+14155238886'
NUMERO_ENCARGADA = 'whatsapp:+51921264742'
client_twilio = Client(ACCOUNT_SID, AUTH_TOKEN)

@app.route("/whatsapp", methods=['POST'])
def reply():
    num = request.form.get('From')
    num_limpio = num.replace('whatsapp:', '').replace('+', '')
    body = request.form.get('Body', '').strip().upper()
    media_url = request.form.get('MediaUrl0')
    res = MessagingResponse()
    sesion = db_get(num)

    # 1. MENÚ PRINCIPAL
    if not sesion or body in ["HOLA", "MENU", "REINICIAR"]:
        db_save(num, "menu_principal")
        msg = ("📚 *LIBRERÍA R&V*\n¡Hola! ¿Qué deseas llevar hoy?\n\n"
               "1️⃣ *Enviar Lista:* Cotizamos tus útiles.\n"
               "2️⃣ *Regalos:* Detalles para toda ocasión.\n"
               "3️⃣ *Producto Único:* Compra algo específico.\n"
               "4️⃣ *Consultas:* Hablar con nosotros.")
        res.message(msg)
        return str(res)

    # 2. LÓGICA DEL MENÚ
    elif sesion["step"] == "menu_principal":
        if body == "1":
            db_save(num, "esperando_lista")
            res.message("📝 *MODO LISTA*\nEscribe todos tus útiles aquí. La encargada te responderá pronto.")
        elif body == "2":
            datos = hoja_regalos.get_all_records()
            categorias = list(set([r["Categoría"] for r in datos]))
            db_save(num, "regalo_cat")
            txt = "💝 *CATEGORÍAS DE REGALOS*\n"
            for i, cat in enumerate(categorias, 1): txt += f"*{i}.* {cat}\n"
            res.message(txt + "\nElige una categoría.")
        elif body == "3":
            datos = hoja_prod.get_all_records()
            categorias = list(set([p["Categoría"] for p in datos]))
            db_save(num, "cat")
            txt = "🛍️ *PRODUCTOS*\n"
            for i, cat in enumerate(categorias, 1): txt += f"*{i}.* {cat}\n"
            res.message(txt + "\nElige una categoría.")
        else: res.message("❌ Opción inválida. Elige 1, 2, 3 o 4.")

    # 3. SUB-MENÚ REGALOS (Categorías)
    elif sesion["step"] == "regalo_cat":
        datos_todos = hoja_regalos.get_all_records()
        categorias = list(set([r["Categoría"] for r in datos_todos]))
        idx = int(body) - 1 if body.isdigit() else -1
        if 0 <= idx < len(categorias):
            cat_sel = categorias[idx]
            regalos_filtrados = [r for r in datos_todos if r["Categoría"] == cat_sel]
            txt = f"🎁 *REGALOS: {cat_sel}*\n"
            for i, r in enumerate(regalos_filtrados, 1):
                txt += f"*{i}.* {r['Detalle']} - S/ {r['Precio']:.2f}\n"
            db_save(num, f"regalo_item_{cat_sel}")
            res.message(txt + "\nElige el número del regalo.")
        else: res.message("❌ Elige una categoría válida.")

    # 4. SELECCIÓN DE REGALO ESPECÍFICO
    elif "regalo_item_" in sesion["step"]:
        cat_sel = sesion["step"].replace("regalo_item_", "")
        regalos_filtrados = [r for r in hoja_regalos.get_all_records() if r["Categoría"] == cat_sel]
        idx = int(body) - 1 if body.isdigit() else -1
        if 0 <= idx < len(regalos_filtrados):
            r = regalos_filtrados[idx]
            # Marcamos prod_nom con prefijo REGALO para filtrado posterior
            db_save(num, "finalizar", orden=str(random.randint(1000, 9999)), prod_nom=f"REGALO: {r['Detalle']}", total=float(r['Precio']), cant=1)
            res.message(f"Elegiste: *{r['Detalle']}*\nTotal: S/ {float(r['Precio']):.2f}\n\nYapea al *987654321* y envía *NOMBRE + CAPTURA*.")
        else: res.message("❌ Número inválido.")

    # 5. FLUJO DE LISTA
    elif sesion["step"] == "esperando_lista":
        orden = str(random.randint(1000, 9999))
        db_save(num, "finalizar", orden=orden, prod_nom=f"LISTA: {body[:30]}...")
        client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA,
            body=f"📝 *NUEVA LISTA* #{orden}\nCliente: {num}\nPedido:\n{body}")
        res.message("✅ Lista recibida. Envía tu *NOMBRE + CAPTURA* del pago una vez la encargada te dé el monto.")

    # 6. FINALIZAR (Validación de Imagen y Registro)
    elif sesion["step"] == "finalizar":
        if not media_url:
            res.message("⚠️ *¡FALTA LA CAPTURA!* Por favor, vuelve a enviar tu nombre pero esta vez *adjunta la imagen* de tu pago.")
            return str(res)
        
        nombre = body.title()
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        # Filtrar hoja de destino
        hoja_destino = hoja_ventas_regalos if "REGALO" in sesion['p_nom'] else hoja_ventas_utiles
        hoja_destino.append_row([fecha, nombre, sesion['o'], sesion['c'], sesion['p_nom'], sesion['t']])

        # Alerta a encargada con Link
        wa_link = f"https://wa.me/{num_limpio}"
        resumen = (f"🚨 *NUEVO PAGO*\nCliente: {nombre}\nPedido: {sesion['p_nom']}\nTotal: S/ {sesion['t']:.2f}\nChat: {wa_link}")
        
        client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA, body=resumen, media_url=[media_url])
        
        res.message(f"¡Gracias {nombre}! Pedido registrado. Nos comunicaremos contigo pronto. 🚀")
        db_delete(num)

    return str(res)

if __name__ == "__main__":
    app.run()
