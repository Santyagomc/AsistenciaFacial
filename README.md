# Asistencia facial con ESP32-CAM — versión autónoma (VPS)

La cámara funciona **sin PC**. Al conectarla a la corriente, se une al Wi-Fi del aula y abre una conexión segura (WSS) con la VPS. La VPS reconoce los rostros, valida el horario de cada estudiante y registra la asistencia.

| Parte | Dónde corre | Archivos |
|---|---|---|
| Firmware de la cámara | ESP32-CAM | `firmware/esp32cam_asistencia/` |
| API, reconocimiento, base de datos y panel | VPS (Docker) | `server/`, `frontend/`, `deploy/` |
| Script de despliegue | Este PC (Windows) | `desplegar.ps1` / `desplegar.cmd` |

**Guía completa (VPS, firmware, portal Wi-Fi, OTA, seguridad, problemas): [docs/GUIA_DESPLIEGUE.md](docs/GUIA_DESPLIEGUE.md)**

## Inicio rápido

1. Edita la sección `CONFIGURACION` de `desplegar.ps1`. Como mínimo, `$VPS_IP`.
2. `.\desplegar.cmd verificar`
3. `.\desplegar.cmd vps`: instala la VPS y migra tus datos de XAMPP (deja MySQL encendido).
4. `.\desplegar.cmd firmware`: graba la ESP32-CAM por USB. Solo se hace una vez; las siguientes actualizaciones van por OTA.
5. Configura el Wi-Fi del aula desde el celular en la red `ASISTENCIA-XXXX`.

## Hardware

| Pin | Función |
|---|---|
| GPIO15 | LED verde: ingreso permitido |
| GPIO14 | LED rojo: denegado / no reconocido |
| GPIO13 | Botón (activo en ALTO): un click apaga los LED. No reinicia ni abre el portal. |

GPIO13/14/15 son pines de la ranura microSD: **no insertes tarjeta**. El detalle de los pines seguros está en la cabecera del `.ino`. Alimenta el conjunto con 5 V / 2 A estables (el módulo DC-DC), sin depender del USB.

## Reglas de asistencia

El servidor busca el horario del estudiante y compara su llegada con los valores de la tabla `parametros`:

- El ingreso abre 15 min antes del inicio.
- Hasta la hora de inicio es **cumple** (temprano o a tiempo).
- Hasta 5 min después es **tardío**; luego, **demorado**.
- Si no tiene asignaturas en ese lapso, se le informa.

Cada escenario se muestra como popup en el panel; la lista completa está en la guía, sección 4.

## Desarrollo local (opcional)

Para seguir probando en el PC con XAMPP:

```bash
cd server
venv\Scripts\activate
pip install -r requirements.txt
python preparar_bd.py   # aplica el esquema nuevo (columna token_hash, procedimientos)
python app.py           # http://127.0.0.1:8000
```

Para que una cámara se conecte a este servidor local, en su portal usa: servidor = IP del PC, puerto = 8000, seguridad = 0. Además, `HOST=0.0.0.0` como variable de entorno.

> `firmware/esp32cam_websocket/`, `server/server.py` y `web/index.html` son las versiones anteriores, en las que el PC y el navegador se conectaban directo a la cámara. Ya no funcionan con el firmware nuevo y se conservan solo como referencia: el video ahora se ve en el panel, en la pestaña *En vivo*.
