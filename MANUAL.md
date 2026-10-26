# Presencia en zona — Manual

Sistema de control de presencia en zona para el piloto de Alcalá Inmobiliaria.

---

## 1. Qué es

Es una herramienta que mide **cuánto tiempo pasa cada comercial dentro de su zona de trabajo asignada** durante la jornada laboral. Para ello usa la ubicación del **móvil de empresa**.

Está diseñada para cumplir por construcción la política interna y el documento informativo (art. 20.3 ET, art. 87 y 90 LOPDGDD, art. 13 RGPD):

- **Solo registra en horario laboral:** de lunes a viernes de 10:00 a 14:00 y de 17:00 a 20:30, y los sábados de 10:30 a 14:00, en hora de Madrid. Nunca registra en festivos, en ausencias ni antes de la fecha del acuse de recibo.
- **Los informes solo dan tiempos** (dentro, fuera, sin datos). Nunca muestran rutas, coordenadas ni lugares.
- **Retención:** las posiciones brutas se borran a los **35 días**. Los totales diarios se conservan.
- **Transparencia:** cada comercial puede ver y descargar sus propios datos cuando quiera.
- **Piloto:** todos los informes llevan la marca «🧪 PILOTO — sin efectos disciplinarios».

> Este manual describe el software. La base legal y los documentos (política interna, documento informativo, acuse de recibo, juicio de proporcionalidad y checklist) están en el documento de implantación aparte, y conviene que los revise la asesoría laboral antes de usarlo con datos reales.

---

## 2. Funciones

### App del móvil (Android) — «Presencia»
| Función | Detalle |
|---|---|
| Registro por minuto | Toma aproximadamente una posición por minuto, con precisión equilibrada (~100 m) para ahorrar batería. |
| Solo en jornada | Arranca sola al empezar cada tramo de jornada y se para al terminar. Los tramos se los indica el servidor. |
| Notificación fija | Mientras registra, muestra «Registro de presencia activo — Jornada laboral hasta las HH:MM». |
| Detección de incidencias | Registra si la ubicación está desactivada, si se ha retirado el permiso o si la ubicación es **simulada** (en ese caso no se envía la posición falsa). |
| Sin cobertura | Guarda las muestras en el móvil y las envía cuando vuelve la conexión. Caben hasta unas 2 semanas. |
| Sin botón de pausa | Fuera de jornada no registra nada por diseño; dentro de jornada no se puede desactivar. |
| «Ver mis datos» | Muestra los totales propios de hoy, la semana y el mes. |
| Baja automática | Si la empresa da de baja al comercial o revoca el token, la app deja de registrar. |

### Bot de Telegram — `@presenciaenzonabot`
**Comercial (en chat privado con el bot):**
| Comando | Qué hace |
|---|---|
| `/start CÓDIGO` | Vincula su Telegram con su ficha. |
| `/mis_datos` | Totales de hoy, de la semana y del mes. |
| `/justificar [dd/mm] HH:MM-HH:MM motivo` | Comunica una salida de zona por motivo laboral o un fallo del móvil. Plazo: 48 h. Ejemplo: `/justificar 22/10 17:30-18:45 Firma en notaría` |
| `/exportar` | Descarga en CSV todas sus posiciones guardadas y sus totales (derecho de acceso). |

**Grupo de coordinación («Presencia en Zona»):**
| Comando | Qué hace |
|---|---|
| `/informe` · `/informe ayer` · `/informe dd/mm` · `/informe semana` · `/informe mes` | Informe del periodo indicado. |
| `/pendientes` | Justificaciones sin decidir, con botones ✅ Aceptar / ❌ Rechazar. |

**Envíos automáticos:**
| Cuándo | Qué |
|---|---|
| Lunes a sábado, 21:00 | Informe diario al grupo y a cada comercial por privado. |
| Sábado, 21:05 | Informe semanal. |
| Día 1 de cada mes, 09:00 | Informe del mes anterior. |
| Cada noche, 03:00 | Recálculo de los últimos días y purga de posiciones de más de 35 días. |

