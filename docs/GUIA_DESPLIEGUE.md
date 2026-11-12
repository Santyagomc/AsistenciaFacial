# Guía de despliegue: ESP32-CAM autónoma + servidor VPS

Esta guía deja el sistema funcionando **sin ningún PC encendido**. La cámara se conecta sola a la VPS al recibir corriente. Solo se usa el PC para instalar la VPS y para grabar la cámara la primera vez.

---

## 1. Arquitectura

```
 Aula                                    Internet                      VPS (Ubuntu + Docker)
┌──────────────────────┐                                       ┌──────────────────────────────────┐
│ ESP32-CAM            │   WSS (TLS, puerto 443), conexión     │ Caddy  (HTTPS automático)        │
│  · captura JPEG      │──────── saliente y persistente ──────▶│   │                              │
│  · LED verde GPIO15  │◀─── GREEN / RED / MODE / CFG / OTA ───│   ▼                              │
│  · LED rojo  GPIO14  │                                       │ app (FastAPI + OpenCV LBPH)      │
│  · botón     GPIO13  │                                       │   │  reconoce, decide, registra  │
└──────────────────────┘                                       │   ▼                              │
                                                               │ MariaDB 10.11 (horarios,         │
 Navegador (admin / docente) ──── HTTPS ──────────────────────▶│  asistencia, triggers, eventos)  │
   panel, video en vivo, popups                                └──────────────────────────────────┘
```

| Componente | Responsabilidad |
|---|---|
| ESP32-CAM | Capturar fotogramas, reconectarse sola (Wi-Fi y servidor), encender LED según la orden del servidor, actualizarse por OTA |
| VPS | Reconocimiento facial, validación del horario, cálculo de cumple/tardío/demorado, base de datos, panel web, video en vivo |

### Por qué WebSocket seguro (WSS)

- **Atraviesa el NAT del aula.** La cámara abre una conexión **saliente** por el puerto 443, como un navegador. No hay que abrir puertos en el router de la universidad.
- **Usa poca memoria.** El cifrado TLS cuesta 1–2 s y unos 40 KB de RAM en la ESP32. Con WebSocket se negocia **una vez** y la conexión queda abierta. Con HTTP POST se pagaría en cada foto.
- **Va en ambos sentidos.** El servidor responde `GREEN`/`RED` por el mismo canal en milisegundos. También le ordena cuándo transmitir y le avisa de firmware nuevo.
- **No necesita más servicios.** MQTT obligaría a instalar y mantener un broker (Mosquitto) sin ninguna ventaja para este caso.

### Protocolo

| Dirección | Mensaje | Significado |
|---|---|---|
| ESP32 → VPS | encabezados `Authorization: Bearer <token>`, `X-Dispositivo: <id>` | autenticación al conectar a `/ws/dispositivo` |
| ESP32 → VPS | binario | un fotograma JPEG |
| ESP32 → VPS | `HELLO fw=1.0.0 ip=… rssi=…` / `STAT …` | estado de la cámara (se ve en el panel) |
| VPS → ESP32 | `GREEN` / `RED` / `OFF` | LED de resultado |
| VPS → ESP32 | `MODE ACTIVE` / `MODE SLEEP` | transmitir (franja de clase) o reposo |
| VPS → ESP32 | `CFG intervalo=100 tam=VGA calidad=12` | velocidad y calidad de captura |
| VPS → ESP32 | `OTA 1.0.1` | descargar firmware nuevo de `/api/dispositivo/firmware` |

La cámara **solo transmite en las franjas de clase de su aula**: desde 15 minutos antes del inicio (`minutos_antes_ingreso`) hasta el fin. El resto del tiempo queda conectada en reposo y casi no consume datos.

---

## 2. VPS recomendada (Clouding u otro proveedor)

Para **1 cámara** basta con lo mínimo:

| Recurso | Recomendado | Por qué |
|---|---|---|
| CPU | 1 vCPU | El reconocimiento LBPH de 1 cámara a ≤10 fps usa una fracción de un núcleo |
| RAM | 2 GB | MariaDB (~250 MB) + OpenCV (~300 MB) + Caddy. El instalador agrega 2 GB de swap como colchón |
| Disco | 20–25 GB SSD | Sistema + imágenes Docker (~2 GB) + base y fotos (MB) + respaldos |
| Sistema | Ubuntu 24.04 LTS (o 22.04) | Probado así |
| Red | IPv4 pública | Necesaria para el certificado HTTPS gratuito |

