#!/usr/bin/env bash
# =====================================================================
#  Instalador / actualizador de la VPS (Ubuntu 22.04 o 24.04)
#
#  Normalmente NO se ejecuta a mano: lo lanza "desplegar.ps1 vps" desde el
#  PC, que sube el codigo a /opt/asistencia/_entrante y llama a este script.
#  Se puede repetir sin perder datos: solo actualiza codigo y configuracion.
#
#  Variables que acepta (las pasa desplegar.ps1):
#    DOMINIO              dominio del panel; vacio = <ip-con-guiones>.sslip.io
#    MIGRAR               auto (importa el respaldo solo si la base esta vacia)
#                         | no | forzar (respalda la base actual y la reemplaza)
#    CAM_INTERVALO_MS, CAM_RESOLUCION, CAM_CALIDAD, CAM_SIEMPRE_ACTIVA, CAM_POTENCIA_WIFI, CAM_GRIS,
#    UMBRAL_CONFIANZA, ESPERA_MISMO_ROSTRO
#
#  Estructura en la VPS:
#    /opt/asistencia/.env        secretos y parametros (se crea una vez)
#    /opt/asistencia/app/        codigo (se reemplaza en cada despliegue)
#    /opt/asistencia/datos/      fotos de referencia y firmware OTA
#    /opt/asistencia/mariadb/    archivos de la base de datos
#    /opt/asistencia/caddy/      certificados HTTPS
#    /opt/asistencia/respaldos/  respaldos diarios (14 dias)
# =====================================================================
set -euo pipefail

RAIZ=/opt/asistencia
ENTRANTE="${ENTRANTE:-$RAIZ/_entrante}"
MIGRAR="${MIGRAR:-auto}"

