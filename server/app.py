"""
ASISTENCIA FACIAL - Servidor unico (API + reconocimiento + pagina web)
Uso local:  python app.py   ->  abre http://127.0.0.1:8000
En la VPS:  lo ejecuta Docker (ver deploy/ y docs/GUIA_DESPLIEGUE.md)

- Sirve el panel (../frontend/index.html) y la API (/api/...).
- Login con usuarios en MySQL/MariaDB (bcrypt). Dos roles:
    admin   -> administra todo (catalogos, aulas, usuarios, estudiantes,
               grupos, horarios, matriculas, asistencia, camaras).
    docente -> solo consulta: sus grupos, la clase en vivo si es suya y el
               reporte de asistencia por fecha de las clases que dicta.
  El rol se valida en el SERVIDOR en cada peticion, no solo en la pagina.
- Las ESP32-CAM se conectan A ESTE SERVIDOR (no al reves) por WebSocket
  seguro en /ws/dispositivo, autenticadas con identificador + token. El
  servidor les dice cuando transmitir (franjas de clase de su aula), reconoce
  rostros (LBPH), registra el ingreso con registrar_ingreso_facial y les
  ordena el LED verde o rojo.
"""
import asyncio
import base64
import contextlib
import hashlib
import hmac
import os
import re
import secrets
import shutil
import sys
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Annotated, Literal

import bcrypt
import cv2
import jwt
import mysql.connector
import numpy as np
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from mysql.connector import errors as mye
from mysql.connector import pooling
from pydantic import BaseModel, BeforeValidator, Field

import config

FRONT = os.path.join(config.BASE, "..", "frontend", "index.html")
CASCADE = os.path.join(config.BASE, "haarcascade_frontalface_default.xml")
ROSTROS = os.path.join(config.DATA_DIR, "rostros_referencia")
CLAVE_FILE = os.path.join(config.DATA_DIR, "secreto.key")
FIRMWARE_DIR = os.path.join(config.DATA_DIR, "firmware")
CODIGO_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
IDENT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
ESPERA = config.ESPERA_MISMO_ROSTRO


# ---------------------- BASE DE DATOS ----------------------

_pool = None
MSG_BD = "No hay conexión con la base de datos. Revisa config.py, que MySQL esté encendido y que ejecutaste preparar_bd.py."

DUPLICADOS = [  # fragmento del nombre de la llave -> mensaje
    ("correo'", "Ya existe un registro con ese correo."),
    ("numero_documento'", "Ya existe un estudiante con ese documento."),
    ("uk_aula_bloque'", "Ya existe un aula con ese nombre en el bloque."),
    ("uk_grupo'", "Ese grupo ya existe para la asignatura y el periodo."),
    ("uk_matricula'", "El estudiante ya está en ese grupo."),
    ("identificador'", "Ya existe un dispositivo con ese identificador."),
    ("usuario'", "Ese nombre de usuario ya existe."),
    ("nombre'", "Ya existe un registro con ese nombre."),
]


def _pool_bd():
    global _pool
    if _pool is None:
        try:
            _pool = pooling.MySQLConnectionPool(
                pool_name="af", pool_size=8, autocommit=True, **config.DB_CONFIG
            )
        except mye.Error:
            raise HTTPException(503, MSG_BD)
    return _pool


def _error_bd(e):
    if e.errno == 1644:  # SIGNAL de un trigger/procedimiento: mensaje para el usuario
        return HTTPException(422, e.msg)
    if e.errno == 1062:
        m = str(e)
        for k, txt in DUPLICADOS:
            if k in m:
                return HTTPException(409, txt)
        return HTTPException(409, "Ya existe un registro con ese código.")
    if e.errno == 1451:
        return HTTPException(409, "No se puede eliminar: tiene registros relacionados. Puedes marcarlo como inactivo.")
    if e.errno == 1452:
        return HTTPException(422, "Falta un dato relacionado. Revisa las listas desplegables.")
    if e.errno in (3819, 4025):  # CHECK (MySQL / MariaDB)
        return HTTPException(422, "Algún valor está fuera del rango permitido.")
    print("Error de base de datos:", e)
    return HTTPException(500, "Error en la base de datos.")


@contextlib.contextmanager
def cur():
    try:
        con = _pool_bd().get_connection()
    except mye.Error:
        raise HTTPException(503, MSG_BD)
    try:
        c = con.cursor(dictionary=True)
        yield c
    except mye.Error as e:
        raise _error_bd(e)
    finally:
        con.close()


# ---------------------- SEGURIDAD Y ROLES ----------------------

def _secreto():
    """Clave para firmar sesiones: de SECRET_KEY o se genera sola la primera vez."""
    if config.SECRET_KEY:
        return config.SECRET_KEY
    os.makedirs(config.DATA_DIR, exist_ok=True)
    if not os.path.exists(CLAVE_FILE):
        with open(CLAVE_FILE, "w") as f:
            f.write(secrets.token_hex(32))
    with open(CLAVE_FILE) as f:
        return f.read().strip()


SECRETO = _secreto()
FALLOS = {}  # (ip, usuario) -> (intentos, ultimo)


def _token(request_or_ws):
    try:
        return jwt.decode(request_or_ws.cookies.get("sid") or "", SECRETO, algorithms=["HS256"])
    except Exception:
        return None


def usuario_actual(request: Request):
    """Valida la sesion y vuelve a leer rol/activo de la BD (un cambio de rol aplica de inmediato)."""
    t = _token(request)
    if not t or "uid" not in t:
        raise HTTPException(401, "Sesión no válida")
    with cur() as c:
        c.execute("SELECT id_usuario, usuario, nombre_completo, rol FROM usuarios WHERE id_usuario=%s AND activo=1",
                  (t["uid"],))
        u = c.fetchone()
    if not u:
        raise HTTPException(401, "Usuario desactivado")
    return {"uid": u["id_usuario"], "usuario": u["usuario"], "nombre": u["nombre_completo"], "rol": u["rol"]}


def solo_admin(u=Depends(usuario_actual)):
    if u["rol"] != "admin":
        raise HTTPException(403, "Solo el administrador puede hacer esto.")
    return u


def filtro_docente(u):
    """None para admin (ve todo); el id del usuario para docente (solo lo suyo)."""
    return None if u["rol"] == "admin" else u["uid"]


# ---------------------- RECONOCIMIENTO FACIAL ----------------------

if cv2.CascadeClassifier(CASCADE).empty():
    print(f"ERROR: no se pudo cargar {CASCADE}")
    sys.exit(1)

# El detector de OpenCV NO admite que dos hilos lo usen a la vez: falla con
# "Assertion failed ... getScaleData" (pasaba al editar fotos mientras la camara
# transmitia). Cada hilo usa su propia copia.
_detectores = threading.local()


def detectar(g, escala=1.1, vecinos=5):
    d = getattr(_detectores, "d", None)
    if d is None:
        d = _detectores.d = cv2.CascadeClassifier(CASCADE)
    return d.detectMultiScale(g, escala, vecinos)


def cara_principal(g):
    """Rectangulo (x, y, w, h) de la cara mas grande, o None. Las caras pequenas
    suelen ser detecciones falsas del fondo."""
    r = detectar(g)
    return tuple(int(v) for v in max(r, key=lambda a: a[2] * a[3])) if len(r) else None


MODELO = {"rec": None, "mapa": {}}
LOCK = threading.Lock()
ENTRENANDO = threading.Lock()
EXT_FOTO = (".jpg", ".jpeg", ".png")
MAX_FOTOS = 15   # fotos por estudiante


def entrenar():
    with ENTRENANDO:
        imgs, etq, mapa = [], [], {}
        carpetas = sorted(os.listdir(ROSTROS)) if os.path.isdir(ROSTROS) else []
        for cod in carpetas:
            sub = os.path.join(ROSTROS, cod)
            if not os.path.isdir(sub) or not CODIGO_RE.match(cod):
                continue
            idx, n = len(mapa), 0
            for f in sorted(os.listdir(sub)):
                g = cv2.imread(os.path.join(sub, f), cv2.IMREAD_GRAYSCALE)
                if g is None:
                    continue
                c = cara_principal(g)
                if c:
                    x, y, w, h = c
                    imgs.append(cv2.resize(g[y:y + h, x:x + w], (200, 200)))
                    etq.append(idx)
                    n += 1
            print(f"  {cod}: {n} rostro(s) detectado(s)")
            if n:
                mapa[idx] = cod
        rec = None
        if imgs:
            rec = cv2.face.LBPHFaceRecognizer_create()
            rec.train(imgs, np.array(etq))
        with LOCK:
            MODELO["rec"], MODELO["mapa"] = rec, mapa
        print(f"Modelo listo con {len(mapa)} estudiante(s).")


