"""
Prepara la base de datos v2. Se puede repetir sin perder datos.

  1. Crea la base indicada en config.DB_CONFIG["database"] y ejecuta
     db/universidad.sql (tablas, validaciones, procedimientos, vistas).
  2. Si existe la base anterior (config.DB_ANTERIOR), ofrece copiar
     estudiantes, aulas, asignaturas y usuarios.
  3. Crea o actualiza un usuario administrador.
  4. Opcional: carga datos de ejemplo para probar la camara hoy mismo.

Uso:  python preparar_bd.py          (interactivo, en el PC)
      python preparar_bd.py --auto   (sin preguntas: lo ejecuta el contenedor
                                      de la VPS en cada arranque. Aplica el
                                      esquema y crea el administrador desde
                                      ADMIN_USUARIO / ADMIN_CLAVE solo si no
                                      hay ninguno)
"""
import getpass
import os
import re
import sys
import time
from datetime import date

import bcrypt
import mysql.connector
from mysql.connector import errors as mye

import config

SQL_BASE = os.path.join(config.BASE, "db", "universidad.sql")
NOMBRE_BD = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def sentencias(texto):
    """Divide el script respetando DELIMITER (necesario para procedimientos y triggers)."""
    delim, buf, out = ";", [], []
    for linea in texto.splitlines():
        s = linea.strip()
        if not buf and (not s or s.startswith("--")):
            continue
        if s.upper().startswith("DELIMITER "):
            delim = s.split()[1]
            continue
        buf.append(linea)
        if s.endswith(delim):
            sql = "\n".join(buf).rstrip()
            out.append(sql[: -len(delim)].strip())
            buf = []
    if buf and "\n".join(buf).strip():
        out.append("\n".join(buf).strip())
    return out


def si_no(pregunta, defecto=False):
    r = input(f"{pregunta} [{'S/n' if defecto else 's/N'}]: ").strip().lower()
    return defecto if not r else r in ("s", "si", "sí", "y", "yes")


def pedir_clave(etiqueta="Contrasena"):
    while True:
        p1 = getpass.getpass(f"{etiqueta} (8 a 72 caracteres): ")
        if len(p1) < 8 or len(p1.encode()) > 72:
            print("Debe tener entre 8 y 72 caracteres.")
            continue
        if p1 != getpass.getpass("Repite la contrasena: "):
            print("No coinciden.")
            continue
        return bcrypt.hashpw(p1.encode(), bcrypt.gensalt()).decode()


def existe_columna(c, bd, tabla, columna):
    c.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
        (bd, tabla, columna),
    )
    return c.fetchone()[0] > 0


