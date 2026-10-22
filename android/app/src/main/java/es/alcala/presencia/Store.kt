package es.alcala.presencia

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Configuración y ventanas de jornada recibidas del servidor.
 *  Regla de oro: si no hay ventanas vigentes, NO se registra nada. */
object Store {
    private const val P = "presencia"

    private fun sp(ctx: Context) = ctx.getSharedPreferences(P, Context.MODE_PRIVATE)

    fun server(ctx: Context): String = sp(ctx).getString("server", "") ?: ""
    fun token(ctx: Context): String = sp(ctx).getString("token", "") ?: ""
    fun isConfigured(ctx: Context) = server(ctx).isNotBlank() && token(ctx).isNotBlank()

    fun configure(ctx: Context, server: String, token: String) {
        sp(ctx).edit().putString("server", server.trimEnd('/')).putString("token", token.trim()).apply()
    }

    fun saveSchedule(ctx: Context, json: JSONObject) {
        sp(ctx).edit()
            .putString("windows", json.getJSONArray("windows").toString())
            .putLong("valid_until", json.getLong("valid_until"))
            .putString("agent", json.optString("agent"))
            .putString("notice", if (json.isNull("notice")) "" else json.optString("notice"))
            .putBoolean("pilot", json.optBoolean("pilot", true))
            .putLong("last_sync", System.currentTimeMillis() / 1000)
            .apply()
    }

    /** Token revocado o comercial de baja: se borran las ventanas y deja de registrar. */
    fun clearSchedule(ctx: Context) {
        sp(ctx).edit().putString("windows", "[]").putLong("valid_until", 0)
            .putString("notice", "Dispositivo desvinculado por la empresa.").apply()
    }

    fun agent(ctx: Context) = sp(ctx).getString("agent", "") ?: ""
    fun notice(ctx: Context) = sp(ctx).getString("notice", "") ?: ""
    fun lastSync(ctx: Context) = sp(ctx).getLong("last_sync", 0)
    fun setLastSample(ctx: Context, ts: Long) = sp(ctx).edit().putLong("last_sample", ts).apply()
    fun lastSample(ctx: Context) = sp(ctx).getLong("last_sample", 0)

    /** Lista de [inicio, fin) en segundos epoch. Vacía si el calendario caducó. */
    fun windows(ctx: Context, nowS: Long = nowS()): List<LongArray> {
        if (nowS > sp(ctx).getLong("valid_until", 0)) return emptyList()
        val arr = JSONArray(sp(ctx).getString("windows", "[]"))
        return (0 until arr.length()).map {
            val w = arr.getJSONArray(it); longArrayOf(w.getLong(0), w.getLong(1))
        }
    }

    fun currentWindow(ctx: Context, nowS: Long = nowS()): LongArray? =
        windows(ctx, nowS).firstOrNull { nowS >= it[0] && nowS < it[1] }

    fun nextWindow(ctx: Context, nowS: Long = nowS()): LongArray? =
        windows(ctx, nowS).filter { it[0] > nowS }.minByOrNull { it[0] }

    fun nowS() = System.currentTimeMillis() / 1000
}

/** Cola de muestras pendientes de envío (sobrevive a reinicios y a falta de cobertura). */
object SampleQueue {
    private const val MAX_LINES = 20_000   // ~2 semanas de jornada sin cobertura
    private fun file(ctx: Context) = File(ctx.filesDir, "queue.jsonl")

    @Synchronized
    fun add(ctx: Context, sample: JSONObject) {
        // Doble barrera: aunque alguien llame aquí fuera de jornada, no se guarda.
        if (Store.currentWindow(ctx, sample.getLong("ts")) == null) return
        val f = file(ctx)
        f.appendText(sample.toString() + "\n")
        Store.setLastSample(ctx, sample.getLong("ts"))
        if (f.length() > MAX_LINES * 120L) trim(f)
    }

    @Synchronized
    fun peek(ctx: Context, max: Int): List<String> {
        val f = file(ctx)
        if (!f.exists()) return emptyList()
        return f.useLines { it.filter(String::isNotBlank).take(max).toList() }
    }

    @Synchronized
    fun drop(ctx: Context, n: Int) {
        val f = file(ctx)
        if (!f.exists()) return
        val rest = f.readLines().filter(String::isNotBlank).drop(n)
        f.writeText(if (rest.isEmpty()) "" else rest.joinToString("\n", postfix = "\n"))
    }

    @Synchronized
    fun size(ctx: Context): Int {
        val f = file(ctx)
        return if (f.exists()) f.useLines { s -> s.count { it.isNotBlank() } } else 0
    }

    private fun trim(f: File) {
        val lines = f.readLines().filter(String::isNotBlank)
        f.writeText(lines.takeLast(MAX_LINES).joinToString("\n", postfix = "\n"))
    }
}
