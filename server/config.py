"""Configuracion del servidor.

En la VPS (Docker) todos los valores llegan como variables de entorno desde
/opt/asistencia/.env, que genera deploy/instalar_vps.sh. En un PC local, sin
variables de entorno, se usan los valores por defecto de abajo (XAMPP).
"""
import os

BASE = os.path.dirname(os.path.abspath(__file__))


def _env(clave, defecto):
    v = os.environ.get(clave)
    return defecto if v is None or v == "" else v


# Carpeta de datos persistentes: fotos de referencia, clave de sesiones y firmware OTA.
# Local: la carpeta server/. En Docker: /datos (volumen).
DATA_DIR = _env("DATA_DIR", BASE)

# Conexion a la base de datos (MariaDB/MySQL)
DB_CONFIG = {
    "host": _env("DB_HOST", "localhost"),
    "port": int(_env("DB_PORT", "3306")),
    "user": _env("DB_USER", "root"),
    "password": _env("DB_PASSWORD", ""),
    "database": _env("DB_NAME", "universidad_v2"),
}

# Base anterior (esquema viejo). preparar_bd.py puede copiar de ahi
# estudiantes, aulas, asignaturas y usuarios. Si no existe, se ignora.
DB_ANTERIOR = _env("DB_ANTERIOR", "universidad")

# Identificador de la camara para los datos de ejemplo de preparar_bd.py.
# Cada camara real se registra en el panel (Infraestructura > Dispositivos),
# con su propio identificador y token.
DISPOSITIVO_CAMARA = _env("DISPOSITIVO_CAMARA", "ESP32-CAM-01")

# Servidor web: "127.0.0.1" = solo este PC; "0.0.0.0" = accesible desde la red / Docker
HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "8000"))

# Detras de un proxy inverso (Caddy en la VPS): confiar en X-Forwarded-For / -Proto
TRUST_PROXY = _env("TRUST_PROXY", "0") == "1"
# Cookie de sesion solo por HTTPS (activar en la VPS)
COOKIE_SECURE = _env("COOKIE_SECURE", "0") == "1"
# Clave para firmar sesiones. Si no se define, se genera y guarda en DATA_DIR/secreto.key
SECRET_KEY = _env("SECRET_KEY", "")

# Reconocimiento LBPH: mas BAJO = mas estricto
UMBRAL_CONFIANZA = float(_env("UMBRAL_CONFIANZA", "60"))
# Ancho (px) de la copia reducida donde se busca el rostro. 400 = el doble de
# rapido que la foto VGA completa, con los mismos aciertos en las pruebas.
ANCHO_DETECCION = int(_env("ANCHO_DETECCION", "400"))

# Segundos que se ignora a un mismo estudiante despues de registrarlo
# (evita escribir en la BD en cada fotograma mientras sigue frente a la camara)
ESPERA_MISMO_ROSTRO = int(_env("ESPERA_MISMO_ROSTRO", "30"))

# ---- Parametros que el servidor envia a la ESP32-CAM al conectarse ----
# Milisegundos entre fotogramas (100 = ~10 fps maximo; la red puede dar menos)
CAM_INTERVALO_MS = int(_env("CAM_INTERVALO_MS", "150"))
# QVGA (320x240), CIF (400x296), VGA (640x480) o SVGA (800x600)
CAM_RESOLUCION = _env("CAM_RESOLUCION", "CIF")
# Calidad JPEG de 10 (mejor, mas pesado) a 40 (peor, mas liviano)
CAM_CALIDAD = int(_env("CAM_CALIDAD", "15"))
# Potencia maxima del Wi-Fi de la camara en dBm (2 a 19.5). 0 = no la cambia (19.5).
# Bajarla (13 o 11) evita que la ESP32-CAM pierda paquetes si su alimentacion es justa.
CAM_POTENCIA_WIFI = float(_env("CAM_POTENCIA_WIFI", "0"))
# 1 = fotos en escala de grises (mas livianas). 0 = a color (recomendado: se ve y reconoce mejor).
CAM_GRIS = _env("CAM_GRIS", "0") == "1"
# 1 = transmitir siempre (pruebas). 0 = solo en franjas de clase del aula.
CAM_SIEMPRE_ACTIVA = _env("CAM_SIEMPRE_ACTIVA", "0") == "1"
