"""
=========================================================
 ASISTENCIA FACIAL - Servidor local (PC)
=========================================================

Que hace este script:
  1. Se conecta como CLIENTE al WebSocket que expone el ESP32-CAM
     (el ESP32 es el servidor, en ws://IP_DEL_ESP32:81).
  2. Recibe cada frame JPEG que transmite la camara.
  3. Detecta el rostro con un clasificador Haar de OpenCV.
  4. Compara ese rostro contra una base de referencia local
     (carpeta rostros_referencia/<codigo_estudiante>/*.jpg)
     usando el algoritmo LBPH (Local Binary Patterns Histograms),
     100% local, sin nube y sin costo.
  5. Si el rostro coincide por debajo del umbral de confianza:
       - Envia "GREEN" al ESP32-CAM (LED verde)
       - Registra la asistencia en la base de datos MySQL
         "universidad" (tabla asistencias), si esta configurada.
     Si no coincide o no hay rostro:
       - Envia "RED" al ESP32-CAM (LED rojo)

REQUISITOS (ver requirements.txt):
  pip install -r requirements.txt --break-system-packages

CONFIGURA ANTES DE EJECUTAR:
  - ESP32_WS_URL con la IP real de tu ESP32-CAM
  - Los datos de conexion a MySQL (o pon USAR_BASE_DE_DATOS = False
    para probar solo el reconocimiento y los LEDs, sin BD todavia)
  - ID_ASIGNATURA_ACTUAL: el id_asignatura (de tu tabla asignaturas)
    para el que este equipo/aula esta tomando asistencia
"""

import asyncio
import io
import os
import sys
from datetime import date

import cv2
import numpy as np
import websockets

# ---------------------- CONFIGURACION ----------------------

#ESP32_WS_URL = "ws://192.168.101.7:81"  # <-- CAMBIA esta IP por la que muestra el ESP32-CAM en el Monitor Serial
ESP32_WS_URL = "ws://110.156.208.66:81"

CARPETA_ROSTROS = os.path.join(os.path.dirname(__file__), "rostros_referencia")
MODELO_CACHE = os.path.join(os.path.dirname(__file__), "modelo_lbph.yml")
ETIQUETAS_CACHE = os.path.join(os.path.dirname(__file__), "etiquetas.txt")

# Confianza LBPH: mientras MAS BAJO el numero, MEJOR la coincidencia.
# Empieza con 60 y ajusta segun tus pruebas (sube si rechaza de mas,
# baja si acepta rostros que no deberia).
UMBRAL_CONFIANZA = 60.0

USAR_BASE_DE_DATOS = True  # Pon True cuando ya tengas MySQL configurado
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "universidad",
}
ID_ASIGNATURA_ACTUAL = 1  # Ajusta al id_asignatura real de tu BD

# ---------------------- RECONOCIMIENTO FACIAL ----------------------

RUTA_CASCADE = os.path.join(os.path.dirname(__file__), "haarcascade_frontalface_default.xml")
detector_rostros = cv2.CascadeClassifier(RUTA_CASCADE)

if detector_rostros.empty():
    print(f"ERROR: no se pudo cargar el clasificador de rostros en {RUTA_CASCADE}")
    print("Verifica que el archivo haarcascade_frontalface_default.xml este junto a server.py")
    sys.exit(1)
reconocedor = cv2.face.LBPHFaceRecognizer_create()
etiqueta_a_codigo = {}  # {0: "A0001", 1: "A0002", ...}