- Esta configuración suele ser de las más baratas del catálogo. Revisa el precio exacto en el configurador del proveedor y si incluye tráfico ilimitado.
- **Consumo de datos estimado.** Unas 5 fotos por segundo de unos 5 KB: unos 90 MB por hora, **solo durante las clases**. Con 6 h de clase al día son unos 550 MB diarios.
- **Latencia.** Si el centro de datos está en Europa y el aula en Colombia, la respuesta del LED tarda alrededor de medio segundo más. Para control de asistencia es aceptable.
- Al crear la VPS elige **Ubuntu 24.04** y anota la **IP pública** y la **contraseña de root**.

### Sin dominio: qué se hace

No hace falta comprar uno. El sistema usa **sslip.io**, un servicio DNS público y gratuito: el nombre `45-12-34-56.sslip.io` apunta automáticamente a la IP `45.12.34.56`. Con ese nombre, Caddy obtiene un certificado HTTPS real de Let's Encrypt y lo renueva solo. El navegador no muestra advertencias y la cámara puede verificar el certificado.

Si más adelante compras un dominio:
1. Crea un registro DNS `A` que apunte a la IP de la VPS.
2. Escribe el dominio en `$DOMINIO` (en `desplegar.ps1`).
3. Repite `.\desplegar.cmd vps`.
4. En el portal de la cámara cambia el campo *Servidor*.

---

## 3. Puesta en marcha (orden exacto)

### 3.1 En el PC: configurar

Abre `desplegar.ps1` y edita **solo** la sección `CONFIGURACION`:

```powershell
$VPS_IP          = "45.12.34.56"     # IP pública de la VPS
$VPS_USUARIO     = "root"
$DISPOSITIVO_ID  = "ESP32-CAM-01"    # igual al del panel (Infraestructura › Dispositivos)
$WIFI_SSID       = ""                # opcional: si lo dejas vacío, se configura en el portal
$PORTAL_CLAVE    = "asistencia2026"  # clave de la red del portal (mínimo 8)
```

El resto ya trae valores adecuados. La cámara envía fotos CIF (400×296) con calidad 15, de unos 5 KB, tan rápido como lo permite la red (`$CAM_INTERVALO_MS = 150`). Mientras envía una, ya captura la siguiente (firmware 1.0.8). Medido con la VPS en España: **unas 5 fotos por segundo** con CIF, contra 1,5 con VGA. Una foto de 5 KB cabe en un solo viaje de red (unos 190 ms) y una VGA de 17 KB necesita tres. En la prueba de reconocimiento, CIF acertó igual que VGA. El servidor tarda unos 40 ms en reconocer cada foto.

### 3.2 Revisar el entorno

En PowerShell, dentro de la carpeta del proyecto:

```powershell
.\desplegar.cmd verificar
```

Comprueba SSH, tar y mysqldump. La primera vez descarga `arduino-cli` en `tools\` e instala la librería WiFiManager. Usa el mismo core ESP32 2.0.14 que ya tienes en Arduino IDE.

### 3.3 Instalar la VPS y migrar los datos (un solo comando)

Deja **MySQL encendido en XAMPP** para que se exporten tus datos, y ejecuta:

```powershell
.\desplegar.cmd vps
```

1. Pide **una sola vez** la contraseña de root e instala una llave SSH. Desde ahí ya no la vuelve a pedir.
2. Exporta tu base `universidad_v2` (estudiantes, grupos, horarios, matrículas, usuarios, asistencias) y las fotos de `rostros_referencia`.
3. En la VPS instala Docker, el firewall, fail2ban y swap. Arranca MariaDB, importa los datos y levanta la aplicación con HTTPS.
4. Al final muestra la URL del panel. El usuario admin es el mismo que usabas en el PC.

La primera vez tarda entre 5 y 10 minutos. Repetirlo es seguro: actualiza el código y **no** vuelve a importar ni borra datos.

### 3.4 Revisar en el panel

1. Entra a `https://<ip-con-guiones>.sslip.io`.
2. En *Infraestructura › Dispositivos*, verifica que `ESP32-CAM-01` esté en el **aula correcta**. En tus datos actuales está en *Sala 1*, pero la clase de prueba está en *Sala 2*. Con esa combinación la cámara respondería "Aula equivocada".
3. Revisa que el periodo esté **Activo** y que el grupo tenga horario en esa aula.

### 3.5 Grabar la ESP32-CAM (solo la primera vez, por USB)

```powershell
.\desplegar.cmd firmware
```

