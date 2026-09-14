"""Feishu Bitable API client."""

import time
import requests


class FeishuBitableClient:
    """Client for Feishu Bitable (multidimensional table) API.
    
    Uses tenant_access_token for app-level auth.
    Token is cached and auto-refreshed on expiry (2h lifetime).
    """

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expires = 0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token

        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu auth failed: {data}")

        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200)
        return self._token

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}

    def _get(self, url: str, params: dict = None) -> dict:
        resp = requests.get(url, headers=self._headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu API error: {data}")
        return data.get("data", {})

    def _post(self, url: str, json: dict = None) -> dict:
        resp = requests.post(url, headers=self._headers, json=json, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu API error: {data}")
        return data.get("data", {})

    # ---- Table operations ----

    def list_tables(self, app_token: str) -> list[dict]:
        """List all tables in a Bitable app."""
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables"
        data = self._get(url)
        return data.get("items", [])

    def get_table_schema(self, app_token: str, table_id: str) -> list[dict]:
        """Get field definitions for a table."""
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        data = self._get(url)
        return data.get("items", [])

    def list_records(
        self,
        app_token: str,
        table_id: str,
        filter_expr: str = "",
        sort: str = "",
        page_size: int = 20,
        page_token: str = "",
    ) -> dict:
        """List records from a table with optional filter and sort.
        
        Args:
            app_token: Bitable app token.
            table_id: Table ID.
            filter_expr: Filter expression, e.g., 'CurrentValue.[status] = "open"'.
            sort: Sort expression, e.g., '["created_at desc"]'.
            page_size: Number of records per page (max 500).
            page_token: Page token for pagination.
        
        Returns:
            Dict with 'items' (list of records), 'total', 'has_more', 'page_token'.
        """
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        params = {"page_size": page_size}
        if filter_expr:
            params["filter"] = filter_expr
        if sort:
            params["sort"] = sort
        if page_token:
            params["page_token"] = page_token

        data = self._get(url, params=params)
        return {
            "items": data.get("items", []),
            "total": data.get("total", 0),
            "has_more": data.get("has_more", False),
            "page_token": data.get("page_token", ""),
        }

    def get_record(self, app_token: str, table_id: str, record_id: str) -> dict:
        """Get a single record by ID."""
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        data = self._get(url)
        return data.get("record", {})

    def list_all_records(
        self,
        app_token: str,
        table_id: str,
        filter_expr: str = "",
        sort: str = "",
    ) -> list[dict]:
        """List ALL records from a table, handling pagination automatically."""
        all_items = []
        page_token = ""
        while True:
            result = self.list_records(
                app_token, table_id,
                filter_expr=filter_expr,
                sort=sort,
                page_size=500,
                page_token=page_token,
            )
            all_items.extend(result["items"])
            if not result["has_more"]:
                break
            page_token = result["page_token"]
        return all_items