def entrenar_o_cargar_modelo():
    """Entrena el reconocedor desde la carpeta de referencias, o carga
    un modelo ya entrenado previamente si no hay cambios."""
    global etiqueta_a_codigo

    if not os.path.isdir(CARPETA_ROSTROS):
        print(f"ERROR: no existe la carpeta {CARPETA_ROSTROS}")
        print("Crea una subcarpeta por estudiante con sus fotos, por ejemplo:")
        print("  rostros_referencia/A0001/foto1.jpg")
        sys.exit(1)

    if os.path.exists(MODELO_CACHE) and os.path.exists(ETIQUETAS_CACHE):
        print("Cargando modelo LBPH ya entrenado (modelo_lbph.yml)...")
        reconocedor.read(MODELO_CACHE)
        with open(ETIQUETAS_CACHE, "r", encoding="utf-8") as f:
            for linea in f:
                idx, codigo = linea.strip().split(",", 1)
                etiqueta_a_codigo[int(idx)] = codigo
        print(f"Modelo cargado con {len(etiqueta_a_codigo)} estudiantes.")
        return

    print("Entrenando modelo LBPH desde rostros_referencia/ ...")
    imagenes = []
    etiquetas = []
    siguiente_id = 0

    for codigo_estudiante in sorted(os.listdir(CARPETA_ROSTROS)):
        subcarpeta = os.path.join(CARPETA_ROSTROS, codigo_estudiante)
        if not os.path.isdir(subcarpeta):
            continue

        etiqueta_a_codigo[siguiente_id] = codigo_estudiante
        fotos_validas = 0

        for archivo in os.listdir(subcarpeta):
            ruta = os.path.join(subcarpeta, archivo)
            img = cv2.imread(ruta, cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue

            rostros = detector_rostros.detectMultiScale(img, 1.1, 5)
            for (x, y, w, h) in rostros:
                recorte = cv2.resize(img[y:y + h, x:x + w], (200, 200))
                imagenes.append(recorte)
                etiquetas.append(siguiente_id)
                fotos_validas += 1

        print(f"  {codigo_estudiante}: {fotos_validas} rostro(s) detectado(s)")
        siguiente_id += 1

    if not imagenes:
        print("ERROR: no se detecto ningun rostro en rostros_referencia/.")
        print("Verifica que las fotos muestren la cara de frente y con buena luz.")
        sys.exit(1)

    reconocedor.train(imagenes, np.array(etiquetas))
    reconocedor.save(MODELO_CACHE)
    with open(ETIQUETAS_CACHE, "w", encoding="utf-8") as f:
        for idx, codigo in etiqueta_a_codigo.items():
            f.write(f"{idx},{codigo}\n")

    print(f"Modelo entrenado y guardado con {len(etiqueta_a_codigo)} estudiantes.")


def reconocer_rostro(frame_bgr):
    """Devuelve (codigo_estudiante, confianza) si reconoce a alguien
    por debajo del umbral, o (None, None) si no."""
    gris = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    rostros = detector_rostros.detectMultiScale(gris, 1.1, 5)

    if len(rostros) == 0:
        return None, None

    # Usa el rostro mas grande detectado (el mas cercano a la camara)
    x, y, w, h = max(rostros, key=lambda r: r[2] * r[3])
    recorte = cv2.resize(gris[y:y + h, x:x + w], (200, 200))

    etiqueta, confianza = reconocedor.predict(recorte)

    if confianza <= UMBRAL_CONFIANZA:
        return etiqueta_a_codigo.get(etiqueta), confianza

    return None, confianza


# ---------------------- BASE DE DATOS ----------------------

def registrar_asistencia(codigo_estudiante):
    if not USAR_BASE_DE_DATOS:
        return

    try:
        import mysql.connector

        conexion = mysql.connector.connect(**DB_CONFIG)
        cursor = conexion.cursor()

        cursor.execute(
            "SELECT id_estudiante FROM estudiantes WHERE codigo = %s",
            (codigo_estudiante,),
        )
        fila = cursor.fetchone()
        if not fila:
            print(f"  Aviso: {codigo_estudiante} no existe en la tabla estudiantes.")
            return

        id_estudiante = fila[0]
        hoy = date.today().isoformat()

        cursor.execute(
            """
            INSERT INTO asistencias (id_estudiante, id_asignatura, fecha, estado)
            VALUES (%s, %s, %s, 'presente')
            ON DUPLICATE KEY UPDATE estado = 'presente'
            """,
            (id_estudiante, ID_ASIGNATURA_ACTUAL, hoy),
        )
        conexion.commit()
        cursor.close()
        conexion.close()
        print(f"  Asistencia registrada para {codigo_estudiante} ({hoy}).")

    except Exception as e:
        print(f"  No se pudo registrar en la base de datos: {e}")


# ---------------------- BUCLE PRINCIPAL (WEBSOCKET) ----------------------

async def procesar_video():
    print(f"Conectando al ESP32-CAM en {ESP32_WS_URL} ...")

    async with websockets.connect(ESP32_WS_URL, max_size=None) as ws:
        print("Conectado. Esperando video...")

        async for mensaje in ws:
            # Los mensajes de texto son estados que el propio ESP32
            # reenvia (por ejemplo si otro cliente cambio el LED);
            # aqui solo nos interesan los mensajes binarios (frames).
            if isinstance(mensaje, str):
                continue

            datos = np.frombuffer(mensaje, dtype=np.uint8)
            frame = cv2.imdecode(datos, cv2.IMREAD_COLOR)
            if frame is None:
                continue

            codigo, confianza = reconocer_rostro(frame)

            if codigo:
                print(f"Reconocido: {codigo} (confianza {confianza:.1f}) -> LED verde")
                await ws.send("GREEN")
                registrar_asistencia(codigo)
            else:
                if confianza is not None:
                    print(f"No coincide (confianza {confianza:.1f}) -> LED rojo")
                else:
                    print("Sin rostro detectado -> LED rojo")
                await ws.send("RED")


async def main():
    entrenar_o_cargar_modelo()

    while True:
        try:
            await procesar_video()
        except (websockets.ConnectionClosed, OSError) as e:
            print(f"Conexion perdida con el ESP32-CAM ({e}). Reintentando en 3s...")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
