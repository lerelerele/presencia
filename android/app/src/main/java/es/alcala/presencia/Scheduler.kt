package es.alcala.presencia

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/** Arranca el servicio al empezar cada ventana y lo para al terminar. */
object Scheduler {
    private const val TAG = "Presencia"

    fun reconcile(ctx: Context) {
        if (!Store.isConfigured(ctx)) return
        val svc = Intent(ctx, TrackingService::class.java)
        if (Store.currentWindow(ctx) != null) {
            try {
                ctx.startForegroundService(svc)
            } catch (e: Exception) {
                // Android limita arrancar servicios desde segundo plano; la alarma exacta
                // y la exención de batería lo permiten. Si falla, se reintenta en el siguiente ciclo.
                Log.w(TAG, "No se pudo iniciar el registro: ${e.message}")
            }
        } else {
            ctx.stopService(svc)
        }
        scheduleNext(ctx)
    }

    fun scheduleNext(ctx: Context) {
        val now = Store.nowS()
        val target = Store.currentWindow(ctx, now)?.get(1) ?: Store.nextWindow(ctx, now)?.get(0) ?: return
        val am = ctx.getSystemService(AlarmManager::class.java)
        val pi = PendingIntent.getBroadcast(
            ctx, 0, Intent(ctx, AlarmReceiver::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val atMs = target * 1000 + 1000
        if (Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()) {
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
        } else {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
        }
    }

    fun ensurePeriodicSync(ctx: Context) {
        val req = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("sync", ExistingPeriodicWorkPolicy.KEEP, req)
    }
}

class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) = Scheduler.reconcile(ctx)
}

class SystemReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        Scheduler.reconcile(ctx)
        Scheduler.ensurePeriodicSync(ctx)
    }
}

/** Cada 15 min con red: actualiza calendario (festivos, ausencias), vacía la cola y revisa el estado. */
class SyncWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        val ctx = applicationContext
        if (!Store.isConfigured(ctx)) return Result.success()
        try {
            Api.syncSchedule(ctx)
            Api.flush(ctx)
        } catch (e: Api.HttpError) {
            if (e.code == 401) Store.clearSchedule(ctx)   // baja o token revocado: deja de registrar
        } catch (e: Exception) {
            Log.i("Presencia", "Sync aplazada: ${e.message}")
        }
        Scheduler.reconcile(ctx)
        return Result.success()
    }
}
