# Presencia en zona — piloto

Software para el piloto de control de presencia en zona de Alcalá Inmobiliaria, construido para cumplir por diseño la política interna y el documento informativo.

```
Móvil de empresa (Android)            VPS                                 Telegram
┌──────────────────────┐   HTTPS   ┌──────────────────────────────┐   ┌──────────────────────┐
│ App "Presencia"      │ ────────► │ API FastAPI  ──► SQLite      │   │ Grupo coordinación:  │
│ · solo en ventanas   │ ◄──────── │ · descarta fuera de jornada  │──►│ informes dia/sem/mes │
│   que da el servidor │ ventanas  │ · minuto a minuto: dentro /  │   │ · justificaciones    │
│ · GPS off / simulada │           │   fuera / sin datos          │   │ Comercial (privado): │
│ · cola sin cobertura │           │ · purga posiciones a 35 días │   │ /mis_datos /exportar │
└──────────────────────┘           └──────────────────────────────┘   └──────────────────────┘
```

## Garantías que impone el software

| Política | Cómo se cumple |
|---|---|
| Nada fuera de horario, festivos ni ausencias (punto 3) | El servidor calcula las ventanas (L-V 10–14 y 17–20:30, S 10:30–14, hora de Madrid) quitando festivos y ausencias. La app solo registra dentro de ellas y además filtra cada muestra. El servidor vuelve a filtrar: lo que llega fuera de jornada **no se guarda**, solo se cuenta (métrica "Respeto del horario"). |
| No implantar sin informar | Un comercial sin fecha de acuse (`manage informado`) no tiene ventanas: la app no registra nada. Tampoco antes de esa fecha. |
| Informes sin rutas ni lugares (punto 6) | Los informes y `/api/v1/me` solo contienen minutos. Hay un test que lo comprueba. |
| Acceso restringido (punto 7) | Informes de equipo solo al chat `COORD_CHAT_ID`; cada comercial solo ve lo suyo en privado. |
| Retención 35 días (punto 8) | Purga nocturna de posiciones. Los totales diarios se conservan. |
| Transparencia / derecho de acceso (12) | `/mis_datos` y la app muestran sus totales; `/exportar` le entrega en CSV todas sus posiciones guardadas. |
| Incidencias en 48 h (10) | `/justificar`; coordinación acepta o rechaza con un botón; lo aceptado cuenta como "fuera justificado". Fuera de plazo, el bot lo rechaza. |
| Cambio de zona comunicado antes (4) | Las zonas tienen fecha de vigencia; avisa si cargas una con fecha pasada. |
| Piloto sin efectos disciplinarios | `PILOT=1` marca todos los informes. `manage fin-piloto` borra los datos (opcionalmente guarda totales agregados sin nombres). |
| Baja / móvil perdido | `manage baja` o `manage token` revocan el token; la app borra sus ventanas al recibir 401 y deja de registrar. |

## Cómo se mide cada minuto de jornada

Una muestra por minuto aprox. (prioridad equilibrada, ~100 m, buena para batería). Cada minuto acaba en una sola categoría:

1. **Justificado** si cae en una justificación aceptada.
2. **Ubicación simulada** si alguna muestra del minuto viene de un proveedor falso.
3. **Sin datos (GPS apagado)** si la localización está desactivada o se retiró el permiso.
4. **Sin datos** si no hay muestra en los últimos 2,5 min o la precisión es peor de 300 m.
5. **Dentro / fuera** según el polígono, con beneficio de la duda: si el punto está fuera pero a menos de su precisión del borde (máx. 100 m), cuenta como dentro. Así se reducen las "salidas falsas".

El mínimo se compara con *dentro + justificado*.

## Puesta en marcha

### 1. Servidor (en tu VPS)
```bash
cd server
cp .env.example .env          # BOT_TOKEN del bot nuevo y COORD_CHAT_ID del grupo
docker compose up -d --build
```
El compose incluye Caddy, que saca el certificado HTTPS automáticamente para `DOMAIN` (por defecto `srv2086002.hstgr.cloud`). La app no habla con servidores sin HTTPS.
En Hostinger también vale: Administrador de Docker → Componer desde URL → este repo, `server/docker-compose.yml`, y las variables en «Entorno».
Para saber el id del grupo: añade el bot y mira `getUpdates`, o usa @RawDataBot.

