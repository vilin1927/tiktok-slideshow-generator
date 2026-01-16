"""
Database Module for Batch Processing
SQLite database for tracking batches, links, and variations
"""
import sqlite3
import os
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

# Database file path
DB_PATH = os.path.join(os.path.dirname(__file__), 'batch_processing.db')


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Enable dict-like access
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize database tables."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Unified Jobs table - tracks ALL jobs (single and batch)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL DEFAULT 'single',
                status TEXT DEFAULT 'pending',
                tiktok_url TEXT,
                total_links INTEGER DEFAULT 1,
                completed_links INTEGER DEFAULT 0,
                failed_links INTEGER DEFAULT 0,
                product_description TEXT,
                folder_name TEXT,
                drive_folder_url TEXT,
                error_message TEXT,
                variations_config TEXT,
                celery_task_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP
            )
        ''')

        # Batches table (legacy, kept for batch_links foreign key)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                total_links INTEGER DEFAULT 0,
                photo_variations INTEGER DEFAULT 1,
                text_variations INTEGER DEFAULT 1,
                variations_config TEXT,
                job_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                drive_folder_url TEXT,
                error_message TEXT
            )
        ''')

        # Batch links table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS batch_links (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                link_index INTEGER NOT NULL,
                link_url TEXT NOT NULL,
                product_photo_path TEXT,
                product_description TEXT,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                drive_folder_url TEXT,
                celery_task_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (batch_id) REFERENCES batches(id)
            )
        ''')

        # Batch variations table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS batch_variations (
                id TEXT PRIMARY KEY,
                batch_link_id TEXT NOT NULL,
                variation_num INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                celery_task_id TEXT,
                output_path TEXT,
                drive_url TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (batch_link_id) REFERENCES batch_links(id)
            )
        ''')

        # Video jobs table - tracks video generation queue
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS video_jobs (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL DEFAULT 'standalone',
                status TEXT DEFAULT 'pending',
                source_session_id TEXT,
                variation_key TEXT,
                image_paths TEXT,
                audio_path TEXT,
                output_path TEXT,
                folder_name TEXT,
                drive_url TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP
            )
        ''')

        # Create indexes for faster queries
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_job_type ON jobs(job_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_batch_links_batch_id ON batch_links(batch_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_batch_links_status ON batch_links(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_batch_variations_link_id ON batch_variations(batch_link_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_batch_variations_status ON batch_variations(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_video_jobs_status ON video_jobs(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_video_jobs_created_at ON video_jobs(created_at)')


# ============ Job Operations (Unified) ============

def create_job(
    job_type: str,
    tiktok_url: str = None,
    total_links: int = 1,
    product_description: str = None,
    folder_name: str = None,
    variations_config: str = None
) -> str:
    """Create a new job and return its ID."""
    import json
    job_id = str(uuid.uuid4())

    # Generate auto folder name if not provided
    if not folder_name:
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        folder_name = f"job_{timestamp}_{job_id[:8]}"

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO jobs (id, job_type, tiktok_url, total_links, product_description, folder_name, variations_config)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (job_id, job_type, tiktok_url, total_links, product_description, folder_name, variations_config))
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get job by ID."""
    import json
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM jobs WHERE id = ?', (job_id,))
        row = cursor.fetchone()
        if row:
            job = dict(row)
            # Parse JSON variations_config if present
            if job.get('variations_config'):
                try:
                    job['variations_config'] = json.loads(job['variations_config'])
                except:
                    pass
            return job
        return None


def update_job_status(
    job_id: str,
    status: str,
    error_message: str = None,
    drive_folder_url: str = None,
    celery_task_id: str = None,
    completed_links: int = None,
    failed_links: int = None
):
    """Update job status."""
    with get_db() as conn:
        cursor = conn.cursor()
        updates = ['status = ?']
        params = [status]

        if status == 'processing':
            updates.append('started_at = ?')
            params.append(datetime.utcnow().isoformat())
        elif status in ('completed', 'failed', 'cancelled'):
            updates.append('completed_at = ?')
            params.append(datetime.utcnow().isoformat())

        if error_message is not None:
            updates.append('error_message = ?')
            params.append(error_message)

        if drive_folder_url is not None:
            updates.append('drive_folder_url = ?')
            params.append(drive_folder_url)

        if celery_task_id is not None:
            updates.append('celery_task_id = ?')
            params.append(celery_task_id)

        if completed_links is not None:
            updates.append('completed_links = ?')
            params.append(completed_links)

        if failed_links is not None:
            updates.append('failed_links = ?')
            params.append(failed_links)

        params.append(job_id)
        cursor.execute(f'''
            UPDATE jobs SET {', '.join(updates)} WHERE id = ?
        ''', params)


def list_jobs(
    job_type: str = None,
    status: str = None,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """List jobs with optional filters, newest first."""
    import json
    with get_db() as conn:
        cursor = conn.cursor()

        query = 'SELECT * FROM jobs WHERE 1=1'
        params = []

        if job_type:
            query += ' AND job_type = ?'
            params.append(job_type)

        if status:
            query += ' AND status = ?'
            params.append(status)

        query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'
        params.extend([limit, offset])

        cursor.execute(query, params)
        jobs = []
        for row in cursor.fetchall():
            job = dict(row)
            if job.get('variations_config'):
                try:
                    job['variations_config'] = json.loads(job['variations_config'])
                except:
                    pass
            jobs.append(job)
        return jobs


def get_jobs_count(job_type: str = None, status: str = None) -> int:
    """Get total count of jobs matching filters."""
    with get_db() as conn:
        cursor = conn.cursor()

        query = 'SELECT COUNT(*) as count FROM jobs WHERE 1=1'
        params = []

        if job_type:
            query += ' AND job_type = ?'
            params.append(job_type)

        if status:
            query += ' AND status = ?'
            params.append(status)

        cursor.execute(query, params)
        return cursor.fetchone()['count']


def delete_job(job_id: str) -> bool:
    """Delete a job by ID. Returns True if deleted."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
        return cursor.rowcount > 0


# ============ Batch Operations ============

def create_batch(
    total_links: int,
    photo_variations: int = 1,
    text_variations: int = 1,
    variations_config: str = None,
    job_id: str = None
) -> str:
    """Create a new batch and return its ID."""
    batch_id = str(uuid.uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO batches (id, total_links, photo_variations, text_variations, variations_config, job_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (batch_id, total_links, photo_variations, text_variations, variations_config, job_id))
    return batch_id


def get_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    """Get batch by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM batches WHERE id = ?', (batch_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_batch_status(
    batch_id: str,
    status: str,
    error_message: str = None,
    drive_folder_url: str = None,
    completed_links: int = None,
    failed_links: int = None
):
    """Update batch status and sync to jobs table."""
    with get_db() as conn:
        cursor = conn.cursor()
        updates = ['status = ?']
        params = [status]

        if status == 'processing':
            updates.append('started_at = ?')
            params.append(datetime.utcnow().isoformat())
        elif status in ('completed', 'failed', 'cancelled'):
            updates.append('completed_at = ?')
            params.append(datetime.utcnow().isoformat())

        if error_message:
            updates.append('error_message = ?')
            params.append(error_message)

        if drive_folder_url:
            updates.append('drive_folder_url = ?')
            params.append(drive_folder_url)

        params.append(batch_id)
        cursor.execute(f'''
            UPDATE batches SET {', '.join(updates)} WHERE id = ?
        ''', params)

        # Also sync status to jobs table if job_id exists
        cursor.execute('SELECT job_id, drive_folder_url FROM batches WHERE id = ?', (batch_id,))
        batch = cursor.fetchone()
        if batch and batch['job_id']:
            job_updates = ['status = ?']
            job_params = [status]

            if status == 'processing':
                job_updates.append('started_at = ?')
                job_params.append(datetime.utcnow().isoformat())
            elif status in ('completed', 'failed', 'cancelled'):
                job_updates.append('completed_at = ?')
                job_params.append(datetime.utcnow().isoformat())

            if error_message:
                job_updates.append('error_message = ?')
                job_params.append(error_message)

            # Use batch's drive_folder_url if available
            batch_drive_url = drive_folder_url or batch['drive_folder_url']
            if batch_drive_url:
                job_updates.append('drive_folder_url = ?')
                job_params.append(batch_drive_url)

            if completed_links is not None:
                job_updates.append('completed_links = ?')
                job_params.append(completed_links)

            if failed_links is not None:
                job_updates.append('failed_links = ?')
                job_params.append(failed_links)

            job_params.append(batch['job_id'])
            cursor.execute(f'''
                UPDATE jobs SET {', '.join(job_updates)} WHERE id = ?
            ''', job_params)


def get_batch_status(batch_id: str) -> Dict[str, Any]:
    """Get batch status with link/variation counts."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Get batch info
        cursor.execute('SELECT * FROM batches WHERE id = ?', (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            return None

        batch_dict = dict(batch)

        # Get link counts by status
        cursor.execute('''
            SELECT status, COUNT(*) as count
            FROM batch_links WHERE batch_id = ?
            GROUP BY status
        ''', (batch_id,))
        link_counts = {row['status']: row['count'] for row in cursor.fetchall()}

        # Get variation counts by status
        cursor.execute('''
            SELECT bv.status, COUNT(*) as count
            FROM batch_variations bv
            JOIN batch_links bl ON bv.batch_link_id = bl.id
            WHERE bl.batch_id = ?
            GROUP BY bv.status
        ''', (batch_id,))
        variation_counts = {row['status']: row['count'] for row in cursor.fetchall()}

        # Get total variations
        cursor.execute('''
            SELECT COUNT(*) as total
            FROM batch_variations bv
            JOIN batch_links bl ON bv.batch_link_id = bl.id
            WHERE bl.batch_id = ?
        ''', (batch_id,))
        total_variations = cursor.fetchone()['total']

        return {
            **batch_dict,
            'links': {
                'total': batch_dict['total_links'],
                'pending': link_counts.get('pending', 0),
                'processing': link_counts.get('processing', 0),
                'completed': link_counts.get('completed', 0),
                'failed': link_counts.get('failed', 0),
            },
            'variations': {
                'total': total_variations,
                'pending': variation_counts.get('pending', 0),
                'processing': variation_counts.get('processing', 0),
                'completed': variation_counts.get('completed', 0),
                'failed': variation_counts.get('failed', 0),
            }
        }


# ============ Batch Link Operations ============

def create_batch_link(
    batch_id: str,
    link_index: int,
    link_url: str,
    product_photo_path: str = None,
    product_description: str = None
) -> str:
    """Create a batch link and return its ID."""
    link_id = str(uuid.uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO batch_links (id, batch_id, link_index, link_url, product_photo_path, product_description)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (link_id, batch_id, link_index, link_url, product_photo_path, product_description))
    return link_id


def get_batch_link(link_id: str) -> Optional[Dict[str, Any]]:
    """Get batch link by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM batch_links WHERE id = ?', (link_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_batch_links(batch_id: str, status: str = None) -> List[Dict[str, Any]]:
    """Get all links for a batch, optionally filtered by status."""
    with get_db() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute('''
                SELECT * FROM batch_links
                WHERE batch_id = ? AND status = ?
                ORDER BY link_index
            ''', (batch_id, status))
        else:
            cursor.execute('''
                SELECT * FROM batch_links
                WHERE batch_id = ?
                ORDER BY link_index
            ''', (batch_id,))
        return [dict(row) for row in cursor.fetchall()]


def update_batch_link_status(
    link_id: str,
    status: str,
    error_message: str = None,
    drive_folder_url: str = None,
    celery_task_id: str = None
):
    """Update batch link status."""
    with get_db() as conn:
        cursor = conn.cursor()
        updates = ['status = ?']
        params = [status]

        if status == 'processing':
            updates.append('started_at = ?')
            params.append(datetime.utcnow().isoformat())
        elif status in ('completed', 'failed'):
            updates.append('completed_at = ?')
            params.append(datetime.utcnow().isoformat())

        if error_message:
            updates.append('error_message = ?')
            params.append(error_message)

        if drive_folder_url:
            updates.append('drive_folder_url = ?')
            params.append(drive_folder_url)

        if celery_task_id:
            updates.append('celery_task_id = ?')
            params.append(celery_task_id)

        params.append(link_id)
        cursor.execute(f'''
            UPDATE batch_links SET {', '.join(updates)} WHERE id = ?
        ''', params)


# ============ Variation Operations ============

def create_variation(batch_link_id: str, variation_num: int) -> str:
    """Create a variation and return its ID."""
    variation_id = str(uuid.uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO batch_variations (id, batch_link_id, variation_num)
            VALUES (?, ?, ?)
        ''', (variation_id, batch_link_id, variation_num))
    return variation_id