### Cómo se calcula el tiempo
Cada minuto de jornada se asigna a **una sola** categoría, en este orden de prioridad:
1. **Fuera justificado:** el minuto está cubierto por una justificación aceptada.
2. **Ubicación simulada:** alguna muestra de ese minuto viene de un GPS falso.
3. **Sin datos (GPS apagado):** la ubicación está desactivada o el permiso retirado.
4. **Sin datos:** no hay muestra en los últimos 2,5 minutos, o la precisión es peor de 300 m.
5. **Dentro / fuera de zona:** se aplica el beneficio de la duda en el borde. Si el punto cae fuera pero a menos distancia del borde que su propia precisión (como máximo 100 m), cuenta como **dentro**.

El **mínimo de presencia** se compara con *dentro + fuera justificado*.

---

## 3. Cómo conecta

```
 Móvil de empresa (Android)                  VPS Hostinger  srv2086002.hstgr.cloud           Telegram
┌─────────────────────────┐   HTTPS   ┌──────────────────────────────────────────┐   ┌────────────────────┐
│ App «Presencia»         │──────────▶│ Caddy (443) ── certificado Let's Encrypt │   │ Grupo coordinación │
│  · pide sus jornadas    │  token    │      │                                   │   │  informes, botones │
│  · envía muestras       │◀──────────│ API FastAPI (8080)                       │   │                    │
│  · consulta sus totales │ jornadas  │   · descarta lo que llegue fuera de      │   │ Comercial (privado)│
└─────────────────────────┘           │     jornada (no se guarda)               │   │  /mis_datos …      │
                                      │   · SQLite  /opt/presencia/server/data   │   └─────────▲──────────┘
                                      │      │                                   │             │
                                      │ Bot (python-telegram-bot) ───────────────┼─────────────┘
                                      └──────────────────────────────────────────┘
```

| Pieza | Dónde | Detalle |
|---|---|---|
| Código | `github.com/lerelerele/presencia` (público, sin secretos) | `server/` con API, bot y gestión; `android/` con la app; `.github/workflows/` con tests y APK. |
| Servidor | VPS Hostinger KVM 1 en Francia, Ubuntu 24.04 + Docker | Carpeta `/opt/presencia/server`. Tres contenedores: `presencia-api`, `presencia-bot` y `presencia-caddy`. |
| Dirección | `https://srv2086002.hstgr.cloud` | `/health` responde `{"ok":true}`. |
| Secretos | `/opt/presencia/server/.env` en el VPS | `BOT_TOKEN`, `COORD_CHAT_ID` y `DOMAIN`. **No se sube nunca a GitHub.** |
| Base de datos | `/opt/presencia/server/data/presencia.db` (SQLite) | Zonas, versiones y asignaciones van dentro. Los `.geojson` originales se guardan en `data/zonas/`. |
| APK | GitHub → Actions → *APK piloto* → Artifacts → `presencia-apk` | Se compila sola en cada push que toque `android/`. |
| Autenticación del móvil | Token por comercial | En el servidor solo se guarda su hash. Se ve una única vez al crearlo. |

**API usada por la app** (todas con `Authorization: Bearer <token>`):
- `GET /api/v1/schedule`: tramos de jornada de los próximos 14 días.
- `POST /api/v1/samples`: lote de muestras. Las que caen fuera de jornada se cuentan pero no se guardan.
- `GET /api/v1/me`: totales propios.

---

## 4. Manual de puesta en marcha

### 4.0 Requisitos previos (ya hechos)
- [x] VPS en Hostinger con la plantilla Docker sobre Ubuntu 24.04.
- [x] Bot creado en BotFather (`@presenciaenzonabot`) y grupo de Telegram «Presencia en Zona» con el bot como miembro.
- [x] Repositorio en GitHub con el código.