1. Genera el token de la cámara en la VPS y lo graba en el firmware junto con el servidor y el identificador.
2. Compila con la partición **Minimal SPIFFS (OTA)**.
3. Pide poner la placa en modo programación: **GPIO0 a GND + RESET**. Con la base ESP32-CAM-MB: mantener IO0 y pulsar RST.
4. Graba y **borra la flash completa**, así no quedan configuraciones viejas.

Después: quita el puente GPIO0–GND y pulsa RESET.

### 3.6 Configurar el Wi-Fi del aula (sin PC)

Si no precargaste `$WIFI_SSID`, la cámara abre la red **`ASISTENCIA-XXXX`** (LED verde y rojo alternando):

1. Desde el celular, conéctate a esa red con la clave `$PORTAL_CLAVE`.
2. Si no se abre sola la página, entra a **http://192.168.4.1** y luego a **Configure WiFi**.
3. Elige la red del aula y escribe su contraseña. Servidor, identificador y token ya vienen precargados. **Save**.
4. La cámara se reinicia y se conecta. Con los LED apagados, está lista.

Para **cambiar de red o de servidor** más adelante: si la red guardada ya no está, la cámara abre el portal sola al encender. Si la red sigue existiendo, vuelve a grabarla por USB con `$WIFI_SSID` y `$WIFI_CLAVE` nuevos (`.\desplegar.cmd firmware`). El botón no abre el portal: con el Wi-Fi transmitiendo, su cable capta ruido y provocaba reinicios.

### 3.7 Prueba

1. En el panel, abre *En vivo*. Debe decir **Conectada**.
2. Si no hay clase ahora, pulsa **Encender 10 min (probar encuadre)** para ver el video.
3. Durante una clase, párate frente a la cámara. Aparece el popup con el escenario y se enciende el LED.

---

## 4. Escenarios de llegada (popups y LED)

Al reconocer a una persona, el servidor busca **su horario** del día y lo compara con la hora actual usando la tabla `parametros`:

| Parámetro | Valor | Efecto |
|---|---|---|
| `minutos_antes_ingreso` | 15 | El ingreso se habilita 15 min antes del inicio (y la cámara se enciende) |
| `minutos_tolerancia_tardio` | 5 | De 1 a 5 min después del inicio es *tardío*; luego, *demorado* |

| Escenario | LED | Popup | Asistencia |
|---|---|---|---|
| Llega 1–15 min antes del inicio | Verde | **Llegó temprano**: "N min antes del inicio" | cumple |
| Llega en el minuto de inicio | Verde | **Llegó a tiempo** | cumple |
| Llega 1–5 min después | Verde | **Llegó tarde** (tolerancia 5 min) | tardío |
| Llega después de la tolerancia | Verde | **Llegó demorado** | demorado |
| Ya tenía ingreso hoy | Verde | **Ya estaba registrado** (se conserva la primera hora) | sin cambio |
| Tiene clase hoy en esta aula, pero falta más de 15 min | Rojo | **Llegó muy temprano**: indica a qué hora abre el ingreso | — |
| No tiene clase en este lapso | Rojo | **Sin asignaturas en este horario**: "No tiene asignaturas vinculadas para este horario" y su próxima clase de hoy, si tiene | — |
| Su clase en esta aula ya terminó | Rojo | **Sin asignaturas en este horario**: dice a qué hora terminó | — |
| Hay clase en el aula, pero no está matriculado | Rojo | **No matriculado en esta clase** | — |
| Su clase de ahora es en otra aula | Rojo | **Aula equivocada**: indica el aula correcta | — |
| Estudiante retirado o inactivo | Rojo | **Estudiante inactivo** | — |
| Rostro desconocido | Rojo | **Rostro no reconocido** | — |

Los popups los ven el administrador y el docente dueño de la clase en curso. Para cambiar los minutos, modifica la tabla `parametros` (ver sección 6); no hay que tocar código.

---

## 5. Qué hace el instalador (guía manual paso a paso)

`deploy/instalar_vps.sh` ejecuta lo siguiente. Si prefieres hacerlo a mano, como root en la VPS:

