#!/bin/bash
# Copies one real K-BOT schema 1:1 into 000_DEMO (drops 000_DEMO first).
# Run on the VPS as root:   bash copy_db_to_demo.sh <source_schema>
set -euo pipefail

SRC="${1:?usage: copy_db_to_demo.sh <source_schema>}"
DST="000_DEMO"
BACKUP="/root/backup_${SRC}_$(date +%Y%m%d_%H%M%S).sql"

if [ "$SRC" = "$DST" ] || [ "$SRC" = "AVACONT_COMUN" ] || [ "$SRC" = "AVACONT_SURSA" ]; then
  echo "Refusing: source must be a real unit schema, not $SRC" >&2
  exit 1
fi

if ! mysql -N -e "SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='${SRC}'" | grep -q 1; then
  echo "Schema ${SRC} does not exist" >&2
  exit 1
fi

echo "1/4 dump of ${SRC} -> ${BACKUP}"
mysqldump --single-transaction --routines --triggers --events --hex-blob \
  --default-character-set=utf8mb4 "${SRC}" | sed -E 's/DEFINER=`[^`]+`@`[^`]+`//g' > "${BACKUP}"

echo "2/4 recreate ${DST}"
CHARSET=$(mysql -N -e "SELECT DEFAULT_CHARACTER_SET_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='${SRC}'")
COLL=$(mysql -N -e "SELECT DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='${SRC}'")
mysql -e "DROP DATABASE IF EXISTS \`${DST}\`; CREATE DATABASE \`${DST}\` CHARACTER SET ${CHARSET} COLLATE ${COLL};"

echo "3/4 load into ${DST}"
mysql --default-character-set=utf8mb4 "${DST}" < "${BACKUP}"

echo "4/4 row counts (source vs demo)"
mysql -N -e "
SELECT s.TABLE_NAME, s.TABLE_ROWS, d.TABLE_ROWS
FROM information_schema.TABLES s
LEFT JOIN information_schema.TABLES d ON d.TABLE_SCHEMA='${DST}' AND d.TABLE_NAME=s.TABLE_NAME
WHERE s.TABLE_SCHEMA='${SRC}' AND s.TABLE_TYPE='BASE TABLE' ORDER BY s.TABLE_NAME;"
echo "Done. Backup kept at ${BACKUP}"
