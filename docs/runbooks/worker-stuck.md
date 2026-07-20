# Worker Stuck

1. Stop new live scans and inspect queue age, heartbeat, active stage, and request ID.
2. Mark the worker unavailable and terminate only after its heartbeat is stale.
3. Let durable recovery requeue the scan; verify planned-call consumption first.
4. Restart one worker, observe a fixture/replay job, then restore capacity.
5. Do not manually duplicate a paid provider request.