### 2. Calendario
```bash
docker compose exec api python -m app.manage festivos festivos.example.txt   # completa antes autonómicos y locales
docker compose exec api python -m app.manage festivo 2026-11-02 "Todos los Santos (trasladado)"   # si aplica
```

### 3. Alta de cada participante
```bash
docker compose exec api python -m app.manage alta --nombre "…" --equipo "Ángel" --min-dia 4h --min-semana 20h
#   → token del móvil (una sola vez) y código /start para Telegram
docker compose exec api python -m app.manage zona 1 /srv/data/zonas/comercial1.geojson --desde 2026-10-26
#   dibuja la zona en https://geojson.io (varios polígonos = varias zonas) y guárdala en server/data/zonas/
docker compose exec api python -m app.manage informado 1 --fecha 2026-10-26   # SOLO tras archivar el acuse firmado
docker compose exec api python -m app.manage listar
```
Ausencias: `manage ausencia 1 2026-11-10 2026-11-12 "Vacaciones"`.

### 4. App en el móvil de empresa
- El APK se compila en GitHub Actions (workflow *APK piloto*) al subir el repo; descárgalo de los artefactos del run.
- Instálalo, abre **Presencia**, pon la URL del servidor y el token.
- Pulsa **Configurar ahora** hasta que no falte nada: ubicación precisa → *Permitir siempre* → notificaciones → sin restricciones de batería → alarmas exactas.
- En Xiaomi/Huawei/Samsung revisa además el "inicio automático" y la batería por app (ver dontkillmyapp.com).

### 5. Telegram
El comercial abre el bot en privado y envía `/start CÓDIGO`. En el grupo de coordinación: `/informe`, `/informe ayer`, `/informe semana`, `/pendientes`.

Envíos automáticos: diario a las 21:00 (L-S), semanal el sábado a las 21:05, mensual el día 1 a las 09:00. Purga y recálculo a las 03:00.

## Evaluación del piloto
```bash
docker compose exec api python -m app.manage metricas --desde 2026-10-26 --hasta 2026-11-22
```
Saca por comercial: % sin datos (umbral < 10 %), muestras recibidas fuera de jornada (umbral 0), minutos de ubicación simulada, nº de justificaciones y tiempo medio de decisión (umbral < 48 h). Batería, salidas falsas y percepción salen de la entrevista.

Al terminar: `manage fin-piloto --anonimizar --si`.

## Puntos del checklist que cubre el software
- *Comprobar en un móvil de prueba que no se registra nada fuera del horario ni en festivos*: déjalo encendido un domingo y un festivo; `metricas` debe dar 0 muestras fuera de jornada y `/exportar` no debe tener ninguna fila de esos días.
- *Comprobar que cada comercial puede ver sus propios datos*: `/mis_datos`, `/exportar` y "Ver mis datos" en la app.
- *Procedimiento para justificar salidas*: `/justificar`.

El resto (asesoría, EIPD, registro de actividades, contrato de encargado con el proveedor del VPS, acuses) es documental.

## Desarrollo
```bash
cd server && pip install -r requirements-dev.txt && python -m pytest -q
DB_PATH=/tmp/sim.db python -m tools.simulate 2026-10-22     # jornada simulada con informe y métricas
```

## Límites conocidos
- Solo Android. iOS no permite muestreo por minuto fiable en segundo plano sin otra arquitectura.
- La detección de ubicación simulada usa la marca del sistema (`isMock`); un móvil rooteado puede ocultarla. Para el piloto basta; si se extiende, valorar Play Integrity.
- La hora de cada muestra es la del móvil. Si alguien cambia el reloj, el servidor descarta lo que caiga fuera de jornada, pero no detecta un desfase dentro de ella.
- La app no se ha compilado en este entorno (sin SDK de Android); el workflow de GitHub la compila. El servidor sí está probado (12 tests + simulación).
