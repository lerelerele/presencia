package es.alcala.presencia

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/** Solo vive dentro de una ventana de jornada. Al salir de ella se detiene sola. */
class TrackingService : Service() {

    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private val fused by lazy { LocationServices.getFusedLocationProviderClient(this) }
    private var ticks = 0
    private var updatesOn = false

    private val callback = object : LocationCallback() {
        override fun onLocationResult(result: LocationResult) {
            result.locations.forEach { record(it) }
        }
    }

    private val tick = object : Runnable {
        override fun run() {
            if (Store.currentWindow(this@TrackingService) == null) {
                stopSelf(); return
            }
            checkDeviceState()
            if (++ticks % 5 == 0) flushAsync()
            main.postDelayed(this, 60_000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val win = Store.currentWindow(this)
        if (win == null) {
            stopSelf(); return START_NOT_STICKY
        }
        startInForeground(win[1])
        startUpdates()
        main.removeCallbacks(tick)
        main.post(tick)
        return START_STICKY
    }

    private fun startInForeground(endS: Long) {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH, "Registro de jornada", NotificationManager.IMPORTANCE_LOW)
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        val end = SimpleDateFormat("HH:mm", Locale("es", "ES")).format(Date(endS * 1000))
        val n: Notification = Notification.Builder(this, CH)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Registro de presencia activo")
            .setContentText("Jornada laboral hasta las $end. Fuera de jornada no se registra nada.")
            .setOngoing(true)
            .setContentIntent(open)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
        } else {
            startForeground(NOTIF_ID, n)
        }
    }

    private fun hasLocationPermission() =
        checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    private fun startUpdates() {
        if (updatesOn || !hasLocationPermission()) return
        val req = LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, 60_000L)
            .setMinUpdateIntervalMillis(45_000L)
            .setMaxUpdateDelayMillis(0)
            .build()
        try {
            fused.requestLocationUpdates(req, callback, Looper.getMainLooper())
            updatesOn = true
        } catch (e: SecurityException) {
            Log.w(TAG, "Sin permiso de ubicación", e)
        }
    }

    private fun record(loc: Location) {
        val ts = loc.time / 1000
        val mock = if (Build.VERSION.SDK_INT >= 31) loc.isMock else @Suppress("DEPRECATION") loc.isFromMockProvider
        val s = JSONObject().put("ts", ts)
        if (mock) {
            s.put("status", "mock")   // la posición falsa no se envía
        } else {
            s.put("status", "ok").put("lat", loc.latitude).put("lon", loc.longitude)
            if (loc.hasAccuracy()) s.put("accuracy", loc.accuracy.toDouble())
        }
        SampleQueue.add(this, s)
    }

    /** Cada minuto: si la localización está apagada o sin permiso, se deja constancia. */
    private fun checkDeviceState() {
        val now = Store.nowS()
        val status = when {
            !hasLocationPermission() -> "no_permission"
            !locationEnabled(this) -> "gps_off"
            else -> null
        }
        if (status != null) {
            SampleQueue.add(this, JSONObject().put("ts", now).put("status", status))
        } else {
            startUpdates()
        }
    }

    private fun flushAsync() = io.execute {
        try {
            Api.flush(this)
            if (Store.nowS() - Store.lastSync(this) > 3600) Api.syncSchedule(this)
        } catch (e: Exception) {
            Log.i(TAG, "Envío pendiente: ${e.message}")
        }
    }

    override fun onDestroy() {
        main.removeCallbacks(tick)
        if (updatesOn) fused.removeLocationUpdates(callback)
        updatesOn = false
        flushAsync()
        io.shutdown()
        Scheduler.scheduleNext(this)
        super.onDestroy()
    }

    companion object {
        private const val TAG = "Presencia"
        private const val CH = "jornada"
        private const val NOTIF_ID = 1

        fun locationEnabled(ctx: Context): Boolean {
            val lm = ctx.getSystemService(LocationManager::class.java)
            return if (Build.VERSION.SDK_INT >= 28) lm.isLocationEnabled
            else lm.isProviderEnabled(LocationManager.GPS_PROVIDER) || lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        }
    }
}