def get_variation(variation_id: str) -> Optional[Dict[str, Any]]:
    """Get variation by ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM batch_variations WHERE id = ?', (variation_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_link_variations(batch_link_id: str) -> List[Dict[str, Any]]:
    """Get all variations for a link."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM batch_variations
            WHERE batch_link_id = ?
            ORDER BY variation_num
        ''', (batch_link_id,))
        return [dict(row) for row in cursor.fetchall()]


def update_variation_status(
    variation_id: str,
    status: str,
    error_message: str = None,
    output_path: str = None,
    drive_url: str = None,
    celery_task_id: str = None
):
    """Update variation status."""
    with get_db() as conn:
        cursor = conn.cursor()
        updates = ['status = ?']
        params = [status]

        if status == 'processing':
            updates.append('started_at = ?')
            params.append(datetime.utcnow().isoformat())
        elif status in ('completed', 'failed'):
            updates.append('completed_at = ?')
            params.append(datetime.utcnow().isoformat())

        if error_message:
            updates.append('error_message = ?')
            params.append(error_message)

        if output_path:
            updates.append('output_path = ?')
            params.append(output_path)

        if drive_url:
            updates.append('drive_url = ?')
            params.append(drive_url)

        if celery_task_id:
            updates.append('celery_task_id = ?')
            params.append(celery_task_id)

        params.append(variation_id)
        cursor.execute(f'''
            UPDATE batch_variations SET {', '.join(updates)} WHERE id = ?
        ''', params)


# ============ Utility Functions ============

def get_failed_links(batch_id: str) -> List[Dict[str, Any]]:
    """Get all failed links for a batch."""
    return get_batch_links(batch_id, status='failed')


def get_pending_links(batch_id: str) -> List[Dict[str, Any]]:
    """Get all pending links for a batch."""
    return get_batch_links(batch_id, status='pending')


def reset_failed_links(batch_id: str):
    """Reset failed links to pending for retry."""
    with get_db() as conn:
        cursor = conn.cursor()
        # Reset links
        cursor.execute('''
            UPDATE batch_links
            SET status = 'pending', error_message = NULL, started_at = NULL, completed_at = NULL
            WHERE batch_id = ? AND status = 'failed'
        ''', (batch_id,))

        # Reset their variations
        cursor.execute('''
            UPDATE batch_variations
            SET status = 'pending', error_message = NULL, started_at = NULL, completed_at = NULL
            WHERE batch_link_id IN (
                SELECT id FROM batch_links WHERE batch_id = ? AND status = 'pending'
            )
        ''', (batch_id,))


def cancel_pending_tasks(batch_id: str):
    """Mark all pending tasks as cancelled."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE batch_links
            SET status = 'cancelled'
            WHERE batch_id = ? AND status = 'pending'
        ''', (batch_id,))

        cursor.execute('''
            UPDATE batch_variations
            SET status = 'cancelled'
            WHERE batch_link_id IN (
                SELECT id FROM batch_links WHERE batch_id = ?
            ) AND status = 'pending'
        ''', (batch_id,))


