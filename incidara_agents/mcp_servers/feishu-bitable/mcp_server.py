"""MCP server for Feishu Bitable (multidimensional table) access.

Provides tools to query Feishu Bitable tables — used by the detection agent
to read user-reported node issues.

Known categories: "Node Unhealthy", "Job Failure", "Vc Access", "其他"
Processed = "Y" means admin marked done. Detail = "done" means ops handled it.
A report is truly new only when Detail != "done".
"""

from __future__ import annotations

import json
import os

from fastmcp import FastMCP

from feishu_bitable.client import FeishuBitableClient

# ---------------------------------------------------------------------------
# Config from env vars
# ---------------------------------------------------------------------------

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_BASE_TOKEN = os.environ.get("FEISHU_BASE_TOKEN", "")
FEISHU_TABLE_ID = os.environ.get("FEISHU_TABLE_ID", "")

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

server = FastMCP("feishu-bitable")

_client: FeishuBitableClient | None = None


def _raise_tool_error(exc: Exception) -> None:
    raise RuntimeError(str(exc)) from exc


def _get_client() -> FeishuBitableClient:
    global _client
    if _client is None:
        if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
            raise ValueError("FEISHU_APP_ID and FEISHU_APP_SECRET must be set")
        _client = FeishuBitableClient(FEISHU_APP_ID, FEISHU_APP_SECRET)
    return _client


# Field names as they appear in the Feishu table
_SUMMARY_FIELDS = [
    "record_id", "提交时间", "UserName", "JobName", "VCName",
    "Issue Category", "Abnormal Node IPs", "Issue Description",
    "How to reproduce", "Did you find any abnormal node",
    "Issue Category-其他-补充内容", "Detail", "Processed",
]


def _extract_summary(record: dict) -> dict:
    """Extract the useful fields from a record."""
    f = record.get("fields", {})
    return {k: f.get(k, "") for k in _SUMMARY_FIELDS if k != "record_id"} | {
        "record_id": record.get("record_id", ""),
    }