paso() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m    %s\033[0m\n' "$*"; }
aviso(){ printf '\033[1;33m    AVISO: %s\033[0m\n' "$*"; }
fallo(){ printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fallo "ejecuta como root (sudo)."
[ -d "$ENTRANTE/server" ] || fallo "no encuentro el codigo en $ENTRANTE (usa desplegar.ps1 vps)."
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || aviso "probado en Ubuntu; este sistema es ${PRETTY_NAME:-desconocido}."

# ---------------------------------------------------------------------
paso "1/9 Zona horaria America/Bogota"
timedatectl set-timezone America/Bogota || aviso "no se pudo cambiar la zona horaria"
ok "$(date)"

# ---------------------------------------------------------------------
paso "2/9 Paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl openssl ufw fail2ban cron >/dev/null
ok "listos"

# ---------------------------------------------------------------------
paso "3/9 Docker"
if ! docker compose version >/dev/null 2>&1; then
  if ! apt-get install -y -qq docker.io docker-compose-v2 >/dev/null 2>&1; then
    aviso "paquetes de Ubuntu no disponibles; usando el instalador oficial de Docker"
    curl -fsSL https://get.docker.com | sh
  fi
fi
systemctl enable --now docker >/dev/null 2>&1 || true
ok "$(docker --version) / $(docker compose version --short 2>/dev/null || echo compose)"

# ---------------------------------------------------------------------
paso "4/9 Memoria de intercambio (swap)"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ] && [ "$MEM_MB" -lt 3500 ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  ok "swap de 2 GB creado (RAM: ${MEM_MB} MB)"
else
  ok "no hace falta (RAM: ${MEM_MB} MB)"
fi

# ---------------------------------------------------------------------
paso "5/9 Firewall (UFW) y fail2ban"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null 2>&1 || true
ok "abiertos solo 22 (SSH), 80 y 443. fail2ban protege SSH contra fuerza bruta"
# Nota: la base (3306) y la app (8000) NO se publican: solo existen en la red interna de Docker.

# ---------------------------------------------------------------------
paso "6/9 Archivos y configuracion"
mkdir -p "$RAIZ"/{datos/rostros_referencia,datos/firmware,mariadb,caddy/data,caddy/config,respaldos}
rm -rf "$RAIZ/app.nuevo"
mkdir -p "$RAIZ/app.nuevo"
cp -a "$ENTRANTE"/server "$ENTRANTE"/frontend "$ENTRANTE"/deploy "$RAIZ/app.nuevo/"
rm -rf "$RAIZ/app"
mv "$RAIZ/app.nuevo" "$RAIZ/app"
cp "$RAIZ/app/deploy/docker-compose.yml" "$RAIZ/docker-compose.yml"
cp "$RAIZ/app/deploy/Caddyfile" "$RAIZ/Caddyfile"
sed -i 's/\r$//' "$RAIZ"/app/deploy/*.sh "$RAIZ/Caddyfile"
chmod +x "$RAIZ"/app/deploy/*.sh

ENV="$RAIZ/.env"
if [ ! -f "$ENV" ]; then
  cat > "$ENV" <<EOF
# Generado por instalar_vps.sh el $(date +%F). Contiene secretos: no compartir.
DB_NAME=universidad_v2
DB_USER=asistencia
DB_PASSWORD=$(openssl rand -hex 24)
DB_ROOT_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_USUARIO=admin
ADMIN_CLAVE=$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-14)
EOF
  chmod 600 "$ENV"
  ok ".env creado con contrasenas aleatorias"
fi

pon() {  # pon CLAVE VALOR -> crea o reemplaza la linea en .env
  if grep -q "^$1=" "$ENV"; then sed -i "s|^$1=.*|$1=$2|" "$ENV"; else echo "$1=$2" >> "$ENV"; fi
}
if [ -z "${DOMINIO:-}" ]; then
  IP=$(curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
  DOMINIO="$(echo "$IP" | tr '.' '-').sslip.io"
fi
pon DOMINIO "$DOMINIO"
pon CAM_INTERVALO_MS "${CAM_INTERVALO_MS:-150}"
pon CAM_RESOLUCION "${CAM_RESOLUCION:-CIF}"
pon CAM_CALIDAD "${CAM_CALIDAD:-15}"
pon CAM_SIEMPRE_ACTIVA "${CAM_SIEMPRE_ACTIVA:-0}"
pon CAM_POTENCIA_WIFI "${CAM_POTENCIA_WIFI:-0}"
pon CAM_GRIS "${CAM_GRIS:-0}"
pon UMBRAL_CONFIANZA "${UMBRAL_CONFIANZA:-60}"
pon ESPERA_MISMO_ROSTRO "${ESPERA_MISMO_ROSTRO:-30}"
set -a; . "$ENV"; set +a
chown -R 10001:10001 "$RAIZ/datos"
ok "dominio del panel: https://$DOMINIO"

cd "$RAIZ"

# ---------------------------------------------------------------------
paso "7/9 Base de datos (MariaDB 10.11)"
docker compose up -d --wait db
ok "MariaDB lista"
sqlroot() { docker compose exec -T -e MYSQL_PWD="$DB_ROOT_PASSWORD" db mariadb -uroot "$@"; }
TABLAS=$(sqlroot -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME'")
DUMP="$ENTRANTE/migracion/dump.sql"
if [ -f "$DUMP" ] && { [ "$MIGRAR" = "forzar" ] || { [ "$MIGRAR" = "auto" ] && [ "$TABLAS" -eq 0 ]; }; }; then
  if [ "$TABLAS" -gt 0 ]; then
    R="respaldos/antes_de_migrar_$(date +%F_%H%M).sql.gz"
    docker compose exec -T -e MYSQL_PWD="$DB_ROOT_PASSWORD" db \
      mariadb-dump -uroot --single-transaction --routines --triggers --events "$DB_NAME" | gzip > "$R"
    ok "base actual respaldada en $RAIZ/$R"
    docker compose stop app >/dev/null 2>&1 || true
    sqlroot -e "DROP DATABASE \`$DB_NAME\`; CREATE DATABASE \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; GRANT ALL ON \`$DB_NAME\`.* TO '$DB_USER'@'%';"
  fi
  sqlroot "$DB_NAME" < "$DUMP"
  ok "datos del PC importados"
  if [ -d "$ENTRANTE/migracion/rostros_referencia" ]; then
    cp -a "$ENTRANTE/migracion/rostros_referencia/." "$RAIZ/datos/rostros_referencia/"
    chown -R 10001:10001 "$RAIZ/datos"
    ok "fotos de referencia copiadas ($(ls "$RAIZ/datos/rostros_referencia" | wc -l) estudiantes)"
  fi
elif [ -f "$DUMP" ]; then
  ok "la base ya tiene datos ($TABLAS tablas): no se importa (usa MIGRAR=forzar para reemplazarla)"
else
  ok "sin datos para migrar"
fi
ADMINS=$(sqlroot -N "$DB_NAME" -e "SELECT COUNT(*) FROM usuarios WHERE rol='admin' AND activo=1" 2>/dev/null || echo 0)

# ---------------------------------------------------------------------
paso "8/9 Aplicacion y HTTPS (puede tardar unos minutos la primera vez)"
docker compose build app
docker compose up -d --remove-orphans
docker image prune -f >/dev/null 2>&1 || true
printf '    esperando el certificado HTTPS y el servidor'
LISTO=0
for i in $(seq 1 60); do
  if curl -fsS --max-time 5 "https://$DOMINIO/api/salud" >/dev/null 2>&1; then LISTO=1; break; fi
  printf '.'; sleep 5
done
echo
if [ "$LISTO" -eq 1 ]; then ok "https://$DOMINIO responde"; else
  aviso "aun no responde por HTTPS. Revisa: cd $RAIZ && docker compose logs --tail 50 caddy app"
fi

# ---------------------------------------------------------------------
paso "9/9 Respaldo automatico diario (02:30)"
echo "30 2 * * * root $RAIZ/app/deploy/respaldar.sh >> $RAIZ/respaldos/respaldo.log 2>&1" > /etc/cron.d/asistencia-respaldo
chmod 644 /etc/cron.d/asistencia-respaldo
ok "programado"

rm -rf "$ENTRANTE"

echo
printf '\033[1;32m=====================================================================\033[0m\n'
printf '\033[1;32m  LISTO\033[0m\n'
echo "  Panel:            https://$DOMINIO"
echo "  Servidor para la camara (portal): $DOMINIO   puerto 443   seguridad 2"
if [ "${ADMINS:-0}" -eq 0 ]; then
  echo "  Administrador:    $ADMIN_USUARIO / $ADMIN_CLAVE   (cambiala al entrar)"
else
  echo "  Administrador:    el mismo usuario y clave que usabas en el PC"
fi
echo "  Secretos:         $ENV"
echo "  Logs:             cd $RAIZ && docker compose logs -f app"
printf '\033[1;32m=====================================================================\033[0m\n'
