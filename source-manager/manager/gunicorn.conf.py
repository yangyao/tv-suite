import os

bind = os.environ.get("MANAGER_BIND", "0.0.0.0:8080")
workers = 1
threads = 12
timeout = 60
accesslog = "-"
errorlog = "-"
graceful_timeout = 10


def worker_exit(server, worker):
    application = worker.wsgi
    if application is not None:
        application.extensions["manager_worker"].terminate()