def _register_tools(mcp: FastMCP):

    # ------------------------------------------------------------------
    # Generic / low-level tools
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_tables_tool(app_token: str = "") -> str:
        """List all tables in a Feishu Bitable app.

        Args:
            app_token: Bitable app token. If empty, uses FEISHU_BASE_TOKEN from env.
        """
        token = app_token or FEISHU_BASE_TOKEN
        if not token:
            raise ValueError("app_token required (or set FEISHU_BASE_TOKEN)")
        try:
            tables = _get_client().list_tables(token)
            return json.dumps(tables, default=str, ensure_ascii=False)
        except Exception as e:
            _raise_tool_error(e)

    @mcp.tool()
    def get_table_schema_tool(app_token: str = "", table_id: str = "") -> str:
        """Get column definitions for a Feishu Bitable table.

        Args:
            app_token: Bitable app token. If empty, uses FEISHU_BASE_TOKEN from env.
            table_id: Table ID. If empty, uses FEISHU_TABLE_ID from env.
        """
        at = app_token or FEISHU_BASE_TOKEN
        tid = table_id or FEISHU_TABLE_ID
        if not at or not tid:
            raise ValueError("app_token and table_id required")
        try:
            fields = _get_client().get_table_schema(at, tid)
            return json.dumps(fields, default=str, ensure_ascii=False)
        except Exception as e:
            _raise_tool_error(e)

    @mcp.tool()
    def query_table_tool(
        app_token: str = "",
        table_id: str = "",
        filter_expr: str = "",
        sort: str = "",
        page_size: int = 20,
        page_token: str = "",
        since_days: int = 0,
    ) -> str:
        """Query records from a Feishu Bitable table with optional filter and pagination.

        Args:
            app_token: Bitable app token. If empty, uses FEISHU_BASE_TOKEN from env.
            table_id: Table ID. If empty, uses FEISHU_TABLE_ID from env.
            filter_expr: Filter expression, e.g., 'CurrentValue.[status] = "open"'.
                         See Feishu API docs for filter syntax. Use && for AND.
                         Time filter: 'CurrentValue.[提交时间] > Today()-7' for last 7 days.
            sort: Sort expression, e.g., '["提交时间 desc"]'.
            page_size: Number of records per page (max 500, default 20).
            page_token: Page token for pagination (from previous response).
            since_days: Only return records with 提交时间 within this many days.
                        0 means no time filter. Adds to filter_expr if provided.
        """
        at = app_token or FEISHU_BASE_TOKEN
        tid = table_id or FEISHU_TABLE_ID
        if not at or not tid:
            raise ValueError("app_token and table_id required")
        try:
            # Add time filter if since_days specified
            if since_days > 0:
                time_filter = f"CurrentValue.[提交时间] > Today()-{since_days}"
                if filter_expr:
                    filter_expr = f"{filter_expr} && {time_filter}"
                else:
                    filter_expr = time_filter
            result = _get_client().list_records(
                at, tid,
                filter_expr=filter_expr,
                sort=sort,
                page_size=min(page_size, 500),
                page_token=page_token,
            )
            items = []
            for record in result["items"]:
                items.append(_extract_summary(record))
            return json.dumps({
                "items": items,
                "total": result["total"],
                "has_more": result["has_more"],
                "page_token": result["page_token"],
            }, default=str, ensure_ascii=False)
        except Exception as e:
            _raise_tool_error(e)

    @mcp.tool()
    def get_record_tool(
        app_token: str = "",
        table_id: str = "",
        record_id: str = "",
    ) -> str:
        """Get full detail of a single record by record ID.
        Returns all fields including the complete Issue Description.

        Args:
            app_token: Bitable app token. If empty, uses FEISHU_BASE_TOKEN from env.
            table_id: Table ID. If empty, uses FEISHU_TABLE_ID from env.
            record_id: The record ID to fetch.
        """
        at = app_token or FEISHU_BASE_TOKEN
        tid = table_id or FEISHU_TABLE_ID
        if not at or not tid:
            raise ValueError("app_token and table_id required")
        if not record_id:
            raise ValueError("record_id required")
        try:
            record = _get_client().get_record(at, tid, record_id)
            return json.dumps(_extract_summary(record), default=str, ensure_ascii=False)
        except Exception as e:
            _raise_tool_error(e)

    # ------------------------------------------------------------------
    # High-level / convenience tools for LTP issue reports
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_issue_categories_tool(
        app_token: str = "",
        table_id: str = "",
    ) -> str:
        """List all Issue Category values and their counts in the table.
        Useful to see what categories exist before querying.

        Args:
            app_token: Bitable app token. If empty, uses FEISHU_BASE_TOKEN from env.
            table_id: Table ID. If empty, uses FEISHU_TABLE_ID from env.
        """
        at = app_token or FEISHU_BASE_TOKEN
        tid = table_id or FEISHU_TABLE_ID
        if not at or not tid:
            raise ValueError("app_token and table_id required")
        try:
            records = _get_client().list_all_records(at, tid)
            from collections import Counter
            cats = Counter(r.get("fields", {}).get("Issue Category", "") for r in records)
            return json.dumps({
                "categories": [
                    {"name": name, "total": count} for name, count in cats.most_common()
                ]
            }, ensure_ascii=False)
        except Exception as e:
            _raise_tool_error(e)

    @mcp.tool()
    def get_unprocessed_reports_tool(
        app_token: str = "",
        table_id: str = "",
        category: str = "",
        since_days: int = 0,
    ) -> str:
        """Get unprocessed user reports. A report is unprocessed when
        Processed = "N" or Processed is empty (not yet reviewed).

        Args:
            app_token: Bitable app token. If empty, uses FEISHU_BASE_TOKEN from env.
            table_id: Table ID. If empty, uses FEISHU_TABLE_ID from env.
            category: Optional filter by Issue Category. Known values:
                      "Node Unhealthy", "Job Failure", "Vc Access", "其他".
                      If empty, returns reports from all categories.
            since_days: Only return reports submitted within this many days from today.
                        0 means no time filter (all time). Default 0.
        """
        at = app_token or FEISHU_BASE_TOKEN
        tid = table_id or FEISHU_TABLE_ID
        if not at or not tid:
            raise ValueError("app_token and table_id required")
        try:
            # Unprocessed = Processed is "N" or empty (not yet reviewed)
            filter_parts = ['CurrentValue.[Processed] = "N" || CurrentValue.[Processed] = ""']
            if category:
                filter_parts.append(f'CurrentValue.[Issue Category] = "{category}"')
            if since_days > 0:
                filter_parts.append(f"CurrentValue.[提交时间] > Today()-{since_days}")
            filter_expr = " && ".join(filter_parts)

            records = _get_client().list_all_records(
                at, tid, filter_expr=filter_expr, sort='["提交时间 desc"]'
            )
            items = [_extract_summary(r) for r in records]
            return json.dumps({
                "total": len(items),
                "reports": items,
            }, default=str, ensure_ascii=False)
        except Exception as e:
            _raise_tool_error(e)

    @mcp.tool()
    def get_node_unhealthy_reports_tool(
        app_token: str = "",
        table_id: str = "",
        since_days: int = 0,
    ) -> str:
        """Get unprocessed Node Unhealthy reports that have node IPs.
        Shortcut for get_unprocessed_reports with category="Node Unhealthy",
        filtered to only records with a non-empty Abnormal Node IPs field.

        Args:
            app_token: Bitable app token. If empty, uses FEISHU_BASE_TOKEN from env.
            table_id: Table ID. If empty, uses FEISHU_TABLE_ID from env.
            since_days: Only return reports submitted within this many days from today.
                        0 means no time filter (all time). Default 0.
        """
        at = app_token or FEISHU_BASE_TOKEN
        tid = table_id or FEISHU_TABLE_ID
        if not at or not tid:
            raise ValueError("app_token and table_id required")
        try:
            filter_parts = ['(CurrentValue.[Processed] = "N" || CurrentValue.[Processed] = "") && CurrentValue.[Issue Category] = "Node Unhealthy"']
            if since_days > 0:
                filter_parts.append(f"CurrentValue.[提交时间] > Today()-{since_days}")
            filter_expr = " && ".join(filter_parts)
            records = _get_client().list_all_records(
                at, tid, filter_expr=filter_expr, sort='["提交时间 desc"]'
            )
            items = []
            for r in records:
                f = r.get("fields", {})
                ip = f.get("Abnormal Node IPs", "")
                if not ip or not str(ip).strip():
                    continue
                items.append(_extract_summary(r))
            return json.dumps({
                "total": len(items),
                "reports": items,
            }, default=str, ensure_ascii=False)
        except Exception as e:
            _raise_tool_error(e)


_register_tools(server)


if __name__ == "__main__":
    server.run()