### 4.1 Instalar o reinstalar el servidor
Desde hPanel → VPS → **Consola web**, como root:

```bash
git clone https://github.com/lerelerele/presencia.git /opt/presencia
cd /opt/presencia/server
cp .env.example .env
nano .env
```
Rellena en `.env`:
```
BOT_TOKEN=<token de BotFather>            # comprobar con https://api.telegram.org/bot<TOKEN>/getMe
COORD_CHAT_ID=-5524899915                 # id del grupo «Presencia en Zona» (negativo)
PILOT=1
RETENTION_DAYS=35
DOMAIN=srv2086002.hstgr.cloud
```
En nano se pega con clic derecho o Ctrl+Shift+V. Se guarda con Ctrl+O y Enter, y se sale con Ctrl+X.

```bash
docker compose up -d --build
docker compose ps                                   # los 3 contenedores "Up"
curl https://srv2086002.hstgr.cloud/health          # {"ok":true}
docker compose logs --tail 20 bot                   # getUpdates "200 OK"
```

**Cómo sacar `COORD_CHAT_ID`:** escribe `/informe@presenciaenzonabot` **dentro del grupo** y abre `https://api.telegram.org/bot<TOKEN>/getUpdates`. Busca un `"chat":{"id":-…,"type":"group"}`. Si el grupo pasa a supergrupo, el id cambia a uno que empieza por `-100…` y hay que actualizarlo.

### 4.2 Calendario
Antes de empezar, carga los festivos nacionales, autonómicos y locales de Alcalá de Henares:
```bash
cd /opt/presencia/server
alias M='docker compose exec -T api python -m app.manage'
M festivos /srv/festivos.example.txt              # carga el archivo del repo (edítalo antes si hace falta)
M festivo 2027-01-06 "Reyes"                       # o uno a uno
```
Para las ausencias de un comercial (vacaciones, baja, formación):
```bash
M ausencia <id> 2026-11-10 2026-11-12 "Vacaciones"
```

### 4.3 Dar de alta a un comercial
Respeta el orden. **No marques «informado» hasta que el acuse esté firmado y archivado.**

1. **Alta:**
   ```bash
   M alta --nombre "Nombre Apellido" --equipo "Ángel" --min-dia 4h --min-semana 20h
   ```
   El comando devuelve el **id**, el **token del móvil** (solo se muestra esta vez) y el **código de Telegram**.
2. **Zonas:** asigna una o varias zonas del catálogo (ver 4.3 bis):
   ```bash
   M asignar <id> "Centro"   --desde 2026-11-02
   M asignar <id> "Ensanche" --desde 2026-11-02
   ```
3. **Informado** (solo con el acuse firmado):
   ```bash
   M informado <id> --fecha 2026-11-02
   ```
4. **Comprobar:**
   ```bash
   M listar
   ```
   Debe salir `acuse=<fecha>`, `zonas=Centro, Ensanche` y `telegram=sí`.

### 4.3 bis Zonas: catálogo y asignaciones
Cada zona es una **pieza con nombre** («Centro», «Ensanche», «Estación»…) que existe una sola vez y se **asigna** a comerciales con fechas:
- Un comercial puede tener **una o varias** zonas; se mide la suma de todas ellas.
- Una zona puede pasar de un comercial a otro, o repartirse cuando alguien se va.
- Dos comerciales pueden compartir zona.
- El historial se conserva: un día pasado se mide siempre con las zonas que el comercial tenía **ese día**.

**Dibujar las zonas en geojson.io**
1. Dibuja cada zona con la herramienta de **polígono** o rectángulo (no valen líneas ni puntos).
2. Pincha cada polígono y añade una propiedad `name` con su nombre, por ejemplo `name` = `Centro`.
3. Exporta: **GeoJSON**, *Truncate coordinates* ✅, *Add BBOX* ❌, winding **RFC7946**, *Include @id* ❌.
4. Copia el contenido al VPS: `nano data/zonas/barrios.geojson`, pega con clic derecho y guarda.

