# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Data Source Integrators for Monitor Service

Based on the real-world implementations from the design doc.
These provide unified interfaces for all data sources.
"""

from copy import deepcopy
import os
import json
import time
from joblib import Parallel, delayed
import pandas as pd
import requests
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class LogEntry:
    message: str
    fields: Dict[str, Any]

def parse_interval(time_str: str) -> int:
    """Parse time string into seconds

    Supports formats:
    - '30s' -> 30 seconds
    - '5m' -> 300 seconds
    - '2h' -> 7200 seconds
    - '1d' -> 86400 seconds
    - '60' -> 60 seconds (no suffix)
    """
    if time_str.endswith('s'):
        return int(time_str[:-1])
    elif time_str.endswith('m'):
        return int(time_str[:-1]) * 60
    elif time_str.endswith('h'):
        return int(time_str[:-1]) * 3600
    elif time_str.endswith('d'):
        return int(time_str[:-1]) * 86400
    else:
        return int(time_str)


class RequestUtil:
    """Utility class for making requests to PAI APIs"""

    @staticmethod
    def get_request(url: str, token: str, timeout: int = 120):
        """Make GET request with token authentication"""
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            return response
        except Exception as e:
            logger.error(f"Request failed for {url}: {e}")
            return None

    @staticmethod
    def openpai_query(query: str, json_content: bool = True, retry: int = 5):
        """Make requests to OpenPAI REST API"""
        base_url = os.getenv("REST_SERVER_URI", "https://test/rest-server")
        query = query.replace("restserver", "api/v2")

        # Handle log-manager URIs - these come from API v2 /jobs/.../logs endpoint
        # Format: log-manager/<node-ip>:<port>/api/v1/logs/... (no leading slash after lstrip)
        # These must be accessed directly, not through rest-server proxy
        if query.startswith("log-manager/"):
            # Convert to direct HTTP URL: http://<node-ip>:<port>/api/v1/logs/...
            query = query.replace("log-manager/", "//", 1)
            url = f"http:{query}"
        else:
            url = f"{base_url}/{query}"

        token = os.getenv("LTP_TOKEN")
        max_retry = retry
        while retry > 0:
            response = None
            try:
                headers = {"Authorization": f"Bearer {token}"}
                response = requests.get(url, headers=headers)
                if response.ok:
                    #logger.info(f"OpenPAI query successful: {url}")
                    return response.json() if json_content else response.text

                # Log at appropriate level based on status code
                if response.status_code == 404 and 'logs' in url:
                    # 404 is expected for old/cleaned-up logs
                    logger.debug(f"OpenPAI query not found (404): {url}")
                    return None
                elif response.status_code == 429:
                    logger.warning(f"OpenPAI rate limited (429), retry {max_retry - retry + 1}/{max_retry}: {url[:100]}")
                else:
                    # Other errors are unexpected
                    logger.error(f"OpenPAI query failed: {response.status_code} {response.text} {url}")

            except Exception as e:
                logger.error(f"OpenPAI request error: {e}")

            retry -= 1
            if retry > 0:  # Only sleep if we're going to retry
                is_429 = response is not None and (response.status_code == 429 or ('Too many requests' in response.text if response.text else False))
                if is_429:
                    # Exponential backoff for rate limiting: 2^(attempt_number) * base_delay
                    attempt = max_retry - retry  # retry already decremented, so this is 1-based attempt number
                    backoff_delay = (2 ** attempt) * 5
                    logger.warning(f"OpenPAI 429 backoff: sleeping {backoff_delay}s (attempt {attempt})")
                    time.sleep(backoff_delay)
                else:
                    time.sleep(1)  # Small delay for other errors

        if response and (response.status_code == 429 or (response.text and 'Too many requests' in response.text)):
            logger.error(f"OpenPAI rate limited (429) after {max_retry} retries: {url}")
        return None

class PrometheusClient:
    """Client for collecting metrics from Prometheus"""

    def __init__(self):
        self.base_url = os.getenv("PROMETHEUS_SERVER_URI", "https://test/prometheus")
        self.token = os.getenv("LTP_TOKEN")

    def query_range(self, query: str, start_time: int, end_time: int, step: str = "30s", retry: int = 3) -> Optional[Dict]:
        """Query Prometheus for metrics data over time range"""
        offset = f"{int(end_time - start_time)}s"
        query = f"query?query=({query})[{offset}:{step}] @ {end_time}"
        url = f"{self.base_url}/prometheus/api/v1/{query}"
        response = None
        while retry > 0:
            response = RequestUtil.get_request(url, self.token)
            if response and response.ok:
                return json.loads(response.content)["data"]
            if response:
                logger.error(f"Prometheus query failed ({response.status_code}): {query}")
            else:
                logger.error(f"Prometheus query failed (no response): {query}")
            retry -= 1
        return None

    def query(self, query, data, retry=5):
        """
        Query Prometheus for a single metric value.
        Args:
            query (str): Prometheus query string
            data (Dict): Additional data to include in the query
            retry (int): Number of retries for the query
        Returns:
            Dict: Parsed response data from Prometheus
        """
        query = f"{self.base_url}/prometheus/api/v1/query?query={query}"
        max_retry = retry
        while retry > 0:
            response = RequestUtil.get_request(query, self.token)
            if response and response.ok:
                return json.loads(response.content)["data"]
            logger.error(
                f"Prometheus query failed. Query: {query} Response:{response.content}"
            )
            retry -= 1
            if retry > 0:  # Only sleep if we're going to retry
                if response and "Too many requests" in response.text:
                    backoff_delay = (2 ** (max_retry - retry)) * 5
                    logger.warning(f"Rate limited, backing off for {backoff_delay}s (retry {max_retry - retry}/{max_retry})")
                    time.sleep(backoff_delay)
                else:
                    time.sleep(1)
        return None

    def get_metric_names(self) -> Optional[List[str]]:
        """Get all available metric names from Prometheus."""
        url = f"{self.base_url}/prometheus/api/v1/label/__name__/values"
        response = RequestUtil.get_request(url, self.token)
        if response and response.ok:
            data = json.loads(response.content)
            if data.get("status") == "success":
                return sorted(data["data"])
        return None

    def get_metric_labels(self, metric: str) -> Optional[Dict[str, List[str]]]:
        """Get label names and sample values for a metric.

        Returns dict of {label_name: [sample_values]} for discovery.
        """
        # Use a simple instant query to get labels — much faster than /series
        url = f"{self.base_url}/prometheus/api/v1/query?query={metric}"
        response = RequestUtil.get_request(url, self.token)
        if not response or not response.ok:
            return None
        data = json.loads(response.content)
        if data.get("status") != "success":
            return None

        results = data.get("data", {}).get("result", [])
        if not results:
            return None

        # Collect all unique label names and their values from the results
        labels = {}
        for series in results[:50]:  # cap to avoid huge responses
            for label_name, label_value in series.get("metric", {}).items():
                if label_name.startswith("__"):
                    continue
                if label_name not in labels:
                    labels[label_name] = set()
                labels[label_name].add(str(label_value)[:80])  # truncate long values

        # Convert sets to sorted lists, cap at 20 values per label
        return {k: sorted(v)[:20] for k, v in sorted(labels.items())}

    def get_metric_sampling_interval(self, metric_name, retry: int = 5):
        """Get the sampling interval for a given metric."""
        query = f"{metric_name}[10m]"
        output = self.query(query, {}, retry=retry)
        if output:
            values = output["result"][0]["values"]
            return int(float(values[1][0]) - float(values[0][0]))
        return None

    def query_step(
        self, query, data, end_time, time_offset, step="6h"
    ):
        """Query Prometheus with time intervals in parallel.
        Args:
            query (str): Prometheus query string with {time_offset} placeholder
            data (Dict): Additional data to include in the query
            end_time (int): End timestamp in seconds
            time_offset (str): Time offset string (e.g., "1h", "30m")
            step (str): Step size for the time intervals (default: "6h")
        Returns:
            List[Tuple[int, int, List[Dict]]]: List of tuples with start time, end time, and query results
        """
        start_time = end_time - parse_interval(time_offset)
        step_timedelta = parse_interval(step)

        time_intervals = []
        current_time = start_time

        # Prepare time intervals for parallel queries
        while current_time < end_time:
            next_time = min(current_time + step_timedelta, end_time)
            time_intervals.append((int(float(current_time)), int(float(next_time))))
            current_time = next_time

        def query_interval(start, end):
            # Create the query with the specific time range
            time_offset = f"{int((end - start))}s"
            query_with_time = query.replace("{time_offset}", time_offset).replace(
                "{end_time_stamp}", str(int(end))
            )
            result = self.query(
                query_with_time, data
            )
            return (
                start,
                end,
                deepcopy(result["result"]) if result and result["result"] else [],
            )

        # Execute queries in parallel using joblib
        final_value = Parallel(n_jobs=4, backend="threading")(
            delayed(query_interval)(start, end) for start, end in time_intervals
        )

        return final_value


class JobLogsClient:
    """Client for collecting job logs from PAI"""

    def __init__(self):
        pass

    def get_job_logs(self, job_name: str, job_meta: Dict[str, Any], job_retry_id: int = 0, task_role_name: str = "worker",
                     index: int = 0, tail: bool = False, retry: int = 3, log_type: str = "user-all"):
        """Get logs for a specific job via log-manager directly"""
        try:
            logs = {}
            if not job_meta:
                return None

            job_data = job_meta
            username = job_data.get("username", job_name.split("~")[0])
            framework_name = job_data.get("frameworkName")
            if not framework_name:
                logger.error(f"No frameworkName (debugId) in job metadata for {job_name}")
                return None

            nodes = job_data.get("nodes", {})
            logger.debug(f"Downloading logs for {len(nodes)} job nodes via log-manager")

            # Download logs for each node in parallel
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=8) as executor:
                future_to_node = {}
                for node, node_data in nodes.items():
                    container_ip = node_data.get("container_ip")
                    container_id = node_data.get("container_id")
                    node_task_role = node_data.get("task_role_name", task_role_name)

                    if not container_ip or not container_id:
                        logger.debug(f"Missing container_ip or container_id for node {node}, skipping")
                        continue

                    future = executor.submit(
                        self.download_log, username, framework_name,
                        container_ip, container_id, node_task_role, tail, retry, log_type
                    )
                    future_to_node[future] = node

                # Collect results
                for future in as_completed(future_to_node):
                    node = future_to_node[future]
                    try:
                        log_content = future.result()
                        logs[node] = log_content
                    except Exception as e:
                        logger.error(f"Error downloading log for node {node}: {e}")
                        logs[node] = None

            logger.debug(f"Downloaded logs for {len(logs)}/{len(nodes)} job nodes")
            return logs

        except Exception as e:
            logger.error(f"Error getting job logs for {job_name}: {e}")
            return None

    def download_log(self, username: str, framework_name: str, container_ip: str,
                      container_id: str, task_role_name: str,
                      tail: bool = False, retry: int = 3,
                      log_type: str = "user-all") -> Optional[str]:
        """Download log content for a specific task via log-manager directly.

        Builds the log-manager URL from job attempt metadata fields instead of
        going through the rest-server proxy.
        """
        log_manager_port = os.getenv("LOG_MANAGER_PORT", "9103")

        # Get fresh token from log-manager
        node_logs_client = NodeLogsClient()
        node_logs_client.log_manager_port = log_manager_port
        try:
            token = node_logs_client.get_token(container_ip)
        except Exception as e:
            logger.debug(f"Failed to get log-manager token from {container_ip}:{log_manager_port}: {e}")
            return None

        # Build log-manager URL directly
        url = f"http://{container_ip}:{log_manager_port}/api/v1/logs/{log_type}"
        params = {
            "username": username,
            "framework-name": framework_name,
            "pod-uid": container_id,
            "taskrole": task_role_name,
            "token": token,
        }
        if tail:
            params["tail-mode"] = "true"

        # Fetch with retry and backoff
        for attempt in range(retry):
            try:
                resp = requests.get(url, params=params, timeout=60)
                if resp.status_code == 404:
                    logger.debug(f"Job log not found (404): {url}")
                    return None
                if resp.status_code == 429 or (resp.text and 'Too many requests' in resp.text):
                    backoff = (2 ** attempt) * 2
                    logger.warning(
                        f"Log-manager rate limited on {container_ip}, "
                        f"retrying in {backoff}s ({attempt + 1}/{retry})"
                    )
                    if attempt < retry - 1:
                        time.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.text
            except Exception as e:
                if attempt < retry - 1:
                    time.sleep(1)
                else:
                    logger.error(f"Failed to fetch job log from {url}: {e}")
        return None

    def parse_logs_with_patterns(self, log_content: str, patterns: List[Dict],
                                max_entries: int = None) -> List[LogEntry]:
        """Parse log content using regex patterns, or return raw lines if no pattern is defined."""
        if not log_content:
            return []

        log_entries = []
        lines = log_content.split('\n')

        if not patterns:
            # No pattern: return raw lines as log entries
            for line in lines:
                if max_entries and len(log_entries) >= max_entries:
                    break
                line = line.strip()
                if not line:
                    continue
                log_entries.append(LogEntry(
                    message=line,
                    fields={}
                ))
            return log_entries

        # Patterns defined: use regex matching
        for line_num, line in enumerate(lines):
            if max_entries and len(log_entries) >= max_entries:
                break
            line = line.strip()
            if not line:
                continue
            for pattern_info in patterns:
                regex_pattern = pattern_info.get('regex')
                if not regex_pattern:
                    continue
                try:
                    match = re.search(regex_pattern, line)
                    if match:
                        fields = match.groupdict() if hasattr(match, 'groupdict') else {}
                        log_entries.append(LogEntry(
                            message=line,
                            fields=fields
                        ))
                        break  # Only match first pattern per line
                except re.error as e:
                    logger.warning(f"Invalid regex pattern '{regex_pattern}': {e}")
                    continue
        return log_entries

class NodeLogsClient:
    """Client for collecting node system logs via log-manager API"""

    def __init__(self,):
        self.base_url = os.getenv('REST_SERVER_URI')
        self.session = requests.Session()
        self.token = None
        self.log_manager_port = os.getenv("LOG_MANAGER_PORT", "9103")

    def get_token(self, node_ip: str) -> str:
        """Get authentication token for log-manager API on a specific node"""
        payload = {
            "username": os.getenv("LOG_MANAGER_USERNAME"),
            "password": os.getenv("LOG_MANAGER_PASSWORD"),
        }
        # Access log-manager directly, not through rest-server proxy
        url = f"http://{node_ip}:{self.log_manager_port}/api/v1/tokens"
        try:
            res = self.session.post(url, json=payload, timeout=30)
            if res.status_code != 200:
                raise Exception(f"Failed to get authentication token: {res.status_code}")
            token_data = res.json()
            if "token" not in token_data:
                raise Exception("Invalid token response format")
            self.token = token_data["token"]
            logger.debug(f"Token obtained successfully from {url}")
            return self.token
        except Exception as e:
            logger.debug(f"Error getting token from {url}: {e}")
            raise

    def collect_node_logs(self, node_ip: str, log_paths: List[str], patterns: List[Dict],
                         max_entries: int = None, tail_lines: int = 1000,
                         start_time_ts: int = None, end_time_ts: int = None,
                         ignore_time_window: bool = False) -> List[LogEntry]:
        """Collect logs from a node based on patterns with optional time filtering

        Args:
            node_ip: IP address of the node
            log_paths: List of log file paths to collect from
            patterns: List of regex patterns to match
            max_entries: Maximum number of log entries to return
            tail_lines: Number of lines to tail from the log file
            start_time_ts: Start timestamp for time-based filtering (ignored if ignore_time_window=True)
            end_time_ts: End timestamp for time-based filtering (ignored if ignore_time_window=True)
            ignore_time_window: If True, ignore time window and just use tail -n max_entries
        """
        if not self.token:
            self.get_token(node_ip)

        all_matched_lines = []

        # Convert timestamps to string format expected by the API
        # Skip time filtering if ignore_time_window is True
        start_time_str = None
        end_time_str = None

        if not ignore_time_window:
            if start_time_ts:
                start_time_str = datetime.fromtimestamp(start_time_ts).strftime('%Y-%m-%d %H:%M:%S')
            if end_time_ts:
                end_time_str = datetime.fromtimestamp(end_time_ts).strftime('%Y-%m-%d %H:%M:%S')

        try:
            node_results = self._collect_from_node(
                node_ip, log_paths, patterns, max_entries, tail_lines,
                start_time_str, end_time_str
            )
            all_matched_lines.extend(node_results)

            if max_entries and len(all_matched_lines) >= max_entries:
                return all_matched_lines[:max_entries]

            return all_matched_lines

        except Exception as e:
            logger.error(f"Failed to collect from node {node_ip}: {e}")
            return []

    def _collect_from_node(self, node_ip: str, log_paths: List[str], patterns: List[Dict],
                          max_entries: int, tail_lines: int,
                          start_time_str: str = None, end_time_str: str = None) -> List[LogEntry]:
        """Collect logs from a single node with optional time filtering"""
        matched_lines = []

        for log_path in log_paths:
            if max_entries and len(matched_lines) >= max_entries:
                break

            log_filename = log_path.split('/')[-1]
            regex_pattern = None
            if len(patterns) == 1 and patterns[0].get('regex'):
                regex_pattern = patterns[0].get('regex')

            try:
                content = self._get_log_content(
                    node_ip, log_filename, tail_lines,
                    start_time=start_time_str, end_time=end_time_str,
                    regex_pattern=regex_pattern
                )
                # if there is only one pattern, use the regex pattern to filter the log
                for line_num, line in enumerate(content.split('\n')):
                    if max_entries and len(matched_lines) >= max_entries:
                        break

                    line = line.strip()
                    if not line:
                        continue

                    if not patterns:
                        matched_lines.append(LogEntry(message=line, fields={}))
                        continue

                    for pattern_info in patterns:
                        regex_pattern = pattern_info.get('regex')
                        if not regex_pattern:
                            continue
                        try:
                            match = re.search(regex_pattern, line)
                            if match:
                                fields = match.groupdict()
                                matched_lines.append(LogEntry(
                                    message=line,
                                    fields=fields
                                ))
                                break
                        except re.error as e:
                            logger.warning(f"Invalid regex pattern '{regex_pattern}': {e}")
                            continue
            except Exception as e:
                logger.error(f"Error processing {node_ip}:{log_path}: {e}")
                continue

        return matched_lines

    def _get_log_content(self, node_ip: str, log_filename: str, lines: int = 1000,
                        tail_mode: bool = True, start_time: str = None, end_time: str = None, regex_pattern: str = None,
                        max_retries: int = 3) -> str:
        """Get log content from specific node with optional time filtering.

        Retries with exponential backoff on log-manager rate limiting (429).
        """
        # Access log-manager directly, not through rest-server proxy
        url = f"http://{node_ip}:{self.log_manager_port}/api/v1/node-logs/{log_filename}"
        params = {
            "token": self.token,
        }
        # When time range is specified, use server-side time filtering
        # without tail-mode — avoids missing events beyond tail_lines
        if start_time:
            params["start-time"] = start_time
        if end_time:
            params["end-time"] = end_time
        if regex_pattern:
            params["log-regex"] = regex_pattern
        # Only use tail-mode when no time filter is provided
        if not start_time and not end_time:
            params["lines"] = lines
            if tail_mode:
                params["tail-mode"] = "true"

        last_exc = None
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=60)
                if response.status_code == 429 or (response.text and 'Too many requests' in response.text):
                    backoff = (2 ** attempt) * 2
                    logger.warning(
                        f"Log-manager rate limited on {node_ip}:{log_filename}, "
                        f"retrying in {backoff}s ({attempt + 1}/{max_retries})"
                    )
                    if attempt < max_retries - 1:
                        time.sleep(backoff)
                    continue
                response.raise_for_status()
                return response.text
            except Exception as e:
                last_exc = e
                if attempt < max_retries - 1:
                    time.sleep(1)

        logger.error(f"Failed to get log content from {node_ip}:{log_filename} after {max_retries} retries: {last_exc}")
        raise last_exc

class JobMetadataClient:
    """Client for collecting job metadata from PAI

    Caches get_job_metadata() and get_filtered_job_attempts() results
    to avoid hammering the REST API when multiple collectors run in
    the same cycle. Completed job data doesn't change, so caching is safe.
    """

    # Class-level cache: shared across all instances within the same process
    _metadata_cache = {"data": None, "timestamp": 0.0}
    _metadata_cache_ttl = 120  # seconds — basic job list TTL

    # Attempts cache: file-based for cross-process sharing (see get_filtered_job_attempts)
    _attempts_cache_ttl = 300  # seconds — attempt details TTL (5 min)

    def __init__(self):
        pass

    def get_job_metadata(self) -> Dict[str, Any]:
        """Get metadata for all jobs (basic job list without attempts).

        Results are cached for 2 minutes to avoid hammering the REST API
        when multiple collectors run in the same cycle.
        """
        import time as _time
        now = _time.time()
        if self._metadata_cache["data"] is not None and (now - self._metadata_cache["timestamp"]) < self._metadata_cache_ttl:
            logger.debug(f"get_job_metadata: returning cached result ({len(self._metadata_cache['data'])} jobs, age={now - self._metadata_cache['timestamp']:.0f}s)")
            return self._metadata_cache["data"]

        query = "restserver/jobs?offset=0&limit=49999&withTotalCount=true&order=completionTime"
        job_metadatas = RequestUtil.openpai_query(query)

        if not job_metadatas:
            logger.error("get_job_metadata: API returned None (check REST_SERVER_URI, LTP_TOKEN, rate limiting)")
            # Return stale cache if available rather than empty dict
            if self._metadata_cache["data"] is not None:
                logger.warning("get_job_metadata: returning stale cache due to API failure")
                return self._metadata_cache["data"]
            return {}

        data = job_metadatas.get("data", [])
        formatted_metadata = {}

        for job in data:
            job_key = f'{job["username"]}~{job["name"]}'
            formatted_metadata[job_key] = job

        self._metadata_cache["data"] = formatted_metadata
        self._metadata_cache["timestamp"] = now
        return formatted_metadata

    def get_job_attempt_metadata(self, job_key, attempt_id, job_data=None):
        """Get detailed metadata for a specific job attempt/retry"""
        attempt_metadata = self.fetch_job_attempt_metadata(job_key, attempt_id)
        if not attempt_metadata:
            return None

        # Create unique key for this attempt
        attempt_key = f"{job_key}~{attempt_id}"
        # Merge basic job data with attempt-specific data
        if not job_data:
            job_data = {}
        attempt_data = job_data.copy()
        attempt_data.update({
            "attemptId": attempt_id,
            'jobPriority': attempt_data.get("jobPriority") if attempt_data.get("jobPriority") else 'default',
            "state": attempt_metadata.get("jobStatus", {}).get("attemptState"),
            "attemptState": attempt_metadata.get("jobStatus", {}).get("attemptState"),
            "createdTime": attempt_metadata.get("jobStatus", {}).get("appCreatedTime"),
            "launchedTime": attempt_metadata.get("jobStatus", {}).get("appLaunchedTime", None),
            "completedTime": attempt_metadata.get("jobStatus", {}).get("appCompletedTime", None),
            "submissionTime": attempt_metadata.get("jobStatus", {}).get("submissionTime"),
            "taskRoles": attempt_metadata.get("taskRoles", {}),
            "job_id": attempt_key,
            "exitSpec": attempt_metadata.get("exitSpec"),
            "frameworkName": attempt_metadata.get("debugId"),
            "nodes": self.get_nodes_in_attempt(attempt_metadata)
        })
        attempt_data.update({
            'submissionDatetime': pd.to_datetime(attempt_data.get("submissionTime"), unit='ms'),
            'launchedDatetime': pd.to_datetime(attempt_data.get("launchedTime"), unit='ms'),
            'completedDatetime': pd.to_datetime(attempt_data.get("completedTime"), unit='ms') if attempt_data.get("completedTime") else None,
            'createdDatetime': pd.to_datetime(attempt_data.get("createdTime"), unit='ms')
                    })
        return attempt_data

    def get_job_attempts_metadata(self, start_time: int, end_time: int, job_finish=False, basic_jobs=None) -> Dict[str, Any]:
        """
        Get metadata for all job attempts within the specified time window.
        Each attempt is treated as a separate job for analysis purposes.

        Args:
            start_time: Start timestamp in seconds
            end_time: End timestamp in seconds
            basic_jobs: Pre-fetched job metadata dict. If None, fetches from API.

        Returns:
            Dictionary where each key is 'username~jobname~attemptId' and value is attempt metadata
        """
        # First get all jobs
        if basic_jobs is None:
            basic_jobs = self.get_job_metadata()
        all_attempts = {}

        for job_key, job_data in basic_jobs.items():
            try:
                # Get all attempts for this job
                attempts = job_data.get("retries", 0)

                for attempt_id in range(attempts+1):
                    if (job_data.get("completedTime") or start_time * 1000) < start_time * 1000:
                        # Job completed before the time window
                        continue
                    # Get detailed metadata for this attempt
                    attempt_metadata = self.get_job_attempt_metadata(job_key, attempt_id, job_data)
                    if not attempt_metadata:
                        continue                    # Check if this attempt was active during the time window
                    attempt_start = attempt_metadata.get("createdTime")
                    attempt_end = attempt_metadata.get("completedTime")

                    if attempt_start:
                        attempt_start_sec = attempt_start / 1000
                        attempt_end_sec = attempt_end / 1000 if attempt_end else end_time

                        if job_finish and attempt_end is None:
                            # Skip attempts that are not finished
                            continue

                        # Attempt was active during the time window
                        if (attempt_start_sec < end_time and attempt_end_sec >= start_time):
                            attempt_key = attempt_metadata.get("job_id", f"{job_key}~{attempt_id}")
                            # Store attempt metadata
                            all_attempts[attempt_key] = attempt_metadata

            except Exception as e:
                logger.error(f"Error processing attempts for job {job_key}: {e}")
                continue

        return all_attempts

    def get_filtered_job_attempts(self, start_time: int, end_time: int,
                                 filters=None, basic_jobs=None,
                                 job_filter: str = "", attempt_filter: str = "") -> Dict[str, Any]:
        """
        Get job attempts with two-level filtering.

        Args:
            start_time: Start timestamp in seconds
            end_time: End timestamp in seconds
            filters: Legacy dict filters (backward compat). Ignored if job_filter/attempt_filter set.
            basic_jobs: Pre-fetched job metadata. If None, fetches from API.
            job_filter: Eval on basic job data (pre-filter, fast). Safe fields:
                        totalTaskNumber, username, name, virtualCluster, tags, etc.
                        NOT safe: state, duration (vary per attempt).
            attempt_filter: Eval on attempt data (post-filter, accurate). All fields:
                        state, duration, gpu_count, nodes, taskRoles, etc.

        Available in eval: now_ms, d(n), h(n), m(n), int, str, len, abs, min, max
        All job/attempt metadata fields are accessible by name.

        Examples:
            job_filter="totalTaskNumber > 32"
            attempt_filter="state == 'FAILED' and launchedTime and now_ms - launchedTime > d(14)"
        """
        import time as _time

        if basic_jobs is None:
            basic_jobs = self.get_job_metadata()

        now_ms = int(_time.time() * 1000)
        eval_scope = {
            "__builtins__": {},
            "now_ms": now_ms,
            "d": lambda n: n * 86400000,
            "h": lambda n: n * 3600000,
            "m": lambda n: n * 60000,
            "True": True, "False": False, "None": None,
            "int": int, "str": str, "float": float, "len": len,
            "abs": abs, "min": min, "max": max,
        }

        def _eval_match(data, expr):
            if not expr:
                return True
            try:
                return bool(eval(expr, dict(eval_scope), dict(data)))
            except Exception:
                return False

        logger.info(f"get_filtered_job_attempts: basic_jobs={len(basic_jobs)}, job_filter={repr(job_filter)}, attempt_filter={repr(attempt_filter)}")

        # Guard: if job_filter references attempt-level fields, move to attempt_filter
        # Fields that can't be safely used in job_filter.
        # Two reasons: (1) value reflects latest attempt only (state, times),
        # or (2) not available in basic job list (nodes, taskRoles, exitSpec).
        # Auto-guard: detect fields in job_filter that are unsafe at job level.
        #
        # Safe for job_filter:
        #   username, name, virtualCluster, tags, executionType, jobPriority,
        #   totalTaskNumber, totalGpuNumber, totalTaskRoleNumber, retries, debugId,
        #   gpu_count (aliased from totalGpuNumber)
        #
        # state is conditionally safe:
        #   "RUNNING" / "WAITING" → safe (job must be in this state for any attempt to be)
        #   "FAILED" / "SUCCEEDED" / "STOPPED" → unsafe (earlier attempts of retried jobs may differ)
        #
        # Always unsafe: times (vary per attempt), nodes/taskRoles/exitSpec (not in basic job list)
        ALWAYS_UNSAFE = {
            "launchedTime", "createdTime", "completedTime",
            "launchedDatetime", "createdDatetime", "completedDatetime",
            "nodes", "taskRoles", "exitSpec", "attemptState",
        }
        # State values that are safe at job level (job can't have these without being in this state)
        SAFE_STATE_VALUES = {"RUNNING", "WAITING"}

        if job_filter:
            should_move = False
            reason = ""

            for field in ALWAYS_UNSAFE:
                if field in job_filter:
                    should_move = True
                    reason = field
                    break

            # Check if 'state' is used with an unsafe value
            if not should_move and "state" in job_filter:
                # If any non-safe state value appears, move to attempt_filter
                has_unsafe_state = any(
                    s in job_filter for s in ["FAILED", "SUCCEEDED", "STOPPED", "TIMEOUT"]
                )
                if has_unsafe_state:
                    should_move = True
                    reason = "state with non-running value"
                # else: state == "RUNNING" or "WAITING" → safe, keep in job_filter

            if should_move:
                logger.warning(
                    f"job_filter contains '{reason}' which varies per attempt. "
                    f"Moving to attempt_filter for correctness."
                )
                attempt_filter = f"({job_filter}) and ({attempt_filter})" if attempt_filter else job_filter
                job_filter = ""

        # Legacy dict filter support (backward compat with NFD callers)
        legacy_state_filter = None
        if filters and isinstance(filters, dict) and not job_filter and not attempt_filter:
            legacy_state_filter = filters.get('status') or filters.get('state')
            if isinstance(legacy_state_filter, str):
                legacy_state_filter = [legacy_state_filter]

        # Step 2: Pre-filter on basic job data
        candidate_jobs = {}
        for job_key, job_data in basic_jobs.items():
            # Time window
            if (job_data.get("completedTime") or start_time * 1000) < start_time * 1000:
                continue

            # job_filter eval — add gpu_count alias for convenience
            if job_filter:
                enriched = dict(job_data)
                enriched["gpu_count"] = job_data.get("totalGpuNumber", 0)
                if not _eval_match(enriched, job_filter):
                    continue

            # Legacy dict: instance count (safe at job level)
            if filters and isinstance(filters, dict) and filters.get('job_instance_count_more_than'):
                total_tasks = job_data.get('totalTaskNumber')
                if total_tasks is not None and int(total_tasks) <= int(filters['job_instance_count_more_than']):
                    continue

            candidate_jobs[job_key] = job_data

        logger.info(f"Step 2: Pre-filtered {len(basic_jobs)} jobs to {len(candidate_jobs)} candidates (job_filter={repr(job_filter)})")

        # Step 3: Fetch attempt details (with cross-process cache)
        # The expensive API calls are cached to a file so ALL processes (cron jobs, MCP tool calls)
        # share the same cache. Completed job data doesn't change, so caching is safe.
        import pickle as _pickle
        _cache_dir = os.environ.get("PATROL_LOG_DIR", "/tmp/patrol_cron/logs")
        _cache_dir = os.path.join(os.path.dirname(_cache_dir), ".attempts_cache") if os.path.isdir(os.path.dirname(_cache_dir)) else "/tmp/patrol_cron/.attempts_cache"
        os.makedirs(_cache_dir, exist_ok=True)
        _cache_file = os.path.join(_cache_dir, "attempts.pkl")

        all_attempts = None
        _now = time.time()

        # Try reading cache file
        try:
            if os.path.exists(_cache_file):
                with open(_cache_file, "rb") as f:
                    _cached = _pickle.load(f)
                if _cached.get("data") and (_now - _cached.get("timestamp", 0)) < self._attempts_cache_ttl:
                    _cst = _cached.get("start_time", 0)
                    _cet = _cached.get("end_time", 0)
                    if (start_time - 60) <= _cst <= (start_time + 60) and (end_time - 60) <= _cet <= (end_time + 60):
                        all_attempts = _cached["data"]
                        logger.info(f"Step 3: Using cached attempts ({len(all_attempts)} entries, age={_now - _cached['timestamp']:.0f}s)")
        except Exception as e:
            logger.debug(f"Cache read failed: {e}")

        if all_attempts is None:
            all_attempts = {}
            fetch_errors = 0
            fetch_ok = 0
            for job_key, job_data in candidate_jobs.items():
                try:
                    attempts = job_data.get("retries", 0)
                    for attempt_id in range(attempts + 1):
                        attempt_metadata = self.get_job_attempt_metadata(job_key, attempt_id, job_data)
                        if not attempt_metadata:
                            fetch_errors += 1
                            continue
                        fetch_ok += 1
                        attempt_start = attempt_metadata.get("createdTime")
                        attempt_end = attempt_metadata.get("completedTime")
                        if attempt_start:
                            attempt_start_sec = attempt_start / 1000
                            attempt_end_sec = attempt_end / 1000 if attempt_end else end_time
                            if attempt_start_sec < end_time and attempt_end_sec >= start_time:
                                attempt_key = attempt_metadata.get("job_id", f"{job_key}~{attempt_id}")
                                all_attempts[attempt_key] = attempt_metadata
                except Exception as e:
                    logger.error(f"Error processing attempts for job {job_key}: {e}")
                    continue

            logger.info(f"Step 3: Fetched {fetch_ok} attempts ({fetch_errors} failed) → {len(all_attempts)} in time window")

            # Write cache file atomically for other processes (pickle for large dicts)
            try:
                _cached = {
                    "start_time": start_time,
                    "end_time": end_time,
                    "timestamp": _now,
                    "data": all_attempts,
                }
                _tmp_file = _cache_file + ".tmp"
                with open(_tmp_file, "wb") as f:
                    _pickle.dump(_cached, f, protocol=_pickle.HIGHEST_PROTOCOL)
                os.replace(_tmp_file, _cache_file)  # atomic on POSIX
                logger.info(f"Step 3: Wrote attempts cache ({len(all_attempts)} entries)")
            except Exception as e:
                logger.warning(f"Cache write failed: {e}")

        # Step 4: Post-filter on attempt data
        filtered_attempts = {}
        for attempt_key, attempt_data in all_attempts.items():
            # attempt_filter eval
            if attempt_filter and not _eval_match(attempt_data, attempt_filter):
                continue

            # Legacy dict: state filter at attempt level
            if legacy_state_filter:
                attempt_state = (attempt_data.get('state') or '').lower()
                if attempt_state not in [s.lower() for s in legacy_state_filter]:
                    continue

            # Legacy dict: runtime filter
            if filters and isinstance(filters, dict) and filters.get('runtime_more_than'):
                filter_runtime = parse_interval(filters['runtime_more_than'])
                completed = attempt_data.get('completedTime') or (end_time * 1000)
                launched = attempt_data.get('launchedTime')
                if launched and (completed - launched) / 1000 < filter_runtime:
                    continue

            filtered_attempts[attempt_key] = attempt_data

        logger.info(f"Step 4: Post-filtered {len(all_attempts)} → {len(filtered_attempts)} (attempt_filter={repr(attempt_filter)})")
        return filtered_attempts

    def get_nodes_in_attempt(self, attempt_data: Dict[str, Any]) -> Dict[str, Any]:
        """Get nodes in attempt"""
        nodes = {}
        for task_role_name, task_role_data in attempt_data.get('taskRoles', {}).items():
            for task_status in task_role_data.get('taskStatuses', []):
                node_name = task_status.get('containerNodeName', None)
                task_state = task_status.get('taskState', None)
                exit_spec = task_status.get('containerExitSpec', {})
                if node_name:
                    nodes[node_name] = {
                        "task_state": task_state,
                        "exit_spec": exit_spec,
                        "task_role_index": task_status.get('taskIndex', None),
                        "container_ip": task_status.get('containerIp', None),
                        "container_id": task_status.get('containerId', None),
                        "task_role_name": task_role_name,
                    }
        return nodes


    def get_filtered_job_nodes(self, start_time: int, end_time: int,
                                 filters) -> List[str]:
        """Get nodes in filtered job attempts"""
        filtered_attempts = self.get_filtered_job_attempts(start_time, end_time, filters)
        nodes = []
        for attempt_key, attempt_data in filtered_attempts.items():
            nodes.extend(self.get_nodes_in_attempt(attempt_data).keys())
        return nodes


    def fetch_job_attempt_metadata(self, job_name: str, job_retry_id: int = 0) -> Optional[Dict[str, Any]]:
        """Get detailed metadata for a specific job attempt/retry"""
        query = f"restserver/jobs/{job_name}/attempts/{job_retry_id}/"
        attempt_metadata = RequestUtil.openpai_query(query)

        if not attempt_metadata:
            return None

        return attempt_metadata

    def get_job_attempts_list(self, job_name: str) -> List[int]:
        """Get list of all attempt IDs for a job"""
        query = f"restserver/jobs/{job_name}/attempts"
        attempts_data = RequestUtil.openpai_query(query)

        if not attempts_data:
            return []

        attempts = attempts_data.get("attempts", [])
        return [attempt.get("attemptId", 0) for attempt in attempts if attempt.get("attemptId") is not None]

    def get_job_list(self, end_time_stamp: int, time_offset: str, finished: bool = False) -> List[str]:
        """
        Get list of job attempts within time window.
        Each attempt is treated as a separate job for analysis purposes.

        Args:
            end_time_stamp: End timestamp in seconds
            time_offset: Time offset string (e.g., "1h", "30m")
            finished: If True, only return completed attempts

        Returns:
            List of job attempt keys in format 'username~jobname~attemptId'
        """
        start_time = end_time_stamp - parse_interval(time_offset)

        # Get all attempts within the time window
        all_attempts = self.get_job_attempts_metadata(start_time, end_time_stamp)

        filtered_attempts = []
        for attempt_key, attempt_data in all_attempts.items():
            attempt_state = attempt_data.get("attemptState", None)
            completed_time = attempt_data.get("completedTime", None)

            if finished:
                # Only include completed attempts
                if completed_time and attempt_state in ["SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT"]:
                    filtered_attempts.append(attempt_key)
            else:
                # Include all attempts (running, completed, failed, etc.)
                filtered_attempts.append(attempt_key)

        return filtered_attempts
