import os, json, random, gspread, sqlite3, re
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime
import re
app = Flask(__name__)

# --- CONFIGURACIÓN IA (GEMINI) ---
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

# --- FUNCIÓN CEREBRO (IA) ---
def procesar_lista_con_ia(texto_usuario, lista_precios):
    prompt = f"""
    Eres el asistente de Librería R&V. Basado en esta lista de precios: {lista_precios}.
    Analiza el pedido del cliente: "{texto_usuario}".
    Extrae los productos, cantidades y calcula el total. 
    Responde ÚNICAMENTE en formato JSON: 
    {{"total": 0.0, "items_encontrados": ["2x Cuaderno (S/ 10.00)"], "no_encontrados": ["item"]}}
    """
    response = model_ai.generate_content(prompt)
    try:
        Limpiamos la respuesta para quedarnos solo con el JSON
        json_data = re.search(r'\{.*\}', response.text, re.DOTALL).group()
        return json.loads(json_data)
    except:
        return {"total": 0.0, "items_encontrados": [], "no_encontrados": ["Error al procesar"]}

@app.route("/whatsapp", methods=['POST'])
def reply():
    num = request.form.get('From')
    num_limpio = num.replace('whatsapp:', '').replace('+', '')
    body = request.form.get('Body', '').strip()
    media_url = request.form.get('MediaUrl0')
    res = MessagingResponse()
    sesion = db_get(num)

     1. MENÚ PRINCIPAL
    if not sesion or body.upper() in ["HOLA", "MENU", "REINICIAR"]:
        db_save(num, "menu_principal")
        msg = ("📚 *LIBRERÍA R&V*\n¡Hola André! ¿Qué deseas llevar hoy?\n\n"
               "1️⃣ *Enviar Lista:* Escribe todo y la IA lo sumará.\n"
               "2️⃣ *Regalos:* Detalles por categorías.\n"
               "3️⃣ *Producto Único:* Compra algo rápido.\n"
               "4️⃣ *Consultas:* Hablar con nosotros.")
        res.message(msg)
        return str(res)

     2. PROCESAR OPCIÓN 1: LISTA CON IA
    elif sesion["step"] == "esperando_lista":
        precios = hoja_prod.get_all_records()
        resultado = procesar_lista_con_ia(body, precios)
        
        orden = str(random.randint(1000, 9999))
        resumen = f"📝 *ORDEN #{orden}*\n" + "\n".join(resultado['items_encontrados'])
        if resultado['no_encontrados']:
            resumen += f"\n\n⚠️ *NO ENCONTRADO:* {', '.join(resultado['no_encontrados'])}"
        
        resumen += f"\n\n💰 *TOTAL ESTIMADO: S/ {resultado['total']:.2f}*"
        
        db_save(num, "seleccion_pago", orden=orden, total=resultado['total'], prod_nom=f"LISTA #{orden}", cant=len(resultado['items_encontrados']))
        res.message(f"{resumen}\n\n*¿Cómo pagar?*\n1. Yape\n2. Tarjeta\n3. Efectivo")

    # 3. SELECCIÓN DE PAGO (Común para todos)
    elif sesion["step"] == "seleccion_pago":
        metodos = {"1": "YAPE", "2": "TARJETA", "3": "EFECTIVO"}
        pago = metodos.get(body, "YAPE")
        db_save(num, "finalizar", orden=sesion['o'], total=sesion['t'], prod_nom=f"{sesion['p_nom']} ({pago})", cant=sesion['c'])
        
        msg = "Envía tu *NOMBRE* y adjunta la *CAPTURA* del pago." if pago != "EFECTIVO" else "Envía tu *NOMBRE* para confirmar."
        res.message(f"✅ Has elegido: *{pago}*.\nPara terminar, {msg}")

    # 4. FINALIZAR Y ENVIAR A ENCARGADA (Asegurando Imagen)
    elif sesion["step"] == "finalizar":
        if "EFECTIVO" not in sesion['p_nom'] and not media_url:
            res.message("⚠️ *¡Falta la imagen!* Envía tu nombre adjuntando la captura de pago.")
            return str(res)

        nombre = body.title()
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
        
        # Guardar en la hoja correcta
        hoja = hoja_v_listas if "LISTA" in sesion['p_nom'] else (hoja_v_regalos if "REGALO" in sesion['p_nom'] else hoja_v_utiles)
        hoja.append_row([fecha, nombre, sesion['o'], sesion['c'], sesion['p_nom'], sesion['t']])

        # Notificar a la encargada
        wa_link = f"https://wa.me/{num_limpio}"
        notif = f"🚀 *NUEVA VENTA #{sesion['o']}*\n👤 Cliente: {nombre}\n📦: {sesion['p_nom']}\n💰: S/ {sesion['t']:.2f}\n📱 Chat: {wa_link}"
        
        client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA, body=notif, media_url=[media_url] if media_url else None)
        
        res.message(f"¡Gracias {nombre}! Pedido #{sesion['o']} registrado. La encargada revisará el stock y te contactará. 🚀")
        sqlite3.connect(DB_NAME).execute("DELETE FROM sesiones WHERE num = ?", (num,)).close()

    # (Aquí irían las lógicas de Regalos y Producto Único que ya tienes)
    return str(res)

if __name__ == "__main__":
    app.run()
