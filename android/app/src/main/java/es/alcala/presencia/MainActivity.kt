package es.alcala.presencia

import android.Manifest
import android.app.Activity
import android.app.AlarmManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/** Pantalla única: vinculación, permisos, estado y "mis datos". Sin botón de pausa: la app
 *  no registra nada fuera de jornada por diseño, y dentro de jornada no se puede desactivar. */
class MainActivity : Activity() {

    private val io = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var root: LinearLayout
    private val es = Locale("es", "ES")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(24))
        }
        setContentView(ScrollView(this).apply { addView(root) })
    }

    override fun onResume() {
        super.onResume()
        render()
        if (Store.isConfigured(this)) io.execute {
            runCatching { Api.syncSchedule(this) }
            Scheduler.reconcile(this)
            ui.post { render() }
        }
    }

    private fun render() {
        root.removeAllViews()
        title("Presencia en zona")
        if (!Store.isConfigured(this)) renderSetup() else renderStatus()
    }

    private fun renderSetup() {
        text("Vincula este móvil de empresa con los datos que te ha dado coordinación.")
        val server = edit("Servidor (https://…)", InputType.TYPE_TEXT_VARIATION_URI)
        val token = edit("Código del dispositivo", InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD)
        button("Vincular") {
            Store.configure(this, server.text.toString(), token.text.toString())
            io.execute {
                val err = runCatching { Api.syncSchedule(this) }.exceptionOrNull()
                ui.post {
                    if (err != null) {
                        Store.configure(this, "", "")
                        render(); text("No se pudo vincular: ${err.message}")
                    } else {
                        Scheduler.ensurePeriodicSync(this); render()
                    }
                }
            }
        }
    }

    private fun renderStatus() {
        val now = Store.nowS()
        val name = Store.agent(this)
        if (name.isNotBlank()) text("Comercial: $name")
        Store.notice(this).takeIf { it.isNotBlank() }?.let { text("⚠️ $it") }

        val cur = Store.currentWindow(this, now)
        val nxt = Store.nextWindow(this, now)
        text(
            when {
                cur != null -> "🟢 En jornada: registrando hasta las ${hm(cur[1])}."
                nxt != null -> "⚪ Fuera de jornada: no se registra nada.\nPróxima jornada: ${dayHm(nxt[0])}."
                else -> "⚪ Sin jornadas programadas: no se registra nada."
            }, bold = true
        )
        val pend = SampleQueue.size(this)
        val last = Store.lastSample(this)
        text("Pendiente de enviar: $pend · Última muestra: ${if (last > 0) dayHm(last) else "—"}")

        val missing = missingSetup()
        if (missing.isNotEmpty()) {
            text("Falta configurar:\n• " + missing.joinToString("\n• "))
            button("Configurar ahora") { nextSetupStep() }
        }

        button("Ver mis datos") { showMyData() }
        button("Sincronizar ahora") {
            io.execute {
                val r = runCatching { Api.syncSchedule(this); Api.flush(this) }
                Scheduler.reconcile(this)
                ui.post { render(); if (r.isFailure) text("Sin conexión: se enviará más tarde.") }
            }
        }
        text(
            "Solo se toma la posición en tu horario laboral (L-V 10:00-14:00 y 17:00-20:30, " +
                "S 10:30-14:00), nunca en festivos ni ausencias. Los informes muestran tiempos, no rutas. " +
                "Puedes consultar y descargar tus datos con el bot de Telegram (/mis_datos, /exportar).",
            small = true
        )
    }

    // ── Permisos, paso a paso ──

    private fun granted(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    private fun missingSetup(): List<String> {
        val m = mutableListOf<String>()
        if (!granted(Manifest.permission.ACCESS_FINE_LOCATION)) m += "Permiso de ubicación precisa"
        if (Build.VERSION.SDK_INT >= 29 && !granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION))
            m += "Ubicación «Permitir siempre» (necesaria para registrar con la pantalla apagada)"
        if (Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS))
            m += "Notificaciones (avisan cuando el registro está activo)"
        if (!getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName))
            m += "Sin restricciones de batería"
        if (Build.VERSION.SDK_INT >= 31 && !getSystemService(AlarmManager::class.java).canScheduleExactAlarms())
            m += "Alarmas exactas (para empezar y parar a la hora)"
        if (!TrackingService.locationEnabled(this)) m += "Activar la ubicación del teléfono"
        return m
    }

    private fun nextSetupStep() {
        when {
            !granted(Manifest.permission.ACCESS_FINE_LOCATION) -> requestPermissions(
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION), 1)
            Build.VERSION.SDK_INT >= 29 && !granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION) ->
                requestPermissions(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), 2)
            Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS) ->
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 3)
            !getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName) ->
                startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
            Build.VERSION.SDK_INT >= 31 && !getSystemService(AlarmManager::class.java).canScheduleExactAlarms() ->
                startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:$packageName")))
            !TrackingService.locationEnabled(this) ->
                startActivity(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
        }
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, perms, results)
        render()
        if (results.isNotEmpty() && results.all { it == PackageManager.PERMISSION_GRANTED }) nextSetupStep()
    }

    // ── Mis datos ──

    private fun showMyData() = io.execute {
        val r = runCatching { Api.me(this) }
        ui.post {
            render()
            r.onSuccess { j -> text(formatMe(j)) }.onFailure { text("No se pudo consultar: ${it.message}") }
        }
    }

    private fun formatMe(j: JSONObject): String {
        val p = j.getJSONObject("periods")
        val sb = StringBuilder()
        if (j.optBoolean("pilot")) sb.append("PILOTO — sin efectos disciplinarios\n\n")
        for ((key, label) in listOf("hoy" to "Hoy", "semana" to "Esta semana", "mes" to "Este mes")) {
            val t = p.getJSONObject(key)
            sb.append("$label (jornada ${dur(t.getInt("jornada_min"))})\n")
            sb.append("  Dentro: ${dur(t.getInt("dentro_min"))} · Fuera: ${dur(t.getInt("fuera_min"))}\n")
            if (t.getInt("justificado_min") > 0) sb.append("  Justificado: ${dur(t.getInt("justificado_min"))}\n")
            sb.append("  Sin datos: ${dur(t.getInt("sin_datos_min"))}\n\n")
        }
        if (!j.isNull("minimo_diario_min")) sb.append("Mínimo diario: ${dur(j.getInt("minimo_diario_min"))}\n")
        if (!j.isNull("minimo_semanal_min")) sb.append("Mínimo semanal: ${dur(j.getInt("minimo_semanal_min"))}")
        return sb.toString().trim()
    }

    // ── utilidades de interfaz ──

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun dur(min: Int) = if (min >= 60) "${min / 60} h ${"%02d".format(min % 60)} min" else "$min min"
    private fun hm(s: Long) = SimpleDateFormat("HH:mm", es).format(Date(s * 1000))
    private fun dayHm(s: Long) = SimpleDateFormat("EEEE d/M 'a las' HH:mm", es).format(Date(s * 1000))

    private fun title(s: String) = text(s).apply { textSize = 24f; setTypeface(null, Typeface.BOLD) }

    private fun text(s: String, bold: Boolean = false, small: Boolean = false): TextView =
        TextView(this).apply {
            text = s
            textSize = if (small) 13f else 16f
            if (bold) setTypeface(null, Typeface.BOLD)
            setPadding(0, dp(8), 0, dp(8))
            root.addView(this)
        }

    private fun edit(hint: String, type: Int) = EditText(this).apply {
        this.hint = hint
        inputType = InputType.TYPE_CLASS_TEXT or type
        root.addView(this)
    }

    private fun button(label: String, onClick: (View) -> Unit) = Button(this).apply {
        text = label
        setOnClickListener(onClick)
        root.addView(this)
    }
}
