"""Bot de Telegram: informes a coordinación y acceso de cada comercial a sus datos.

Comercial (chat privado):
  /start CÓDIGO           vincula su Telegram (código de `manage alta`)
  /mis_datos              totales de hoy, semana y mes
  /justificar [dd/mm] HH:MM-HH:MM motivo   salida de zona por trabajo o fallo del móvil
  /exportar               CSV con todas sus posiciones guardadas y sus totales (derecho de acceso)

Grupo de coordinación (COORD_CHAT_ID):
  /informe [hoy|ayer|dd/mm|semana|mes]
  /pendientes             justificaciones sin decidir (con botones)
"""
import csv
import io
import logging
import re
import time
from datetime import date, datetime, time as dtime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes)

from . import config, db, reports, schedule
from .manage import purge
from .totals import recompute, totals_range

log = logging.getLogger("presencia.bot")
HTML = ParseMode.HTML


def today() -> date:
    return schedule.local_day(int(time.time()))


def is_coord(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.id == config.COORD_CHAT_ID


def agent_of(conn, update: Update):
    if update.effective_chat.type != "private":
        return None
    return conn.execute("SELECT * FROM agents WHERE telegram_id=? AND active=1",
                        (update.effective_user.id,)).fetchone()


# ───────── Comercial ─────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if not ctx.args:
        await update.message.reply_text("Envía /start seguido del código que te ha dado coordinación.")
        return
    with db.session() as conn:
        row = conn.execute("SELECT id, name FROM agents WHERE link_code=? AND active=1",
                           (ctx.args[0].strip().upper(),)).fetchone()
        if not row:
            await update.message.reply_text("Código no válido.")
            return
        conn.execute("UPDATE agents SET telegram_id=?, link_code=NULL WHERE id=?", (update.effective_user.id, row["id"]))
        db.audit(conn, f"tg:{update.effective_user.id}", "vincular", f"agent={row['id']}")
    await update.message.reply_text(
        f"Hola, {row['name']}. Desde aquí recibes tus informes y puedes consultar tus datos cuando quieras.\n\n"
        "/mis_datos — tus totales\n/justificar — comunicar una salida de zona por trabajo o un fallo del móvil\n"
        "/exportar — descargar todos tus datos guardados")


async def cmd_mis_datos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db.session() as conn:
        a = agent_of(conn, update)
        if not a:
            await update.message.reply_text("Este comando funciona en privado y con tu Telegram vinculado.")
            return
        parts = [reports.build(conn, k, today(), a["id"]) for k in ("dia", "semana", "mes")]
    await update.message.reply_text("\n\n— — —\n\n".join(parts), parse_mode=HTML)


JUST_RE = re.compile(r"^(?:(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s+)?(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})\s+(.+)$", re.S)


async def cmd_justificar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    with db.session() as conn:
        a = agent_of(conn, update)
        if not a:
            await update.message.reply_text("Este comando funciona en privado y con tu Telegram vinculado.")
            return
        m = JUST_RE.match(text.strip())
        if not m:
            await update.message.reply_text(
                "Formato: /justificar [dd/mm] HH:MM-HH:MM motivo\n"
                "Ejemplo: /justificar 22/10 17:30-18:45 Firma en notaría\n(sin fecha = hoy)")
            return
        dd, mm, yy, h1, m1, h2, m2, motivo = m.groups()
        d = today()
        if dd:
            y = int(yy) + (2000 if yy and len(yy) == 2 else 0) if yy else d.year
            d = date(y, int(mm), int(dd))
        try:
            start = datetime.combine(d, dtime(int(h1), int(m1)), config.TZ)
            end = datetime.combine(d, dtime(int(h2), int(m2)), config.TZ)
        except ValueError:
            await update.message.reply_text("Hora no válida.")
            return
        now = int(time.time())
        if end <= start:
            await update.message.reply_text("La hora de fin debe ser posterior a la de inicio.")
            return
        if end.timestamp() < now - config.JUSTIFY_DEADLINE_HOURS * 3600:
            await update.message.reply_text(
                f"Ha pasado el plazo de {config.JUSTIFY_DEADLINE_HOURS} h. Coméntalo directamente con coordinación.")
            return
        if start.timestamp() > now + 7 * 86400:
            await update.message.reply_text("Solo se pueden anticipar salidas de los próximos 7 días.")
            return
        cur = conn.execute(
            "INSERT INTO justifications (agent_id, start_ts, end_ts, reason, created_at) VALUES (?,?,?,?,?)",
            (a["id"], int(start.timestamp()), int(end.timestamp()), motivo.strip()[:300], now))
        jid = cur.lastrowid
    await update.message.reply_text("Recibido. Coordinación lo revisará y te avisaré de la decisión.")
    await send_justification(ctx, jid)


async def send_justification(ctx, jid: int):
    with db.session() as conn:
        j = conn.execute("SELECT j.*, a.name FROM justifications j JOIN agents a ON a.id=j.agent_id WHERE j.id=?",
                         (jid,)).fetchone()
    s = datetime.fromtimestamp(j["start_ts"], config.TZ)
    e = datetime.fromtimestamp(j["end_ts"], config.TZ)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Aceptar", callback_data=f"j:ok:{jid}"),
                                InlineKeyboardButton("❌ Rechazar", callback_data=f"j:no:{jid}")]])
    await ctx.bot.send_message(
        config.COORD_CHAT_ID,
        f"📝 <b>Justificación #{jid}</b> — {j['name']}\n{reports.fmt_day(s.date())}, "
        f"{s:%H:%M}–{e:%H:%M}\nMotivo: {j['reason']}",
        parse_mode=HTML, reply_markup=kb)