```bash
# 1. Zona horaria (la asistencia se calcula con la hora del servidor)
timedatectl set-timezone America/Bogota

# 2. Paquetes y Docker
apt-get update && apt-get install -y ca-certificates curl openssl ufw fail2ban cron docker.io docker-compose-v2
systemctl enable --now docker fail2ban

# 3. Swap de 2 GB (VPS con poca RAM)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 4. Firewall: solo SSH, HTTP y HTTPS
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

# 5. Carpetas (el código va en /opt/asistencia/app)
mkdir -p /opt/asistencia/{datos/rostros_referencia,datos/firmware,mariadb,caddy/data,caddy/config,respaldos}
cp /opt/asistencia/app/deploy/{docker-compose.yml,Caddyfile} /opt/asistencia/
chown -R 10001:10001 /opt/asistencia/datos

# 6. Secretos en /opt/asistencia/.env (chmod 600)
#    DB_NAME, DB_USER, DB_PASSWORD, DB_ROOT_PASSWORD, SECRET_KEY, ADMIN_USUARIO, ADMIN_CLAVE,
#    DOMINIO, CAM_INTERVALO_MS, CAM_RESOLUCION, CAM_CALIDAD, CAM_SIEMPRE_ACTIVA,
#    UMBRAL_CONFIANZA, ESPERA_MISMO_ROSTRO  (contraseñas con: openssl rand -hex 24)

# 7. Base de datos e importación de la migración
cd /opt/asistencia
docker compose up -d --wait db
docker compose exec -T -e MYSQL_PWD="$DB_ROOT_PASSWORD" db mariadb -uroot universidad_v2 < dump.sql

# 8. Aplicación + HTTPS
docker compose up -d --build

# 9. Respaldo diario a las 02:30
echo "30 2 * * * root /opt/asistencia/app/deploy/respaldar.sh" > /etc/cron.d/asistencia-respaldo
```

Al arrancar, el contenedor `app` ejecuta `preparar_bd.py --auto`. Aplica el esquema de forma idempotente (por ejemplo, la columna `token_hash` y los procedimientos nuevos) y crea el administrador solo si no existe ninguno.

---

## 6. Operación diaria

Todos los comandos se ejecutan en la VPS dentro de `cd /opt/asistencia`:

| Tarea | Comando |
|---|---|
| Ver estado | `docker compose ps` |
| Ver logs en vivo | `docker compose logs -f app` |
| Reiniciar la app | `docker compose restart app` |
| Actualizar el código | en el PC: `.\desplegar.cmd vps` |
| Respaldo manual | `./app/deploy/respaldar.sh` (queda en `respaldos/`) |
| Restaurar un respaldo | `gunzip -c respaldos/bd_FECHA.sql.gz \| docker compose exec -T -e MYSQL_PWD=<root> db mariadb -uroot universidad_v2` |
| Nueva clave del admin | `docker compose exec app python herramientas.py clave admin` |
| Nuevo token de cámara | en el panel: *Dispositivos › Nuevo token*, o en el PC: `.\desplegar.cmd token` |
| Cambiar minutos de tolerancia | `docker compose exec db mariadb -u root -p universidad_v2 -e "UPDATE parametros SET valor=10 WHERE clave='minutos_tolerancia_tardio'"` |

### Velocidad y calidad de la cámara

En `desplegar.ps1` cambia `$CAM_INTERVALO_MS` (150 recomendado: la cámara envía tan rápido como permita la red), `$CAM_RESOLUCION`, `$CAM_CALIDAD` o `$CAM_POTENCIA_WIFI`, y ejecuta `.\desplegar.cmd vps`. La cámara recibe los valores nuevos al reconectarse, sin grabarla otra vez.

También puedes editar `/opt/asistencia/.env` y ejecutar `docker compose up -d`.

### Actualizar el firmware por Wi-Fi (OTA)

1. Sube `$FW_VERSION` en `desplegar.ps1` (por ejemplo `1.0.0` → `1.0.1`).
2. Ejecuta `.\desplegar.cmd ota`.

Las cámaras conectadas descargan la versión nueva en menos de 1 minuto (LED verde parpadeando rápido) y se reinician solas. La configuración (Wi-Fi, token) se conserva.

---

## 7. Seguridad aplicada

