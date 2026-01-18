#!/usr/bin/env python3
"""
Cleanup Stuck Jobs Script

Finds and handles jobs stuck in "processing" state for too long.

Usage:
    python cleanup_stuck_jobs.py --dry-run              # Preview what would be cleaned
    python cleanup_stuck_jobs.py --action=fail          # Mark stuck jobs as failed
    python cleanup_stuck_jobs.py --action=delete        # Delete stuck jobs entirely
    python cleanup_stuck_jobs.py --action=reset         # Reset to pending (will retry)
    python cleanup_stuck_jobs.py --threshold=60         # Jobs stuck > 60 minutes (default: 30)

Examples:
    # See what's stuck without making changes
    python cleanup_stuck_jobs.py --dry-run

    # Delete all jobs stuck for more than 1 hour
    python cleanup_stuck_jobs.py --action=delete --threshold=60
"""

import argparse
import sqlite3
from datetime import datetime, timedelta
import os

# Database path
DB_PATH = os.path.join(os.path.dirname(__file__), 'batch_processing.db')


def get_stuck_jobs(conn, threshold_minutes):
    """Find jobs stuck in 'processing' for longer than threshold."""
    c = conn.cursor()

    threshold_time = datetime.now() - timedelta(minutes=threshold_minutes)
    threshold_str = threshold_time.strftime('%Y-%m-%d %H:%M:%S')

    # Find stuck jobs
    c.execute("""
        SELECT id, status, folder_name, created_at, started_at
        FROM jobs
        WHERE status = 'processing'
        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
    """, (threshold_str, threshold_str))

    jobs = c.fetchall()
    return jobs


def get_stuck_batches(conn, threshold_minutes):
    """Find batches stuck in 'processing' for longer than threshold."""
    c = conn.cursor()

    threshold_time = datetime.now() - timedelta(minutes=threshold_minutes)
    threshold_str = threshold_time.strftime('%Y-%m-%d %H:%M:%S')

    # Find stuck batches
    c.execute("""
        SELECT id, status, total_links, created_at, started_at
        FROM batches
        WHERE status = 'processing'
        AND (started_at < ? OR (started_at IS NULL AND created_at < ?))
    """, (threshold_str, threshold_str))

    batches = c.fetchall()
    return batches


def cleanup_jobs(conn, action, dry_run=False):
    """Perform cleanup action on stuck jobs."""
    c = conn.cursor()

    if action == 'delete':
        if not dry_run:
            c.execute("DELETE FROM jobs WHERE status = 'processing'")
        return c.rowcount if not dry_run else "would delete"

    elif action == 'fail':
        if not dry_run:
            c.execute("""
                UPDATE jobs
                SET status = 'failed',
                    error_message = 'Automatically marked as failed - stuck in processing'
                WHERE status = 'processing'
            """)
        return c.rowcount if not dry_run else "would mark failed"

    elif action == 'reset':
        if not dry_run:
            c.execute("""
                UPDATE jobs
                SET status = 'pending',
                    started_at = NULL
                WHERE status = 'processing'
            """)
        return c.rowcount if not dry_run else "would reset"

    return 0


def cleanup_batches(conn, action, dry_run=False):
    """Perform cleanup action on stuck batches."""
    c = conn.cursor()

    if action == 'delete':
        if not dry_run:
            # Delete batch links first
            c.execute("""
                DELETE FROM batch_links
                WHERE batch_id IN (SELECT id FROM batches WHERE status = 'processing')
            """)
            links_deleted = c.rowcount

            # Then delete batches
            c.execute("DELETE FROM batches WHERE status = 'processing'")
            batches_deleted = c.rowcount

            return f"{batches_deleted} batches, {links_deleted} links"
        return "would delete"

    elif action == 'fail':
        if not dry_run:
            c.execute("""
                UPDATE batches
                SET status = 'failed',
                    error_message = 'Automatically marked as failed - stuck in processing'
                WHERE status = 'processing'
            """)
            c.execute("""
                UPDATE batch_links
                SET status = 'failed',
                    error_message = 'Parent batch marked as failed'
                WHERE batch_id IN (SELECT id FROM batches WHERE status = 'failed')
                AND status = 'processing'
            """)
        return c.rowcount if not dry_run else "would mark failed"

    elif action == 'reset':
        if not dry_run:
            c.execute("""
                UPDATE batches
                SET status = 'pending',
                    started_at = NULL
                WHERE status = 'processing'
            """)
            c.execute("""
                UPDATE batch_links
                SET status = 'pending',
                    started_at = NULL
                WHERE status = 'processing'
            """)
        return c.rowcount if not dry_run else "would reset"

    return 0


def main():
    parser = argparse.ArgumentParser(description='Cleanup stuck jobs')
    parser.add_argument('--action', choices=['fail', 'delete', 'reset'],
                        help='Action to take on stuck jobs')
    parser.add_argument('--threshold', type=int, default=30,
                        help='Minutes threshold for considering a job stuck (default: 30)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview what would be cleaned without making changes')

    args = parser.parse_args()

    if not args.action and not args.dry_run:
        parser.print_help()
        print("\n⚠️  Use --dry-run to preview or --action to perform cleanup")
        return

    conn = sqlite3.connect(DB_PATH)

    print(f"🔍 Looking for jobs stuck in 'processing' for > {args.threshold} minutes...\n")

    # Find stuck items
    stuck_jobs = get_stuck_jobs(conn, args.threshold)
    stuck_batches = get_stuck_batches(conn, args.threshold)

    # Display findings
    print("=== Stuck Jobs ===")
    if stuck_jobs:
        for job in stuck_jobs:
            print(f"  {job[0][:8]} | {job[2] or 'unnamed'} | started: {job[4]}")
    else:
        print("  None found")

    print("\n=== Stuck Batches ===")
    if stuck_batches:
        for batch in stuck_batches:
            print(f"  {batch[0][:8]} | {batch[2]} links | started: {batch[4]}")
    else:
        print("  None found")

    print(f"\nTotal: {len(stuck_jobs)} jobs, {len(stuck_batches)} batches")

    # Perform action
    if args.dry_run:
        print(f"\n🔍 DRY RUN - no changes made")
        if args.action:
            print(f"   Would {args.action}: {len(stuck_jobs)} jobs, {len(stuck_batches)} batches")
    elif args.action:
        print(f"\n🧹 Performing action: {args.action}")

        job_result = cleanup_jobs(conn, args.action)
        batch_result = cleanup_batches(conn, args.action)

        conn.commit()

        print(f"   Jobs: {job_result}")
        print(f"   Batches: {batch_result}")
        print("✅ Cleanup complete!")

    conn.close()


if __name__ == '__main__':
    main()