def reentrenar_async():
    threading.Thread(target=entrenar, daemon=True).start()


def reconocer(frame):
    """Devuelve (hay_rostro, codigo o None)."""
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # El rostro se busca en una copia reducida (mucho mas rapido: ~40 ms en vez de
    # ~85 ms por foto VGA en la VPS) y se reconoce con el recorte de la foto completa,
    # asi que la precision no cambia.
    h0, w0 = g.shape
    f = min(1.0, config.ANCHO_DETECCION / w0)
    peq = cv2.resize(g, (int(w0 * f), int(h0 * f)), interpolation=cv2.INTER_AREA) if f < 1 else g
    r = detectar(peq)
    if len(r) == 0:
        return False, None
    x, y, w, h = [int(v / f) for v in max(r, key=lambda a: a[2] * a[3])]
    with LOCK:
        rec, mapa = MODELO["rec"], MODELO["mapa"]
    if rec is None:
        return True, None
    et, conf = rec.predict(cv2.resize(g[y:y + h, x:x + w], (200, 200)))
    return True, (mapa.get(et) if conf <= config.UMBRAL_CONFIANZA else None)


def listar_fotos(cod):
    d = os.path.join(ROSTROS, cod)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.lower().endswith(EXT_FOTO))


def huellas_de_otros(codigo):
    """Huella (sha256) de cada foto de los DEMAS estudiantes -> codigo de su dueno."""
    huellas = {}
    if not os.path.isdir(ROSTROS):
        return huellas
    for cod in os.listdir(ROSTROS):
        if cod == codigo or not CODIGO_RE.match(cod):
            continue
        for f in listar_fotos(cod):
            with open(os.path.join(ROSTROS, cod, f), "rb") as fh:
                huellas[hashlib.sha256(fh.read()).hexdigest()] = cod
    return huellas


def validar_fotos(fotos, codigo):
    """Decodifica las fotos (dataURL) y devuelve (buenas, motivos de las rechazadas).
    Rechaza las que no tienen un rostro y las que ya son de otro estudiante (si dos
    estudiantes comparten foto, el sistema los confunde)."""
    buenas, rechazadas, otros = [], [], huellas_de_otros(codigo)
    for i, s in enumerate(fotos[:8], 1):
        try:
            raw = base64.b64decode(s.split(",", 1)[-1])
        except Exception:
            rechazadas.append(f"foto {i}: no es una imagen válida")
            continue
        if len(raw) > 3_000_000:
            rechazadas.append(f"foto {i}: pesa más de 3 MB")
            continue
        dueno = otros.get(hashlib.sha256(raw).hexdigest())
        if dueno:
            rechazadas.append(f"foto {i}: es la misma foto del estudiante {dueno}")
            continue
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            rechazadas.append(f"foto {i}: no es una imagen válida")
        elif not cara_principal(img):
            rechazadas.append(f"foto {i}: no se ve un rostro de frente")
        else:
            buenas.append(raw)
    return buenas, rechazadas


def agregar_fotos(codigo, buenas):
    """Guarda las fotos nuevas SIN borrar las que ya tenia el estudiante."""
    carpeta = os.path.join(ROSTROS, codigo)
    os.makedirs(carpeta, exist_ok=True)
    # Fecha + sufijo aleatorio: dos subidas en el mismo segundo no se pisan
    marca = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
    for i, raw in enumerate(buenas, 1):
        with open(os.path.join(carpeta, f"foto_{marca}_{i}.jpg"), "wb") as f:
            f.write(raw)


def foto_url(cod):
    fs = listar_fotos(cod)
    return f"/fotos/{cod}/{fs[0]}" if fs else ""


def resumen_fotos(buenas, rechazadas):
    txt = f"{len(buenas)} foto(s) agregada(s)."
    if rechazadas:
        txt += " Rechazadas: " + "; ".join(rechazadas) + "."
    return txt


# ---------------------- CAMARAS (ESP32-CAM conectadas a la VPS) ----------------------
#
# Protocolo sobre el WebSocket /ws/dispositivo (la ESP32 es el cliente):
#   ESP32 -> servidor   binario : fotograma JPEG
#                       "HELLO fw=1.0.0 ip=... rssi=-60 ..." al conectar
#                       "STAT rssi=... heap=... up=..."    cada minuto
#                       "STATUS:GREEN|RED|OFF"             confirma el LED
#                       "OTA:..."                          resultado de una actualizacion
#   servidor -> ESP32   "GREEN" | "RED" | "OFF"            LED de resultado
#                       "MODE ACTIVE" | "MODE SLEEP"       transmitir o no
#                       "CFG intervalo=100 tam=VGA calidad=12"
#                       "OTA 1.0.1"                        hay firmware nuevo

ULTIMO = {}     # (codigo, dispositivo) -> (instante, evento): evita registrar en cada fotograma
CLIENTES = {}   # websocket del navegador -> (rol, id_usuario)
CAMARAS = {}    # identificador -> Camara

SQL_CLASE_AULA = """
    SELECT h.id_horario, g.id_usuario_docente AS docente
      FROM horarios h
      JOIN grupos g   ON g.id_grupo = h.id_grupo
      JOIN periodos p ON p.id_periodo = g.id_periodo
     WHERE h.id_aula = %s AND h.dia_semana = WEEKDAY(CURDATE()) + 1
       AND g.estado <> 'cancelado' AND p.estado = 'activo'
       AND CURDATE() BETWEEN p.fecha_inicio AND p.fecha_fin
       AND CURTIME() BETWEEN SUBTIME(h.hora_inicio, SEC_TO_TIME(60 * IFNULL(
             (SELECT valor FROM parametros WHERE clave = 'minutos_antes_ingreso'), 15)))
           AND h.hora_fin
     ORDER BY h.hora_inicio LIMIT 1"""


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def autenticar_dispositivo(ident, token):
    """Devuelve la fila del dispositivo si el identificador y el token son validos."""
    if not ident or not token or not IDENT_RE.match(ident):
        return None
    with cur() as c:
        c.execute("SELECT id_dispositivo, id_aula, token_hash FROM dispositivos_acceso "
                  "WHERE identificador = %s AND activo = 1", (ident,))
        d = c.fetchone()
    if not d or not d["token_hash"] or not hmac.compare_digest(d["token_hash"], hash_token(token)):
        return None
    return d


def credenciales_dispositivo(headers):
    ident = (headers.get("x-dispositivo") or "").strip()
    auth = headers.get("authorization") or ""
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    return ident, token


def estado_aula(ident):
    """(id_aula, activo, clase_en_ventana) del dispositivo segun la BD."""
    with cur() as c:
        c.execute("SELECT id_aula, activo FROM dispositivos_acceso WHERE identificador = %s", (ident,))
        d = c.fetchone()
        if not d:
            return None
        c.execute(SQL_CLASE_AULA, (d["id_aula"],))
        return d["id_aula"], bool(d["activo"]), c.fetchone()


def firmware_disponible():
    """(version, ruta_bin) del firmware publicado para OTA, o None."""
    v, b = os.path.join(FIRMWARE_DIR, "version.txt"), os.path.join(FIRMWARE_DIR, "firmware.bin")
    if os.path.isfile(v) and os.path.isfile(b):
        with open(v) as f:
            return f.read().strip(), b
    return None


