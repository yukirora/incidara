import { describe, it, expect, vi } from "vitest";
import { getHistoryUtilization } from "./utilization-store.js";

function makeMockPool(rows: any[]) {
  return {
    query: vi.fn(async () => ({ rows })),
  } as any;
}

describe("utilization-store", () => {
  describe("getHistoryUtilization", () => {
    it("computes daily GPU-hours from 5-min snapshots", async () => {
      // Simulate 2 days with 2 snapshots each (simplified)
      const pool = makeMockPool([
        { day: "2026-06-16", total_gpu_hours: "24576.0", allocated_gpu_hours: "24000.0", utilized_gpu_hours: "20000.0", idle_gpu_hours: "4000.0", non_used_gpu_hours: "576.0", unhealthy_gpu_hours: "0", avg_utilization: "92.5" },
        { day: "2026-06-17", total_gpu_hours: "24576.0", allocated_gpu_hours: "23500.0", utilized_gpu_hours: "19500.0", idle_gpu_hours: "4000.0", non_used_gpu_hours: "1076.0", unhealthy_gpu_hours: "0", avg_utilization: "91.0" },
      ]);

      const result = await getHistoryUtilization(pool, "2026-06-16", "2026-06-18", "h200");

      // Daily rows
      expect(result.daily.length).toBe(2);
      expect(result.daily[0].day).toBe("2026-06-16");
      expect(result.daily[0].total_gpu_hours).toBe(24576.0);
      expect(result.daily[0].utilized_gpu_hours).toBe(20000.0);
      expect(result.daily[0].avg_utilization).toBe(92.5);
      expect(result.daily[1].day).toBe("2026-06-17");

      // Summary (second call to pool.query)
      expect(pool.query).toHaveBeenCalledTimes(2);
    });

    it("returns empty daily array when no snapshots", async () => {
      const pool = {
        query: vi.fn()
          .mockResolvedValueOnce({ rows: [] }) // daily query
          .mockResolvedValueOnce({ rows: [{}] }), // summary query
      } as any;

      const result = await getHistoryUtilization(pool, "2026-06-16", "2026-06-18", "h200");
      expect(result.daily).toEqual([]);
      expect(result.summary.total_gpu_hours).toBe(0);
    });

    it("applies SKU filter to SQL", async () => {
      const pool = makeMockPool([]);

      await getHistoryUtilization(pool, "2026-06-16", "2026-06-18", "h200");

      const sql = pool.query.mock.calls[0][0] as string;
      expect(sql).toContain("AND sku = 'h200'");
    });

    it("skips SKU filter when sku is 'all'", async () => {
      const pool = makeMockPool([]);

      await getHistoryUtilization(pool, "2026-06-16", "2026-06-18", "all");

      const sql = pool.query.mock.calls[0][0] as string;
      expect(sql).not.toContain("AND sku =");
    });

    it("GPU-hours formula: uses LEAD() window function for actual interval", async () => {
      const pool = makeMockPool([]);
      await getHistoryUtilization(pool, "2026-06-16", "2026-06-18");

      const sql = pool.query.mock.calls[0][0] as string;
      // Should use LEAD() window function for actual time gap
      expect(sql).toContain("LEAD(timestamp)");
      expect(sql).toContain("interval_hours");
      // Should cap intervals at 1 hour to ignore gaps
      expect(sql).toContain("interval_hours < 1");
    });
  });
});