**Crear zonas**
```bash
M zonas-importar /srv/data/zonas/barrios.geojson         # una zona por polígono, con el nombre de su `name`
M zona-nueva "Centro" /srv/data/zonas/centro.geojson     # o una a una (el archivo entero = una zona)
```

**Asignar, quitar y traspasar** (fechas `AAAA-MM-DD`; `--desde` es el primer día en que aplica el cambio):
| Caso | Comando |
|---|---|
| Darle una zona más | `M asignar 4 "Ensanche" --desde 2026-11-02` |
| Asignación temporal (p. ej. cubrir unas vacaciones) | `M asignar 4 "Estación" --desde 2026-11-02 --hasta 2026-11-16` |
| Quitarle una zona | `M desasignar 4 "Ensanche" --desde 2026-12-01` |
| Pasar una zona de un comercial a otro | `M traspasar "Centro" --de 3 --a 4 --desde 2026-12-01` |
| Cambiar la forma de una zona | `M zona-forma "Centro" /srv/data/zonas/centro-v2.geojson --desde 2026-12-01` |
| Ver quién tiene qué | `M zonas` · `M zonas --dia 2026-12-01` · `M zonas --historial` |
| Atajo: crear y asignar en un paso | `M zona 4 /srv/data/zonas/x.geojson --nombre "Centro" --desde 2026-11-02` |

**Ejemplo: un comercial se va y sus zonas se reparten.** Sonia (id 3) tiene «Centro» y «Ensanche» y se va el 30/11. Desde el 1/12, «Centro» pasa a Justo (1) y «Ensanche» a Deli (2):
```bash
M traspasar "Centro"   --de 3 --a 1 --desde 2026-12-01
M traspasar "Ensanche" --de 3 --a 2 --desde 2026-12-01
M baja 3 --desde 2026-12-01      # si le quedara alguna zona, la libera y lo avisa
M zonas --dia 2026-12-01         # comprobar
```

Los cambios se comunican al comercial **antes** de aplicarlos, con una fecha `--desde` futura. Si se pone una fecha pasada, el sistema avisa y recalcula los días afectados que aún tengan posiciones guardadas.

Un comercial **sin ninguna zona** ese día no se mide: su informe dice «Sin zona asignada» y el minuto cuenta como sin datos.

### 4.4 Preparar el móvil de empresa
1. Descarga el APK desde GitHub → Actions → *APK piloto* → **Artifacts → presencia-apk**, y descomprime el zip.
2. Instálalo. Android pedirá permitir apps de origen desconocido para el navegador o el gestor de archivos.
3. Abre **Presencia** y rellena dos campos **separados**:
   - **Servidor:** `https://srv2086002.hstgr.cloud`, sin barra final ni token.
   - **Código del dispositivo:** el token del paso 4.3.1.
4. Pulsa **Vincular** y después **Configurar ahora** hasta que no falte nada:
   ubicación precisa → **Permitir siempre** → notificaciones → **sin restricciones de batería** → alarmas exactas → ubicación del teléfono activada.
5. En Samsung, Xiaomi o Huawei, revisa además el inicio automático y la batería de la app (guía en dontkillmyapp.com).
6. En Telegram, el comercial abre `@presenciaenzonabot` en privado y envía `/start CÓDIGO`.

**Qué debe mostrar la app:**
| Estado | Significado |
|---|---|
| ⚠️ Pendiente de acuse de recibo | Falta el paso `informado`. |
| ⚠️ Sin zona asignada todavía | Falta asignarle zonas (`asignar`). |
| ⚪ Fuera de jornada: no se registra nada | Correcto fuera de horario. |
| 🟢 En jornada: registrando hasta las HH:MM | Registrando, con la notificación fija visible. |

Si falta algo, pulsa **Sincronizar ahora**.