# ============ Video Job Operations ============

def create_video_job(
    job_type: str,
    image_paths: List[str],
    audio_path: str = None,
    folder_name: str = None,
    source_session_id: str = None,
    variation_key: str = None
) -> str:
    """Create a video job and return its ID."""
    import json
    job_id = str(uuid.uuid4())

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO video_jobs (id, job_type, image_paths, audio_path, folder_name, source_session_id, variation_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (job_id, job_type, json.dumps(image_paths), audio_path, folder_name, source_session_id, variation_key))
    return job_id


def get_video_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get video job by ID."""
    import json
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM video_jobs WHERE id = ?', (job_id,))
        row = cursor.fetchone()
        if row:
            job = dict(row)
            if job.get('image_paths'):
                try:
                    job['image_paths'] = json.loads(job['image_paths'])
                except:
                    pass
            return job
        return None


def update_video_job_status(
    job_id: str,
    status: str,
    error_message: str = None,
    output_path: str = None,
    drive_url: str = None
):
    """Update video job status."""
    with get_db() as conn:
        cursor = conn.cursor()
        updates = ['status = ?']
        params = [status]

        if status == 'processing':
            updates.append('started_at = ?')
            params.append(datetime.utcnow().isoformat())
        elif status in ('completed', 'failed'):
            updates.append('completed_at = ?')
            params.append(datetime.utcnow().isoformat())

        if error_message is not None:
            updates.append('error_message = ?')
            params.append(error_message)

        if output_path is not None:
            updates.append('output_path = ?')
            params.append(output_path)

        if drive_url is not None:
            updates.append('drive_url = ?')
            params.append(drive_url)

        params.append(job_id)
        cursor.execute(f'''
            UPDATE video_jobs SET {', '.join(updates)} WHERE id = ?
        ''', params)


def get_next_pending_video_job() -> Optional[Dict[str, Any]]:
    """Get the next pending video job (FIFO order)."""
    import json
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM video_jobs
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 1
        ''')
        row = cursor.fetchone()
        if row:
            job = dict(row)
            if job.get('image_paths'):
                try:
                    job['image_paths'] = json.loads(job['image_paths'])
                except:
                    pass
            return job
        return None


def list_video_jobs(
    status: str = None,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """List video jobs with optional status filter."""
    import json
    with get_db() as conn:
        cursor = conn.cursor()

        query = 'SELECT * FROM video_jobs WHERE 1=1'
        params = []

        if status:
            query += ' AND status = ?'
            params.append(status)

        query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'
        params.extend([limit, offset])

        cursor.execute(query, params)
        jobs = []
        for row in cursor.fetchall():
            job = dict(row)
            if job.get('image_paths'):
                try:
                    job['image_paths'] = json.loads(job['image_paths'])
                except:
                    pass
            jobs.append(job)
        return jobs


def get_video_jobs_count(status: str = None) -> int:
    """Get count of video jobs, optionally by status."""
    with get_db() as conn:
        cursor = conn.cursor()

        query = 'SELECT COUNT(*) as count FROM video_jobs WHERE 1=1'
        params = []

        if status:
            query += ' AND status = ?'
            params.append(status)

        cursor.execute(query, params)
        return cursor.fetchone()['count']


# Initialize database on import
init_db()
