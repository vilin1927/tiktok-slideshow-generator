#!/bin/bash
# Celery Worker Startup Script
# Usage: ./worker.sh [start|stop|restart|status]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment if exists
if [ -d "../venv" ]; then
    source ../venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Worker settings
CONCURRENCY=5
LOG_LEVEL=info
LOG_FILE=/var/log/celery/worker.log
PID_FILE=/var/run/celery/worker.pid

# Ensure log and pid directories exist
mkdir -p /var/log/celery
mkdir -p /var/run/celery

case "$1" in
    start)
        echo "Starting Celery worker..."
        celery -A celery_app worker \
            --loglevel=$LOG_LEVEL \
            --concurrency=$CONCURRENCY \
            --logfile=$LOG_FILE \
            --pidfile=$PID_FILE \
            --detach
        echo "Worker started. PID: $(cat $PID_FILE 2>/dev/null)"
        ;;
    stop)
        echo "Stopping Celery worker..."
        if [ -f "$PID_FILE" ]; then
            kill $(cat $PID_FILE) 2>/dev/null
            rm -f $PID_FILE
            echo "Worker stopped."
        else
            echo "No PID file found. Worker may not be running."
        fi
        ;;
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;
    status)
        if [ -f "$PID_FILE" ] && kill -0 $(cat $PID_FILE) 2>/dev/null; then
            echo "Worker is running. PID: $(cat $PID_FILE)"
        else
            echo "Worker is not running."
        fi
        ;;
    foreground)
        echo "Starting Celery worker in foreground..."
        celery -A celery_app worker \
            --loglevel=$LOG_LEVEL \
            --concurrency=$CONCURRENCY
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|foreground}"
        exit 1
        ;;
esac
