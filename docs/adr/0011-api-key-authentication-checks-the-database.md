# API-key authentication checks the database

Caching accepted API keys would save database lookups, but immediate revocation would require coordinating invalidation across API workers and app instances, preventing stale cache fills, and handling missed notifications.
Each protected request instead validates the API key's hash, status, and expiry with a fresh read from the primary PostgreSQL database, so checks starting after revocation commits reject the API key; requests already authorized may finish. We accept a database lookup per authenticated request and dependence on database availability in exchange for simpler, consistent revocation and a CLI that can revoke directly in PostgreSQL; database failures prevent authorization, while public health checks remain available.

I am making the assumption for now that we are just using one app server, but that this could change. So based on napkin math, our database can handle 2-3x the load. I am assuming there will not be a consistent 1000 requests per second to the users of this app who auto-deploy to their VPC. If this assumption is incorrect, then we should plan for a cache.
