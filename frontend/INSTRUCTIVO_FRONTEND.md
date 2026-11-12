# Panel de asistencia facial - Instructivo

El panel (`frontend/index.html`) lo sirve el servidor: ejecuta `python app.py`
dentro de `server/` y abre http://127.0.0.1:8000. Ya no tiene modo demo: para
probar sin datos propios, responde **s** a "Cargar datos de ejemplo" en
`python preparar_bd.py`.

## Quién ve qué

El servidor decide lo que cada uno puede ver y hacer. Si un docente intenta usar
una función de administrador, aunque sea llamando a la API directamente, el
servidor responde "Solo el administrador puede hacer esto".

| Pestaña | Administrador | Docente |
|---|---|---|
| En vivo | Clase en curso en el aula de la cámara, con los LED y la lista | Solo si la clase en curso es suya |
| Reportes / Mis clases | Todas las clases por fecha; puede corregir estados y cerrar clases | Solo sus asignaturas y las clases que dicta, en modo lectura |
| Estudiantes | Registrar, editar y eliminar. Al editar se ven todas sus fotos: las nuevas se agregan (no reemplazan) y cada una se puede quitar con la ✕ | — |
| Grupos y horarios | Crear grupos, asignar docente y armar el horario | — |
| Matrícula | Matricular y retirar estudiantes de un grupo | — |
| Académico | Programas, asignaturas, periodos y jornadas | — |
| Infraestructura | Bloques, tipos de aula, aulas y dispositivos (cámaras) | — |
| Usuarios | Crear administradores y docentes, cambiar rol, contraseña o desactivar | — |

## Reporte por fecha

1. Elige el rango (*Hoy*, *Últimos 7 días* o fechas propias, hasta 62 días) y,
   si quieres, una asignatura.
2. Cada clase muestra su fecha, horario, aula, el porcentaje de asistencia y la
   lista de estudiantes con su estado, hora de ingreso y salida, y método
   (Cámara, Manual o Cierre de clase).
3. **Descargar CSV** exporta lo que ves para abrirlo en Excel.
4. Solo el administrador: el menú de cada estudiante corrige el estado (por
   ejemplo, a Justificado con su motivo), y **Cerrar clase** marca como ausentes
   a quienes no ingresaron. Esto último también ocurre solo cada noche si
   activaste el `event_scheduler`.

## Fotos de los estudiantes

- Sube **5 a 8 fotos** por estudiante (máximo 15): de frente, un poco de lado, con buena
  luz. Sirven las capturas de la propia cámara; mejor si son del lugar donde estará instalada.
- Al editar, cada foto que eliges se revisa al instante. El panel dice cuántas se agregaron y
  por qué se rechazó cada una ("no se ve un rostro de frente" o "es la misma foto del
  estudiante X").
- Nunca uses la misma foto para dos estudiantes: el sistema los confundiría.
- Debe quedar al menos una foto por estudiante.

## Problemas frecuentes

- **"No hay cámaras registradas"** o **"Esta cámara aún no tiene token"**: crea el
  dispositivo en *Infraestructura › Dispositivos* (tipo Cámara facial, con el aula
  donde está), pulsa **Generar token** y escribe los datos en el portal de la cámara.
- **"Cámara en reposo"**: es normal fuera de clase; transmite desde 15 min antes de
  cada clase de su aula. El admin puede pulsar **Encender 10 min** para probar.
- **"No hay clase en esta aula ahora"**: revisa que el grupo tenga horario en esa
  aula para hoy y que el periodo esté **Activo** y cubra la fecha de hoy.
- **"No está matriculado en la clase de esta aula"**: matricúlalo en *Matrícula*.
- **"Cruce de horario…"** o **"El grupo no tiene cupos"**: son validaciones de la
  base de datos; el mensaje dice qué se cruza.
