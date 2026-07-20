# Bad Public Deployment

1. Disable traffic to the new revision and preserve its logs/release manifest.
2. Roll frontend, API, and worker to prior image digests.
3. Restore prior configuration and dataset activation pointers.
4. Use forward-fix for unsafe schema downgrades.
5. Run no-cost health, fixture, replay, and routing smoke tests before traffic.