### 4.5 Prueba antes del piloto (checklist técnico)
- [ ] Con un móvil de prueba, un día laborable: «Última muestra» se actualiza cada minuto y `/mis_datos` va sumando.
- [ ] A las 14:00 y a las 20:30 el registro se para solo y la notificación desaparece.
- [ ] Un domingo o un festivo: no aparece ninguna notificación.
- [ ] `M metricas --desde … --hasta …` da **0 muestras fuera de jornada**.
- [ ] `/exportar` no tiene filas de fuera de horario, domingos ni festivos.
- [ ] Una `/justificar` llega al grupo, se acepta con el botón y cuenta como «fuera justificado».
- [ ] A las 21:00 llega el informe diario al grupo y al privado.

### 4.6 Durante y después del piloto
```bash
M metricas --desde 2026-11-02 --hasta 2026-11-29   # tabla de evaluación (fiabilidad <10 % sin datos, 0 fuera de jornada…)
M recalcular --desde 2026-11-02 --hasta 2026-11-08 # forzar recálculo (asignar/traspasar ya lo hacen solos)
M token <id>                                       # móvil perdido o cambiado: nuevo token (el viejo deja de valer)
M baja <id>                                        # fin de la relación: el móvil deja de registrar
M fin-piloto --anonimizar --si                     # al acabar: borra posiciones y totales; guarda solo agregados sin nombres
```
Batería, salidas falsas y percepción se evalúan en la entrevista con cada participante.

### 4.7 Mantenimiento
| Tarea | Cómo |
|---|---|
| Actualizar el código | `cd /opt/presencia && git pull && cd server && docker compose up -d --build` |
| Ver logs | `docker compose logs --tail 50 api` (o `bot`, `caddy`) |
| Reiniciar | `docker compose restart` |
| Copia de seguridad | `cp data/presencia.db data/backup-$(date +%F).db`, o mejor, los backups automáticos del VPS en hPanel. |
| Cambiar el token del bot | BotFather → `/revoke`, edita `.env` y ejecuta `docker compose up -d`. |

---

## 5. Problemas conocidos y soluciones

| Síntoma | Causa | Solución |
|---|---|---|
| `getUpdates` da `404 Not Found` | URL mal formada | `https://api.telegram.org/bot<TOKEN>/getUpdates`, con `bot` pegado al token y sin `<>` ni `@`. |
| `getUpdates` da `401 Unauthorized` | Token revocado, incompleto o de otro bot | Copia otra vez el token desde BotFather con el botón de copiar. |
| `getUpdates` solo muestra `"type":"private"` | El mensaje se mandó al bot en privado | Escribe el comando **dentro del grupo**. |
| La app dice «No se pudo vincular: HTTP 401» | Token mal copiado | `M token <id>` y vuelve a vincular. |
| `git push` rechazado con GH007 | El commit lleva un email privado | Usa `4605949+lerelerele@users.noreply.github.com` como email de git. |
| El APK falla en «Set up Android» | La acción `setup-android` pedía el paquete `tools`, ya retirado | Corregido: el workflow usa el SDK que ya trae el runner. |
| El bot no arranca y el log dice «Falta BOT_TOKEN o COORD_CHAT_ID» | `.env` incompleto | Rellénalo y ejecuta `docker compose up -d`. |
| Mucho «sin datos» en un móvil | La batería mata la app | Revisa «sin restricciones de batería» y el inicio automático (dontkillmyapp.com). |

## 6. Límites
- Solo Android. iOS no permite un muestreo fiable por minuto en segundo plano.
- La detección de ubicación simulada usa la marca del sistema. Un móvil rooteado podría ocultarla.
- La hora de cada muestra es la del móvil. Lo que caiga fuera de jornada se descarta, pero no se detecta un reloj desajustado dentro de ella.
- La línea «Comercial: <nombre>» no aparece en la pantalla principal de la app. Es un fallo menor, pendiente de revisar, y no afecta al registro.
