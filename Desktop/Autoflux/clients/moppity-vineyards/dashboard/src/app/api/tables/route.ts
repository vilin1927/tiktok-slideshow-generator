export const dynamic = 'force-dynamic';

import { NextResponse } from 'next/server';
import getPool from '@/lib/db';

/**
 * GET /api/tables
 * Returns all public tables with row counts.
 * Includes core, system, staging, and access_* tables.
 */

interface TableInfo {
  table_name: string;
  row_count: number;
}

export async function GET() {
  try {
    const pool = getPool();

    // Get all public tables including staging tables
    const tablesResult = await pool.query(`
      SELECT table_name
      FROM information_schema.tables
      WHERE table_schema = 'public'
        AND table_type IN ('BASE TABLE', 'VIEW')
      ORDER BY table_name
    `);

    // Get row count for each table using a dynamic query
    // Using pg_class for fast approximate counts would be better at scale,
    // but with ~50K rows max this is fine with exact counts.
    const tables: TableInfo[] = [];

    for (const row of tablesResult.rows) {
      const tableName = row.table_name;

      // Validate table name to prevent SQL injection
      // (information_schema provides safe names, but defense in depth)
      if (!/^[a-z_][a-z0-9_]*$/.test(tableName)) {
        continue;
      }

      const countResult = await pool.query(
        `SELECT count(*)::integer AS count FROM "${tableName}"`
      );
      tables.push({
        table_name: tableName,
        row_count: countResult.rows[0].count,
      });
    }

    return NextResponse.json(tables);
  } catch (err) {
    console.error('[/api/tables] Error:', err);
    return NextResponse.json(
      { error: 'Failed to fetch table data' },
      { status: 500 }
    );
  }
}
