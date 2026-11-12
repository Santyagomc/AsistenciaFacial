-- =====================================================================
--  UNIVERSIDAD v2 - Gestión académica y control de ingreso a aulas
--  Motor: MySQL 8 / MariaDB 10.4+ (XAMPP)
--
--  NO se importa a mano: lo ejecuta `python preparar_bd.py`, que crea la
--  base indicada en config.py y corre este archivo. Se puede repetir sin
--  perder datos (tablas IF NOT EXISTS; procedimientos y vistas se recrean).
-- =====================================================================

-- =====================================================================
-- 1. CATÁLOGOS
-- =====================================================================

CREATE TABLE IF NOT EXISTS bloques (
  id_bloque   INT AUTO_INCREMENT PRIMARY KEY,
  nombre      VARCHAR(50) NOT NULL UNIQUE,
  estado      ENUM('activo','inactivo') NOT NULL DEFAULT 'activo'
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS tipos_aula (
  id_tipo_aula INT AUTO_INCREMENT PRIMARY KEY,
  nombre       VARCHAR(50) NOT NULL UNIQUE,
  estado       ENUM('activo','inactivo') NOT NULL DEFAULT 'activo'
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS jornadas (
  id_jornada  INT AUTO_INCREMENT PRIMARY KEY,
  nombre      VARCHAR(30) NOT NULL UNIQUE,
  estado      ENUM('activo','inactivo') NOT NULL DEFAULT 'activo'
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS programas (
  id_programa INT AUTO_INCREMENT PRIMARY KEY,
  codigo      VARCHAR(20) NOT NULL UNIQUE,
  nombre      VARCHAR(100) NOT NULL UNIQUE,
  estado      ENUM('activo','inactivo') NOT NULL DEFAULT 'activo'
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS periodos (
  id_periodo   INT AUTO_INCREMENT PRIMARY KEY,
  nombre       VARCHAR(20) NOT NULL UNIQUE,
  fecha_inicio DATE NOT NULL,
  fecha_fin    DATE NOT NULL,
  estado       ENUM('planeado','activo','cerrado') NOT NULL DEFAULT 'planeado',
  CONSTRAINT ck_periodo_fechas CHECK (fecha_fin > fecha_inicio)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS parametros (
  clave       VARCHAR(50) PRIMARY KEY,
  valor       INT NOT NULL,
  descripcion VARCHAR(150) NOT NULL
) ENGINE=InnoDB;

-- =====================================================================
-- 2. INFRAESTRUCTURA
-- =====================================================================

CREATE TABLE IF NOT EXISTS aulas (
  id_aula      INT AUTO_INCREMENT PRIMARY KEY,
  nombre       VARCHAR(50) NOT NULL,
  piso         INT NOT NULL,
  capacidad    INT NOT NULL,
  id_bloque    INT NOT NULL,
  id_tipo_aula INT NOT NULL,
  estado       ENUM('activa','mantenimiento','inactiva') NOT NULL DEFAULT 'activa',
  UNIQUE KEY uk_aula_bloque (id_bloque, nombre),
  CONSTRAINT ck_aula_capacidad CHECK (capacidad > 0),
  CONSTRAINT fk_aula_bloque FOREIGN KEY (id_bloque)
    REFERENCES bloques (id_bloque) ON UPDATE CASCADE,
  CONSTRAINT fk_aula_tipo FOREIGN KEY (id_tipo_aula)
    REFERENCES tipos_aula (id_tipo_aula) ON UPDATE CASCADE
) ENGINE=InnoDB;

-- =====================================================================
-- 3. PERSONAS
-- =====================================================================

CREATE TABLE IF NOT EXISTS usuarios (
  id_usuario      INT AUTO_INCREMENT PRIMARY KEY,
  usuario         VARCHAR(50) NOT NULL UNIQUE,
  clave_hash      VARCHAR(100) NOT NULL,
  nombre_completo VARCHAR(150) NOT NULL,
  correo          VARCHAR(120) NULL UNIQUE,
  rol             ENUM('admin','docente') NOT NULL DEFAULT 'docente',
  activo          TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS estudiantes (
  id_estudiante    INT AUTO_INCREMENT PRIMARY KEY,
  codigo           VARCHAR(20) NOT NULL UNIQUE,
  tipo_documento   ENUM('CC','TI','CE','PA','PPT') NULL,
  numero_documento VARCHAR(20) NULL UNIQUE,
  nombres          VARCHAR(80) NOT NULL,
  apellidos        VARCHAR(80) NOT NULL,
  correo           VARCHAR(120) NOT NULL UNIQUE,
  telefono         VARCHAR(20) NULL,
  fecha_nacimiento DATE NULL,
  fecha_ingreso    DATE NOT NULL DEFAULT (CURRENT_DATE),
  estado           ENUM('activo','inactivo','egresado','retirado') NOT NULL DEFAULT 'activo',
  id_programa      INT NOT NULL,
  semestre         TINYINT NOT NULL DEFAULT 1,
  KEY idx_est_estado (estado),
  CONSTRAINT ck_est_semestre CHECK (semestre BETWEEN 1 AND 12),
  CONSTRAINT fk_est_programa FOREIGN KEY (id_programa)
    REFERENCES programas (id_programa) ON UPDATE CASCADE
) ENGINE=InnoDB;

-- =====================================================================
-- 4. ESTRUCTURA ACADÉMICA
--    asignatura -> grupo (periodo, jornada, docente) -> horarios (día, hora, aula)
--    estudiante -> matrícula (una vez por grupo)
-- =====================================================================

CREATE TABLE IF NOT EXISTS asignaturas (
  id_asignatura INT AUTO_INCREMENT PRIMARY KEY,
  codigo        VARCHAR(20) NOT NULL UNIQUE,
  nombre        VARCHAR(100) NOT NULL,
  creditos      TINYINT NOT NULL,
  semestre      TINYINT NOT NULL,
  id_programa   INT NOT NULL,
  estado        ENUM('activo','inactivo') NOT NULL DEFAULT 'activo',
  CONSTRAINT ck_asig_creditos CHECK (creditos BETWEEN 1 AND 10),
  CONSTRAINT ck_asig_semestre CHECK (semestre BETWEEN 1 AND 12),
  CONSTRAINT fk_asig_programa FOREIGN KEY (id_programa)
    REFERENCES programas (id_programa) ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS grupos (
  id_grupo           INT AUTO_INCREMENT PRIMARY KEY,
  id_asignatura      INT NOT NULL,
  id_periodo         INT NOT NULL,
  id_jornada         INT NOT NULL,
  id_usuario_docente INT NULL,
  codigo_grupo       VARCHAR(10) NOT NULL,
  cupo               INT NOT NULL DEFAULT 30,
  estado             ENUM('abierto','cerrado','cancelado') NOT NULL DEFAULT 'abierto',
  UNIQUE KEY uk_grupo (id_asignatura, id_periodo, codigo_grupo),
  KEY idx_grupo_docente (id_usuario_docente),
  CONSTRAINT ck_grupo_cupo CHECK (cupo > 0),
  CONSTRAINT fk_grupo_asig FOREIGN KEY (id_asignatura)
    REFERENCES asignaturas (id_asignatura) ON UPDATE CASCADE,
  CONSTRAINT fk_grupo_periodo FOREIGN KEY (id_periodo)
    REFERENCES periodos (id_periodo) ON UPDATE CASCADE,
  CONSTRAINT fk_grupo_jornada FOREIGN KEY (id_jornada)
    REFERENCES jornadas (id_jornada) ON UPDATE CASCADE,
  CONSTRAINT fk_grupo_docente FOREIGN KEY (id_usuario_docente)
    REFERENCES usuarios (id_usuario) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS horarios (
  id_horario  INT AUTO_INCREMENT PRIMARY KEY,
  id_grupo    INT NOT NULL,
  id_aula     INT NOT NULL,
  dia_semana  TINYINT NOT NULL,                     -- 1=lunes ... 7=domingo
  hora_inicio TIME NOT NULL,
  hora_fin    TIME NOT NULL,
  KEY idx_hor_aula_dia (id_aula, dia_semana, hora_inicio),
  CONSTRAINT ck_hor_dia CHECK (dia_semana BETWEEN 1 AND 7),
  CONSTRAINT ck_hor_horas CHECK (hora_fin > hora_inicio),
  CONSTRAINT fk_hor_grupo FOREIGN KEY (id_grupo)
    REFERENCES grupos (id_grupo) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_hor_aula FOREIGN KEY (id_aula)
    REFERENCES aulas (id_aula) ON UPDATE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS matriculas (
  id_matricula    INT AUTO_INCREMENT PRIMARY KEY,
  id_grupo        INT NOT NULL,
  id_estudiante   INT NOT NULL,
  fecha_matricula DATE NOT NULL DEFAULT (CURRENT_DATE),
  estado          ENUM('activo','inactivo') NOT NULL DEFAULT 'activo',
  UNIQUE KEY uk_matricula (id_grupo, id_estudiante),
  KEY idx_mat_estudiante (id_estudiante, estado),
  CONSTRAINT fk_mat_grupo FOREIGN KEY (id_grupo)
    REFERENCES grupos (id_grupo) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_mat_estudiante FOREIGN KEY (id_estudiante)
    REFERENCES estudiantes (id_estudiante) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;

-- =====================================================================
-- 5. CONTROL DE ACCESO
-- =====================================================================

CREATE TABLE IF NOT EXISTS credenciales (
  id_credencial     INT AUTO_INCREMENT PRIMARY KEY,
  id_estudiante     INT NOT NULL,
  tipo              ENUM('qr','rfid','codigo_barras','biometrico') NOT NULL,
  valor_hash        CHAR(64) NOT NULL UNIQUE,           -- SHA2(valor,256)
  activa            TINYINT(1) NOT NULL DEFAULT 1,
  fecha_emision     DATE NOT NULL DEFAULT (CURRENT_DATE),
  fecha_vencimiento DATE NULL,
  KEY idx_cred_est (id_estudiante),
  CONSTRAINT fk_cred_est FOREIGN KEY (id_estudiante)
    REFERENCES estudiantes (id_estudiante) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;

-- Cada lector o cámara instalado en un aula. La ESP32-CAM se conecta a la VPS
-- con su identificador y un token (solo se guarda el SHA-256 del token).
CREATE TABLE IF NOT EXISTS dispositivos_acceso (
  id_dispositivo INT AUTO_INCREMENT PRIMARY KEY,
  id_aula        INT NOT NULL,
  nombre         VARCHAR(50) NOT NULL,
  tipo           ENUM('camara_facial','lector_qr','lector_rfid','biometrico','manual') NOT NULL,
  identificador  VARCHAR(64) NOT NULL UNIQUE,
  activo         TINYINT(1) NOT NULL DEFAULT 1,
  token_hash     CHAR(64) NULL,
  CONSTRAINT fk_disp_aula FOREIGN KEY (id_aula)
    REFERENCES aulas (id_aula) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;

-- Migración de bases creadas antes de la versión VPS (preparar_bd.py ignora
-- el error "columna duplicada" si ya existe)
ALTER TABLE dispositivos_acceso ADD COLUMN token_hash CHAR(64) NULL;

CREATE TABLE IF NOT EXISTS registros_acceso (
  id_registro     BIGINT AUTO_INCREMENT PRIMARY KEY,
  id_dispositivo  INT NULL,
  id_aula         INT NOT NULL,
  id_estudiante   INT NULL,
  id_credencial   INT NULL,
  id_horario      INT NULL,
  fecha_hora      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  tipo_movimiento ENUM('entrada','salida') NOT NULL,
  resultado       ENUM('permitido','denegado') NOT NULL,
  motivo          VARCHAR(255) NULL,
  KEY idx_acc_aula_fecha (id_aula, fecha_hora),
  KEY idx_acc_est_fecha (id_estudiante, fecha_hora),
  CONSTRAINT fk_acc_disp FOREIGN KEY (id_dispositivo)
    REFERENCES dispositivos_acceso (id_dispositivo) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_acc_aula FOREIGN KEY (id_aula)
    REFERENCES aulas (id_aula) ON UPDATE CASCADE,
  CONSTRAINT fk_acc_est FOREIGN KEY (id_estudiante)
    REFERENCES estudiantes (id_estudiante) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_acc_cred FOREIGN KEY (id_credencial)
    REFERENCES credenciales (id_credencial) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_acc_hor FOREIGN KEY (id_horario)
    REFERENCES horarios (id_horario) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB;

ALTER TABLE registros_acceso MODIFY motivo VARCHAR(255) NULL;

-- =====================================================================
-- 6. ASISTENCIA
--    cumple      : desde 15 min antes hasta la hora exacta de inicio
--    tardio      : de 1 a 5 min después del inicio
--    demorado    : más de 5 min después del inicio
--    ausente     : nunca ingresó (lo genera el cierre de la clase)
--    justificado : excusa válida (lo marca el administrador)
-- =====================================================================

CREATE TABLE IF NOT EXISTS asistencias (
  id_asistencia      INT AUTO_INCREMENT PRIMARY KEY,
  id_estudiante      INT NOT NULL,
  id_horario         INT NOT NULL,
  fecha              DATE NOT NULL,
  hora_ingreso       TIME NULL,
  hora_salida        TIME NULL,
  estado             ENUM('cumple','tardio','demorado','ausente','justificado') NOT NULL,
  metodo             ENUM('facial','qr','rfid','codigo_barras','biometrico','manual','automatico') NOT NULL DEFAULT 'manual',
  id_registro_acceso BIGINT NULL,
  registrado_por     INT NULL,
  observaciones      VARCHAR(255) NULL,
  UNIQUE KEY uk_asistencia (id_estudiante, id_horario, fecha),
  KEY idx_asis_fecha (fecha),
  KEY idx_asis_horario_fecha (id_horario, fecha),
  CONSTRAINT fk_asis_est FOREIGN KEY (id_estudiante)
    REFERENCES estudiantes (id_estudiante) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_asis_hor FOREIGN KEY (id_horario)
    REFERENCES horarios (id_horario) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_asis_reg FOREIGN KEY (id_registro_acceso)
    REFERENCES registros_acceso (id_registro) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_asis_usuario FOREIGN KEY (registrado_por)
    REFERENCES usuarios (id_usuario) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB;

-- =====================================================================
-- 7. VALIDACIONES (procedimientos + triggers)
-- =====================================================================

DROP PROCEDURE IF EXISTS validar_grupo;
DROP PROCEDURE IF EXISTS validar_horario;
DROP PROCEDURE IF EXISTS validar_matricula;
DROP PROCEDURE IF EXISTS validar_asistencia;
DROP TRIGGER IF EXISTS trg_grupos_bi;
DROP TRIGGER IF EXISTS trg_grupos_bu;
DROP TRIGGER IF EXISTS trg_horarios_bi;
DROP TRIGGER IF EXISTS trg_horarios_bu;
DROP TRIGGER IF EXISTS trg_matriculas_bi;
DROP TRIGGER IF EXISTS trg_matriculas_bu;
DROP TRIGGER IF EXISTS trg_asistencias_bi;

DELIMITER $$

CREATE PROCEDURE validar_grupo(IN p_id_grupo INT, IN p_docente INT, IN p_cupo INT)
BEGIN
  IF p_docente IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM usuarios WHERE id_usuario = p_docente AND rol = 'docente' AND activo = 1) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El usuario asignado no es un docente activo';
  END IF;
  IF p_id_grupo IS NOT NULL AND EXISTS (
       SELECT 1 FROM horarios h JOIN aulas a ON a.id_aula = h.id_aula
        WHERE h.id_grupo = p_id_grupo AND a.capacidad < p_cupo) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El cupo supera la capacidad de un aula asignada al grupo';
  END IF;
END$$

CREATE PROCEDURE validar_horario(IN p_id_horario INT, IN p_id_grupo INT, IN p_id_aula INT,
                                 IN p_dia TINYINT, IN p_ini TIME, IN p_fin TIME)
BEGIN
  DECLARE v_periodo INT;
  DECLARE v_docente INT;
  DECLARE v_cupo INT;
  DECLARE v_estado_aula VARCHAR(20);
  DECLARE v_capacidad INT;

  SELECT id_periodo, id_usuario_docente, cupo INTO v_periodo, v_docente, v_cupo
    FROM grupos WHERE id_grupo = p_id_grupo;
  SELECT estado, capacidad INTO v_estado_aula, v_capacidad
    FROM aulas WHERE id_aula = p_id_aula;

  IF p_fin <= p_ini THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'La hora de fin debe ser mayor que la de inicio';
  END IF;
  IF v_estado_aula <> 'activa' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El aula no está activa';
  END IF;
  IF v_cupo > v_capacidad THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El cupo del grupo supera la capacidad del aula';
  END IF;
  IF EXISTS (
       SELECT 1 FROM horarios h JOIN grupos g ON g.id_grupo = h.id_grupo
        WHERE h.id_aula = p_id_aula AND h.dia_semana = p_dia
          AND g.id_periodo = v_periodo AND g.estado <> 'cancelado'
          AND h.id_horario <> IFNULL(p_id_horario, 0)
          AND h.hora_inicio < p_fin AND p_ini < h.hora_fin) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cruce de horario: el aula ya está ocupada en esa franja';
  END IF;
  IF v_docente IS NOT NULL AND EXISTS (
       SELECT 1 FROM horarios h JOIN grupos g ON g.id_grupo = h.id_grupo
        WHERE g.id_usuario_docente = v_docente AND h.dia_semana = p_dia
          AND g.id_periodo = v_periodo AND g.estado <> 'cancelado'
          AND h.id_horario <> IFNULL(p_id_horario, 0)
          AND h.hora_inicio < p_fin AND p_ini < h.hora_fin) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cruce de horario: el docente ya tiene clase en esa franja';
  END IF;
END$$

CREATE PROCEDURE validar_matricula(IN p_id_matricula INT, IN p_id_grupo INT, IN p_id_estudiante INT)
BEGIN
  DECLARE v_estado_est VARCHAR(20);
  DECLARE v_estado_grupo VARCHAR(20);
  DECLARE v_cupo INT;
  DECLARE v_periodo INT;
  DECLARE v_ocupados INT;

  SELECT estado INTO v_estado_est FROM estudiantes WHERE id_estudiante = p_id_estudiante;
  SELECT estado, cupo, id_periodo INTO v_estado_grupo, v_cupo, v_periodo
    FROM grupos WHERE id_grupo = p_id_grupo;

  IF v_estado_est <> 'activo' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El estudiante no está activo';
  END IF;
  IF v_estado_grupo <> 'abierto' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El grupo no está abierto para matrícula';
  END IF;

  SELECT COUNT(*) INTO v_ocupados FROM matriculas
   WHERE id_grupo = p_id_grupo AND estado = 'activo'
     AND id_matricula <> IFNULL(p_id_matricula, 0);
  IF v_ocupados >= v_cupo THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El grupo no tiene cupos disponibles';
  END IF;

  IF EXISTS (
       SELECT 1
         FROM matriculas m
         JOIN grupos g2   ON g2.id_grupo = m.id_grupo
         JOIN horarios h2 ON h2.id_grupo = g2.id_grupo
         JOIN horarios h  ON h.id_grupo  = p_id_grupo
                         AND h.dia_semana = h2.dia_semana
                         AND h.hora_inicio < h2.hora_fin
                         AND h2.hora_inicio < h.hora_fin
        WHERE m.id_estudiante = p_id_estudiante
          AND m.estado = 'activo'
          AND m.id_grupo <> p_id_grupo
          AND m.id_matricula <> IFNULL(p_id_matricula, 0)
          AND g2.id_periodo = v_periodo
          AND g2.estado <> 'cancelado') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cruce de horario con otra asignatura matriculada';
  END IF;
END$$

CREATE PROCEDURE validar_asistencia(IN p_id_estudiante INT, IN p_id_horario INT, IN p_fecha DATE)
BEGIN
  IF NOT EXISTS (
       SELECT 1 FROM horarios h
         JOIN matriculas m ON m.id_grupo = h.id_grupo
        WHERE h.id_horario = p_id_horario
          AND m.id_estudiante = p_id_estudiante
          AND m.estado = 'activo') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El estudiante no está matriculado en este grupo';
  END IF;
  IF NOT EXISTS (
       SELECT 1 FROM horarios WHERE id_horario = p_id_horario
          AND dia_semana = WEEKDAY(p_fecha) + 1) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'La fecha no corresponde al día de este horario';
  END IF;
END$$

CREATE TRIGGER trg_grupos_bi BEFORE INSERT ON grupos FOR EACH ROW
  CALL validar_grupo(NULL, NEW.id_usuario_docente, NEW.cupo)$$
CREATE TRIGGER trg_grupos_bu BEFORE UPDATE ON grupos FOR EACH ROW
  CALL validar_grupo(NEW.id_grupo, NEW.id_usuario_docente, NEW.cupo)$$

CREATE TRIGGER trg_horarios_bi BEFORE INSERT ON horarios FOR EACH ROW
  CALL validar_horario(NULL, NEW.id_grupo, NEW.id_aula, NEW.dia_semana, NEW.hora_inicio, NEW.hora_fin)$$
CREATE TRIGGER trg_horarios_bu BEFORE UPDATE ON horarios FOR EACH ROW
  CALL validar_horario(NEW.id_horario, NEW.id_grupo, NEW.id_aula, NEW.dia_semana, NEW.hora_inicio, NEW.hora_fin)$$

CREATE TRIGGER trg_matriculas_bi BEFORE INSERT ON matriculas FOR EACH ROW
BEGIN
  IF NEW.estado = 'activo' THEN
    CALL validar_matricula(NULL, NEW.id_grupo, NEW.id_estudiante);
  END IF;
END$$
CREATE TRIGGER trg_matriculas_bu BEFORE UPDATE ON matriculas FOR EACH ROW
BEGIN
  IF NEW.estado = 'activo' THEN
    CALL validar_matricula(NEW.id_matricula, NEW.id_grupo, NEW.id_estudiante);
  END IF;
END$$

CREATE TRIGGER trg_asistencias_bi BEFORE INSERT ON asistencias FOR EACH ROW
  CALL validar_asistencia(NEW.id_estudiante, NEW.id_horario, NEW.fecha)$$

DELIMITER ;

-- =====================================================================
-- 8. OPERACIÓN
-- =====================================================================

DROP PROCEDURE IF EXISTS procesar_movimiento;
DROP PROCEDURE IF EXISTS registrar_movimiento;
DROP PROCEDURE IF EXISTS registrar_ingreso_facial;
DROP PROCEDURE IF EXISTS marcar_asistencia_manual;
DROP PROCEDURE IF EXISTS cerrar_clase;
DROP PROCEDURE IF EXISTS cerrar_clases_del_dia;

DELIMITER $$

-- 8.1 Núcleo: valida y registra una entrada/salida de un estudiante ya
--     identificado (por credencial o por rostro) en el aula de un dispositivo.
--
--     p_codigo describe el escenario (lo usa el panel para el popup):
--       Permitidos : temprano, a_tiempo, tardio, demorado, reingreso, salida
--       Denegados  : sin_asignatura, clase_finalizada, muy_temprano,
--                    no_matriculado, otra_aula, estudiante_inactivo,
--                    no_identificado, sin_ingreso, dispositivo_invalido,
--                    movimiento_invalido
--
--     Reglas de llegada (tabla parametros, se cambian sin tocar código):
--       El ingreso abre minutos_antes_ingreso antes del inicio (15 por defecto).
--       Hasta la hora de inicio            -> cumple   (temprano / a tiempo)
--       Hasta minutos_tolerancia_tardio    -> tardio
--       Después, hasta el fin de la clase  -> demorado
CREATE PROCEDURE procesar_movimiento(
  IN  p_id_estudiante INT,
  IN  p_id_credencial INT,
  IN  p_metodo        VARCHAR(20),
  IN  p_dispositivo   VARCHAR(64),
  IN  p_movimiento    VARCHAR(10),
  OUT p_resultado     VARCHAR(10),
  OUT p_mensaje       VARCHAR(255),
  OUT p_id_horario    INT,
  OUT p_codigo        VARCHAR(30)
)
proc: BEGIN
  DECLARE v_id_dispositivo INT DEFAULT NULL;
  DECLARE v_id_aula        INT DEFAULT NULL;
  DECLARE v_estado_est     VARCHAR(20) DEFAULT NULL;
  -- Clase del estudiante cuya ventana de ingreso incluye la hora actual
  DECLARE v_id_horario     INT DEFAULT NULL;
  DECLARE v_aula_clase     INT DEFAULT NULL;
  DECLARE v_hora_inicio    TIME DEFAULT NULL;
  DECLARE v_asignatura     VARCHAR(100) DEFAULT NULL;
  DECLARE v_aula_nombre    VARCHAR(110) DEFAULT NULL;
  -- Clase en curso en el aula del dispositivo (sea o no del estudiante)
  DECLARE v_hor_aula       INT DEFAULT NULL;
  DECLARE v_asig_aula      VARCHAR(100) DEFAULT NULL;
  -- Próxima clase del estudiante hoy y la última suya que ya terminó en esta aula
  DECLARE v_prox_inicio    TIME DEFAULT NULL;
  DECLARE v_prox_asig      VARCHAR(100) DEFAULT NULL;
  DECLARE v_prox_aula      VARCHAR(110) DEFAULT NULL;
  DECLARE v_prox_misma     INT DEFAULT 0;
  DECLARE v_fin_asig       VARCHAR(100) DEFAULT NULL;
  DECLARE v_fin_hora       TIME DEFAULT NULL;
  DECLARE v_prox_txt       VARCHAR(200) DEFAULT '';
  DECLARE v_id_asistencia  INT DEFAULT NULL;
  DECLARE v_estado_previo  VARCHAR(20) DEFAULT NULL;
  DECLARE v_hora_previa    TIME DEFAULT NULL;
  DECLARE v_id_registro    BIGINT DEFAULT NULL;
  DECLARE v_ahora          DATETIME DEFAULT NOW();
  DECLARE v_hoy            DATE DEFAULT CURDATE();
  DECLARE v_hora           TIME DEFAULT CURTIME();
  DECLARE v_dia            TINYINT DEFAULT WEEKDAY(CURDATE()) + 1;
  DECLARE v_min_antes      INT DEFAULT 15;
  DECLARE v_min_tardio     INT DEFAULT 5;
  DECLARE v_min_salida     INT DEFAULT 30;
  DECLARE v_seg            INT DEFAULT 0;
  DECLARE v_estado         VARCHAR(20) DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND BEGIN END;

  SET p_resultado = 'denegado';
  SET p_id_horario = NULL;
  SET p_codigo = NULL;
  SET p_movimiento = LOWER(p_movimiento);

  SELECT valor INTO v_min_antes  FROM parametros WHERE clave = 'minutos_antes_ingreso';
  SELECT valor INTO v_min_tardio FROM parametros WHERE clave = 'minutos_tolerancia_tardio';
  SELECT valor INTO v_min_salida FROM parametros WHERE clave = 'minutos_despues_salida';

  IF p_movimiento NOT IN ('entrada','salida') THEN
    SET p_codigo = 'movimiento_invalido', p_mensaje = 'Movimiento inválido';
    LEAVE proc;
  END IF;

  SELECT d.id_dispositivo, d.id_aula INTO v_id_dispositivo, v_id_aula
    FROM dispositivos_acceso d
    JOIN aulas a ON a.id_aula = d.id_aula
   WHERE d.identificador = p_dispositivo COLLATE utf8mb4_unicode_ci
     AND d.activo = 1 AND a.estado = 'activa'
   LIMIT 1;

  IF v_id_aula IS NULL THEN
    SET p_codigo = 'dispositivo_invalido',
        p_mensaje = 'Dispositivo no registrado, inactivo o aula fuera de servicio';
    LEAVE proc;
  END IF;

  IF p_id_estudiante IS NOT NULL THEN
    SELECT estado INTO v_estado_est FROM estudiantes WHERE id_estudiante = p_id_estudiante;
  END IF;

  IF p_movimiento = 'entrada' THEN
    -- Clase en curso (o por empezar) en el aula de la cámara
    SELECT h.id_horario, LEFT(asg.nombre, 60) INTO v_hor_aula, v_asig_aula
      FROM horarios h
      JOIN grupos g        ON g.id_grupo = h.id_grupo
      JOIN periodos p      ON p.id_periodo = g.id_periodo
      JOIN asignaturas asg ON asg.id_asignatura = g.id_asignatura
     WHERE h.id_aula = v_id_aula
       AND h.dia_semana = v_dia
       AND g.estado <> 'cancelado' AND p.estado = 'activo'
       AND v_hoy BETWEEN p.fecha_inicio AND p.fecha_fin
       AND v_hora BETWEEN SUBTIME(h.hora_inicio, SEC_TO_TIME(v_min_antes * 60)) AND h.hora_fin
     ORDER BY ABS(TIME_TO_SEC(TIMEDIFF(v_hora, h.hora_inicio)))
     LIMIT 1;

    IF v_estado_est IS NULL THEN
      SET p_codigo = 'no_identificado', p_mensaje = 'Persona no identificada en la base de datos';
    ELSEIF v_estado_est <> 'activo' THEN
      SET p_codigo = 'estudiante_inactivo',
          p_mensaje = CONCAT('El estudiante no está activo (estado: ', v_estado_est, ')');
    ELSE
      -- Horario del estudiante: su clase de hoy cuya ventana incluye la hora
      -- actual (prioriza la que es en esta aula y la de inicio más cercano)
      SELECT h.id_horario, h.id_aula, h.hora_inicio, LEFT(asg.nombre, 60),
             CONCAT(b.nombre, ' · ', a.nombre)
        INTO v_id_horario, v_aula_clase, v_hora_inicio, v_asignatura, v_aula_nombre
        FROM matriculas m
        JOIN grupos g        ON g.id_grupo = m.id_grupo
        JOIN periodos p      ON p.id_periodo = g.id_periodo
        JOIN asignaturas asg ON asg.id_asignatura = g.id_asignatura
        JOIN horarios h      ON h.id_grupo = g.id_grupo
        JOIN aulas a         ON a.id_aula = h.id_aula
        JOIN bloques b       ON b.id_bloque = a.id_bloque
       WHERE m.id_estudiante = p_id_estudiante AND m.estado = 'activo'
         AND g.estado <> 'cancelado' AND p.estado = 'activo'
         AND v_hoy BETWEEN p.fecha_inicio AND p.fecha_fin
         AND h.dia_semana = v_dia
         AND v_hora BETWEEN SUBTIME(h.hora_inicio, SEC_TO_TIME(v_min_antes * 60)) AND h.hora_fin
       ORDER BY (h.id_aula = v_id_aula) DESC, ABS(TIME_TO_SEC(TIMEDIFF(v_hora, h.hora_inicio)))
       LIMIT 1;

      IF v_id_horario IS NOT NULL AND v_aula_clase = v_id_aula THEN
        -- Escenario permitido: compara la llegada con la hora de inicio
        SET p_resultado = 'permitido';
        SET v_seg = TIME_TO_SEC(TIMEDIFF(v_hora, v_hora_inicio));
        IF v_seg <= 0 THEN
          SET v_estado = 'cumple';
          IF v_seg <= -60 THEN
            SET p_codigo = 'temprano',
                p_mensaje = CONCAT('Llegó temprano: ', FLOOR(-v_seg / 60), ' min antes del inicio (',
                                   TIME_FORMAT(v_hora_inicio, '%H:%i'), ') de ', v_asignatura,
                                   '. Asistencia: cumple');
          ELSE
            SET p_codigo = 'a_tiempo',
                p_mensaje = CONCAT('Llegó a tiempo a ', v_asignatura, ' (inicio ',
                                   TIME_FORMAT(v_hora_inicio, '%H:%i'), '). Asistencia: cumple');
          END IF;
        ELSEIF v_seg <= v_min_tardio * 60 THEN
          SET v_estado = 'tardio', p_codigo = 'tardio',
              p_mensaje = CONCAT('Llegó tarde: ', CEIL(v_seg / 60), ' min después del inicio (',
                                 TIME_FORMAT(v_hora_inicio, '%H:%i'), ') de ', v_asignatura,
                                 '. Asistencia: tardío (tolerancia ', v_min_tardio, ' min)');
        ELSE
          SET v_estado = 'demorado', p_codigo = 'demorado',
              p_mensaje = CONCAT('Llegó demorado: ', CEIL(v_seg / 60), ' min después del inicio (',
                                 TIME_FORMAT(v_hora_inicio, '%H:%i'), ') de ', v_asignatura,
                                 '. Superó la tolerancia de ', v_min_tardio, ' min');
        END IF;

        SELECT id_asistencia, estado, hora_ingreso INTO v_id_asistencia, v_estado_previo, v_hora_previa
          FROM asistencias
         WHERE id_estudiante = p_id_estudiante AND id_horario = v_id_horario AND fecha = v_hoy;
        IF v_id_asistencia IS NOT NULL THEN
          SET p_codigo = 'reingreso',
              p_mensaje = CONCAT('Ya tenía ingreso registrado hoy en ', v_asignatura,
                                 IF(v_hora_previa IS NULL, '',
                                    CONCAT(' a las ', TIME_FORMAT(v_hora_previa, '%H:%i'))),
                                 ' (', v_estado_previo, '). Se conserva la primera llegada');
        END IF;

      ELSEIF v_id_horario IS NOT NULL THEN
        SET p_codigo = 'otra_aula',
            p_mensaje = CONCAT('Aula equivocada: su clase de ', v_asignatura, ' (',
                               TIME_FORMAT(v_hora_inicio, '%H:%i'), ') es en ', v_aula_nombre);

      ELSE
        -- No tiene clase en este lapso: busca la próxima de hoy y la última que terminó aquí
        SELECT h.hora_inicio, LEFT(asg.nombre, 60), CONCAT(b.nombre, ' · ', a.nombre),
               IF(h.id_aula = v_id_aula, 1, 0)
          INTO v_prox_inicio, v_prox_asig, v_prox_aula, v_prox_misma
          FROM matriculas m
          JOIN grupos g        ON g.id_grupo = m.id_grupo
          JOIN periodos p      ON p.id_periodo = g.id_periodo
          JOIN asignaturas asg ON asg.id_asignatura = g.id_asignatura
          JOIN horarios h      ON h.id_grupo = g.id_grupo
          JOIN aulas a         ON a.id_aula = h.id_aula
          JOIN bloques b       ON b.id_bloque = a.id_bloque
         WHERE m.id_estudiante = p_id_estudiante AND m.estado = 'activo'
           AND g.estado <> 'cancelado' AND p.estado = 'activo'
           AND v_hoy BETWEEN p.fecha_inicio AND p.fecha_fin
           AND h.dia_semana = v_dia
           AND h.hora_inicio > v_hora
         ORDER BY h.hora_inicio
         LIMIT 1;

        SELECT LEFT(asg.nombre, 60), h.hora_fin INTO v_fin_asig, v_fin_hora
          FROM matriculas m
          JOIN grupos g        ON g.id_grupo = m.id_grupo
          JOIN periodos p      ON p.id_periodo = g.id_periodo
          JOIN asignaturas asg ON asg.id_asignatura = g.id_asignatura
          JOIN horarios h      ON h.id_grupo = g.id_grupo
         WHERE m.id_estudiante = p_id_estudiante AND m.estado = 'activo'
           AND g.estado <> 'cancelado' AND p.estado = 'activo'
           AND v_hoy BETWEEN p.fecha_inicio AND p.fecha_fin
           AND h.dia_semana = v_dia
           AND h.id_aula = v_id_aula
           AND h.hora_fin < v_hora
         ORDER BY h.hora_fin DESC
         LIMIT 1;

        IF v_prox_inicio IS NOT NULL THEN
          SET v_prox_txt = CONCAT('. Su próxima clase hoy: ', v_prox_asig, ' a las ',
                                  TIME_FORMAT(v_prox_inicio, '%H:%i'), ' en ', v_prox_aula);
        END IF;

        IF v_prox_inicio IS NOT NULL AND v_prox_misma = 1 THEN
          SET p_codigo = 'muy_temprano',
              p_mensaje = CONCAT('Llegó muy temprano: su clase de ', v_prox_asig, ' empieza a las ',
                                 TIME_FORMAT(v_prox_inicio, '%H:%i'), '. El ingreso se habilita a las ',
                                 TIME_FORMAT(SUBTIME(v_prox_inicio, SEC_TO_TIME(v_min_antes * 60)), '%H:%i'),
                                 ' (', v_min_antes, ' min antes)');
        ELSEIF v_hor_aula IS NOT NULL THEN
          SET p_codigo = 'no_matriculado',
              p_mensaje = CONCAT('No está matriculado en ', v_asig_aula,
                                 ', la clase en curso en esta aula', v_prox_txt);
        ELSEIF v_fin_asig IS NOT NULL THEN
          SET p_codigo = 'clase_finalizada',
              p_mensaje = CONCAT('No tiene asignaturas vinculadas para este horario: su clase de ',
                                 v_fin_asig, ' terminó a las ', TIME_FORMAT(v_fin_hora, '%H:%i'), v_prox_txt);
        ELSE
          SET p_codigo = 'sin_asignatura',
              p_mensaje = CONCAT('No tiene asignaturas vinculadas para este horario', v_prox_txt);
        END IF;
      END IF;
    END IF;

    SET p_mensaje = LEFT(p_mensaje, 255);
    SET p_id_horario = IF(p_resultado = 'permitido', v_id_horario, v_hor_aula);

    INSERT INTO registros_acceso (id_dispositivo, id_aula, id_estudiante, id_credencial,
                                  id_horario, fecha_hora, tipo_movimiento, resultado, motivo)
    VALUES (v_id_dispositivo, v_id_aula,
            IF(v_estado_est IS NULL, NULL, p_id_estudiante), p_id_credencial,
            p_id_horario, v_ahora, 'entrada', p_resultado, p_mensaje);
    SET v_id_registro = LAST_INSERT_ID();

    IF p_resultado = 'permitido' THEN
      IF v_id_asistencia IS NULL THEN
        INSERT INTO asistencias (id_estudiante, id_horario, fecha, hora_ingreso, estado,
                                 metodo, id_registro_acceso)
        VALUES (p_id_estudiante, v_id_horario, v_hoy, v_hora, v_estado,
                p_metodo, v_id_registro);
      ELSE
        UPDATE asistencias SET hora_salida = NULL WHERE id_asistencia = v_id_asistencia;
      END IF;
    END IF;

  ELSE  -- salida
    IF v_estado_est IS NOT NULL THEN
      SELECT a.id_asistencia, a.id_horario INTO v_id_asistencia, v_id_horario
        FROM asistencias a
        JOIN horarios h ON h.id_horario = a.id_horario
       WHERE a.id_estudiante = p_id_estudiante
         AND a.fecha = v_hoy
         AND h.id_aula = v_id_aula
         AND a.hora_ingreso IS NOT NULL
         AND v_hora <= ADDTIME(h.hora_fin, SEC_TO_TIME(v_min_salida * 60))
       ORDER BY h.hora_inicio DESC
       LIMIT 1;
    END IF;

    IF v_estado_est IS NULL THEN
      SET p_codigo = 'no_identificado', p_mensaje = 'Persona no identificada en la base de datos';
    ELSEIF v_id_asistencia IS NULL THEN
      SET p_codigo = 'sin_ingreso', p_mensaje = 'No tiene un ingreso registrado hoy en esta aula';
    ELSE
      SET p_resultado = 'permitido';
      SET p_codigo = 'salida', p_mensaje = 'Salida registrada';
      UPDATE asistencias SET hora_salida = v_hora WHERE id_asistencia = v_id_asistencia;
    END IF;

    INSERT INTO registros_acceso (id_dispositivo, id_aula, id_estudiante, id_credencial,
                                  id_horario, fecha_hora, tipo_movimiento, resultado, motivo)
    VALUES (v_id_dispositivo, v_id_aula,
            IF(v_estado_est IS NULL, NULL, p_id_estudiante), p_id_credencial,
            v_id_horario, v_ahora, 'salida', p_resultado, p_mensaje);
    SET p_id_horario = v_id_horario;
  END IF;
END$$

-- 8.2 Entrada/salida con carné (QR, RFID...): lo llamaría un lector físico
CREATE PROCEDURE registrar_movimiento(
  IN  p_credencial  VARCHAR(128),
  IN  p_dispositivo VARCHAR(64),
  IN  p_movimiento  VARCHAR(10),
  OUT p_resultado   VARCHAR(10),
  OUT p_mensaje     VARCHAR(255)
)
BEGIN
  DECLARE v_id_credencial INT DEFAULT NULL;
  DECLARE v_id_estudiante INT DEFAULT NULL;
  DECLARE v_tipo          VARCHAR(20) DEFAULT NULL;
  DECLARE v_id_horario    INT DEFAULT NULL;
  DECLARE v_codigo        VARCHAR(30) DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND BEGIN END;

  SELECT id_credencial, id_estudiante, tipo INTO v_id_credencial, v_id_estudiante, v_tipo
    FROM credenciales
   WHERE valor_hash = SHA2(p_credencial, 256) COLLATE utf8mb4_unicode_ci
     AND activa = 1
     AND (fecha_vencimiento IS NULL OR fecha_vencimiento >= CURDATE())
   LIMIT 1;

  CALL procesar_movimiento(v_id_estudiante, v_id_credencial, IFNULL(v_tipo, 'manual'),
                           p_dispositivo, p_movimiento, p_resultado, p_mensaje, v_id_horario, v_codigo);
  IF v_id_estudiante IS NULL AND v_codigo = 'no_identificado' THEN
    SET p_mensaje = 'Credencial inválida o vencida';
  END IF;
END$$

-- 8.3 Entrada por reconocimiento facial: lo llama app.py con el código
--     del estudiante que reconoció la ESP32-CAM
CREATE PROCEDURE registrar_ingreso_facial(
  IN  p_codigo_est  VARCHAR(20),
  IN  p_dispositivo VARCHAR(64),
  OUT p_resultado   VARCHAR(10),
  OUT p_mensaje     VARCHAR(255),
  OUT p_id_horario  INT,
  OUT p_codigo      VARCHAR(30)
)
BEGIN
  DECLARE v_id_estudiante INT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND BEGIN END;

  SELECT id_estudiante INTO v_id_estudiante FROM estudiantes
   WHERE codigo = p_codigo_est COLLATE utf8mb4_unicode_ci;

  CALL procesar_movimiento(v_id_estudiante, NULL, 'facial', p_dispositivo, 'entrada',
                           p_resultado, p_mensaje, p_id_horario, p_codigo);
END$$

-- 8.4 Marcación o corrección manual (docente del grupo o admin)
CREATE PROCEDURE marcar_asistencia_manual(
  IN p_id_estudiante INT, IN p_id_horario INT, IN p_fecha DATE,
  IN p_estado VARCHAR(20), IN p_id_usuario INT, IN p_observaciones VARCHAR(255)
)
BEGIN
  IF NOT EXISTS (
       SELECT 1 FROM usuarios u
        WHERE u.id_usuario = p_id_usuario AND u.activo = 1
          AND (u.rol = 'admin' OR EXISTS (
                 SELECT 1 FROM horarios h JOIN grupos g ON g.id_grupo = h.id_grupo
                  WHERE h.id_horario = p_id_horario AND g.id_usuario_docente = u.id_usuario))) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Solo el docente del grupo o un administrador puede marcar asistencia';
  END IF;

  INSERT INTO asistencias (id_estudiante, id_horario, fecha, hora_ingreso, estado,
                           metodo, registrado_por, observaciones)
  VALUES (p_id_estudiante, p_id_horario, p_fecha,
          IF(p_estado IN ('ausente','justificado'), NULL, CURTIME()),
          p_estado, 'manual', p_id_usuario, p_observaciones)
  ON DUPLICATE KEY UPDATE
    estado         = VALUES(estado),
    metodo         = 'manual',
    registrado_por = VALUES(registrado_por),
    observaciones  = VALUES(observaciones);
END$$

-- 8.5 Cierre: marca 'ausente' a los matriculados que no ingresaron
CREATE PROCEDURE cerrar_clase(IN p_id_horario INT, IN p_fecha DATE)
BEGIN
  INSERT IGNORE INTO asistencias (id_estudiante, id_horario, fecha, estado, metodo)
  SELECT m.id_estudiante, h.id_horario, p_fecha, 'ausente', 'automatico'
    FROM horarios h
    JOIN matriculas m ON m.id_grupo = h.id_grupo AND m.estado = 'activo'
   WHERE h.id_horario = p_id_horario
     AND h.dia_semana = WEEKDAY(p_fecha) + 1;
END$$

CREATE PROCEDURE cerrar_clases_del_dia(IN p_fecha DATE)
BEGIN
  INSERT IGNORE INTO asistencias (id_estudiante, id_horario, fecha, estado, metodo)
  SELECT m.id_estudiante, h.id_horario, p_fecha, 'ausente', 'automatico'
    FROM horarios h
    JOIN grupos g     ON g.id_grupo = h.id_grupo AND g.estado <> 'cancelado'
    JOIN periodos p   ON p.id_periodo = g.id_periodo AND p.estado = 'activo'
    JOIN matriculas m ON m.id_grupo = g.id_grupo AND m.estado = 'activo'
   WHERE h.dia_semana = WEEKDAY(p_fecha) + 1
     AND p_fecha BETWEEN p.fecha_inicio AND p.fecha_fin;
END$$

DELIMITER ;

-- Cierre automático cada noche (en XAMPP/MariaDB hay que activar el
-- programador una vez: SET GLOBAL event_scheduler = ON;)
CREATE EVENT IF NOT EXISTS ev_cierre_diario
  ON SCHEDULE EVERY 1 DAY STARTS TIMESTAMP(CURDATE(), '23:55:00')
  DO CALL cerrar_clases_del_dia(CURDATE());

-- =====================================================================
-- 9. VISTAS
-- =====================================================================

CREATE OR REPLACE VIEW v_horarios AS
SELECT h.id_horario, g.id_grupo, g.id_usuario_docente, p.nombre AS periodo, j.nombre AS jornada,
       asg.codigo AS cod_asignatura, asg.nombre AS asignatura, g.codigo_grupo,
       u.nombre_completo AS docente, h.dia_semana,
       ELT(h.dia_semana,'Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo') AS dia,
       h.hora_inicio, h.hora_fin, h.id_aula,
       b.nombre AS bloque, a.nombre AS aula, ta.nombre AS tipo_aula, g.cupo
  FROM horarios h
  JOIN grupos g        ON g.id_grupo = h.id_grupo
  JOIN asignaturas asg ON asg.id_asignatura = g.id_asignatura
  JOIN periodos p      ON p.id_periodo = g.id_periodo
  JOIN jornadas j      ON j.id_jornada = g.id_jornada
  JOIN aulas a         ON a.id_aula = h.id_aula
  JOIN bloques b       ON b.id_bloque = a.id_bloque
  JOIN tipos_aula ta   ON ta.id_tipo_aula = a.id_tipo_aula
  LEFT JOIN usuarios u ON u.id_usuario = g.id_usuario_docente;

-- =====================================================================
-- 10. DATOS BASE (no se duplican al repetir)
-- =====================================================================

INSERT IGNORE INTO parametros (clave, valor, descripcion) VALUES
('minutos_antes_ingreso',     15, 'Minutos antes del inicio en que se habilita el ingreso (cuenta como cumple)'),
('minutos_tolerancia_tardio',  5, 'Minutos después del inicio que cuentan como tardío; después es demorado'),
('minutos_despues_salida',    30, 'Minutos después del fin de clase en que aún se acepta registrar salida');

INSERT IGNORE INTO jornadas (nombre) VALUES ('Diurna'), ('Nocturna'), ('Fin de semana');
INSERT IGNORE INTO tipos_aula (nombre) VALUES ('Teórica'), ('Laboratorio'), ('Auditorio');
