#!/usr/bin/env bash
# Respaldo diario de la base y de las fotos (lo programa instalar_vps.sh en cron).
# Guarda 14 dias en /opt/asistencia/respaldos.
set -euo pipefail
cd /opt/asistencia
set -a; . ./.env; set +a
mkdir -p respaldos
f="respaldos/bd_$(date +%F_%H%M).sql.gz"
docker compose exec -T -e MYSQL_PWD="$DB_ROOT_PASSWORD" db \
  mariadb-dump -uroot --single-transaction --routines --triggers --events "$DB_NAME" | gzip > "$f"
tar -czf "respaldos/fotos_$(date +%F).tgz" -C datos rostros_referencia 2>/dev/null || true
find respaldos -type f -mtime +14 -delete
echo "Respaldo listo: $f"
