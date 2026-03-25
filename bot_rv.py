from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

# Base de datos simple de Librería R&V
inventario = {
    "cuaderno": 6.50,
    "lapicero": 1.50,
    "borrador": 1.00,
    "regla": 2.50
}

@app.route("/whatsapp", methods=['POST'])
def reply_whatsapp():
    # Usamos .form.get() que es más seguro para Twilio
    mensaje_cliente = request.form.get('Body', '').lower()
    
    # Creamos la respuesta de Twilio
    respuesta = MessagingResponse()
    msg = respuesta.message()

    encontrado = False
    
    # Lógica de búsqueda mejorada
    for producto, precio in inventario.items():
        if producto in mensaje_cliente:
            msg.body(f"¡Sí tenemos! El {producto} en Librería R&V cuesta S/ {precio:.2f}. ¿Te lo separo?")
            encontrado = True
            break
    
    if not encontrado:
        if "hola" in mensaje_cliente:
            msg.body("¡Hola! Bienvenido a Librería R&V 📚. ¿Qué producto buscas hoy?")
        else:
            msg.body("No encontré ese producto, pero puedes preguntar por: cuaderno, lapicero, borrador o regla.")

    return str(respuesta)

if __name__ == "__main__":
    # Importante: debug=True te dirá el error exacto en la consola si vuelve a fallar
    app.run(port=5000, debug=True)