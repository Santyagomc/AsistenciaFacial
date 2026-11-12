"""
Herramientas de administracion por consola (en la VPS se ejecutan dentro del
contenedor; el script desplegar.ps1 las usa por SSH):

  python herramientas.py token <identificador> [--crear]
      Genera un token nuevo para la camara y lo imprime en la ultima linea.
      El token anterior deja de servir. Con --crear, si el dispositivo no
      existe se crea en la primera aula activa (luego se cambia en el panel).

  python herramientas.py clave <usuario>
      Genera una contrasena nueva para el usuario y la imprime.

Ejemplo en la VPS:
  cd /opt/asistencia && docker compose exec app python herramientas.py token ESP32-CAM-01
"""
import hashlib
import re
import secrets
import sys

import bcrypt
import mysql.connector

import config

IDENT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def conectar():
    return mysql.connector.connect(**config.DB_CONFIG, autocommit=True)


def info(msg):
    print(msg, file=sys.stderr)


def token(ident, crear):
    if not IDENT_RE.match(ident):
        sys.exit("Identificador invalido: use letras, numeros y . _ : - (maximo 64)")
    con = conectar()
    c = con.cursor()
    c.execute("SELECT id_dispositivo FROM dispositivos_acceso WHERE identificador = %s", (ident,))
    fila = c.fetchone()
    if not fila:
        if not crear:
            sys.exit(f"No existe el dispositivo '{ident}'. Crealo en el panel o usa --crear.")
        c.execute("SELECT a.id_aula, CONCAT(b.nombre, ' - ', a.nombre) FROM aulas a "
                  "JOIN bloques b ON b.id_bloque = a.id_bloque WHERE a.estado = 'activa' ORDER BY a.id_aula LIMIT 1")
        aula = c.fetchone()
        if not aula:
            sys.exit("No hay aulas activas. Crea una en el panel (Infraestructura > Aulas) primero.")
        c.execute("INSERT INTO dispositivos_acceso (id_aula, nombre, tipo, identificador) "
                  "VALUES (%s, %s, 'camara_facial', %s)", (aula[0], f"Camara {ident}"[:50], ident))
        info(f"Dispositivo '{ident}' creado en el aula '{aula[1]}'. Cambia el aula en el panel si no es esa.")
    t = secrets.token_urlsafe(32)
    c.execute("UPDATE dispositivos_acceso SET token_hash = %s, activo = 1 WHERE identificador = %s",
              (hashlib.sha256(t.encode()).hexdigest(), ident))
    con.close()
    info(f"Token nuevo para '{ident}' (el anterior ya no sirve):")
    print(t)


def clave(usuario):
    con = conectar()
    c = con.cursor()
    nueva = secrets.token_urlsafe(12)
    c.execute("UPDATE usuarios SET clave_hash = %s, activo = 1 WHERE usuario = %s",
              (bcrypt.hashpw(nueva.encode(), bcrypt.gensalt()).decode(), usuario))
    if c.rowcount == 0:
        sys.exit(f"No existe el usuario '{usuario}'.")
    con.close()
    info(f"Contrasena nueva para '{usuario}':")
    print(nueva)


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "token":
        token(a[1], "--crear" in a)
    elif len(a) == 2 and a[0] == "clave":
        clave(a[1])
    else:
        sys.exit(__doc__)