def version_tupla(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def texto_cfg():
    tam = config.CAM_RESOLUCION.upper() if config.CAM_RESOLUCION.upper() in ("QVGA", "CIF", "VGA", "SVGA") else "VGA"
    txt = (f"CFG intervalo={max(50, min(config.CAM_INTERVALO_MS, 5000))} tam={tam} "
           f"calidad={max(10, min(config.CAM_CALIDAD, 40))} gris={1 if config.CAM_GRIS else 0}")
    if config.CAM_POTENCIA_WIFI > 0:
        txt += f" tx={max(2.0, min(config.CAM_POTENCIA_WIFI, 19.5)):g}"
    return txt


class Camara:
    """Estado de una ESP32-CAM. Se conserva entre reconexiones para mostrarlo en el panel."""

    def __init__(self, ident):
        self.ident = ident
        self.ws = None
        self.conectada = False
        self.activa = False          # transmitiendo (franja de clase o encendida a mano)
        self.forzar_hasta = 0.0      # encendida a mano por el admin hasta este instante
        self.id_aula = None
        self.docente = None          # docente de la clase en curso en su aula
        self.info = {}               # fw, ip, rssi, heap... que reporta la camara
        self.frame = None
        self.frame_ts = 0.0
        self.fps = 0.0
        self.desde = None
        self.ultima_vez = None
        self.led = None
        self.ota_ofrecida = 0.0
        self.nuevo = asyncio.Event()
        self.lock = asyncio.Lock()

    async def enviar(self, txt):
        ws = self.ws
        if ws is None:
            return
        try:
            async with self.lock:
                await ws.send_text(txt)
        except Exception:
            pass

    async def cerrar(self, codigo=4000):
        ws, self.ws = self.ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close(code=codigo)

    async def sesion(self, ws, disp):
        self.ws, self.id_aula, self.conectada = ws, disp["id_aula"], True
        self.activa, self.led, self.frame, self.fps = False, None, None, 0.0
        self.desde = time.time()
        self.info = {"fw": (ws.headers.get("x-firmware") or "?")[:20]}
        print(f"Cámara {self.ident} conectada desde {ws.client.host if ws.client else '?'}")
        await self.enviar(texto_cfg())
        await revisar_modo(self)
        tarea = asyncio.create_task(self.procesar())
        n, t0 = 0, time.time()
        total_fotos, total_bytes, motivo, inicio = 0, 0, "?", time.time()
        try:
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect":
                    motivo = f"código {m.get('code')}"
                    break
                if m.get("bytes"):
                    self.frame, self.frame_ts = m["bytes"], time.time()
                    n += 1
                    total_fotos += 1
                    total_bytes += len(m["bytes"])
                    if self.frame_ts - t0 >= 5:
                        self.fps, n, t0 = round(n / (self.frame_ts - t0), 1), 0, self.frame_ts
                    self.nuevo.set()
                elif m.get("text"):
                    await self.texto(m["text"][:300])
        except WebSocketDisconnect as e:
            motivo = f"código {e.code}"
        except RuntimeError as e:
            motivo = f"error {e}"
        finally:
            tarea.cancel()
            dur = time.time() - inicio
            prom = total_bytes // total_fotos if total_fotos else 0
            detalle = (f"{motivo}; {dur:.0f} s conectada; {total_fotos} fotos recibidas, "
                       f"promedio {prom // 1024} KB")
            if self.ws is ws:  # si no fue reemplazada por una conexion nueva
                self.ws, self.conectada, self.activa, self.frame = None, False, False, None
                self.ultima_vez = time.time()
                print(f"Cámara {self.ident} desconectada ({detalle})")
            else:
                print(f"Cámara {self.ident}: conexión anterior cerrada ({detalle})")

    async def texto(self, t):
        if t.startswith(("HELLO", "STAT")):
            for par in t.split()[1:]:
                k, _, v = par.partition("=")
                if k in ("fw", "ip", "rssi", "heap", "psram", "up", "mac", "ps", "frames"):
                    self.info[k] = v[:40]
            if t.startswith("HELLO"):
                print(f"Cámara {self.ident}: {t[:200]}")
                await ofrecer_ota(self)
        elif t.startswith("STATUS:"):
            self.led = t[7:]
        elif t.startswith("OTA:"):
            self.info["ota"] = t[4:100]
            print(f"Cámara {self.ident} OTA: {t[4:100]}")

    async def procesar(self):
        """Reconoce el ultimo fotograma disponible (descarta los que llegan mientras procesa)."""
        deseado, hasta, ultima = "OFF", 0.0, None
        while True:
            await self.nuevo.wait()
            self.nuevo.clear()
            data = self.frame
            if not data:
                continue
            ahora, r = time.time(), None
            try:
                r = await asyncio.to_thread(evaluar_jpeg, data, self)
            except HTTPException as e:
                print(f"Cámara {self.ident}: {e.detail}")
            except Exception as e:
                print(f"Cámara {self.ident}: error evaluando rostro: {e}")
            if r:
                deseado, hasta = r[0], ahora + 3
                clave = (r[0], r[1].get("codigo"), r[1].get("resultado"))
                if clave != ultima:
                    ultima = clave
                    await difundir(r[1])
            elif ahora > hasta:
                deseado, ultima = "OFF", None
            if deseado != self.led:
                self.led = deseado
                await self.enviar(deseado)


async def revisar_modo(cam):
    """Decide si la camara transmite: franja de clase de su aula (desde X min antes) o encendida a mano."""
    try:
        r = await asyncio.to_thread(estado_aula, cam.ident)
    except Exception as e:
        print(f"Cámara {cam.ident}: no se pudo consultar el horario ({getattr(e, 'detail', e)})")
        return
    if not r or not r[1]:
        print(f"Cámara {cam.ident}: eliminada o desactivada en el panel, se desconecta")
        await cam.cerrar(4403)
        return
    cam.id_aula, clase = r[0], r[2]
    cam.docente = clase["docente"] if clase else None
    activa = bool(clase) or config.CAM_SIEMPRE_ACTIVA or time.time() < cam.forzar_hasta
    if activa != cam.activa:
        cam.activa = activa
        await cam.enviar("MODE ACTIVE" if activa else "MODE SLEEP")
        if not activa:
            cam.frame, cam.fps = None, 0.0
            if cam.led not in (None, "OFF"):
                cam.led = "OFF"
                await cam.enviar("OFF")


async def ofrecer_ota(cam):
    fw = firmware_disponible()
    if not fw or not cam.conectada:
        return
    if version_tupla(fw[0]) > version_tupla(cam.info.get("fw")) and time.time() - cam.ota_ofrecida > 900:
        cam.ota_ofrecida = time.time()
        print(f"Cámara {cam.ident}: ofreciendo firmware {fw[0]} (tiene {cam.info.get('fw')})")
        await cam.enviar(f"OTA {fw[0]}")


async def planificador():
    """Cada 20 s: revisa horario de cada camara y si hay firmware nuevo."""
    while True:
        await asyncio.sleep(20)
        for cam in list(CAMARAS.values()):
            if cam.conectada:
                await revisar_modo(cam)
                await ofrecer_ota(cam)


def registrar_facial(cod, cam):
    """Llama al procedimiento de la BD y arma el evento para la pagina."""
    ahora = time.time()
    previo = ULTIMO.get((cod, cam.ident))
    if previo and ahora - previo[0] < ESPERA:
        return previo[1]
    with cur() as c:
        c.execute("CALL registrar_ingreso_facial(%s, %s, @r, @m, @h, @c)", (cod, cam.ident))
        c.execute("SELECT @r AS r, @m AS m, @h AS h, @c AS c")
        res = c.fetchone()
        c.execute("SELECT CONCAT(nombres, ' ', apellidos) AS nombre FROM estudiantes WHERE codigo = %s", (cod,))
        est = c.fetchone()
        docente = cam.docente
        if res["h"]:
            c.execute("SELECT g.id_usuario_docente AS d FROM horarios h JOIN grupos g ON g.id_grupo = h.id_grupo "
                      "WHERE h.id_horario = %s", (res["h"],))
            docente = (c.fetchone() or {}).get("d")
    ev = {
        "ok": res["r"] == "permitido",
        "codigo": cod,
        "nombre": est["nombre"] if est else cod,
        "resultado": res["c"] or ("permitido" if res["r"] == "permitido" else "denegado"),
        "mensaje": res["m"],
        "hora": datetime.now().strftime("%H:%M:%S"),
        "dispositivo": cam.ident,
        "_docente": docente,
    }
    ULTIMO[(cod, cam.ident)] = (ahora, ev)
    print(f"  {cam.ident} {cod}: {res['r']} [{res['c']}] - {res['m']}")
    return ev


def evaluar_jpeg(data, cam):
    """Devuelve (LED, evento) o None si no hay rostro en el fotograma."""
    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    hay, cod = reconocer(frame)
    if cod:
        ev = registrar_facial(cod, cam)
        return ("GREEN" if ev["ok"] else "RED"), ev
    if hay:
        return "RED", {"ok": False, "codigo": None, "nombre": None, "resultado": "no_reconocido",
                       "mensaje": "Rostro no reconocido: la persona no está registrada o la imagen no es clara",
                       "hora": datetime.now().strftime("%H:%M:%S"), "dispositivo": cam.ident,
                       "_docente": cam.docente}
    return None


async def difundir(ev):
    """Envia el evento al admin y solo al docente dueño de la clase."""
    msg = {k: v for k, v in ev.items() if not k.startswith("_")}
    for ws, (rol, uid) in list(CLIENTES.items()):
        if rol != "admin" and uid != ev.get("_docente"):
            continue
        try:
            await ws.send_json(msg)
        except Exception:
            CLIENTES.pop(ws, None)


@contextlib.asynccontextmanager
async def lifespan(app):
    print("Entrenando modelo desde rostros_referencia/ ...")
    await asyncio.to_thread(entrenar)
    tarea = asyncio.create_task(planificador())
    yield
    tarea.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarea
    for cam in list(CAMARAS.values()):
        await cam.cerrar(1001)
    # Si hay un re-entrenamiento en curso, esperar a que termine antes de cerrar
    if ENTRENANDO.acquire(timeout=60):
        ENTRENANDO.release()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------- MODELOS DE ENTRADA ----------------------

def _vacio(v):
    return None if isinstance(v, str) and not v.strip() else v


Opc = lambda n: Annotated[Annotated[str, Field(max_length=n)] | None, BeforeValidator(_vacio)]  # noqa: E731
Nombre = lambda n: Field(min_length=1, max_length=n)  # noqa: E731
EstadoAI = Literal["activo", "inactivo"]
CORREO = r"^\S+@\S+\.\S+$"


class LoginIn(BaseModel):
    usuario: str = Field(min_length=1, max_length=50)
    clave: str = Field(min_length=1, max_length=200)


class BloqueIn(BaseModel):
    nombre: str = Nombre(50)
    estado: EstadoAI = "activo"


class TipoAulaIn(BloqueIn):
    pass


class JornadaIn(BaseModel):
    nombre: str = Nombre(30)
    estado: EstadoAI = "activo"


class ProgramaIn(BaseModel):
    codigo: str = Field(pattern=r"^[A-Za-z0-9_-]{1,20}$")
    nombre: str = Nombre(100)
    estado: EstadoAI = "activo"


class PeriodoIn(BaseModel):
    nombre: str = Nombre(20)
    fecha_inicio: date
    fecha_fin: date
    estado: Literal["planeado", "activo", "cerrado"] = "planeado"


class AulaIn(BaseModel):
    nombre: str = Nombre(50)
    piso: int = Field(ge=0, le=50)
    capacidad: int = Field(ge=1, le=1000)
    id_bloque: int
    id_tipo_aula: int
    estado: Literal["activa", "mantenimiento", "inactiva"] = "activa"


class DispositivoIn(BaseModel):
    id_aula: int
    nombre: str = Nombre(50)
    tipo: Literal["camara_facial", "lector_qr", "lector_rfid", "biometrico", "manual"] = "camara_facial"
    identificador: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    activo: bool = True


class AsignaturaIn(BaseModel):
    codigo: str = Field(pattern=r"^[A-Za-z0-9_-]{1,20}$")
    nombre: str = Nombre(100)
    creditos: int = Field(ge=1, le=10)
    semestre: int = Field(ge=1, le=12)
    id_programa: int
    estado: EstadoAI = "activo"


class GrupoIn(BaseModel):
    id_asignatura: int
    id_periodo: int
    id_jornada: int
    id_usuario_docente: int | None = None
    codigo_grupo: str = Field(pattern=r"^[A-Za-z0-9_-]{1,10}$")
    cupo: int = Field(ge=1, le=1000)
    estado: Literal["abierto", "cerrado", "cancelado"] = "abierto"


class HorarioIn(BaseModel):
    id_grupo: int
    id_aula: int
    dia_semana: int = Field(ge=1, le=7)
    hora_inicio: dtime
    hora_fin: dtime


class UsuarioIn(BaseModel):
    usuario: str = Field(pattern=r"^[A-Za-z0-9._-]{3,50}$")
    nombre_completo: str = Nombre(150)
    correo: Opc(120) = None
    rol: Literal["admin", "docente"] = "docente"
    clave: str = Field(min_length=8, max_length=72)


class UsuarioUpd(BaseModel):
    nombre_completo: str = Nombre(150)
    correo: Opc(120) = None
    rol: Literal["admin", "docente"]
    activo: bool = True
    clave: Opc(72) = None


class EstudianteBase(BaseModel):
    tipo_documento: Annotated[Literal["CC", "TI", "CE", "PA", "PPT"] | None, BeforeValidator(_vacio)] = None
    numero_documento: Opc(20) = None
    nombres: str = Nombre(80)
    apellidos: str = Nombre(80)
    correo: str = Field(pattern=CORREO, max_length=120)
    telefono: Opc(20) = None
    fecha_nacimiento: Annotated[date | None, BeforeValidator(_vacio)] = None
    id_programa: int
    semestre: int = Field(ge=1, le=12)
    estado: Literal["activo", "inactivo", "egresado", "retirado"] = "activo"


class EstudianteIn(EstudianteBase):
    codigo: str = Field(pattern=r"^[A-Za-z0-9_-]{1,20}$")
    fotos: list[str] = Field(min_length=1, max_length=8)


class EstudianteUpd(EstudianteBase):
    fotos: list[str] = Field(default=[], max_length=8)   # se AGREGAN a las existentes


class FotosIn(BaseModel):
    fotos: list[str] = Field(min_length=1, max_length=8)


class MatriculaIn(BaseModel):
    id_grupo: int
    id_estudiante: int
    activo: bool = True


class ManualIn(BaseModel):
    id_estudiante: int
    id_horario: int
    fecha: date
    estado: Literal["cumple", "tardio", "demorado", "ausente", "justificado"]
    observaciones: Opc(255) = None


class CierreIn(BaseModel):
    id_horario: int
    fecha: date


# ---------------------- PAGINA Y SESION ----------------------

@app.get("/")
def index():
    return FileResponse(FRONT)


@app.post("/api/login")
def login(d: LoginIn, request: Request, response: Response):
    k = (request.client.host if request.client else "?", d.usuario.lower())
    n, t = FALLOS.get(k, (0, 0))
    if n >= 5 and time.time() - t < 300:
        raise HTTPException(429, "Demasiados intentos. Espera 5 minutos.")
    with cur() as c:
        c.execute("SELECT id_usuario, usuario, clave_hash, nombre_completo, rol FROM usuarios "
                  "WHERE usuario = %s AND activo = 1", (d.usuario,))
        u = c.fetchone()
    ok = False
    if u:
        try:
            ok = bcrypt.checkpw(d.clave.encode()[:72], u["clave_hash"].encode())
        except ValueError:
            ok = False
    if not ok:
        FALLOS[k] = (n + 1, time.time())
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    FALLOS.pop(k, None)
    token = jwt.encode(
        {"uid": u["id_usuario"], "sub": u["usuario"], "rol": u["rol"],
         "exp": datetime.now(timezone.utc) + timedelta(hours=8)},
        SECRETO, algorithm="HS256",
    )
    response.set_cookie("sid", token, httponly=True, samesite="lax", max_age=8 * 3600,
                        secure=config.COOKIE_SECURE)
    return {"usuario": u["usuario"], "nombre": u["nombre_completo"], "rol": u["rol"]}


@app.get("/api/yo")
def yo(u=Depends(usuario_actual)):
    return u


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("sid")
    return {"ok": True}


# ---------------------- USUARIOS (admin) ----------------------

@app.get("/api/usuarios")
def listar_usuarios(_=Depends(solo_admin)):
    with cur() as c:
        c.execute("""SELECT u.id_usuario AS id, u.usuario, u.nombre_completo, u.correo, u.rol, u.activo,
                            (SELECT COUNT(*) FROM grupos g WHERE g.id_usuario_docente = u.id_usuario) AS grupos
                       FROM usuarios u ORDER BY u.rol, u.nombre_completo""")
        return c.fetchall()


@app.post("/api/usuarios")
def crear_usuario(d: UsuarioIn, _=Depends(solo_admin)):
    if len(d.clave.encode()) > 72:
        raise HTTPException(422, "La contraseña es demasiado larga.")
    h = bcrypt.hashpw(d.clave.encode(), bcrypt.gensalt()).decode()
    with cur() as c:
        c.execute("INSERT INTO usuarios (usuario, clave_hash, nombre_completo, correo, rol) VALUES (%s,%s,%s,%s,%s)",
                  (d.usuario, h, d.nombre_completo, d.correo, d.rol))
        return {"ok": True, "id": c.lastrowid}


@app.put("/api/usuarios/{id_usuario}")
def editar_usuario(id_usuario: int, d: UsuarioUpd, u=Depends(solo_admin)):
    if id_usuario == u["uid"] and (d.rol != "admin" or not d.activo):
        raise HTTPException(400, "No puedes quitarte el rol de administrador ni desactivar tu propia cuenta.")
    if d.clave is not None and len(d.clave) < 8:
        raise HTTPException(422, "La contraseña debe tener al menos 8 caracteres.")
    with cur() as c:
        c.execute("UPDATE usuarios SET nombre_completo=%s, correo=%s, rol=%s, activo=%s WHERE id_usuario=%s",
                  (d.nombre_completo, d.correo, d.rol, int(d.activo), id_usuario))
        if d.clave:
            h = bcrypt.hashpw(d.clave.encode()[:72], bcrypt.gensalt()).decode()
            c.execute("UPDATE usuarios SET clave_hash=%s WHERE id_usuario=%s", (h, id_usuario))
    return {"ok": True}


# ---------------------- ESTUDIANTES (admin) ----------------------

@app.get("/api/estudiantes")
def listar_estudiantes(_=Depends(solo_admin)):
    with cur() as c:
        c.execute("""SELECT e.id_estudiante AS id, e.codigo, e.tipo_documento, e.numero_documento, e.nombres,
                            e.apellidos, e.correo, e.telefono, e.fecha_nacimiento, e.fecha_ingreso, e.estado,
                            e.id_programa, p.nombre AS programa, e.semestre,
                            (SELECT COUNT(*) FROM matriculas m WHERE m.id_estudiante = e.id_estudiante
                               AND m.estado = 'activo') AS grupos
                       FROM estudiantes e JOIN programas p ON p.id_programa = e.id_programa
                      ORDER BY e.apellidos, e.nombres""")
        filas = c.fetchall()
    for f in filas:
        f["foto"] = foto_url(f["codigo"])
        f["nfotos"] = len(listar_fotos(f["codigo"]))
    return filas


def _datos_est(d):
    return (d.tipo_documento, d.numero_documento, d.nombres, d.apellidos, d.correo, d.telefono,
            d.fecha_nacimiento, d.id_programa, d.semestre, d.estado)


@app.post("/api/estudiantes")
def crear_estudiante(d: EstudianteIn, _=Depends(solo_admin)):
    buenas, rechazadas = validar_fotos(d.fotos, d.codigo)
    if not buenas:
        raise HTTPException(422, "Ninguna foto sirve: " + "; ".join(rechazadas) +
                            ". Usa fotos del rostro de frente, con buena luz.")
    with cur() as c:
        c.execute("""INSERT INTO estudiantes (tipo_documento, numero_documento, nombres, apellidos, correo,
                                              telefono, fecha_nacimiento, id_programa, semestre, estado, codigo)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", _datos_est(d) + (d.codigo,))
    shutil.rmtree(os.path.join(ROSTROS, d.codigo), ignore_errors=True)   # restos de un estudiante borrado
    agregar_fotos(d.codigo, buenas)
    ULTIMO.clear()
    reentrenar_async()
    return {"ok": True, "fotos": len(buenas), "mensaje": resumen_fotos(buenas, rechazadas)}


def _codigo_est(id_estudiante):
    with cur() as c:
        c.execute("SELECT codigo FROM estudiantes WHERE id_estudiante=%s", (id_estudiante,))
        e = c.fetchone()
    if not e:
        raise HTTPException(404, "No existe ese estudiante")
    return e["codigo"]


@app.put("/api/estudiantes/{id_estudiante}")
def editar_estudiante(id_estudiante: int, d: EstudianteUpd, _=Depends(solo_admin)):
    codigo = _codigo_est(id_estudiante)
    buenas, rechazadas = [], []
    if d.fotos:
        buenas, rechazadas = validar_fotos(d.fotos, codigo)
        if not buenas:
            raise HTTPException(422, "Ninguna foto nueva sirve: " + "; ".join(rechazadas) +
                                ". No se guardó ningún cambio.")
        if len(listar_fotos(codigo)) + len(buenas) > MAX_FOTOS:
            raise HTTPException(409, f"Máximo {MAX_FOTOS} fotos por estudiante. Quita algunas antes de agregar más.")
    with cur() as c:
        c.execute("""UPDATE estudiantes SET tipo_documento=%s, numero_documento=%s, nombres=%s, apellidos=%s,
                            correo=%s, telefono=%s, fecha_nacimiento=%s, id_programa=%s, semestre=%s, estado=%s
                      WHERE id_estudiante=%s""", _datos_est(d) + (id_estudiante,))
    if buenas:
        agregar_fotos(codigo, buenas)
        reentrenar_async()
    ULTIMO.clear()
    return {"ok": True, "fotos": len(buenas), "mensaje": resumen_fotos(buenas, rechazadas) if d.fotos else ""}


@app.get("/api/estudiantes/{id_estudiante}/fotos")
def fotos_estudiante(id_estudiante: int, _=Depends(solo_admin)):
    codigo = _codigo_est(id_estudiante)
    return {"max": MAX_FOTOS,
            "fotos": [{"archivo": f, "url": f"/fotos/{codigo}/{f}"} for f in listar_fotos(codigo)]}


@app.post("/api/estudiantes/{id_estudiante}/fotos")
def agregar_fotos_estudiante(id_estudiante: int, d: FotosIn, _=Depends(solo_admin)):
    codigo = _codigo_est(id_estudiante)
    buenas, rechazadas = validar_fotos(d.fotos, codigo)
    if not buenas:
        raise HTTPException(422, "Ninguna foto sirve: " + "; ".join(rechazadas) + ".")
    if len(listar_fotos(codigo)) + len(buenas) > MAX_FOTOS:
        raise HTTPException(409, f"Máximo {MAX_FOTOS} fotos por estudiante. Quita algunas antes de agregar más.")
    agregar_fotos(codigo, buenas)
    ULTIMO.clear()
    reentrenar_async()
    return {"ok": True, "fotos": len(buenas), "mensaje": resumen_fotos(buenas, rechazadas)}


@app.delete("/api/estudiantes/{id_estudiante}/fotos/{archivo}")
def borrar_foto_estudiante(id_estudiante: int, archivo: str, _=Depends(solo_admin)):
    codigo = _codigo_est(id_estudiante)
    actuales = listar_fotos(codigo)
    if archivo != os.path.basename(archivo) or archivo not in actuales:
        raise HTTPException(404, "Esa foto no existe")
    if len(actuales) <= 1:
        raise HTTPException(409, "Debe quedar al menos una foto; agrega otra antes de quitar esta.")
    os.remove(os.path.join(ROSTROS, codigo, archivo))
    ULTIMO.clear()
    reentrenar_async()
    return {"ok": True}


@app.delete("/api/estudiantes/{id_estudiante}")
def borrar_estudiante(id_estudiante: int, _=Depends(solo_admin)):
    with cur() as c:
        c.execute("SELECT codigo FROM estudiantes WHERE id_estudiante=%s", (id_estudiante,))
        e = c.fetchone()
        if not e:
            raise HTTPException(404, "No existe ese estudiante")
        c.execute("DELETE FROM estudiantes WHERE id_estudiante = %s", (id_estudiante,))
    if CODIGO_RE.match(e["codigo"]):
        shutil.rmtree(os.path.join(ROSTROS, e["codigo"]), ignore_errors=True)
    ULTIMO.clear()
    reentrenar_async()
    return {"ok": True}


@app.get("/fotos/{codigo}/{archivo}")
def foto(codigo: str, archivo: str, _=Depends(solo_admin)):
    ruta = os.path.join(ROSTROS, codigo, archivo)
    if not CODIGO_RE.match(codigo) or archivo != os.path.basename(archivo) or not os.path.isfile(ruta):
        raise HTTPException(404, "No encontrada")
    return FileResponse(ruta)


# ---------------------- MATRICULAS (admin) ----------------------

@app.get("/api/matriculas")
def listar_matriculas(id_grupo: int, _=Depends(solo_admin)):
    with cur() as c:
        c.execute("""SELECT e.id_estudiante AS id, e.codigo, CONCAT(e.nombres, ' ', e.apellidos) AS nombre,
                            p.nombre AS programa, e.semestre, m.estado AS matricula
                       FROM estudiantes e
                       JOIN programas p ON p.id_programa = e.id_programa
                       LEFT JOIN matriculas m ON m.id_estudiante = e.id_estudiante AND m.id_grupo = %s
                      WHERE e.estado = 'activo' OR m.estado = 'activo'
                      ORDER BY (m.estado = 'activo') DESC, e.apellidos, e.nombres""", (id_grupo,))
        return c.fetchall()


@app.post("/api/matriculas")
def matricular(d: MatriculaIn, _=Depends(solo_admin)):
    estado = "activo" if d.activo else "inactivo"
    with cur() as c:
        c.execute("SELECT id_matricula FROM matriculas WHERE id_grupo=%s AND id_estudiante=%s",
                  (d.id_grupo, d.id_estudiante))
        m = c.fetchone()
        if m:
            c.execute("UPDATE matriculas SET estado=%s WHERE id_matricula=%s", (estado, m["id_matricula"]))
        elif d.activo:
            c.execute("INSERT INTO matriculas (id_grupo, id_estudiante) VALUES (%s,%s)", (d.id_grupo, d.id_estudiante))
    ULTIMO.clear()
    return {"ok": True}


# ---------------------- CATALOGOS, AULAS, GRUPOS, HORARIOS (admin) ----------------------
# CRUD generico: nombre de ruta -> (tabla, llave, modelo, consulta de listado)

ENTIDADES = {
    "bloques": ("bloques", "id_bloque", BloqueIn,
                "SELECT id_bloque AS id, nombre, estado FROM bloques ORDER BY nombre"),
    "tipos_aula": ("tipos_aula", "id_tipo_aula", TipoAulaIn,
                   "SELECT id_tipo_aula AS id, nombre, estado FROM tipos_aula ORDER BY nombre"),
    "jornadas": ("jornadas", "id_jornada", JornadaIn,
                 "SELECT id_jornada AS id, nombre, estado FROM jornadas ORDER BY id_jornada"),
    "programas": ("programas", "id_programa", ProgramaIn,
                  "SELECT id_programa AS id, codigo, nombre, estado FROM programas ORDER BY nombre"),
    "periodos": ("periodos", "id_periodo", PeriodoIn,
                 "SELECT id_periodo AS id, nombre, fecha_inicio, fecha_fin, estado FROM periodos "
                 "ORDER BY fecha_inicio DESC"),
    "aulas": ("aulas", "id_aula", AulaIn,
              """SELECT a.id_aula AS id, a.nombre, a.piso, a.capacidad, a.id_bloque, a.id_tipo_aula, a.estado,
                        b.nombre AS bloque, t.nombre AS tipo
                   FROM aulas a JOIN bloques b ON b.id_bloque = a.id_bloque
                   JOIN tipos_aula t ON t.id_tipo_aula = a.id_tipo_aula ORDER BY b.nombre, a.nombre"""),
    "dispositivos": ("dispositivos_acceso", "id_dispositivo", DispositivoIn,
                     """SELECT d.id_dispositivo AS id, d.id_aula, d.nombre, d.tipo, d.identificador, d.activo,
                               CONCAT(b.nombre, ' · ', a.nombre) AS aula, d.token_hash IS NOT NULL AS tiene_token
                          FROM dispositivos_acceso d JOIN aulas a ON a.id_aula = d.id_aula
                          JOIN bloques b ON b.id_bloque = a.id_bloque ORDER BY aula, d.nombre"""),
    "asignaturas": ("asignaturas", "id_asignatura", AsignaturaIn,
                    """SELECT s.id_asignatura AS id, s.codigo, s.nombre, s.creditos, s.semestre, s.id_programa,
                              s.estado, p.nombre AS programa
                         FROM asignaturas s JOIN programas p ON p.id_programa = s.id_programa
                        ORDER BY p.nombre, s.semestre, s.nombre"""),
    "grupos": ("grupos", "id_grupo", GrupoIn,
               """SELECT g.id_grupo AS id, g.id_asignatura, g.id_periodo, g.id_jornada, g.id_usuario_docente,
                         g.codigo_grupo, g.cupo, g.estado, s.codigo AS cod_asignatura, s.nombre AS asignatura,
                         p.nombre AS periodo, j.nombre AS jornada, u.nombre_completo AS docente,
                         (SELECT COUNT(*) FROM matriculas m WHERE m.id_grupo = g.id_grupo
                            AND m.estado = 'activo') AS matriculados
                    FROM grupos g
                    JOIN asignaturas s ON s.id_asignatura = g.id_asignatura
                    JOIN periodos p ON p.id_periodo = g.id_periodo
                    JOIN jornadas j ON j.id_jornada = g.id_jornada
                    LEFT JOIN usuarios u ON u.id_usuario = g.id_usuario_docente
                   ORDER BY p.fecha_inicio DESC, s.nombre, g.codigo_grupo"""),
    "horarios": ("horarios", "id_horario", HorarioIn,
                 """SELECT id_horario AS id, id_grupo, id_aula, dia_semana, dia,
                           LEFT(CAST(hora_inicio AS CHAR), 5) AS hora_inicio, LEFT(CAST(hora_fin AS CHAR), 5) AS hora_fin,
                           CONCAT(bloque, ' · ', aula) AS aula
                      FROM v_horarios ORDER BY dia_semana, hora_inicio"""),
}


def _entidad(nombre):
    if nombre not in ENTIDADES:
        raise HTTPException(404, "No existe")
    return ENTIDADES[nombre]


def _valores(d):
    return {k: (int(v) if isinstance(v, bool) else v) for k, v in d.model_dump().items()}


@app.get("/api/cat/{nombre}")
def listar(nombre: str, _=Depends(solo_admin)):
    sql = _entidad(nombre)[3]
    with cur() as c:
        c.execute(sql)
        return c.fetchall()


@app.post("/api/cat/{nombre}")
def crear(nombre: str, cuerpo: dict, _=Depends(solo_admin)):
    tabla, _pk, modelo, _sql = _entidad(nombre)
    v = _valores(modelo.model_validate(cuerpo))
    cols = ", ".join(f"`{k}`" for k in v)
    with cur() as c:
        c.execute(f"INSERT INTO `{tabla}` ({cols}) VALUES ({', '.join(['%s'] * len(v))})", tuple(v.values()))
        nuevo = c.lastrowid
    ULTIMO.clear()
    return {"ok": True, "id": nuevo}


@app.put("/api/cat/{nombre}/{id_reg}")
def editar(nombre: str, id_reg: int, cuerpo: dict, _=Depends(solo_admin)):
    tabla, pk, modelo, _sql = _entidad(nombre)
    v = _valores(modelo.model_validate(cuerpo))
    sets = ", ".join(f"`{k}` = %s" for k in v)
    with cur() as c:
        c.execute(f"UPDATE `{tabla}` SET {sets} WHERE `{pk}` = %s", tuple(v.values()) + (id_reg,))
    ULTIMO.clear()
    return {"ok": True}


@app.delete("/api/cat/{nombre}/{id_reg}")
def borrar(nombre: str, id_reg: int, _=Depends(solo_admin)):
    tabla, pk, _m, _sql = _entidad(nombre)
    with cur() as c:
        c.execute(f"DELETE FROM `{tabla}` WHERE `{pk}` = %s", (id_reg,))
        if c.rowcount == 0:
            raise HTTPException(404, "No existe ese registro")
    ULTIMO.clear()
    return {"ok": True}


# ---------------------- CONSULTAS: ADMIN Y DOCENTE ----------------------

@app.get("/api/mis-grupos")
def mis_grupos(u=Depends(usuario_actual)):
    """Grupos con sus horarios: el admin ve todos, el docente solo los que dicta."""
    doc = filtro_docente(u)
    with cur() as c:
        c.execute("""SELECT g.id_grupo AS id, s.codigo AS cod_asignatura, s.nombre AS asignatura, g.codigo_grupo,
                            p.nombre AS periodo, p.estado AS estado_periodo, j.nombre AS jornada, g.estado,
                            u.nombre_completo AS docente,
                            (SELECT COUNT(*) FROM matriculas m WHERE m.id_grupo = g.id_grupo
                               AND m.estado = 'activo') AS matriculados,
                            (SELECT GROUP_CONCAT(CONCAT(v.dia, ' ', LEFT(CAST(v.hora_inicio AS CHAR), 5), '-',
                                                        LEFT(CAST(v.hora_fin AS CHAR), 5), ' · ', v.aula)
                                                 ORDER BY v.dia_semana, v.hora_inicio SEPARATOR ' | ')
                               FROM v_horarios v WHERE v.id_grupo = g.id_grupo) AS horarios
                       FROM grupos g
                       JOIN asignaturas s ON s.id_asignatura = g.id_asignatura
                       JOIN periodos p ON p.id_periodo = g.id_periodo
                       JOIN jornadas j ON j.id_jornada = g.id_jornada
                       LEFT JOIN usuarios u ON u.id_usuario = g.id_usuario_docente
                      WHERE (%s IS NULL OR g.id_usuario_docente = %s)
                      ORDER BY p.fecha_inicio DESC, s.nombre, g.codigo_grupo""", (doc, doc))
        return c.fetchall()


def lista_clase(c, id_horario, id_grupo, fecha):
    """Matriculados de un grupo con su asistencia en una fecha concreta."""
    c.execute("""SELECT e.id_estudiante, e.codigo, CONCAT(e.nombres, ' ', e.apellidos) AS nombre,
                        IFNULL(a.estado, 'sin_registro') AS estado,
                        CAST(a.hora_ingreso AS CHAR) AS hora_ingreso,
                        CAST(a.hora_salida AS CHAR) AS hora_salida,
                        a.metodo, a.observaciones
                   FROM matriculas m
                   JOIN estudiantes e ON e.id_estudiante = m.id_estudiante
                   LEFT JOIN asistencias a ON a.id_estudiante = e.id_estudiante
                                          AND a.id_horario = %s AND a.fecha = %s
                  WHERE m.id_grupo = %s AND (m.estado = 'activo' OR a.id_asistencia IS NOT NULL)
                  ORDER BY e.apellidos, e.nombres""", (id_horario, fecha, id_grupo))
    return c.fetchall()


def resumen(est):
    r = {k: 0 for k in ("cumple", "tardio", "demorado", "ausente", "justificado", "sin_registro")}
    for e in est:
        r[e["estado"]] = r.get(e["estado"], 0) + 1
    r["total"] = len(est)
    asistio = r["cumple"] + r["tardio"] + r["demorado"] + r["justificado"]
    r["porcentaje"] = round(100 * asistio / len(est), 1) if est else None
    return r


CLASE_SQL = """SELECT v.id_horario, v.id_grupo, v.cod_asignatura, v.asignatura, v.codigo_grupo, v.docente,
                      v.periodo, v.jornada, v.dia, CONCAT(v.bloque, ' · ', v.aula) AS aula,
                      LEFT(CAST(v.hora_inicio AS CHAR), 5) AS hora_inicio,
                      LEFT(CAST(v.hora_fin AS CHAR), 5) AS hora_fin
                 FROM v_horarios v
                 JOIN grupos g ON g.id_grupo = v.id_grupo
                 JOIN periodos p ON p.id_periodo = g.id_periodo
                WHERE g.estado <> 'cancelado' AND p.estado <> 'planeado'"""


@app.get("/api/reporte")
def reporte(desde: date, hasta: date, id_grupo: int | None = None, u=Depends(usuario_actual)):
    """Clases dictadas entre dos fechas con la asistencia de cada estudiante.
    El docente solo recibe las clases de sus grupos (lo filtra el servidor)."""
    if hasta < desde:
        raise HTTPException(422, "La fecha final es anterior a la inicial.")
    if (hasta - desde).days > 62:
        raise HTTPException(422, "El rango máximo es de 62 días.")
    doc = filtro_docente(u)
    clases = []
    with cur() as c:
        d = desde
        while d <= hasta:
            c.execute(CLASE_SQL + """ AND v.dia_semana = %s AND %s BETWEEN p.fecha_inicio AND p.fecha_fin
                                      AND (%s IS NULL OR g.id_usuario_docente = %s)
                                      AND (%s IS NULL OR g.id_grupo = %s)
                                    ORDER BY v.hora_inicio""",
                      (d.isoweekday(), d, doc, doc, id_grupo, id_grupo))
            for cl in c.fetchall():
                est = lista_clase(c, cl["id_horario"], cl["id_grupo"], d)
                cl.update(fecha=d.isoformat(), estudiantes=est, resumen=resumen(est))
                clases.append(cl)
            d += timedelta(days=1)
    total = resumen([e for cl in clases for e in cl["estudiantes"]])
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "clases": clases, "total": total}


def estado_camara(ident):
    cam = CAMARAS.get(ident)
    if not cam:
        return {"conectada": False, "activa": False, "info": {}, "fps": 0, "ultima_vez": None, "forzada_min": 0}
    ult = datetime.fromtimestamp(cam.ultima_vez).strftime("%Y-%m-%d %H:%M") if cam.ultima_vez else None
    return {"conectada": cam.conectada, "activa": cam.activa, "info": cam.info,
            "fps": cam.fps if cam.activa else 0, "ultima_vez": ult,
            "forzada_min": max(0, round((cam.forzar_hasta - time.time()) / 60))}


def clase_en_curso(c, id_aula):
    """Clase del aula en su ventana de ingreso (desde X min antes hasta el fin), o None."""
    hoy = date.today()
    c.execute(CLASE_SQL + """ AND v.id_aula = %s AND v.dia_semana = %s AND p.estado = 'activo'
                              AND %s BETWEEN p.fecha_inicio AND p.fecha_fin
                              AND CURTIME() BETWEEN SUBTIME(v.hora_inicio, SEC_TO_TIME(60 * IFNULL(
                                  (SELECT valor FROM parametros WHERE clave = 'minutos_antes_ingreso'), 15)))
                                  AND v.hora_fin
                            ORDER BY v.hora_inicio LIMIT 1""",
              (id_aula, hoy.isoweekday(), hoy))
    cl = c.fetchone()
    if cl:
        c.execute("SELECT id_usuario_docente AS d FROM grupos WHERE id_grupo = %s", (cl["id_grupo"],))
        cl["_docente"] = c.fetchone()["d"]
    return cl


def camaras_registradas(c):
    c.execute("""SELECT d.identificador, d.nombre, d.id_aula, CONCAT(b.nombre, ' · ', a.nombre) AS aula,
                        d.token_hash IS NOT NULL AS tiene_token
                   FROM dispositivos_acceso d JOIN aulas a ON a.id_aula = d.id_aula
                   JOIN bloques b ON b.id_bloque = a.id_bloque
                  WHERE d.activo = 1 AND d.tipo = 'camara_facial'
                  ORDER BY d.nombre""")
    return c.fetchall()


def puede_ver_video(u, ident):
    """Admin siempre; docente solo si la clase en curso en el aula de la camara es suya."""
    if u["rol"] == "admin":
        return True
    with cur() as c:
        cam = next((x for x in camaras_registradas(c) if x["identificador"] == ident), None)
        cl = clase_en_curso(c, cam["id_aula"]) if cam else None
    return bool(cl and cl["_docente"] == u["uid"])


@app.get("/api/en-vivo")
def en_vivo(dispositivo: str | None = None, u=Depends(usuario_actual)):
    """Estado de una camara, la clase vigente en su aula y la asistencia de hoy."""
    out = {"camaras": [], "dispositivo": None, "aula": None, "estado": None, "video": False,
           "clase": None, "nota": None, "aviso": None, "min_antes": 15}
    with cur() as c:
        c.execute("SELECT valor FROM parametros WHERE clave = 'minutos_antes_ingreso'")
        out["min_antes"] = (c.fetchone() or {}).get("valor", 15)
        cams = camaras_registradas(c)
        out["camaras"] = [{"identificador": x["identificador"], "nombre": x["nombre"], "aula": x["aula"]} for x in cams]
        if not cams:
            out["nota"] = ("No hay cámaras registradas. Créala en Infraestructura › Dispositivos "
                           "(tipo Cámara facial) y genera su token.")
            return out
        disp = next((x for x in cams if x["identificador"] == dispositivo), cams[0])
        out.update(dispositivo=disp["identificador"], aula=disp["aula"], estado=estado_camara(disp["identificador"]))
        if not disp["tiene_token"]:
            out["aviso"] = ("Esta cámara aún no tiene token: genéralo en Infraestructura › Dispositivos "
                            "y escríbelo en el portal de configuración de la cámara.")
        out["video"] = u["rol"] == "admin"
        cl = clase_en_curso(c, disp["id_aula"])
        if not cl:
            out["nota"] = (f"No hay clase en curso en esta aula. La cámara transmite desde "
                           f"{out['min_antes']} min antes de cada clase hasta su fin.")
            return out
        dueno = cl.pop("_docente")
        if u["rol"] != "admin" and dueno != u["uid"]:
            out["nota"] = "La clase en curso en esta aula es de otro docente."
            return out
        out["video"] = True
        hoy = date.today()
        est = lista_clase(c, cl["id_horario"], cl["id_grupo"], hoy)
        cl.update(fecha=hoy.isoformat(), estudiantes=est, resumen=resumen(est))
        out["clase"] = cl
    return out


@app.get("/api/video/{ident}")
async def video(ident: str, request: Request, u=Depends(usuario_actual)):
    """Video en vivo (MJPEG) retransmitido desde la camara. Solo mientras la camara transmite."""
    if not IDENT_RE.match(ident) or not await asyncio.to_thread(puede_ver_video, u, ident):
        raise HTTPException(403, "No tienes permiso para ver esta cámara ahora.")

    async def flujo():
        ultimo = 0.0
        while not await request.is_disconnected():
            cam = CAMARAS.get(ident)
            f = cam.frame if cam and cam.activa else None
            if f and cam.frame_ts > ultimo:
                ultimo = cam.frame_ts
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(f)).encode() + b"\r\n\r\n" + f + b"\r\n")
            await asyncio.sleep(0.08)

    return StreamingResponse(flujo(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class ActivarIn(BaseModel):
    minutos: int = Field(ge=0, le=120)


@app.post("/api/camaras/{ident}/activar")
async def activar_camara(ident: str, d: ActivarIn, _=Depends(solo_admin)):
    """Enciende la transmision a mano (pruebas, ajustar el encuadre) aunque no haya clase."""
    cam = CAMARAS.get(ident)
    if not cam or not cam.conectada:
        raise HTTPException(409, "La cámara no está conectada al servidor.")
    cam.forzar_hasta = time.time() + d.minutos * 60
    await revisar_modo(cam)
    return {"ok": True, "activa": cam.activa}


@app.post("/api/dispositivos/{id_dispositivo}/token")
async def generar_token(id_dispositivo: int, _=Depends(solo_admin)):
    """Genera un token nuevo para la camara. Se muestra UNA vez; en la BD solo queda su SHA-256."""
    token = secrets.token_urlsafe(32)

    def guardar():
        with cur() as c:
            c.execute("SELECT identificador FROM dispositivos_acceso WHERE id_dispositivo = %s", (id_dispositivo,))
            d = c.fetchone()
            if not d:
                raise HTTPException(404, "No existe ese dispositivo")
            c.execute("UPDATE dispositivos_acceso SET token_hash = %s WHERE id_dispositivo = %s",
                      (hash_token(token), id_dispositivo))
            return d["identificador"]

    ident = await asyncio.to_thread(guardar)
    cam = CAMARAS.get(ident)
    if cam:  # la conexion con el token anterior deja de ser valida
        await cam.cerrar(4401)
    return {"identificador": ident, "token": token}


@app.websocket("/ws/dispositivo")
async def ws_dispositivo(ws: WebSocket):
    """Conexion persistente de una ESP32-CAM (header Authorization: Bearer <token> y X-Dispositivo)."""
    ident, token = credenciales_dispositivo(ws.headers)
    try:
        disp = await asyncio.to_thread(autenticar_dispositivo, ident, token)
    except Exception:
        disp = None
    if not disp:
        print(f"Conexión de cámara rechazada ('{ident[:64]}') desde {ws.client.host if ws.client else '?'}")
        await ws.close(code=4401)
        return
    ofrecidos = ws.scope.get("subprotocols") or []
    await ws.accept(subprotocol=ofrecidos[0] if ofrecidos else None)
    cam = CAMARAS.get(ident)
    if cam is None:
        cam = CAMARAS[ident] = Camara(ident)
    await cam.cerrar(4000)  # una sola conexion por camara: la nueva reemplaza a la anterior
    await cam.sesion(ws, disp)


@app.get("/api/dispositivo/firmware")
def descargar_firmware(request: Request):
    """La ESP32-CAM descarga aqui su actualizacion OTA (con su token)."""
    if not autenticar_dispositivo(*credenciales_dispositivo(request.headers)):
        raise HTTPException(401, "Dispositivo no autorizado")
    fw = firmware_disponible()
    if not fw:
        raise HTTPException(404, "No hay firmware publicado")
    return FileResponse(fw[1], media_type="application/octet-stream")


@app.get("/api/salud")
def salud():
    try:
        with cur() as c:
            c.execute("SELECT 1 AS ok")
            c.fetchall()
    except HTTPException:
        return JSONResponse({"ok": False, "bd": False}, status_code=503)
    return {"ok": True, "bd": True, "camaras": sum(1 for x in CAMARAS.values() if x.conectada)}


@app.post("/api/asistencias/manual")
def asistencia_manual(d: ManualIn, u=Depends(solo_admin)):
    with cur() as c:
        c.execute("CALL marcar_asistencia_manual(%s, %s, %s, %s, %s, %s)",
                  (d.id_estudiante, d.id_horario, d.fecha, d.estado, u["uid"], d.observaciones))
    return {"ok": True}


@app.post("/api/clases/cerrar")
def cerrar(d: CierreIn, _=Depends(solo_admin)):
    with cur() as c:
        c.execute("CALL cerrar_clase(%s, %s)", (d.id_horario, d.fecha))
    return {"ok": True}


@app.get("/api/estado")
def estado(_=Depends(usuario_actual)):
    return {"camaras": {k: estado_camara(k) for k in CAMARAS}}


@app.websocket("/ws")
async def ws_front(ws: WebSocket):
    t = _token(ws)
    if not t or "uid" not in t:
        await ws.close(code=4401)
        return
    await ws.accept()
    CLIENTES[ws] = (t.get("rol"), t["uid"])
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        CLIENTES.pop(ws, None)


if __name__ == "__main__":
    print(f"Servidor en http://{config.HOST}:{config.PORT} (Ctrl+C para detener)")
    # ws_ping_timeout amplio: la camara puede tardar varios segundos en responder
    # si su Wi-Fi tiene un bache o esta ocupada enviando una foto.
    uvicorn.run(app, host=config.HOST, port=config.PORT,
                proxy_headers=config.TRUST_PROXY, forwarded_allow_ips="*" if config.TRUST_PROXY else None,
                ws_ping_interval=20, ws_ping_timeout=60)
