"""Locust load test for the polyarb daemon API.

Run with::

    locust -f polyarb/tests/load/locustfile.py --host http://localhost:8080
"""

from __future__ import annotations

from locust import HttpUser, between, task


class DaemonUser(HttpUser):
    """Simulates a dashboard client polling the daemon API."""

    wait_time = between(0.5, 2.0)

    @task(5)
    def get_status(self):
        self.client.get("/health/status")

    @task(3)
    def get_matches(self):
        self.client.get("/matches")

    @task(3)
    def get_opportunities(self):
        self.client.get("/opportunities")

    @task(2)
    def get_config(self):
        self.client.get("/config")

    @task(1)
    def health_ready(self):
        self.client.get("/health/ready")

    @task(1)
    def health_live(self):
        self.client.get("/health/live")

    @task(1)
    def get_metrics(self):
        self.client.get("/metrics")