async def cmd_exportar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db.session() as conn:
        a = agent_of(conn, update)
        if not a:
            await update.message.reply_text("Este comando funciona en privado y con tu Telegram vinculado.")
            return
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["fecha_hora_madrid", "estado", "lat", "lon", "precision_m"])
        for r in conn.execute("SELECT * FROM positions WHERE agent_id=? ORDER BY ts", (a["id"],)):
            w.writerow([datetime.fromtimestamp(r["ts"], config.TZ).strftime("%Y-%m-%d %H:%M:%S"),
                        r["status"], r["lat"], r["lon"], r["accuracy"]])
        buf2 = io.StringIO()
        w2 = csv.writer(buf2)
        w2.writerow(["dia", "jornada_min", "dentro_min", "fuera_min", "justificado_min",
                     "sin_datos_min", "gps_apagado_min", "simulada_min"])
        for r in conn.execute("SELECT * FROM daily_totals WHERE agent_id=? ORDER BY day", (a["id"],)):
            w2.writerow([r["day"], r["scheduled_min"], r["inside_min"], r["outside_min"], r["justified_min"],
                         r["nodata_min"], r["gps_off_min"], r["mock_min"]])
        db.audit(conn, f"tg:{update.effective_user.id}", "exportar", f"agent={a['id']}")
    await update.message.reply_document(io.BytesIO(buf.getvalue().encode("utf-8-sig")), filename="mis_posiciones.csv",
                                        caption=f"Posiciones guardadas (se borran a los {config.RETENTION_DAYS} días).")
    await update.message.reply_document(io.BytesIO(buf2.getvalue().encode("utf-8-sig")), filename="mis_totales.csv")


# ───────── Coordinación ─────────

async def on_decision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_coord(update):
        await q.answer("Solo desde el grupo de coordinación.", show_alert=True)
        return
    _, verdict, jid = q.data.split(":")
    status = "aceptada" if verdict == "ok" else "rechazada"
    who = q.from_user.full_name
    with db.session() as conn:
        j = conn.execute("SELECT * FROM justifications WHERE id=?", (int(jid),)).fetchone()
        if not j or j["status"] != "pendiente":
            await q.answer("Ya estaba decidida.")
            return
        conn.execute("UPDATE justifications SET status=?, decided_by=?, decided_at=? WHERE id=?",
                     (status, who, int(time.time()), j["id"]))
        db.audit(conn, f"tg:{q.from_user.id}", f"justificacion_{status}", f"id={jid}")
        d = schedule.local_day(j["start_ts"])
        recompute(conn, d, d, j["agent_id"])
        tg = conn.execute("SELECT telegram_id FROM agents WHERE id=?", (j["agent_id"],)).fetchone()["telegram_id"]
    await q.answer()
    await q.edit_message_text(q.message.text_html + f"\n\n→ <b>{status}</b> por {who}", parse_mode=HTML)
    if tg:
        await ctx.bot.send_message(tg, f"Tu justificación #{jid} ha sido {status}.")


