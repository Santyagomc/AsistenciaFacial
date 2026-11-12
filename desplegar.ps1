<#
=====================================================================
 ASISTENCIA FACIAL - Despliegue desde este PC (Windows)

 USO (desde la carpeta del proyecto):
   .\desplegar.cmd verificar   Revisa que todo este listo en el PC y la VPS
   .\desplegar.cmd vps         Instala / actualiza la VPS y migra los datos
   .\desplegar.cmd firmware    Compila y graba la ESP32-CAM por USB (1a vez)
   .\desplegar.cmd ota         Publica un firmware nuevo: las camaras se
                               actualizan solas por Wi-Fi
   .\desplegar.cmd token       Genera un token nuevo para la camara
   .\desplegar.cmd monitor     Abre el monitor serie de la ESP32-CAM
   .\desplegar.cmd todo        vps + firmware

 Lo UNICO que hay que editar es la seccion CONFIGURACION de abajo.
 (desplegar.cmd evita el bloqueo de ejecucion de scripts de PowerShell)
=====================================================================
#>
param([Parameter(Position = 0)][string]$Accion = "ayuda")

# ============================ CONFIGURACION ============================

# --- VPS (Clouding u otro proveedor, Ubuntu 22.04/24.04) ---
$VPS_IP          = "46.183.112.86"          # IP publica de la VPS, ej. "45.12.34.56"
$VPS_USUARIO     = "root"      # usuario SSH (root recomendado en una VPS nueva)
$VPS_PUERTO_SSH  = 22
$DOMINIO         = ""          # vacio = <ip-con-guiones>.sslip.io (HTTPS gratis, sin comprar dominio)

# --- Migracion de datos desde XAMPP (este PC) ---
$MIGRAR_DATOS    = "auto"      # auto = solo si la base de la VPS esta vacia | no | forzar (reemplaza)
$MYSQLDUMP       = "C:\xampp\mysql\bin\mysqldump.exe"
$DB_LOCAL        = "universidad_v2"
$DB_LOCAL_USUARIO = "root"
$DB_LOCAL_CLAVE  = ""

# --- Parametros de la camara (la VPS se los envia a la ESP32-CAM) ---
$CAM_INTERVALO_MS = 150        # ms minimos entre fotos. 150 = la camara envia tan rapido como permita la red (~3-5 fotos/s)
$CAM_RESOLUCION  = "CIF"       # CIF (400x296) recomendado: ~5 fotos/s. VGA: ~1,5 fotos/s por Internet
$CAM_CALIDAD     = 15          # JPEG: 10 (mejor, mas pesado) a 40 (mas liviano). 15 = fotos de ~6-7 KB
$CAM_GRIS        = 0           # 0 = a color (recomendado). 1 = escala de grises (mas liviano, pero se ve peor)
$CAM_POTENCIA_WIFI = 0         # dBm del Wi-Fi de la camara (2-19.5). 0 = maxima. Bajala si pierde paquetes
$UMBRAL_CONFIANZA = 60         # reconocimiento: mas bajo = mas estricto

# --- Firmware de la ESP32-CAM ---
$FW_VERSION      = "1.0.8"     # subelo (1.0.1, 1.0.2...) antes de cada "ota"
$DISPOSITIVO_ID  = "ESP32-CAM-01"  # igual al identificador del panel (Infraestructura > Dispositivos)
$DISPOSITIVO_TOKEN = ""        # vacio = se genera en la VPS al grabar por USB
$SERVIDOR_PUERTO = 443
$SEGURIDAD_TLS   = 2           # 2 = HTTPS verificado | 1 = HTTPS sin verificar | 0 = sin cifrar
$WIFI_SSID       = "CANGREJO"          # opcional: precarga el Wi-Fi (si no, se configura en el portal)
$WIFI_CLAVE      = "65812983"
$PORTAL_CLAVE    = "asistencia2026"  # clave de la red "ASISTENCIA-XXXX" del portal (minimo 8)
$PUERTO_COM      = ""          # vacio = detectar (ej. "COM5")

