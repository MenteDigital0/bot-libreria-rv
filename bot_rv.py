import os, json, random, gspread, sqlite3
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime

app = Flask(__name__)

# --- BASE DE DATOS SQLITE (Persistencia de Sesión) ---
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
hoja_v_utiles = spreadsheet.worksheet("Ventas")
hoja_v_regalos = spreadsheet.worksheet("Ventas_Regalos")
hoja_v_listas = spreadsheet.worksheet("Ventas_Listas")

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
        msg = ("📚 *LIBRERÍA R&V*\n¡Hola! ¿Qué deseas llevar?\n\n"
               "1️⃣ *Enviar Lista:* Sumamos tus útiles automáticamente.\n"
               "2️⃣ *Regalos:* Detalles por categorías.\n"
               "3️⃣ *Producto Único:* Compra algo rápido.\n"
               "4️⃣ *Consultas:* Hablar con nosotros.")
        res.message(msg)
        return str(res)

    # 2. LÓGICA DEL MENÚ
    elif sesion["step"] == "menu_principal":
        if body == "1":
            db_save(num, "esperando_lista")
            res.message("📝 *MODO LISTA*\nEscribe tus productos (uno por línea).\nEjemplo:\nCuaderno Standford\nLápiz Faber\nBorrador")
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
        else: res.message("❌ Elige una opción válida (1-4).")

    # 3. PROCESAR LISTA (SUMA AUTOMÁTICA)
    elif sesion["step"] == "esperando_lista":
        items_tienda = hoja_prod.get_all_records()
        lineas = body.split("\n")
        total = 0
        encontrados = []
        no_encontrados = []

        for l in lineas:
            match = next((p for p in items_tienda if p['Producto'].upper() in l), None)
            if match:
                total += float(match['Precio'])
                encontrados.append(match['Producto'])
            else:
                no_encontrados.append(l[:15])

        orden = str(random.randint(1000, 9999))
        txt_resumen = f"📝 *ORDEN #{orden}*\n" + "\n".join([f"✅ {e}" for e in encontrados])
        if no_encontrados: txt_resumen += f"\n\n⚠️ *A CONSULTAR:* {', '.join(no_encontrados)}"
        txt_resumen += f"\n\n💰 *SUBTOTAL ESTIMADO: S/ {total:.2f}*"
        
        db_save(num, "seleccion_pago", orden=orden, total=total, prod_nom="LISTA DE UTILES", cant=len(encontrados))
        res.message(f"{txt_resumen}\n\n*¿Cómo deseas pagar?*\n1. Yape/Plin\n2. Tarjeta (Link)\n3. Efectivo (Contraentrega)")

    # 4. SUB-MENÚ REGALOS
    elif sesion["step"] == "regalo_cat":
        datos = hoja_regalos.get_all_records()
        categorias = list(set([r["Categoría"] for r in datos]))
        idx = int(body) - 1 if body.isdigit() else -1
        if 0 <= idx < len(categorias):
            cat_sel = categorias[idx]
            filtrados = [r for r in datos if r["Categoría"] == cat_sel]
            txt = f"🎁 *{cat_sel}*\n"
            for i, r in enumerate(filtrados, 1): txt += f"*{i}.* {r['Detalle']} - S/ {r['Precio']:.2f}\n"
            db_save(num, f"regalo_item_{cat_sel}")
            res.message(txt)
        else: res.message("❌ Categoría no válida.")

    elif "regalo_item_" in sesion["step"]:
        cat = sesion["step"].replace("regalo_item_", "")
        filtrados = [r for r in hoja_regalos.get_all_records() if r["Categoría"] == cat]
        idx = int(body) - 1 if body.isdigit() else -1
        if 0 <= idx < len(filtrados):
            r = filtrados[idx]
            db_save(num, "seleccion_pago", orden=str(random.randint(1000, 9999)), total=float(r['Precio']), prod_nom=f"REGALO: {r['Detalle']}", cant=1)
            res.message(f"Has elegido: *{r['Detalle']}*\nTotal: S/ {r['Precio']:.2f}\n\n*¿Cómo pagar?*\n1. Yape/Plin\n2. Tarjeta\n3. Efectivo")
        else: res.message("❌ Número no válido.")

    # 5. SELECCIÓN DE PAGO
    elif sesion["step"] == "seleccion_pago":
        metodos = {"1": "YAPE", "2": "TARJETA", "3": "EFECTIVO"}
        pago = metodos.get(body, "YAPE")
        db_save(num, "finalizar", orden=sesion['o'], total=sesion['t'], prod_nom=f"{sesion['p_nom']} ({pago})", cant=sesion['c'])
        
        instruccion = "envía tu nombre y la captura del Yape." if pago != "EFECTIVO" else "envía tu nombre para confirmar."
        res.message(f"✅ Método: *{pago}*.\nPara terminar, {instruccion}")

    # 6. FINALIZAR Y NOTIFICAR
    elif sesion["step"] == "finalizar":
        if "EFECTIVO" not in sesion['p_nom'] and not media_url:
            res.message("⚠️ *¡Captura obligatoria!* Envía tu nombre adjuntando la foto de tu pago.")
            return str(res)

        nombre = body.title()
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        # Selección de hoja
        if "LISTA" in sesion['p_nom']: hoja = hoja_v_listas
        elif "REGALO" in sesion['p_nom']: hoja = hoja_v_regalos
        else: hoja = hoja_v_utiles
        
        hoja.append_row([fecha, nombre, sesion['o'], sesion['c'], sesion['p_nom'], sesion['t']])

        # Alerta a Encargada
        wa_link = f"https://wa.me/{num_limpio}"
        notificacion = (f"🚀 *NUEVO PEDIDO #{sesion['o']}*\n👤 Cliente: {nombre}\n📦: {sesion['p_nom']}\n💰: S/ {sesion['t']:.2f}\n📱 Chat: {wa_link}")
        client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA, body=notificacion, media_url=[media_url] if media_url else None)
        
        res.message(f"¡Gracias {nombre}! Pedido registrado. La encargada te contactará pronto. 🚀")
        db_delete(num)

    return str(res)

if __name__ == "__main__":
    app.run()
