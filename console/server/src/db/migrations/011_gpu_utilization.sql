-- GPU utilization snapshots — collected every 5 min from Prometheus
CREATE TABLE gpu_utilization_snapshots (
  id                  SERIAL PRIMARY KEY,
  timestamp           TIMESTAMPTZ NOT NULL DEFAULT now(),
  sku                 VARCHAR(32) NOT NULL,
  virtual_cluster     VARCHAR(64) NOT NULL,
  total_gpus          INTEGER,
  used_gpus           INTEGER,
  active_gpus         INTEGER,
  idle_gpus           INTEGER,
  sum_gpu_util        FLOAT,
  avg_gpu_util        FLOAT,
  unhealthy_gpus      INTEGER
);

CREATE INDEX idx_gpu_snap_ts  ON gpu_utilization_snapshots (timestamp);
CREATE INDEX idx_gpu_snap_vc  ON gpu_utilization_snapshots (virtual_cluster, timestamp);
CREATE INDEX idx_gpu_snap_sku ON gpu_utilization_snapshots (sku, timestamp);