def parse_ref(arg: str) -> tuple[str, date]:
    t = today()
    arg = (arg or "hoy").lower()
    if arg == "hoy":
        return "dia", t
    if arg == "ayer":
        return "dia", t - timedelta(days=1)
    if arg == "semana":
        return "semana", t
    if arg == "mes":
        return "mes", t
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", arg)
    if m:
        return "dia", date(int(m.group(3) or t.year), int(m.group(2)), int(m.group(1)))
    raise ValueError(arg)


async def cmd_informe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_coord(update):
        return
    try:
        kind, ref = parse_ref(ctx.args[0] if ctx.args else "hoy")
    except ValueError:
        await update.message.reply_text("Uso: /informe [hoy|ayer|dd/mm|semana|mes]")
        return
    with db.session() as conn:
        recompute(conn, min(ref, today() - timedelta(days=3)), today() - timedelta(days=1))
        txt = reports.build(conn, kind, ref)
    await update.message.reply_text(txt, parse_mode=HTML)


async def cmd_pendientes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_coord(update):
        return
    with db.session() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM justifications WHERE status='pendiente' ORDER BY id")]
    if not ids:
        await update.message.reply_text("No hay justificaciones pendientes.")
    for jid in ids:
        await send_justification(ctx, jid)


# ───────── Tareas programadas ─────────

async def job_daily(ctx: ContextTypes.DEFAULT_TYPE):
    t = today()
    with db.session() as conn:
        recompute(conn, t - timedelta(days=3), t)
        await ctx.bot.send_message(config.COORD_CHAT_ID, reports.build(conn, "dia", t), parse_mode=HTML)
        for a in conn.execute("SELECT id, telegram_id FROM agents WHERE active=1 AND telegram_id IS NOT NULL").fetchall():
            if totals_range(conn, a["id"], t, t).scheduled_min:
                await ctx.bot.send_message(a["telegram_id"], reports.build(conn, "dia", t, a["id"]), parse_mode=HTML)


async def job_weekly(ctx: ContextTypes.DEFAULT_TYPE):
    t = today()
    with db.session() as conn:
        await ctx.bot.send_message(config.COORD_CHAT_ID, reports.build(conn, "semana", t), parse_mode=HTML)
        for a in conn.execute("SELECT id, telegram_id FROM agents WHERE active=1 AND telegram_id IS NOT NULL").fetchall():
            await ctx.bot.send_message(a["telegram_id"], reports.build(conn, "semana", t, a["id"]), parse_mode=HTML)


async def job_monthly(ctx: ContextTypes.DEFAULT_TYPE):
    ref = today().replace(day=1) - timedelta(days=1)   # mes anterior
    with db.session() as conn:
        await ctx.bot.send_message(config.COORD_CHAT_ID, reports.build(conn, "mes", ref), parse_mode=HTML)
        for a in conn.execute("SELECT id, telegram_id FROM agents WHERE active=1 AND telegram_id IS NOT NULL").fetchall():
            await ctx.bot.send_message(a["telegram_id"], reports.build(conn, "mes", ref, a["id"]), parse_mode=HTML)


async def job_nightly(ctx: ContextTypes.DEFAULT_TYPE):
    t = today()
    with db.session() as conn:
        recompute(conn, t - timedelta(days=3), t - timedelta(days=1))   # recoge envíos tardíos
        n = purge(conn)
    log.info("Purga nocturna: %s posiciones borradas", n)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN or not config.COORD_CHAT_ID:
        raise SystemExit("Faltan BOT_TOKEN o COORD_CHAT_ID")
    db.connect().close()
    app = Application.builder().token(config.BOT_TOKEN).build()
    for name, fn in [("start", cmd_start), ("mis_datos", cmd_mis_datos), ("justificar", cmd_justificar),
                     ("exportar", cmd_exportar), ("informe", cmd_informe), ("pendientes", cmd_pendientes)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_decision, pattern=r"^j:(ok|no):\d+$"))

    jq = app.job_queue
    tz = config.TZ
    # PTB: days 0=domingo … 6=sábado
    jq.run_daily(job_daily, dtime(21, 0, tzinfo=tz), days=(1, 2, 3, 4, 5, 6), name="diario")
    jq.run_daily(job_weekly, dtime(21, 5, tzinfo=tz), days=(6,), name="semanal")
    jq.run_monthly(job_monthly, dtime(9, 0, tzinfo=tz), day=1, name="mensual")
    jq.run_daily(job_nightly, dtime(3, 0, tzinfo=tz), name="purga")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
