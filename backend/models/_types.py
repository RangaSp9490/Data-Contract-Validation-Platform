from sqlalchemy import DateTime
from sqlalchemy.dialects import mysql

# Millisecond-precision DATETIME on MySQL (default DATETIME truncates to seconds,
# which would make run durations and event ordering imprecise).
Timestamp = DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql")
