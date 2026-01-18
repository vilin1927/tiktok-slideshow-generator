"""
Celery utility functions for task management.
"""
from celery_app import celery_app
import logging

logger = logging.getLogger(__name__)


def revoke_task(task_id: str, terminate: bool = True) -> bool:
    """
    Revoke a Celery task by ID.

    Args:
        task_id: The Celery task ID to revoke
        terminate: If True, terminate the task immediately (SIGTERM)

    Returns:
        True if revoke command was sent successfully
    """
    if not task_id:
        return False
    try:
        celery_app.control.revoke(task_id, terminate=terminate)
        logger.info(f"Revoked Celery task: {task_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to revoke task {task_id}: {e}")
        return False


def revoke_tasks(task_ids: list) -> int:
    """
    Revoke multiple Celery tasks.

    Args:
        task_ids: List of Celery task IDs to revoke

    Returns:
        Count of successfully revoked tasks
    """
    if not task_ids:
        return 0
    return sum(1 for tid in task_ids if revoke_task(tid))
