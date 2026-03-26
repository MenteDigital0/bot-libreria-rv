import os, json, random, gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURACIÓN SEGURA ---
json_env = os.environ.get('GOOGLE_JSON_CONTENT')
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_env), ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
client_gs = gspread.authorize(creds)

# Conexión al Excel (Asegúrate que el nombre sea exacto)
nombre_excel = "Libreria RV Datos"
try:
    spreadsheet = client_gs.open(nombre_excel)
    hoja_prod = spreadsheet.worksheet("Productos")
    hoja_vent = spreadsheet.worksheet("Ventas")
    print("✅ Conexión exitosa al Excel")
except Exception as e:
    print(f"❌ Error de conexión: {e}")
# Twilio (Reemplaza con tus datos reales)
ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
# --- LÍNEAS DE PRUEBA ---
if not ACCOUNT_SID or not AUTH_TOKEN:
    print("❌ ERROR: No se cargaron las credenciales de Twilio desde Render")
else:
    print(f"✅ Credenciales detectadas (SID termina en: {ACCOUNT_SID[-4:]})")
# ------------------------
NUMERO_BOT = 'whatsapp:+14155238886'
NUMERO_ENCARGADA = 'whatsapp:+51921264742' # <--- PON TU CEL AQUÍ PARA PROBAR
client_twilio = Client(ACCOUNT_SID, AUTH_TOKEN)
sesiones = {}

@app.route("/whatsapp", methods=['POST'])
def reply():
    num = request.form.get('From')
    body = request.form.get('Body', '').strip().upper()
    media_url = request.form.get('MediaUrl0')
    res = MessagingResponse()

    # --- LÓGICA PARA LA ENCARGADA (VALIDAR STOCK) ---
    if num == NUMERO_ENCARGADA and ("SI" in body or "NO" in body):
        partes = body.split()
        if len(partes) >= 2:
            accion = partes[0]
            orden_ref = partes[1]
            cliente_whatsapp = next((c for c, d in sesiones.items() if str(d.get("o")) == orden_ref), None)
            
            if cliente_whatsapp:
                if accion == "SI":
                    total = sesiones[cliente_whatsapp]["t"]
                    client_twilio.messages.create(from_=NUMERO_BOT, to=cliente_whatsapp, 
                        body=f"✅ *¡STOCK CONFIRMADO!*\nTotal: *S/ {total:.2f}*\n\nPor favor, yapea al *987654321* y envía tu nombre con la captura del pago.")
                    sesiones[cliente_whatsapp]["step"] = "finalizar"
                else:
                    client_twilio.messages.create(from_=NUMERO_BOT, to=cliente_whatsapp, 
                        body="😔 *LO SENTIMOS*\nNo hay stock. Escribe 'MENU' para ver otras opciones.")
                    del sesiones[cliente_whatsapp]
            return str(res)

    # --- LÓGICA PARA EL CLIENTE ---
    if num not in sesiones or body in ["HOLA", "MENU", "REINICIAR"]:
        datos = hoja_prod.get_all_records()
        categorias = list(set([f["Categoría"] for f in datos]))
        sesiones[num] = {"step": "cat", "datos_raw": datos, "cats": categorias}
        txt = "📚 *LIBRERÍA R&V*\n¿Qué categoría buscas?\n"
        for i, c in enumerate(categorias, 1): txt += f"*{i}.* {c}\n"
        res.message(txt)

    elif sesiones[num]["step"] == "cat":
        idx = int(body) - 1 if body.isdigit() else -1
        if 0 <= idx < len(sesiones[num]["cats"]):
            cat_sel = sesiones[num]["cats"][idx]
            productos = [p for p in sesiones[num]["datos_raw"] if p["Categoría"] == cat_sel]
            txt = f"📂 *{cat_sel}*\n"
            menu_p = {}
            for i, p in enumerate(productos, 1):
                txt += f"*{i}.* {p['Producto']} - S/ {p['Precio']:.2f}\n"
                menu_p[str(i)] = p
            sesiones[num].update({"step": "prod", "menu_p": menu_p})
            res.message(txt + "\nElige el número del producto.")
        else: res.message("❌ Elige una categoría válida.")

    elif sesiones[num]["step"] == "prod":
        if body in sesiones[num]["menu_p"]:
            sesiones[num].update({"p": sesiones[num]["menu_p"][body], "step": "cant"})
            res.message(f"¿Cuántos de *{sesiones[num]['p']['Producto']}*?")
        else: res.message("❌ Elige un número.")

    elif sesiones[num]["step"] == "cant":
        if body.isdigit():
            cant, p = int(body), sesiones[num]["p"]
            total, orden = cant * float(p['Precio']), random.randint(1000, 9999)
            sesiones[num].update({"c": cant, "t": total, "o": orden, "step": "esperando_stock"})
            res.message("⏳ *Verificando Stock...*\nTe avisaremos en un momento.")
            client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA, 
                body=f"❓ *STOCK? ORDEN #{orden}*\n{cant}x {p['Producto']}\nResponde: *SI {orden}* o *NO {orden}*")
        else: res.message("❌ Escribe un número.")

    elif sesiones[num]["step"] == "finalizar":
        nombre = body.title()
        s = sesiones[num]
        hoja_vent.append_row([datetime.now().strftime("%d/%m/%Y %H:%M"), nombre, s['o'], s['c'], s['p']['Producto'], s['t']])
        client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA, 
            body=f"🚨 *PAGO RECIBIDO #{s['o']}*\nCliente: {nombre}", media_url=[media_url] if media_url else None)
        res.message(f"¡Gracias {nombre}! Pedido registrado. 🚀")
        del sesiones[num]

    return str(res)

if __name__ == "__main__":
    app.run()
