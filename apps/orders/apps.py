import logging
import os
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orders"
    label = "orders"

    def ready(self):
        # Warm the rembg ONNX session in a background thread so the first
        # customer request after a backend boot doesn't eat the 25-40 s
        # cold-start penalty (model load + onnxruntime warm-up).
        #
        # Skipped under management commands (migrate, test, makemigrations,
        # collectstatic, etc.) so we don't slow down developer workflows.
        # The autoreloader spawns this twice in dev — once in the parent
        # watcher process, once in the worker. We only warm in the worker
        # (RUN_MAIN env var, set by Django's autoreloader before launching
        # the actual server).
        if os.environ.get("DJANGO_SKIP_REMBG_WARMUP") == "1":
            return
        if not _is_serving():
            return
        threading.Thread(
            target=_warm_rembg_in_background,
            name="rembg-warmup",
            daemon=True,
        ).start()


def _is_serving() -> bool:
    """True when this process is running the Django dev/wsgi server.

    Detects management commands so test runs / migrations don't trigger
    the 170 MB ONNX load. The dev server's autoreloader sets RUN_MAIN=true
    on the worker process; gunicorn / uvicorn don't set it, so we also
    check that argv looks like a server invocation.
    """
    import sys

    argv = sys.argv
    # Dev server worker — definitely serving.
    if os.environ.get("RUN_MAIN") == "true":
        return True
    # Skip explicit management commands.
    if len(argv) > 1 and argv[1] in {
        "migrate",
        "makemigrations",
        "test",
        "shell",
        "collectstatic",
        "createsuperuser",
        "showmigrations",
        "dbshell",
        "check",
    }:
        return False
    # gunicorn / uvicorn / runserver in autoreload-parent (we still warm
    # in the parent under runserver — the cost is paid once per restart
    # either way).
    return True


def _warm_rembg_in_background() -> None:
    """Trigger _get_session() so the ONNX model loads. Errors are caught
    and logged — a missing ONNX shouldn't crash boot. Real customer
    calls will surface the same error as a 503."""
    try:
        # Local import — avoids loading rembg's deps at AppConfig import
        # time, where Django might not be fully ready yet.
        from .services_smart_cut import _get_session

        _get_session()
        logger.info("rembg session warmed at boot")
    except Exception:
        logger.exception(
            "rembg warmup failed at boot — first customer request will retry"
        )
