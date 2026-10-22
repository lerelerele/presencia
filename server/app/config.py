"""Configuración del sistema de control de presencia en zona.

Todo lo que fija la política interna (horario, retención, plazos) está aquí,
para que coincida literalmente con el documento entregado a los comerciales.
"""
import os
from datetime import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")

DB_PATH = os.environ.get("DB_PATH", "data/presencia.db")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Chat (grupo) de coordinación: único destino de los informes de equipo.
COORD_CHAT_ID = int(os.environ.get("COORD_CHAT_ID", "0") or 0)

# Piloto: los informes se marcan "sin efectos disciplinarios".
PILOT = os.environ.get("PILOT", "1") == "1"
PILOT_BANNER = "🧪 PILOTO — sin efectos disciplinarios"

# Política, punto 3: lunes=0 … domingo=6.
WEEKLY_SCHEDULE: dict[int, list[tuple[time, time]]] = {
    0: [(time(10, 0), time(14, 0)), (time(17, 0), time(20, 30))],
    1: [(time(10, 0), time(14, 0)), (time(17, 0), time(20, 30))],
    2: [(time(10, 0), time(14, 0)), (time(17, 0), time(20, 30))],
    3: [(time(10, 0), time(14, 0)), (time(17, 0), time(20, 30))],
    4: [(time(10, 0), time(14, 0)), (time(17, 0), time(20, 30))],
    5: [(time(10, 30), time(14, 0))],
}

# Política, punto 8.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "35"))
# Política, punto 10.
JUSTIFY_DEADLINE_HOURS = 48

# Ventanas que se envían al móvil (días hacia delante).
SCHEDULE_HORIZON_DAYS = 14
# Margen para relojes desajustados al aceptar muestras en el borde de la ventana.
EDGE_TOLERANCE_S = 30

# Cálculo por minutos.
SAMPLE_CARRY_S = 150          # una muestra cubre los minutos siguientes hasta 2,5 min
ACCURACY_MAX_M = 300          # peor precisión → "sin datos"
BOUNDARY_TOLERANCE_MAX_M = 100  # beneficio de la duda en el borde de la zona
