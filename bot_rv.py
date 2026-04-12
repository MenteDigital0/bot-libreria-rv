import os, json, random, gspread, sqlite3, re
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime

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

# --- BASE DE DATOS SQLITE (Persistencia) ---
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
    conn.execute("DELETE FROM sesiones WHERE num = ?", (num,))
    conn.commit()
    conn.close()

# --- CEREBRO IA (Procesa lenguaje natural) ---
def procesar_lista_con_ia(texto_usuario, lista_precios):
    # 1. Limpieza de datos del Excel para la IA
    # Esto asegura que la IA reciba 'nombre' y 'precio' aunque las columnas tengan espacios
    datos_para_ia = []
    for fila in lista_precios:
        nombre = fila.get('Producto', '').strip()
        # Intentamos obtener el precio de la columna 'Precio' o de la siguiente si está movida
        precio = fila.get('Precio') if fila.get('Precio') else fila.get('', 0)
        
        if nombre and precio:
            datos_para_ia.append({"n": nombre, "p": precio})

    # 2. El Prompt (Instrucciones)
    prompt = f"""
    Eres el sistema de facturación de 'Librería R&V'.
    BASE DE DATOS: {json.dumps(datos_para_ia)}
    PEDIDO: "{texto_usuario}"

    TAREA:
    - Encuentra los productos que más se parezcan al pedido.
    - Si dicen "cuaderno", usa "Cuaderno A4 100h". Si dicen "lapiz", usa "Lapis Carbon".
    - Calcula el total (cantidad x precio).
    - Responde EXCLUSIVAMENTE en formato JSON:
    {{
      "total": 0.0, 
      "items_encontrados": ["2x Cuaderno A4 100h (S/ 13.00)"], 
      "no_encontrados": []
    }}
    """

    try:
        response = model_ai.generate_content(prompt)
        
        # 3. Extracción robusta del JSON (Uso de Regex)
        # Esto busca cualquier cosa que esté entre llaves { }
        match = re.search(r'\{.*\}', response.text, re.DOTALL)
        
        if match:
            json_data = json.loads(match.group())
            return json_data
        else:
            print("Error: La IA no devolvió un JSON válido")
            return {"total": 0.0, "items_encontrados": [], "no_encontrados": ["Error de formato"]}

    except Exception as e:
        print(f"Error crítico en función procesar: {e}")
        return {"total": 0.0, "items_encontrados": [], "no_encontrados": ["Error de conexión"]}
    # 2. LÓGICA DE SELECCIÓN DE MENÚ
    elif sesion["step"] == "menu_principal":
        if body == "1":
            db_save(num, "esperando_lista")
            res.message("📝 *MODO LISTA*\nEscribe tus útiles (ej: 2 cuadernos, 1 borrador, 3 lápices).")
        elif body == "2":
            datos = hoja_regalos.get_all_records()
            categorias = sorted(list(set([r["Categoría"] for r in datos])))
            db_save(num, "regalo_cat")
            txt = "💝 *CATEGORÍAS DE REGALOS*\n"
            for i, cat in enumerate(categorias, 1): txt += f"*{i}.* {cat}\n"
            res.message(txt + "\nElige el número de una categoría.")
        elif body == "3":
            datos = hoja_prod.get_all_records()
            categorias = sorted(list(set([p["Categoría"] for p in datos])))
            db_save(num, "prod_cat")
            txt = "🛍️ *PRODUCTOS*\n"
            for i, cat in enumerate(categorias, 1): txt += f"*{i}.* {cat}\n"
            res.message(txt + "\nElige el número de una categoría.")
        else:
            res.message("❌ Elige una opción (1-4).")
        return str(res)

    # 3. PROCESAR LISTA CON IA
    elif sesion["step"] == "esperando_lista":
        resultado = procesar_lista_con_ia(body, hoja_prod.get_all_records())
        orden = str(random.randint(1000, 9999))
        
        if not resultado['items_encontrados']:
            res.message("❌ No reconocí productos. Prueba escribiéndolos de nuevo.")
            return str(res)

        resumen = f"📝 *ORDEN #{orden}*\n" + "\n".join(resultado['items_encontrados'])
        if resultado['no_encontrados']:
            resumen += f"\n\n⚠️ *A CONSULTAR:* {', '.join(resultado['no_encontrados'])}"
        
        db_save(num, "seleccion_pago", orden=orden, total=resultado['total'], prod_nom=f"LISTA #{orden}", cant=len(resultado['items_encontrados']))
        res.message(f"{resumen}\n\n💰 *TOTAL: S/ {resultado['total']:.2f}*\n\n*¿Cómo pagar?*\n1. Yape/Plin\n2. Tarjeta\n3. Efectivo")
        return str(res)

    # 4. FLUJO DE REGALOS
    elif sesion["step"] == "regalo_cat":
        datos = hoja_regalos.get_all_records()
        categorias = sorted(list(set([r["Categoría"] for r in datos])))
        idx = int(body)-1 if body.isdigit() else -1
        if 0 <= idx < len(categorias):
            cat_sel = categorias[idx]
            items = [r for r in datos if r["Categoría"] == cat_sel]
            txt = f"🎁 *{cat_sel}*\n"
            for i, r in enumerate(items, 1): txt += f"*{i}.* {r['Detalle']} - S/ {r['Precio']:.2f}\n"
            db_save(num, f"regalo_item_{cat_sel}")
            res.message(txt + "\nElige el número del detalle.")
        else: res.message("❌ Categoría inválida.")
        return str(res)

    elif "regalo_item_" in sesion["step"]:
        cat = sesion["step"].replace("regalo_item_", "")
        items = [r for r in hoja_regalos.get_all_records() if r["Categoría"] == cat]
        idx = int(body)-1 if body.isdigit() else -1
        if 0 <= idx < len(items):
            it = items[idx]
            db_save(num, "seleccion_pago", orden=str(random.randint(1000, 9999)), total=float(it['Precio']), prod_nom=f"REGALO: {it['Detalle']}", cant=1)
            res.message(f"Elegiste: *{it['Detalle']}*\nTotal: S/ {it['Precio']:.2f}\n\n1. Yape\n2. Tarjeta\n3. Efectivo")
        else: res.message("❌ Opción inválida.")
        return str(res)

    # 5. SELECCIÓN DE PAGO
    elif sesion["step"] == "seleccion_pago":
        metodos = {"1": "YAPE", "2": "TARJETA", "3": "EFECTIVO"}
        pago = metodos.get(body, "YAPE")
        db_save(num, "finalizar", orden=sesion['o'], total=sesion['t'], prod_nom=f"{sesion['p_nom']} ({pago})", cant=sesion['c'])
        
        instru = "envía nombre y CAPTURA." if pago != "EFECTIVO" else "envía tu nombre."
        res.message(f"✅ Pago: *{pago}*. Ahora {instru}")
        return str(res)

    # 6. FINALIZAR Y REGISTRAR
    elif sesion["step"] == "finalizar":
        if "EFECTIVO" not in sesion['p_nom'] and not media_url:
            res.message("⚠️ Falta la captura del pago. Envíala junto con tu nombre.")
            return str(res)

        nombre, fecha = body.title(), datetime.now().strftime("%d/%m/%Y %H:%M")
        hoja = hoja_v_listas if "LISTA" in sesion['p_nom'] else (hoja_v_regalos if "REGALO" in sesion['p_nom'] else hoja_v_utiles)
        hoja.append_row([fecha, nombre, sesion['o'], sesion['c'], sesion['p_nom'], sesion['t']])

        notif = f"🚀 *NUEVA VENTA #{sesion['o']}*\n👤: {nombre}\n📦: {sesion['p_nom']}\n💰: S/ {sesion['t']:.2f}\n📱: https://wa.me/{num_limpio}"
        client_twilio.messages.create(from_=NUMERO_BOT, to=NUMERO_ENCARGADA, body=notif, media_url=[media_url] if media_url else None)
        
        res.message(f"¡Gracias {nombre}! Pedido #{sesion['o']} registrado con éxito. 🚀")
        db_delete(num)
        return str(res)

    return str(res)

if __name__ == "__main__":
    app.run()