# =======================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RAIZ      = Split-Path -Parent $MyInvocation.MyCommand.Path
$SKETCH    = Join-Path $RAIZ "firmware\esp32cam_asistencia"
$BUILD     = Join-Path $RAIZ "firmware\build"
$TOOLS     = Join-Path $RAIZ "tools"
$FQBN_OTA  = "esp32:esp32:esp32cam:PartitionScheme=min_spiffs,DebugLevel=error"
$FQBN_USB  = "esp32:esp32:esp32cam:PartitionScheme=min_spiffs,EraseFlash=all,DebugLevel=error"
$CORE      = "esp32:esp32@2.0.14"
$URL_ESP32 = "https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json"
$LIBRERIAS = @("WiFiManager@2.0.17", "WebSockets@2.7.2")
$SSH_KEY   = Join-Path $HOME ".ssh\id_ed25519"
$TAR       = Join-Path $env:SystemRoot "System32\tar.exe"

function Titulo($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Ok($t)     { Write-Host "    $t" -ForegroundColor Green }
function Aviso($t)  { Write-Host "    AVISO: $t" -ForegroundColor Yellow }
function Falla($t)  { Write-Host ""; Write-Host "ERROR: $t" -ForegroundColor Red; exit 1 }

function Revisar($que) {
    if ($LASTEXITCODE -ne 0) { Falla "$que (codigo $LASTEXITCODE)" }
}

# ----------------------------- VPS / SSH -----------------------------

function Get-Dominio {
    if ($DOMINIO) { return $DOMINIO }
    return (($VPS_IP -replace '\.', '-') + ".sslip.io")
}

function Assert-Vps {
    if (-not $VPS_IP) { Falla "Escribe la IP de la VPS en `$VPS_IP (seccion CONFIGURACION de desplegar.ps1)." }
    if ($VPS_IP -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { Falla "`$VPS_IP no parece una IP valida: $VPS_IP" }
    foreach ($h in @("ssh", "scp")) {
        if (-not (Get-Command $h -ErrorAction SilentlyContinue)) {
            Falla "Falta '$h'. Instala el Cliente OpenSSH: Configuracion > Aplicaciones > Caracteristicas opcionales > Cliente OpenSSH."
        }
    }
}

# -n: no leer el teclado. Sin esto, el ssh de Windows se queda esperando al
# terminar el comando en algunas terminales (por ejemplo la de VS Code).
function Get-SshArgs { return @("-n", "-p", "$VPS_PUERTO_SSH", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", "-i", $SSH_KEY) }
function Get-Sudo { if ($VPS_USUARIO -eq "root") { return "" } return "sudo " }

function Invoke-Vps([string]$comando, [switch]$Capturar) {
    $a = (Get-SshArgs) + @("$VPS_USUARIO@$VPS_IP", $comando)
    if ($Capturar) { $out = & ssh @a; return $out }
    & ssh @a
}

function Ensure-SshKey {
    Titulo "Acceso SSH a la VPS ($VPS_USUARIO@$VPS_IP)"
    if (-not (Test-Path $SSH_KEY)) {
        New-Item -ItemType Directory -Force (Split-Path $SSH_KEY) | Out-Null
        & ssh-keygen -t ed25519 -N '""' -q -f $SSH_KEY -C "asistencia-despliegue"
        Revisar "No se pudo crear la llave SSH"
        Ok "llave SSH creada en $SSH_KEY"
    }
    $a = (Get-SshArgs) + @("-o", "BatchMode=yes", "$VPS_USUARIO@$VPS_IP", "echo ok")
    $ErrorActionPreference = "Continue"   # en PS 5.1, stderr redirigido no debe detener el script
    $r = & ssh @a 2>$null
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -eq 0 -and "$r" -match "ok") { Ok "acceso por llave correcto"; return }
    Write-Host "    Se instalara la llave SSH en la VPS. Escribe UNA vez la contrasena de $VPS_USUARIO (la de Clouding):" -ForegroundColor Yellow
    $pub = Get-Content "$SSH_KEY.pub" -Raw
    $pub | & ssh -p $VPS_PUERTO_SSH -o StrictHostKeyChecking=accept-new "$VPS_USUARIO@$VPS_IP" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" | Out-Host
    Revisar "No se pudo instalar la llave SSH (revisa IP, usuario y contrasena)"
    Ok "llave instalada: ya no se pedira la contrasena"
}

# ----------------------------- Accion: vps -----------------------------

function Export-BaseLocal($destino) {
    # Si se pidio migrar y no se puede leer la base local, se detiene: antes solo
    # avisaba y la VPS quedaba sin estudiantes ni horarios sin que se notara.
    $sinMigrar = 'Si la VPS ya tiene tus datos y solo actualizas el codigo, pon $MIGRAR_DATOS = "no".'
    if (-not (Test-Path $MYSQLDUMP)) {
        Falla "No encuentro ${MYSQLDUMP} (revisa `$MYSQLDUMP). $sinMigrar"
    }
    $dump = Join-Path $destino "dump.sql"
    $dumpArgs = @("-u", $DB_LOCAL_USUARIO, "--single-transaction", "--routines", "--triggers", "--events",
                  "--default-character-set=utf8mb4", "--skip-dump-date", "--result-file=$dump", $DB_LOCAL)
    if ($DB_LOCAL_CLAVE) { $dumpArgs = @("--password=$DB_LOCAL_CLAVE") + $dumpArgs }
    & $MYSQLDUMP @dumpArgs | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Falla "No se pudo leer la base local '$DB_LOCAL'. Enciende MySQL en el panel de XAMPP y repite. $sinMigrar"
    }
    # Quita DEFINER=`usuario`@`host`: los procedimientos se crean con el usuario de la VPS
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $txt = [IO.File]::ReadAllText($dump, $utf8)
    $txt = [regex]::Replace($txt, 'DEFINER=`[^`]*`@`[^`]*`', '')
    [IO.File]::WriteAllText($dump, $txt, $utf8)
    Ok ("base '$DB_LOCAL' exportada ({0:N0} KB)" -f ((Get-Item $dump).Length / 1KB))
    $fotos = Join-Path $RAIZ "server\rostros_referencia"
    if (Test-Path $fotos) {
        Copy-Item $fotos (Join-Path $destino "rostros_referencia") -Recurse
        Ok ("fotos de referencia: {0} estudiantes" -f (Get-ChildItem $fotos -Directory).Count)
    }
    return $true
}

function Accion-Vps {
    Assert-Vps
    Ensure-SshKey
    $dom = Get-Dominio

    Titulo "Empaquetando el codigo"
    $tmp = Join-Path $env:TEMP ("asistencia_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $paquete = Join-Path $tmp "paquete.tgz"
    $conMigracion = $false
    if ($MIGRAR_DATOS -ne "no") {
        $mig = Join-Path $tmp "migracion"
        New-Item -ItemType Directory -Force $mig | Out-Null
        $conMigracion = Export-BaseLocal $mig
    }
    $tarArgs = @("-czf", $paquete,
                 "--exclude", "server/venv", "--exclude", "*/__pycache__",
                 "--exclude", "server/rostros_referencia", "--exclude", "server/secreto.key",
                 "-C", $RAIZ, "server", "frontend", "deploy")
    if ($conMigracion) { $tarArgs += @("-C", $tmp, "migracion") }
    & $TAR @tarArgs
    Revisar "No se pudo crear el paquete"
    Ok ("paquete listo ({0:N1} MB)" -f ((Get-Item $paquete).Length / 1MB))

    Titulo "Subiendo a la VPS"
    $sa = @("-P", "$VPS_PUERTO_SSH", "-o", "StrictHostKeyChecking=accept-new", "-i", $SSH_KEY, $paquete, "$VPS_USUARIO@${VPS_IP}:/tmp/asistencia_paquete.tgz")
    & scp @sa
    Revisar "No se pudo subir el paquete"
    Ok "subido"

    Titulo "Instalando en la VPS (la primera vez tarda 5-10 minutos)"
    $s = Get-Sudo
    $vars = "DOMINIO=$dom MIGRAR=$MIGRAR_DATOS CAM_INTERVALO_MS=$CAM_INTERVALO_MS CAM_RESOLUCION=$CAM_RESOLUCION " +
            "CAM_CALIDAD=$CAM_CALIDAD CAM_POTENCIA_WIFI=$CAM_POTENCIA_WIFI CAM_GRIS=$CAM_GRIS UMBRAL_CONFIANZA=$UMBRAL_CONFIANZA"
    $cmd = "set -e; ${s}rm -rf /opt/asistencia/_entrante; ${s}mkdir -p /opt/asistencia/_entrante; " +
           "${s}tar -xzf /tmp/asistencia_paquete.tgz -C /opt/asistencia/_entrante; rm -f /tmp/asistencia_paquete.tgz; " +
           "${s}sed -i " + '''s/\r$//''' + " /opt/asistencia/_entrante/deploy/instalar_vps.sh; " +
           "${s}env $vars bash /opt/asistencia/_entrante/deploy/instalar_vps.sh"
    Invoke-Vps $cmd
    Revisar "La instalacion en la VPS fallo (mira los mensajes de arriba)"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

    Titulo "Comprobando desde este PC"
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 "https://$dom/api/salud"
        Ok "https://$dom responde: $($r.Content)"
    } catch { Aviso "https://$dom aun no responde desde aqui ($($_.Exception.Message)). Espera 1-2 minutos y abrelo en el navegador." }
    Write-Host ""
    Write-Host "  Panel: https://$dom" -ForegroundColor Green
    Write-Host "  Siguiente paso: .\desplegar.cmd firmware   (con la ESP32-CAM conectada por USB)" -ForegroundColor Green
}

# ----------------------------- Arduino CLI -----------------------------

function Get-ArduinoCli {
    $local = Join-Path $TOOLS "arduino-cli\arduino-cli.exe"
    if (Test-Path $local) { return $local }
    $g = Get-Command arduino-cli -ErrorAction SilentlyContinue
    if ($g) { return $g.Source }
    Titulo "Descargando arduino-cli (una sola vez)"
    $dir = Split-Path $local
    New-Item -ItemType Directory -Force $dir | Out-Null
    $zip = Join-Path $dir "arduino-cli.zip"
    Invoke-WebRequest -UseBasicParsing "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $dir -Force
    Remove-Item $zip
    if (-not (Test-Path $local)) { Falla "No se pudo instalar arduino-cli" }
    Ok "arduino-cli en $local"
    return $local
}

function Ensure-Entorno($cli) {
    Titulo "Entorno de compilacion (usa el mismo core y librerias que Arduino IDE)"
    $cores = (& $cli core list) -join "`n"
    if ($cores -notmatch "esp32:esp32\s+2\.0\.14") {
        Aviso "instalando $CORE (puede tardar varios minutos)"
        & $cli core update-index --additional-urls $URL_ESP32 | Out-Host; Revisar "core update-index"
        & $cli core install $CORE --additional-urls $URL_ESP32 | Out-Host; Revisar "core install"
    }
    Ok "core $CORE"
    $libs = (& $cli lib list) -join "`n"
    $indice = $false
    foreach ($l in $LIBRERIAS) {
        $n, $v = $l -split "@"
        if ($libs -notmatch "(?m)^$n\s+$([regex]::Escape($v))") {
            if (-not $indice) {   # regenera el indice de librerias (puede estar vacio o danado)
                & $cli lib update-index | Out-Host; Revisar "lib update-index"
                $indice = $true
            }
            & $cli lib install $l | Out-Host; Revisar "lib install $l"
        }
        Ok "libreria $l"
    }
}

function Cadena-C([string]$s) { return '"' + ($s -replace '\\', '\\' -replace '"', '\"') + '"' }

function Escribir-Defaults([hashtable]$v) {
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $h = @"
// GENERADO por desplegar.ps1 - se deja vacio al terminar de compilar.
// Valores del primer arranque; despues manda lo guardado en el portal.
#define DEF_WIFI_SSID    $(Cadena-C $v.ssid)
#define DEF_WIFI_CLAVE   $(Cadena-C $v.clave)
#define DEF_SERVIDOR     $(Cadena-C $v.servidor)
#define DEF_PUERTO       $SERVIDOR_PUERTO
#define DEF_SEGURIDAD    $SEGURIDAD_TLS
#define DEF_DISPOSITIVO  $(Cadena-C $v.id)
#define DEF_TOKEN        $(Cadena-C $v.token)
#define DEF_PORTAL_CLAVE $(Cadena-C $PORTAL_CLAVE)
"@
    [IO.File]::WriteAllText((Join-Path $SKETCH "config_defaults.h"), $h, $utf8)
    [IO.File]::WriteAllText((Join-Path $SKETCH "version.h"),
        "// GENERADO por desplegar.ps1`n#define FW_VERSION $(Cadena-C $FW_VERSION)`n", $utf8)
}

function Pem($cert) {
    $b64 = [Convert]::ToBase64String($cert.RawData, [Base64FormattingOptions]::InsertLineBreaks)
    return "-----BEGIN CERTIFICATE-----`n" + ($b64 -replace "`r", "") + "`n-----END CERTIFICATE-----`n"
}

function Escribir-Certificados {
    Titulo "Certificados raiz para verificar la VPS"
    $pems = New-Object System.Collections.Generic.List[string]
    foreach ($u in @("https://letsencrypt.org/certs/isrgrootx1.pem", "https://letsencrypt.org/certs/isrg-root-x2.pem")) {
        try {
            $f = Join-Path $env:TEMP "ca_asistencia.pem"
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 $u -OutFile $f
            $t = (Get-Content $f -Raw) -replace "`r", ""
            if ($t -match "BEGIN CERTIFICATE") { $pems.Add($t.Trim() + "`n"); Ok ("descargado " + (Split-Path $u -Leaf)) }
        } catch { Aviso "no se pudo descargar $u" }
    }
    # Raiz real que presenta la VPS ahora mismo (cubre cambios de cadena de Let's Encrypt)
    if ($VPS_IP) {
        $dom = Get-Dominio
        try {
            $tcp = New-Object Net.Sockets.TcpClient($dom, 443)
            $ssl = New-Object Net.Security.SslStream($tcp.GetStream(), $false, ({ $true }))
            $ssl.AuthenticateAsClient($dom)
            $hoja = New-Object Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
            $cadena = New-Object Security.Cryptography.X509Certificates.X509Chain
            [void]$cadena.Build($hoja)
            $raiz = $cadena.ChainElements[$cadena.ChainElements.Count - 1].Certificate
            $ssl.Dispose(); $tcp.Dispose()
            $p = Pem $raiz
            if (-not ($pems | Where-Object { $_.Trim() -eq $p.Trim() })) { $pems.Add($p) }
            Ok "raiz de https://$dom : $($raiz.GetNameInfo('SimpleName', $false))"
        } catch { Aviso "no se pudo leer el certificado de https://$dom (la VPS aun no esta lista?)" }
    }
    if ($pems.Count -eq 0) {
        if ($SEGURIDAD_TLS -eq 2) { Falla "Sin certificados CA no se puede compilar con SEGURIDAD_TLS = 2. Revisa la conexion a Internet." }
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $h = "// GENERADO por desplegar.ps1 (" + (Get-Date -Format "yyyy-MM-dd") + "): CA raiz para verificar la VPS`n" +
         "static const char CA_BUNDLE[] = R""PEM(`n" + ($pems -join "") + ")PEM"";`n"
    [IO.File]::WriteAllText((Join-Path $SKETCH "certificados.h"), $h, $utf8)
    Ok "$($pems.Count) certificado(s) incluidos"
}

function Compilar($cli, $fqbn, [hashtable]$defaults) {
    Titulo "Compilando firmware $FW_VERSION"
    Escribir-Certificados
    Escribir-Defaults $defaults
    try {
        & $cli compile --fqbn $fqbn --output-dir $BUILD $SKETCH | Out-Host
        Revisar "La compilacion fallo"
    } finally {
        Escribir-Defaults @{ ssid = ""; clave = ""; servidor = ""; id = $DISPOSITIVO_ID; token = "" }
    }
    $bin = Join-Path $BUILD "esp32cam_asistencia.ino.bin"
    if (-not (Test-Path $bin)) { Falla "No se genero $bin" }
    Ok ("firmware: {0:N0} KB de 1.920 KB disponibles" -f ((Get-Item $bin).Length / 1KB))
    return $bin
}

function Get-Token {
    if ($DISPOSITIVO_TOKEN) { return $DISPOSITIVO_TOKEN }
    Assert-Vps
    Ensure-SshKey
    Titulo "Generando token para '$DISPOSITIVO_ID' en la VPS"
    Aviso "si otra camara usaba este identificador, dejara de conectarse"
    $out = Invoke-Vps "cd /opt/asistencia && $(Get-Sudo)docker compose exec -T app python herramientas.py token '$DISPOSITIVO_ID' --crear" -Capturar
    Revisar "No se pudo generar el token (la VPS esta instalada? usa primero: .\desplegar.cmd vps)"
    $t = ($out | Where-Object { $_ -match '^[A-Za-z0-9_-]{40,}$' } | Select-Object -Last 1)
    if (-not $t) { Falla "La VPS no devolvio un token valido" }
    Ok "token generado"
    return $t
}

function Get-Puerto {
    if ($PUERTO_COM) { return $PUERTO_COM }
    $p = @([IO.Ports.SerialPort]::GetPortNames() | Sort-Object -Unique)
    if ($p.Count -eq 0) { Falla "No hay puertos COM. Conecta la ESP32-CAM (base ESP32-CAM-MB o adaptador USB-serie)." }
    if ($p.Count -eq 1) { return $p[0] }
    $r = Read-Host ("Puertos encontrados: " + ($p -join ", ") + ". Cual es la ESP32-CAM?")
    return $r.Trim().ToUpper()
}

# ----------------------------- Acciones de firmware -----------------------------

function Accion-Firmware {
    $cli = Get-ArduinoCli
    Ensure-Entorno $cli
    $token = Get-Token
    $servidor = if ($VPS_IP) { Get-Dominio } else { "" }
    if (-not $servidor) { Aviso "sin VPS configurada: el servidor se escribira en el portal de la camara" }
    $null = Compilar $cli $FQBN_USB @{ ssid = $WIFI_SSID; clave = $WIFI_CLAVE; servidor = $servidor; id = $DISPOSITIVO_ID; token = $token }

    $com = Get-Puerto
    Titulo "Grabando por USB en $com"
    Write-Host "    1) Pon la placa en modo programacion: GPIO0 (IO0) a GND y pulsa RESET." -ForegroundColor Yellow
    Write-Host "       Con la base ESP32-CAM-MB: manten IO0, pulsa RST y suelta IO0." -ForegroundColor Yellow
    Read-Host  "    2) Presiona Enter para grabar"
    & $cli upload -p $com --fqbn $FQBN_USB --input-dir $BUILD $SKETCH
    Revisar "No se pudo grabar (revisa el modo programacion y el puerto)"
    Ok "firmware grabado (se borro la flash: arranca con la configuracion nueva)"
    Write-Host ""
    Write-Host "    Quita el puente GPIO0-GND y pulsa RESET." -ForegroundColor Yellow
    if ($WIFI_SSID) {
        Write-Host "    La camara se conectara a '$WIFI_SSID' y luego a la VPS." -ForegroundColor Green
    } else {
        Write-Host "    La camara abrira la red Wi-Fi 'ASISTENCIA-XXXX' (clave: $PORTAL_CLAVE)." -ForegroundColor Green
        Write-Host "    Conectate con el celular, abre http://192.168.4.1 > Configure WiFi, elige la red del aula y guarda." -ForegroundColor Green
        Write-Host "    Servidor, identificador y token ya vienen precargados." -ForegroundColor Green
    }
    $m = Read-Host "    Abrir el monitor serie para ver el arranque? (s/N)"
    if ($m -match '^[sSyY]') { Accion-Monitor }
}

function Accion-Ota {
    Assert-Vps
    Ensure-SshKey
    $cli = Get-ArduinoCli
    Ensure-Entorno $cli
    $actual = (Invoke-Vps "cat /opt/asistencia/datos/firmware/version.txt 2>/dev/null || echo 0.0.0" -Capturar | Select-Object -Last 1)
    try { $va = [version]("$actual".Trim()); $vn = [version]$FW_VERSION } catch { Falla "Version invalida: '$FW_VERSION' o '$actual' (usa el formato 1.2.3)" }
    if ($vn -le $va) { Falla "La VPS ya publica la version $actual. Sube `$FW_VERSION (ej. $($va.Major).$($va.Minor).$($va.Build + 1)) y repite." }
    $bin = Compilar $cli $FQBN_OTA @{ ssid = ""; clave = ""; servidor = ""; id = $DISPOSITIVO_ID; token = "" }

    Titulo "Publicando firmware $FW_VERSION en la VPS"
    $s = Get-Sudo
    $sa = @("-P", "$VPS_PUERTO_SSH", "-o", "StrictHostKeyChecking=accept-new", "-i", $SSH_KEY, $bin, "$VPS_USUARIO@${VPS_IP}:/tmp/firmware_asistencia.bin")
    & scp @sa
    Revisar "No se pudo subir el firmware"
    $d = "/opt/asistencia/datos/firmware"
    Invoke-Vps ("set -e; ${s}mkdir -p $d; ${s}mv /tmp/firmware_asistencia.bin $d/firmware.bin; " +
                "echo '$FW_VERSION' | ${s}tee $d/version.txt >/dev/null; ${s}chown -R 10001:10001 $d")
    Revisar "No se pudo publicar el firmware"
    Ok "publicado. Las camaras conectadas se actualizan solas en menos de 1 minuto (LED verde parpadeando rapido)."
}

function Accion-Token {
    $t = Get-Token
    Write-Host ""
    Write-Host "  Datos para el portal de la camara:" -ForegroundColor Green
    Write-Host "    Servidor:       $(Get-Dominio)"
    Write-Host "    Puerto:         $SERVIDOR_PUERTO"
    Write-Host "    Seguridad:      $SEGURIDAD_TLS"
    Write-Host "    Identificador:  $DISPOSITIVO_ID"
    Write-Host "    Token:          $t"
}

function Accion-Monitor {
    $com = Get-Puerto
    # En la base ESP32-CAM-MB, DTR/RTS manejan IO0 y EN: si quedan activos, la
    # placa se queda en reset o en modo de grabacion y no imprime nada. Por eso
    # se dejan apagados y se da un pulso de RTS para reiniciarla y ver el arranque.
    $p = New-Object System.IO.Ports.SerialPort $com, 115200, None, 8, One
    $p.DtrEnable = $false
    $p.RtsEnable = $false
    try { $p.Open() } catch {
        Falla "No se pudo abrir $com. Cierra el Arduino IDE u otro monitor que lo este usando."
    }
    Write-Host "==> Monitor serie en $com (reiniciando la placa...). Ctrl+C para salir." -ForegroundColor Cyan
    try {
        $p.RtsEnable = $true; Start-Sleep -Milliseconds 200; $p.RtsEnable = $false
        while ($true) {
            $t = $p.ReadExisting()
            if ($t) { [Console]::Write($t) }
            Start-Sleep -Milliseconds 100
        }
    } finally {
        $p.Close()
    }
}

function Accion-Verificar {
    Titulo "Este PC"
    foreach ($h in @("ssh", "scp", "ssh-keygen")) {
        if (Get-Command $h -ErrorAction SilentlyContinue) { Ok "$h disponible" } else { Aviso "falta $h (Cliente OpenSSH de Windows)" }
    }
    if (Test-Path $TAR) { Ok "tar disponible" } else { Aviso "falta $TAR" }
    if (Test-Path $MYSQLDUMP) { Ok "mysqldump de XAMPP" } else { Aviso "no encuentro $MYSQLDUMP (solo hace falta para migrar)" }
    $cli = Get-ArduinoCli
    Ok "arduino-cli: $cli"
    Ensure-Entorno $cli
    $p = @([IO.Ports.SerialPort]::GetPortNames())
    if ($p.Count) { Ok ("puertos COM: " + ($p -join ", ")) } else { Aviso "no hay puertos COM (conecta la ESP32-CAM para grabarla)" }
    if ($PORTAL_CLAVE.Length -lt 8) { Aviso "`$PORTAL_CLAVE debe tener al menos 8 caracteres" }
    if (-not $VPS_IP) { Aviso "falta `$VPS_IP en la configuracion"; return }
    Titulo "VPS $VPS_IP"
    $t = Test-NetConnection $VPS_IP -Port $VPS_PUERTO_SSH -WarningAction SilentlyContinue
    if ($t.TcpTestSucceeded) { Ok "SSH alcanzable" } else { Aviso "no se alcanza el puerto SSH $VPS_PUERTO_SSH" }
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 "https://$(Get-Dominio)/api/salud"
        Ok "panel https://$(Get-Dominio) responde: $($r.Content)"
    } catch { Aviso "https://$(Get-Dominio) no responde (normal si aun no ejecutaste: .\desplegar.cmd vps)" }
}

switch ($Accion.ToLower()) {
    "verificar" { Accion-Verificar }
    "vps"       { Accion-Vps }
    "firmware"  { Accion-Firmware }
    "ota"       { Accion-Ota }
    "token"     { Accion-Token }
    "monitor"   { Accion-Monitor }
    "todo"      { Accion-Vps; Accion-Firmware }
    default {
        Write-Host "Uso: .\desplegar.cmd <accion>"
        Write-Host "  verificar  revisa el PC y la VPS"
        Write-Host "  vps        instala / actualiza la VPS y migra los datos"
        Write-Host "  firmware   compila y graba la ESP32-CAM por USB (primera vez)"
        Write-Host "  ota        publica un firmware nuevo (las camaras se actualizan por Wi-Fi)"
        Write-Host "  token      genera un token nuevo para la camara"
        Write-Host "  monitor    monitor serie de la ESP32-CAM"
        Write-Host "  todo       vps + firmware"
        Write-Host "Antes edita la seccion CONFIGURACION de desplegar.ps1."
    }
}
