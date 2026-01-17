"""
Unit tests for database module.
"""
import os
import sys
import pytest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestDatabaseInit:
    """Tests for database initialization."""

    def test_init_db_creates_tables(self, test_db):
        """init_db() should create required tables."""
        from database import get_db
        with get_db() as db:
            cursor = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = [row[0] for row in cursor.fetchall()]
            # Check that essential tables exist
            assert len(tables) > 0


class TestJobOperations:
    """Tests for job CRUD operations."""

    def test_create_job(self, test_db):
        """create_job() should return a job ID."""
        from database import create_job
        job_id = create_job(
            job_type='single',
            tiktok_url='https://www.tiktok.com/@test/photo/123',
            total_links=1,
            folder_name='test_folder'
        )
        assert job_id is not None
        assert isinstance(job_id, str)

    def test_get_job(self, test_db):
        """get_job() should return job data."""
        from database import create_job, get_job
        job_id = create_job(
            job_type='single',
            tiktok_url='https://www.tiktok.com/@test/photo/123',
            total_links=1,
            folder_name='test_folder'
        )
        job = get_job(job_id)
        assert job is not None
        assert job['folder_name'] == 'test_folder'

    def test_get_job_invalid_id(self, test_db):
        """get_job() with invalid ID should return None."""
        from database import get_job
        job = get_job('nonexistent-job-id')
        assert job is None

    def test_update_job_status(self, test_db):
        """update_job_status() should update the job."""
        from database import create_job, get_job, update_job_status
        job_id = create_job(
            job_type='single',
            tiktok_url='https://www.tiktok.com/@test/photo/123',
            total_links=1,
            folder_name='test_folder'
        )
        update_job_status(job_id, 'completed')
        job = get_job(job_id)
        assert job['status'] == 'completed'

    def test_delete_job(self, test_db):
        """delete_job() should remove the job."""
        from database import create_job, get_job, delete_job
        job_id = create_job(
            job_type='single',
            tiktok_url='https://www.tiktok.com/@test/photo/123',
            total_links=1,
            folder_name='test_folder'
        )
        delete_job(job_id)
        job = get_job(job_id)
        assert job is None

    def test_list_jobs(self, test_db):
        """list_jobs() should return list of jobs."""
        from database import create_job, list_jobs
        # Create a few jobs
        create_job('single', 'https://tiktok.com/1', 1, 'folder1')
        create_job('single', 'https://tiktok.com/2', 1, 'folder2')

        jobs = list_jobs()
        assert isinstance(jobs, list)
        assert len(jobs) >= 2


class TestBatchOperations:
    """Tests for batch CRUD operations."""

    def test_create_batch(self, test_db):
        """create_batch() should return a batch ID."""
        from database import create_batch
        batch_id = create_batch(
            total_links=5,
            preset_id='classic_shadow',
            drive_folder_id='test_folder_123'
        )
        assert batch_id is not None
        assert isinstance(batch_id, str)

    def test_get_batch(self, test_db):
        """get_batch() should return batch data."""
        from database import create_batch, get_batch
        batch_id = create_batch(
            total_links=5,
            preset_id='classic_shadow',
            drive_folder_id='test_folder_123'
        )
        batch = get_batch(batch_id)
        assert batch is not None
        assert batch['total_links'] == 5

    def test_get_batch_invalid_id(self, test_db):
        """get_batch() with invalid ID should return None."""
        from database import get_batch
        batch = get_batch('nonexistent-batch-id')
        assert batch is None

    def test_update_batch_status(self, test_db):
        """update_batch_status() should update the batch."""
        from database import create_batch, get_batch, update_batch_status
        batch_id = create_batch(
            total_links=5,
            preset_id='classic_shadow',
            drive_folder_id='test_folder_123'
        )
        update_batch_status(batch_id, 'completed')
        batch = get_batch(batch_id)
        assert batch['status'] == 'completed'


class TestBatchLinkOperations:
    """Tests for batch link CRUD operations."""

    def test_create_batch_link(self, test_db):
        """create_batch_link() should return a link ID."""
        from database import create_batch, create_batch_link
        batch_id = create_batch(5, 'classic_shadow', 'folder_123')
        link_id = create_batch_link(
            batch_id=batch_id,
            tiktok_url='https://www.tiktok.com/@test/photo/123',
            product_description='Test product'
        )
        assert link_id is not None
        assert isinstance(link_id, str)

    def test_get_batch_link(self, test_db):
        """get_batch_link() should return link data."""
        from database import create_batch, create_batch_link, get_batch_link
        batch_id = create_batch(5, 'classic_shadow', 'folder_123')
        link_id = create_batch_link(
            batch_id=batch_id,
            tiktok_url='https://www.tiktok.com/@test/photo/123',
            product_description='Test product'
        )
        link = get_batch_link(link_id)
        assert link is not None
        assert link['product_description'] == 'Test product'

    def test_get_batch_links(self, test_db):
        """get_batch_links() should return all links for a batch."""
        from database import create_batch, create_batch_link, get_batch_links
        batch_id = create_batch(3, 'classic_shadow', 'folder_123')
        create_batch_link(batch_id, 'https://tiktok.com/1', 'Product 1')
        create_batch_link(batch_id, 'https://tiktok.com/2', 'Product 2')
        create_batch_link(batch_id, 'https://tiktok.com/3', 'Product 3')

        links = get_batch_links(batch_id)
        assert len(links) == 3


class TestVariationOperations:
    """Tests for variation CRUD operations."""

    def test_create_variation(self, test_db):
        """create_variation() should return a variation ID."""
        from database import create_batch, create_batch_link, create_variation
        batch_id = create_batch(1, 'classic_shadow', 'folder_123')
        link_id = create_batch_link(batch_id, 'https://tiktok.com/1', 'Product')
        variation_id = create_variation(link_id, 1)
        assert variation_id is not None
        assert isinstance(variation_id, str)

    def test_get_variation(self, test_db):
        """get_variation() should return variation data."""
        from database import (create_batch, create_batch_link,
                              create_variation, get_variation)
        batch_id = create_batch(1, 'classic_shadow', 'folder_123')
        link_id = create_batch_link(batch_id, 'https://tiktok.com/1', 'Product')
        variation_id = create_variation(link_id, 1)
        variation = get_variation(variation_id)
        assert variation is not None
        assert variation['variation_num'] == 1

    def test_get_link_variations(self, test_db):
        """get_link_variations() should return all variations for a link."""
        from database import (create_batch, create_batch_link,
                              create_variation, get_link_variations)
        batch_id = create_batch(1, 'classic_shadow', 'folder_123')
        link_id = create_batch_link(batch_id, 'https://tiktok.com/1', 'Product')
        create_variation(link_id, 1)
        create_variation(link_id, 2)
        create_variation(link_id, 3)

        variations = get_link_variations(link_id)
        assert len(variations) == 3
