/**
 * Report API routes — /api/reports/*
 */
import { Router, Request, Response } from "express";
import {
  getDailyAvailability,
  getCurrentSnapshot,
  getUnmanagedCount,
  getHardwareInventory,
  getRmaSummary,
  getReliabilityData,
  getMtbfData,
  getJobMetricsData,
} from "../report-store.js";
import { getLiveUtilization, getHistoryUtilization, getVcUtilizationSummary, getVcDailyBreakdown } from "../utilization-store.js";
import { startGpuCollector } from "../gpu-collector.js";
import { createPool } from "../db/client.js";

export const reportRouter = Router();

// ─── GET /api/reports/availability ───────────────────────────────────
// Query params: from (YYYY-MM-DD), to (YYYY-MM-DD), category (all|h200|b300|cpu|storage|ctrl)

reportRouter.get("/availability", async (req: Request, res: Response) => {
  try {
    const from = req.query.from as string;
    const to = req.query.to as string;
    const category = (req.query.category as string) || "all";

    if (!from || !to) {
      res.status(400).json({ error: "from and to are required (YYYY-MM-DD)" });
      return;
    }

    // Validate date format
    const dateRe = /^\d{4}-\d{2}-\d{2}$/;
    if (!dateRe.test(from) || !dateRe.test(to)) {
      res.status(400).json({ error: "Invalid date format, use YYYY-MM-DD" });
      return;
    }

    const [daily, current, unmanagedCount, inventory, rma] =
      await Promise.all([
        getDailyAvailability(from, to, category),
        getCurrentSnapshot(category),
        getUnmanagedCount(category),
        getHardwareInventory(),
        getRmaSummary(from, to, category),
      ]);

    // Add Unmanaged row to current snapshot
    const totalNodes = current.reduce((sum, r) => sum + r.count, 0);
    if (unmanagedCount > 0) {
      current.push({
        status_group: "Unmanaged",
        count: unmanagedCount,
        pct:
          totalNodes + unmanagedCount > 0
            ? Math.round(
                (unmanagedCount / (totalNodes + unmanagedCount)) * 1000
              ) / 10
            : 0,
      });
    }

    res.json({ daily, current, inventory, rma });
  } catch (err: any) {
    console.error("[reports/availability]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ─── GET /api/reports/reliability ────────────────────────────────────
// Query params: from (YYYY-MM-DD), to (YYYY-MM-DD), category (all|h200|b300|cpu|storage|ctrl)

reportRouter.get("/reliability", async (req: Request, res: Response) => {
  // Admin only
  if (!(req as any).auth?.isAdmin) {
    res.status(403).json({ error: "Admin access required" });
    return;
  }
  try {
    const from = req.query.from as string;
    const to = req.query.to as string;
    const category = (req.query.category as string) || "all";

    if (!from || !to) {
      res.status(400).json({ error: "from and to are required (YYYY-MM-DD)" });
      return;
    }

    const dateRe = /^\d{4}-\d{2}-\d{2}$/;
    if (!dateRe.test(from) || !dateRe.test(to)) {
      res.status(400).json({ error: "Invalid date format, use YYYY-MM-DD" });
      return;
    }

    const data = await getReliabilityData(from, to, category);
    res.json(data);
  } catch (err: any) {
    console.error("[reports/reliability]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ─── GET /api/reports/reliability/mtbf ──────────────────────────────
reportRouter.get("/reliability/mtbf", async (req: Request, res: Response) => {
  if (!(req as any).auth?.isAdmin) {
    res.status(403).json({ error: "Admin access required" });
    return;
  }
  try {
    const from = req.query.from as string;
    const to = req.query.to as string;
    const category = (req.query.category as string) || "all";
    const reasonSearch = (req.query.reason_search as string) || "";
    const nodeSearch = (req.query.node_search as string) || "";
    const mtbfType = (req.query.mtbf_type as string) || "hardware";

    if (!from || !to) {
      res.status(400).json({ error: "from and to are required (YYYY-MM-DD)" });
      return;
    }

    const dateRe = /^\d{4}-\d{2}-\d{2}$/;
    if (!dateRe.test(from) || !dateRe.test(to)) {
      res.status(400).json({ error: "Invalid date format, use YYYY-MM-DD" });
      return;
    }

    const data = await getMtbfData(from, to, category, reasonSearch, nodeSearch, mtbfType);
    res.json(data);
  } catch (err: any) {
    console.error("[reports/mtbf]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ─── GET /api/reports/job-metrics ────────────────────────────────────
reportRouter.get("/job-metrics", async (req: Request, res: Response) => {
  if (!(req as any).auth?.isAdmin) {
    res.status(403).json({ error: "Admin access required" });
    return;
  }
  try {
    const from = req.query.from as string;
    const to = req.query.to as string;
    const vc = (req.query.vc as string) || "all";

    if (!from || !to) {
      res.status(400).json({ error: "from and to are required (YYYY-MM-DD)" });
      return;
    }

    const dateRe = /^\d{4}-\d{2}-\d{2}$/;
    if (!dateRe.test(from) || !dateRe.test(to)) {
      res.status(400).json({ error: "Invalid date format, use YYYY-MM-DD" });
      return;
    }

    const data = await getJobMetricsData(from, to, vc);
    res.json(data);
  } catch (err: any) {
    console.error("[reports/job-metrics]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ─── Utilization ─────────────────────────────────────────────────────

const chatUiPool = createPool(process.env.CHAT_UI_DATABASE_URL!);

// Start GPU utilization collector (every 5 min → snapshots in chat-ui DB)
// Only consumer is the utilization dashboard
startGpuCollector(chatUiPool);

// GET /api/reports/utilization/live — real-time from Prometheus
reportRouter.get("/utilization/live", async (req: Request, res: Response) => {
  try {
    const sku = (req.query.sku as string) || "all";
    const data = await getLiveUtilization(sku);
    res.json(data);
  } catch (err: any) {
    console.error("[reports/utilization/live]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/reports/utilization/history — historical from PostgreSQL
reportRouter.get("/utilization/history", async (req: Request, res: Response) => {
  try {
    const from = req.query.from as string;
    const to = req.query.to as string;
    const sku = (req.query.sku as string) || "all";

    if (!from || !to) {
      res.status(400).json({ error: "from and to are required (YYYY-MM-DD)" });
      return;
    }

    const data = await getHistoryUtilization(chatUiPool, from, to, sku);
    res.json(data);
  } catch (err: any) {
    console.error("[reports/utilization/history]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/reports/utilization/vc-summary — per-VC breakdown for date range
reportRouter.get("/utilization/vc-summary", async (req: Request, res: Response) => {
  try {
    const from = req.query.from as string;
    const to = req.query.to as string;
    const sku = (req.query.sku as string) || "all";

    if (!from || !to) {
      res.status(400).json({ error: "from and to are required (YYYY-MM-DD)" });
      return;
    }

    const data = await getVcUtilizationSummary(chatUiPool, from, to, sku);
    res.json({ vcs: data });
  } catch (err: any) {
    console.error("[reports/utilization/vc-summary]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/reports/utilization/vc-daily — per-VC daily breakdown for date range
reportRouter.get("/utilization/vc-daily", async (req: Request, res: Response) => {
  try {
    const from = req.query.from as string;
    const to = req.query.to as string;
    const sku = (req.query.sku as string) || "all";

    if (!from || !to) {
      res.status(400).json({ error: "from and to are required (YYYY-MM-DD)" });
      return;
    }

    const data = await getVcDailyBreakdown(chatUiPool, from, to, sku);
    res.json({ daily: data });
  } catch (err: any) {
    console.error("[reports/utilization/vc-daily]", err.message);
    res.status(500).json({ error: err.message });
  }
});