- **La cámara se autentica** con un token aleatorio de 256 bits. En la base solo se guarda su SHA-256. El token se puede revocar desde el panel; la conexión vieja se corta al instante.
- **Se verifica el certificado del servidor.** El firmware incluye las CA raíz de Let's Encrypt (ISRG Root X1/X2) y la raíz real de la VPS, así nadie puede suplantarla en la red del aula. Si alguna vez falla la verificación, el portal permite elegir *seguridad 1* (cifrado sin verificar) sin necesidad de USB.
- **Solo están expuestos 22, 80 y 443.** MariaDB y la app no publican puertos; solo existen en la red interna de Docker. Ojo: Docker se salta UFW en los puertos que **publica**. Por eso solo Caddy publica.
- **Panel solo por HTTPS** con HSTS. La cookie de sesión es `Secure` y `HttpOnly`. El login se bloquea 5 minutos tras 5 intentos fallidos.
- **fail2ban** bloquea ataques de fuerza bruta a SSH. Recomendado: desactivar el login por contraseña de root una vez que la llave funcione (`PasswordAuthentication no` en `/etc/ssh/sshd_config`).
- **Los secretos viven en `/opt/asistencia/.env`** (permisos 600), no en el código. `desplegar.ps1` deja vacío `config_defaults.h` después de cada compilación.
- Respaldos diarios de base y fotos, con retención de 14 días.

---

## 8. Solución de problemas

| LED de la cámara | Significado | Qué revisar |
|---|---|---|
| Rojo parpadeo lento | Sin Wi-Fi | Red del aula; tras 3 min se reinicia y abre el portal |
| Verde y rojo a la vez, parpadeando | Wi-Fi sí, VPS no | Token y servidor en el portal; `docker compose logs app` ("Conexión de cámara rechazada" = token o identificador mal) |
| Verde/rojo alternando | Portal abierto | Conéctate a `ASISTENCIA-XXXX` |
| Rojo parpadeo rápido | Error de cámara | Cable plano de la OV2640 y fuente de 5 V / 2 A |
| Apagados | Conectada (en clase o en reposo) | Normal |

| Problema | Causa probable |
|---|---|
| El panel no abre por HTTPS | La primera emisión del certificado tarda 1–2 min. Revisa `docker compose logs caddy` y que los puertos 80/443 estén abiertos en el firewall del proveedor |
| "Aula equivocada" para todos | El dispositivo está en un aula distinta a la del horario |
| Hora corrida 5 h | `timedatectl` de la VPS. La base usa `-05:00` fijo |
| La cámara se reinicia sola | Fuente débil (brownout): usa 5 V / 2 A y cables cortos |
| La OTA no se aplica | El firmware inicial debe grabarse con `desplegar.cmd firmware` (partición OTA). El mensaje queda en *En vivo › Firmware* y en los logs |

### Video lento o que se corta

Diagnóstico hecho con la ESP32-CAM real (noviembre 2026):

- **La radio Wi-Fi de la placa pierde paquetes por momentos**, aunque reporte buena señal (-45 dBm). El router y el PC, en la misma red, no pierden nada. Alimentarla desde el USB del PC lo empeora; un cargador de celular de 5 V / 2 A ayuda.
- **Causa principal: el reloj de la cámara (XCLK) a 10 MHz mete ruido en el Wi-Fi de la propia placa.** Era el valor del firmware original. Con 10 MHz la placa perdía el 80-100 % de los paquetes y no lograba enviar ninguna foto; con 20 MHz el Wi-Fi queda casi igual que sin cámara. El firmware 1.0.6 usa 20 MHz (`XCLK_CAMARA_HZ` en el `.ino`). **No lo vuelvas a bajar a 10 MHz.**
- **La cámara capturando sin parar retrasa al Wi-Fi**, porque su tarea corre en el mismo núcleo del procesador. Desde el firmware 1.0.5 captura solo cuando va a enviar una foto. La escala de grises (`CAM_GRIS = 1`) existe pero está desactivada: se ve peor y al reconocimiento le cuesta más.
- **La librería WebSockets cortaba la conexión si un envío tardaba más de 5 s.** El archivo `firmware/esp32cam_asistencia/build_opt.h` sube ese límite a 20 s, y los pings de control tienen más margen en la placa (3 × 10 s) y en el servidor (60 s).

Resultado con el firmware 1.0.6, en pruebas alternadas: con XCLK a 20 MHz, entre 50 y 150 fotos enviadas cada 110 s, sin cortes (unos 0,2-0,6 s por foto); con 10 MHz, 0 fotos.

Cómo medir el enlace de la placa desde el PC (debe dar pocas pérdidas y menos de ~50 ms):

```powershell
ping -n 40 -l 1400 IP_DE_LA_CAMARA
```

La IP aparece en el monitor serie (`Wi-Fi conectado. IP: ...`).

Si aun así se corta:

- Aleja los cables de la protoboard del extremo de la antena impresa (las pistas en zigzag).
- Alimenta la placa con un cargador y no con el PC.
- Baja `$CAM_CALIDAD` a 20.
