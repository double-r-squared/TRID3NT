"""GRACE-2 session broker package.

The thin always-on connection broker for Fargate-per-session agent isolation
(reports/design/agent_isolation_spike.md). See app.py for the per-connection
control flow, routing.py for resolve/provision, cognito_verify.py for the
zero-drift Cognito verify, and proxy.py for the (skeleton) byte-proxy.
"""
