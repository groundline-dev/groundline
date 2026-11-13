# Public IP HTTPS endpoints before custom domains

Requiring a customer-owned domain would add DNS setup to deployment, either manually or through provider-specific automation. Disposable deployments therefore expose a publicly trusted HTTPS endpoint at their public IP address, with custom-domain support deferred to separate work. This simplifies initial setup for developer callers, at the cost of callers updating their endpoint when the address changes and relying on Caddy to renew short-lived certificates automatically.