def crear_esquema(c, bd):
    c.execute(f"CREATE DATABASE IF NOT EXISTS `{bd}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    c.execute(f"USE `{bd}`")
    c.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
    with open(SQL_BASE, encoding="utf-8") as f:
        for s in sentencias(f.read()):
            try:
                c.execute(s)
            except mye.Error as e:
                if e.errno in (1060, 1061, 1050, 1537):  # columna, indice, tabla o evento ya existen
                    continue
                print("\nError ejecutando db/universidad.sql:", e)
                print("Sentencia:\n", s[:300])
                sys.exit(1)
    print(f"Esquema listo en la base '{bd}'.")


def migrar(c, bd, vieja):
    """Copia datos del esquema anterior. INSERT IGNORE: no duplica si se repite."""
    if not existe_columna(c, vieja, "estudiantes", "codigo"):
        return
    print(f"\nSe encontro la base anterior '{vieja}'.")
    if not si_no("Copiar de alli estudiantes, aulas, asignaturas y usuarios?", True):
        return
    V = f"`{vieja}`"
    c.execute("INSERT IGNORE INTO programas (codigo, nombre) VALUES ('GEN', 'Programa general (ajustar)')")
    c.execute("SELECT id_programa FROM programas WHERE codigo='GEN'")
    prog = c.fetchone()[0]

    if existe_columna(c, vieja, "aulas", "edificio"):
        c.execute(f"INSERT IGNORE INTO bloques (nombre) SELECT DISTINCT edificio FROM {V}.aulas")
        c.execute(
            f"""INSERT IGNORE INTO aulas (nombre, piso, capacidad, id_bloque, id_tipo_aula)
                SELECT a.nombre, a.piso, GREATEST(a.capacidad, 1), b.id_bloque, t.id_tipo_aula
                  FROM {V}.aulas a
                  JOIN bloques b ON b.nombre = a.edificio
                  JOIN tipos_aula t ON t.nombre = CASE a.tipo
                       WHEN 'laboratorio' THEN 'Laboratorio'
                       WHEN 'auditorio' THEN 'Auditorio' ELSE 'Teórica' END"""
        )
        print(f"  Aulas copiadas: {c.rowcount}")

    c.execute(
        f"""INSERT IGNORE INTO estudiantes (codigo, nombres, apellidos, correo, telefono,
                                           fecha_nacimiento, fecha_ingreso, estado, id_programa)
            SELECT e.codigo, e.nombres, e.apellidos, e.correo, e.telefono, e.fecha_nacimiento,
                   IF(e.fecha_ingreso IS NULL OR e.fecha_ingreso < '1900-01-01', CURDATE(), e.fecha_ingreso),
                   IFNULL(e.estado, 'activo'), %s
              FROM {V}.estudiantes e""",
        (prog,),
    )
    print(f"  Estudiantes copiados: {c.rowcount}")

    c.execute(
        f"""INSERT IGNORE INTO asignaturas (codigo, nombre, creditos, semestre, id_programa)
            SELECT codigo, nombre, LEAST(GREATEST(creditos, 1), 10), LEAST(GREATEST(semestre, 1), 12), %s
              FROM {V}.asignaturas""",
        (prog,),
    )
    print(f"  Asignaturas copiadas: {c.rowcount}")

    if existe_columna(c, vieja, "usuarios", "clave_hash"):
        col = "nombre" if existe_columna(c, vieja, "usuarios", "nombre") else "usuario"
        c.execute(
            f"""INSERT IGNORE INTO usuarios (usuario, clave_hash, nombre_completo, rol, activo)
                SELECT usuario, clave_hash, `{col}`, rol, activo FROM {V}.usuarios"""
        )
        print(f"  Usuarios copiados: {c.rowcount}")
    print("  Las matriculas y asistencias anteriores no se copian: ahora dependen de grupos y horarios.")
    print("  Revisa en el panel el programa 'GEN' de los estudiantes copiados.")


def crear_admin(c):
    c.execute("SELECT COUNT(*) FROM usuarios WHERE rol='admin' AND activo=1")
    hay = c.fetchone()[0] > 0
    if hay and not si_no("\nYa hay un administrador. Crear o cambiar la contrasena de uno?"):
        return
    print("\nUsuario administrador (si ya existe, se cambia su contrasena)")
    usuario = input("Usuario [admin]: ").strip() or "admin"
    nombre = input("Nombre completo: ").strip() or usuario
    h = pedir_clave()
    c.execute(
        """INSERT INTO usuarios (usuario, clave_hash, nombre_completo, rol) VALUES (%s, %s, %s, 'admin')
           ON DUPLICATE KEY UPDATE clave_hash=VALUES(clave_hash), nombre_completo=VALUES(nombre_completo),
                                   rol='admin', activo=1""",
        (usuario, h, nombre),
    )


def datos_demo(c):
    print("\nDatos de ejemplo: una clase de prueba TODOS los dias a toda hora (00:00-23:59) en 'Sala 1',")
    print("con la camara asignada a esa aula, un docente y todos los estudiantes matriculados.")
    if not si_no("Cargarlos?"):
        return
    hoy = date.today()
    sem = 1 if hoy.month <= 6 else 2
    ini = date(hoy.year, 1 if sem == 1 else 7, 1)
    fin = date(hoy.year, 6, 30) if sem == 1 else date(hoy.year, 12, 31)
    periodo = f"{hoy.year}-{sem}"

    c.execute("INSERT IGNORE INTO bloques (nombre) VALUES ('Bloque A')")
    c.execute(
        """INSERT IGNORE INTO aulas (nombre, piso, capacidad, id_bloque, id_tipo_aula)
           SELECT 'Sala 1', 1, 30, b.id_bloque, t.id_tipo_aula FROM bloques b, tipos_aula t
            WHERE b.nombre='Bloque A' AND t.nombre='Laboratorio'"""
    )
    c.execute("SELECT a.id_aula FROM aulas a JOIN bloques b ON b.id_bloque=a.id_bloque "
              "WHERE b.nombre='Bloque A' AND a.nombre='Sala 1'")
    aula = c.fetchone()[0]
    c.execute(
        "INSERT IGNORE INTO dispositivos_acceso (id_aula, nombre, tipo, identificador) "
        "VALUES (%s, 'Camara puerta Sala 1', 'camara_facial', %s)",
        (aula, config.DISPOSITIVO_CAMARA),
    )
    c.execute("INSERT IGNORE INTO programas (codigo, nombre) VALUES ('ISIS', 'Ingeniería de Sistemas')")
    c.execute("SELECT id_programa FROM programas WHERE codigo='ISIS'")
    prog = c.fetchone()[0]
    c.execute(
        "INSERT IGNORE INTO periodos (nombre, fecha_inicio, fecha_fin, estado) VALUES (%s, %s, %s, 'activo')",
        (periodo, ini, fin),
    )
    c.execute("SELECT id_periodo FROM periodos WHERE nombre=%s", (periodo,))
    per = c.fetchone()[0]
    c.execute(
        "INSERT IGNORE INTO asignaturas (codigo, nombre, creditos, semestre, id_programa) "
        "VALUES ('DEMO101', 'Clase de prueba', 2, 1, %s)",
        (prog,),
    )
    c.execute("SELECT id_asignatura FROM asignaturas WHERE codigo='DEMO101'")
    asig = c.fetchone()[0]

    c.execute("SELECT id_usuario FROM usuarios WHERE usuario='docente'")
    fila = c.fetchone()
    if not fila:
        print("Se crea el usuario 'docente' para probar el reporte del docente.")
        h = pedir_clave("Contrasena para 'docente'")
        c.execute(
            "INSERT INTO usuarios (usuario, clave_hash, nombre_completo, rol) "
            "VALUES ('docente', %s, 'Docente de prueba', 'docente')",
            (h,),
        )
        doc = c.lastrowid
    else:
        doc = fila[0]

    c.execute("SELECT id_jornada FROM jornadas WHERE nombre='Diurna'")
    jor = c.fetchone()[0]
    c.execute(
        "INSERT IGNORE INTO grupos (id_asignatura, id_periodo, id_jornada, id_usuario_docente, codigo_grupo, cupo) "
        "VALUES (%s, %s, %s, %s, 'DEMO', 30)",
        (asig, per, jor, doc),
    )
    c.execute("SELECT id_grupo FROM grupos WHERE id_asignatura=%s AND id_periodo=%s AND codigo_grupo='DEMO'",
              (asig, per))
    grupo = c.fetchone()[0]
    for dia in range(1, 8):
        try:
            c.execute(
                "INSERT INTO horarios (id_grupo, id_aula, dia_semana, hora_inicio, hora_fin) "
                "SELECT %s, %s, %s, '00:00', '23:59' FROM DUAL WHERE NOT EXISTS "
                "(SELECT 1 FROM horarios WHERE id_grupo=%s AND dia_semana=%s)",
                (grupo, aula, dia, grupo, dia),
            )
        except mye.Error as e:
            print(f"  Horario dia {dia} omitido: {e.msg}")
    c.execute("SELECT id_estudiante FROM estudiantes WHERE estado='activo'")
    n = 0
    for (est,) in c.fetchall():
        try:
            c.execute("INSERT IGNORE INTO matriculas (id_grupo, id_estudiante) VALUES (%s, %s)", (grupo, est))
            n += c.rowcount
        except mye.Error as e:
            print(f"  Estudiante {est} no matriculado: {e.msg}")
    print(f"Datos de ejemplo listos (periodo {periodo}, {n} matricula(s) nuevas).")


def admin_desde_entorno(c):
    """Modo --auto: crea el administrador solo si la base no tiene ninguno."""
    c.execute("SELECT COUNT(*) FROM usuarios WHERE rol='admin' AND activo=1")
    if c.fetchone()[0] > 0:
        return
    usuario = os.environ.get("ADMIN_USUARIO", "admin")
    clave = os.environ.get("ADMIN_CLAVE", "")
    if len(clave) < 8:
        print("AVISO: no hay administrador y ADMIN_CLAVE no esta definida (minimo 8 caracteres).")
        return
    h = bcrypt.hashpw(clave.encode()[:72], bcrypt.gensalt()).decode()
    c.execute(
        """INSERT INTO usuarios (usuario, clave_hash, nombre_completo, rol) VALUES (%s, %s, 'Administrador', 'admin')
           ON DUPLICATE KEY UPDATE clave_hash=VALUES(clave_hash), rol='admin', activo=1""",
        (usuario, h),
    )
    print(f"Administrador '{usuario}' creado con la clave de ADMIN_CLAVE.")


def conectar(cfg, auto):
    intentos = 30 if auto else 1
    for i in range(intentos):
        try:
            return mysql.connector.connect(**cfg, autocommit=True)
        except mye.Error as e:
            if i == intentos - 1:
                print("No se pudo conectar a la base de datos:", e)
                print("Revisa que MySQL/MariaDB este encendido y los datos de config.py.")
                sys.exit(1)
            print("Esperando la base de datos...")
            time.sleep(2)


def main():
    auto = "--auto" in sys.argv
    cfg = dict(config.DB_CONFIG)
    bd = cfg.pop("database")
    vieja = getattr(config, "DB_ANTERIOR", "")
    if not NOMBRE_BD.match(bd) or (vieja and not NOMBRE_BD.match(vieja)):
        print("Nombre de base de datos invalido en config.py")
        sys.exit(1)
    if vieja == bd:
        print("DB_ANTERIOR no puede ser la misma base que DB_CONFIG['database'].")
        sys.exit(1)
    con = conectar(cfg, auto)
    c = con.cursor()
    crear_esquema(c, bd)
    if auto:
        admin_desde_entorno(c)
    else:
        if vieja:
            migrar(c, bd, vieja)
        crear_admin(c)
        datos_demo(c)
    c.close()
    con.close()
    if not auto:
        print("\nListo. Ahora ejecuta: python app.py")


if __name__ == "__main__":
    main()
