package es.alcala.presencia

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Llamadas bloqueantes: usar siempre fuera del hilo principal. */
object Api {
    class HttpError(val code: Int, msg: String) : Exception(msg)

    private fun call(ctx: Context, method: String, path: String, body: String? = null): JSONObject {
        val url = URL(Store.server(ctx) + path)
        require(url.protocol == "https" || url.host == "10.0.2.2") { "El servidor debe usar HTTPS" }
        val c = url.openConnection() as HttpURLConnection
        try {
            c.requestMethod = method
            c.connectTimeout = 15_000
            c.readTimeout = 30_000
            c.setRequestProperty("Authorization", "Bearer " + Store.token(ctx))
            c.setRequestProperty("Accept", "application/json")
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toByteArray()) }
            }
            val code = c.responseCode
            val text = (if (code in 200..299) c.inputStream else c.errorStream)
                ?.bufferedReader()?.use { it.readText() } ?: ""
            if (code !in 200..299) throw HttpError(code, "HTTP $code: $text")
            return JSONObject(text)
        } finally {
            c.disconnect()
        }
    }

    fun syncSchedule(ctx: Context): JSONObject {
        val j = call(ctx, "GET", "/api/v1/schedule")
        Store.saveSchedule(ctx, j)
        return j
    }

    fun me(ctx: Context): JSONObject = call(ctx, "GET", "/api/v1/me")

    /** Envía la cola en lotes. Devuelve cuántas muestras salieron del móvil. */
    @Synchronized
    fun flush(ctx: Context): Int {
        var sent = 0
        while (true) {
            val batch = SampleQueue.peek(ctx, 500)
            if (batch.isEmpty()) return sent
            val arr = JSONArray()
            batch.forEach { arr.put(JSONObject(it)) }
            call(ctx, "POST", "/api/v1/samples", JSONObject().put("samples", arr).toString())
            SampleQueue.drop(ctx, batch.size)
            sent += batch.size
        }
    }
}
